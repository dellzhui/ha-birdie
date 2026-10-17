# Birdie Cloud Bridge API (v1)

Status: **draft**. Last updated 2026-10-17.

The contract between the Birdie Home Assistant integration (client) and the
Birdie Cloud Bridge (server). The bridge holds Birdie's Tuya Cloud project
credentials and exposes only what one signed-in Birdie user may see and do.
See `architecture.md` for why the bridge exists.

Sections marked **TBD** depend on how the Birdie app signs users in (Firebase
Auth or Tuya's user pool); this is being confirmed with Birdie.

## 1. Conventions

* Base URL: `https://<bridge-host>/v1`. HTTPS only.
* Request and response bodies are JSON (`application/json; charset=utf-8`).
* Timestamps are ISO 8601 in UTC, e.g. `2026-10-17T12:00:00Z`.
* Every response carries `X-Request-ID`. A client may send its own
  `X-Request-ID`; the bridge echoes it. Include it when reporting problems.
* Compatibility: within `v1` the bridge only makes additive changes (new
  fields, new status codes, new models). Clients must ignore unknown fields
  and must not fail on unknown DP codes or models.

## 2. Authentication

Every endpoint except `GET /v1/health` requires:

```
Authorization: Bearer <access_token>
```

The bridge resolves the token to exactly one Birdie user and, through that
user, to their Tuya user. All device access is limited to that user's devices.

### 2.1 Token issuance (TBD)

To be specified once Birdie confirms its sign-in system. Requirements for any
variant:

* Access tokens are short-lived (at most 1 hour) and refreshable without
  asking the user to sign in again.
* Users who sign in with Google, Apple or Facebook (no password) must be
  supported, so Home Assistant cannot rely on a username/password form alone.
* The client stores only the refresh token (or equivalent) in its config
  entry, never the user's password.

Candidate variants:

| Variant | Access token | Bridge validation |
|---|---|---|
| Firebase Auth | Firebase ID token | Verify signature with Google's public keys, check `aud`/`iss` = Birdie's Firebase project, map Firebase UID to Tuya UID |
| Tuya user pool | Bridge-issued token after a Tuya-backed sign-in | Bridge session store keyed to the Tuya UID |

### 2.2 Development tokens

For local development and remote debugging only, the bridge can be started
with a static token map (environment variable, never committed), e.g.
`BRIDGE_DEV_TOKENS=<token>=<tuya_uid>`. Development tokens must be disabled
in any deployed environment.

## 3. Resources

### 3.1 Device object

```json
{
  "id": "bf1234567890abcdef",
  "name": "Living Room Purifier",
  "model": "pedestal",
  "model_name": "Birdie Pedestal",
  "product_id": "a2zf7q0zxcutn5dy",
  "online": true
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Tuya device id. Stable; use it as the unique id. |
| `name` | string | Name the user gave the device in the Birdie app, else the product name. |
| `model` | string | Birdie model key derived by the bridge from the Tuya product id. Currently only `pedestal`. |
| `model_name` | string | Display name for the device registry. |
| `product_id` | string | Tuya product id, informational. |
| `online` | boolean | Whether Tuya Cloud currently sees the device as connected. |

The bridge **never** returns Tuya `local_key`, IP address, geolocation, the
Tuya UID or any credential.

### 3.2 Status object

A flat map of DP code to its latest reported value, as reported by Tuya:

```json
{
  "switch": true,
  "pm25": 12,
  "mode": "auto",
  "air_quality": "good",
  "filter_total_minutes": 129600,
  "filter_used_minutes": 32400,
  "filter_status": false,
  "reset_filter": false,
  "filter_cover": false,
  "hw_version": "1.0.0",
  "device_error": "normal"
}
```

Values are passed through unchanged; interpretation (units, meaning of the
boolean DPs) belongs to the client's device model. DPs may be missing, for
example right after pairing. Unknown DPs must be ignored by the client.

## 4. Endpoints

### 4.1 `GET /v1/health`

No authentication. Returns `200 {"status": "ok"}` for uptime monitoring.

### 4.2 `GET /v1/account`

Identifies the signed-in account. Clients use `id` to avoid adding the same
account twice and to match re-authentication to the right config entry.

Response `200`:

```json
{ "id": "a1b2c3d4e5f6", "display_name": "jane@example.com" }
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Opaque, stable per Birdie account. Not the Tuya UID. |
| `display_name` | string or null | Optional human-readable label (e.g. e-mail) for the config entry title. |

### 4.3 `GET /v1/devices`

Lists the signed-in user's supported Birdie devices. Devices of unsupported
products are omitted.

Query parameters:

| Name | Values | Default | Notes |
|---|---|---|---|
| `include` | `status` | none | Also return each device's status and `fetched_at`. |

Response `200`:

```json
{
  "devices": [
    {
      "id": "bf1234567890abcdef",
      "name": "Living Room Purifier",
      "model": "pedestal",
      "model_name": "Birdie Pedestal",
      "product_id": "a2zf7q0zxcutn5dy",
      "online": true,
      "status": { "switch": true, "pm25": 12, "mode": "auto" },
      "fetched_at": "2026-10-17T12:00:00Z"
    }
  ]
}
```

`GET /v1/devices?include=status` is the **polling endpoint**: one request
returns everything Home Assistant needs for all of a user's devices.

### 4.4 `GET /v1/devices/{device_id}`

One device with its status. Same object as an element of
`GET /v1/devices?include=status`.

Errors: `404 device_not_found` if the device does not exist **or** does not
belong to the user (the bridge does not reveal which).

### 4.5 `GET /v1/devices/{device_id}/specification`

The device's data point specification, used by the client to validate
commands. Changes only with firmware/product updates; clients should cache it
for the lifetime of the config entry.

Response `200`:

```json
{
  "category": "kj",
  "functions": [
    { "code": "switch", "type": "Boolean", "values": {} },
    { "code": "mode", "type": "Enum",
      "values": { "range": ["auto", "sleep", "manual1", "manual2", "manual3", "manual4"] } },
    { "code": "reset_filter", "type": "Boolean", "values": {} }
  ],
  "status": [
    { "code": "pm25", "type": "Integer",
      "values": { "unit": "ug/m3", "min": 0, "max": 500, "scale": 0, "step": 1 } }
  ]
}
```

`functions` lists writable DPs, `status` lists reported DPs. `values` is a
JSON object (the bridge decodes Tuya's JSON-in-a-string encoding). `type` uses
Tuya's names: `Boolean`, `Integer`, `Enum`, `String`, `Json`, `Raw`, `Bitmap`.

### 4.6 `POST /v1/devices/{device_id}/commands`

Sends one or more DP commands. Request:

```json
{ "commands": [ { "code": "mode", "value": "manual2" } ] }
```

* 1 to 5 commands per request.
* Only DPs on the bridge's allow-list for the device's model are accepted:

  | Model | Code | Accepted values |
  |---|---|---|
  | `pedestal` | `switch` | `true`, `false` |
  | `pedestal` | `mode` | values in the device's `mode` range |
  | `pedestal` | `reset_filter` | `true` |

Response `202`:

```json
{ "accepted": true }
```

`202` means Tuya Cloud accepted the command for delivery; it does not prove
the device has applied it. Clients confirm by reading status afterwards.

Errors: `404 device_not_found`, `409 device_offline`, `422 command_rejected`.

## 5. Errors

Every error uses this body:

```json
{
  "error": {
    "code": "device_offline",
    "message": "The device is not connected to the cloud.",
    "request_id": "6f1c2b7e0f7a4d0c"
  }
}
```

`message` is for logs, not for display or parsing; clients branch on `code`.

| HTTP | `code` | Meaning | Expected Home Assistant behaviour |
|---|---|---|---|
| 400 | `invalid_request` | Malformed JSON or parameters | Bug; log and raise an error |
| 401 | `unauthorized` | Missing, invalid or expired token | Refresh the token once; if that fails, start re-authentication |
| 403 | `account_disabled` | The user may not use the bridge | Start re-authentication |
| 404 | `device_not_found` | Unknown device, or not the user's | Mark device unavailable; the next device list sync removes it |
| 409 | `device_offline` | Command sent to an offline device | Show "device offline" to the user; not a polling failure |
| 422 | `command_rejected` | DP not allowed, value invalid, or rejected by the device | Show error; do not retry |
| 404 | `not_found` | Unknown endpoint | Bug or version mismatch; log |
| 405 | `method_not_allowed` | Wrong HTTP method | Bug; log |
| 429 | `rate_limited` | Too many requests from this user | Back off for `Retry-After` seconds |
| 502 | `upstream_error` | Tuya returned an unexpected error | Treat as transient polling failure |
| 503 | `upstream_unavailable` | Tuya unreachable, or Birdie's Tuya plan/quota problem | Treat as transient; respect `Retry-After` if present |
| 500 | `internal_error` | Bridge bug | Treat as transient |

`429` and `503` responses may include `Retry-After` (seconds).

## 6. Client guidance (Home Assistant)

* Poll `GET /v1/devices?include=status` once per config entry, default every
  60 seconds, never more often than every 30 seconds.
* After a successful command, request one extra refresh after about 2 seconds.
* Fetch `GET /v1/devices/{id}/specification` once per device and cache it.
* Treat `online: false` as "entities unavailable", not as an error.

## 7. Server requirements (bridge)

* **Ownership:** every device-scoped request checks that the device belongs to
  the token's Tuya user. The ownership list may be cached for up to 60 seconds.
* **Allow-lists:** only products in the bridge's product map are listed, and
  only allow-listed commands are forwarded.
* **Secrets:** Tuya credentials come from the deployment's secret store. Tokens,
  secrets and `local_key` are never logged or returned.
* **Quota:** status may be served from a cache of at most 10 seconds;
  a successful command invalidates that device's cache. Prefer Tuya calls that
  return status for several devices at once.
* **Rate limiting:** per user, e.g. 60 requests per minute (tunable).
* **Implementation note:** the reference bridge reads a user's devices and
  their status in one Tuya call (`GET /v1.0/users/{uid}/devices`, a legacy
  but still documented API). If Tuya retires it, only
  `BridgeService._user_devices` needs to change.
* **Error mapping from Tuya:**

  | Tuya condition | Bridge response |
  |---|---|
  | Device offline (2001, 40000801) | `409 device_offline` |
  | Command/value not supported (1103, 2003, 2008, 40000003, 40000308) | `422 command_rejected` |
  | Frequency limit (1110, 1111, 1199) | `429 rate_limited` |
  | Plan, subscription or quota (28841xxx) | `503 upstream_unavailable` + alert operators |
  | Project credential or permission error (1001-1005, 1106, 1114) | `503 upstream_unavailable` + alert operators |
  | Network error or timeout | `503 upstream_unavailable` |
  | Any other Tuya error | `502 upstream_error` |

## 8. Open items

1. Token issuance and refresh (section 2.1): depends on Birdie's answer.
2. Where the Birdie user to Tuya UID mapping lives (Birdie backend, or derived
   from Tuya UID login).
3. Deployment target (Firebase Cloud Functions or Cloud Run) and region.
4. Final product name for `model_name` ("Pedestal" in the Tuya console,
   "Podium" in public material).
