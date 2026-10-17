# Birdie Cloud Bridge API (v1)

Status: **draft**. Last updated 2026-10-17 (domain model: opaque device ids,
state objects and named commands replace Tuya DP passthrough).

The contract between the Birdie Home Assistant integration (client) and the
Birdie Cloud Bridge (server). The bridge holds Birdie's Tuya Cloud project
credentials and exposes only what one signed-in Birdie user may see and do.
See `architecture.md` for why the bridge exists.

The API describes devices in **Birdie terms only**: opaque device ids, a
per-model state object and named commands. No Tuya identifier, data point
(DP) code or DP value appears in it, so the API can be implemented by any
backend (the reference bridge in `bridge/`, or Birdie's own backend) and the
cloud platform behind it can change without a client update. Translating
between Tuya DPs and this API is the server's job.

Sign-in (section 2) is designed in `sign-in.md` on the assumption that the
Birdie app uses Firebase Authentication; this is being confirmed with Birdie.

## 1. Conventions

* Base URL: `https://<bridge-host>/v1`. HTTPS only.
* Request and response bodies are JSON (`application/json; charset=utf-8`).
* Timestamps are ISO 8601 in UTC, e.g. `2026-10-17T12:00:00Z`.
* Every response carries `X-Request-ID`. A client may send its own
  `X-Request-ID`; the bridge echoes it. Include it when reporting problems.
* Compatibility: within `v1` the bridge only makes additive changes (new
  fields, new enum values, new commands, new status codes, new models).
  Clients must ignore unknown fields and models and treat unknown enum values
  as unknown, not as errors.

## 2. Authentication

Every `/v1` endpoint except `GET /v1/health` requires:

```
Authorization: Bearer <access_token>
```

The bridge resolves the token to exactly one Birdie user and, through that
user, to their Tuya user. All device access is limited to that user's devices.

### 2.1 Sign-in: OAuth 2.0 authorization code with PKCE

Home Assistant obtains tokens with the OAuth 2.0 authorization code grant
(RFC 6749) and PKCE (RFC 7636). The user signs in on a Birdie page served by
the bridge, with the same methods as in the Birdie app (e-mail and password,
Google, Apple, Facebook); the password never passes through Home Assistant.
Design, threat model and Birdie prerequisites: `sign-in.md`.

| Parameter | Value |
|---|---|
| Client | Public client, `client_id` = `home-assistant`, no client secret |
| Allowed `redirect_uri` | Exactly `https://my.home-assistant.io/redirect/oauth` |
| PKCE | Required, `S256` only |
| Scope | None (one implicit scope: the user's Birdie devices) |
| Authorization code | Single use, valid 60 seconds |
| Access token | Opaque, valid 1 hour (`expires_in` = 3600) |
| Refresh token | Opaque, rotated on every use, expires after 90 days without use |

#### `GET /oauth/authorize`

Query: `response_type=code`, `client_id`, `redirect_uri`, `state`,
`code_challenge`, `code_challenge_method=S256`.

* Unknown `client_id` or a `redirect_uri` that is not allowed: an error page,
  **no redirect** (prevents sending codes to attacker-controlled URLs).
* Other invalid parameters: redirect to `redirect_uri` with
  `error=invalid_request` and the unchanged `state`.
* Otherwise: the sign-in page. After sign-in and consent, a `302` to
  `redirect_uri?code=<code>&state=<state>`. If the user cancels:
  `error=access_denied`. `state` is returned unchanged.

#### `POST /oauth/token`

Form-encoded (`application/x-www-form-urlencoded`), as Home Assistant sends it.

| Grant | Fields |
|---|---|
| Authorization code | `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier` |
| Refresh | `grant_type=refresh_token`, `refresh_token`, `client_id` |

Response `200` (with `Cache-Control: no-store`):

```json
{
  "access_token": "bat_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "brt_..."
}
```

Errors use the RFC 6749 body `{"error": "...", "error_description": "..."}`:

| HTTP | `error` | When | Home Assistant reaction |
|---|---|---|---|
| 400 | `invalid_grant` | Code invalid, expired, reused or PKCE mismatch; refresh token invalid, expired, revoked; user disabled, deleted or signed out everywhere (e.g. password change) | Re-authentication |
| 400 | `invalid_request` / `unsupported_grant_type` | Malformed request | Re-authentication (bug) |
| 401 | `invalid_client` | Unknown `client_id` | Re-authentication (bug) |
| 429 | - | Too many token requests | Retry later |
| 503 | - | Identity provider or storage unavailable | Retry later |

Home Assistant treats **every 4xx from the token endpoint as "sign in
again"** and 429/5xx as transient. The bridge therefore must never answer a
temporary problem with a 4xx.

#### `POST /oauth/revoke`

RFC 7009. Form fields `token` (a refresh or access token) and `client_id`.
Always `200`. Revokes the whole grant (all tokens issued from one sign-in).
Called by Home Assistant when the user deletes the config entry.

#### Access token errors on `/v1`

`401 unauthorized` on a `/v1` endpoint means the access token expired or was
revoked; the client refreshes once and retries (section 5).

### 2.2 Development tokens

For local development and remote debugging only, the bridge can be started
with a static token map (environment variable, never committed), e.g.
`BRIDGE_DEV_TOKENS=<token>=<tuya_uid>`. Development tokens must be disabled
in any deployed environment. Home Assistant accepts a development token in
the collapsed "Advanced" section of the Birdie account form.

The development bridge can also run the OAuth flow with configured test
users instead of Birdie's identity provider
(`BRIDGE_DEV_USERS=<email>:<password>=<tuya_uid>`); the sign-in page then
shows a development banner.

## 3. Resources

### 3.1 Device object

```json
{
  "id": "5b0c9e7d2a4f4e1c8a3d6b90",
  "name": "Living Room Purifier",
  "model": "pedestal",
  "model_name": "Birdie Pedestal",
  "online": true,
  "hardware_version": "1.0.0"
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Opaque, stable device id assigned by Birdie. Use it as the unique id; do not parse it. |
| `name` | string | Name the user gave the device in the Birdie app, else the model name. |
| `model` | string | Birdie model key. Currently only `pedestal`. Defines the shape of `state` and the accepted commands. |
| `model_name` | string | Display name for the device registry. |
| `online` | boolean | Whether the device is currently connected to the cloud. |
| `hardware_version` | string or null | Hardware version reported by the device, if known. |

With `include=state` (section 4.3), a device object also has:

| Field | Type | Notes |
|---|---|---|
| `state` | object | Latest state, shaped per `model` (section 3.3). |
| `fetched_at` | string | When the server last read the state from the device cloud. |

The server **never** returns cloud-platform identifiers (Tuya device, product
or user ids), local keys, IP addresses, geolocation or credentials.

### 3.2 State values

Every field of a state object is always present and may be `null` when the
device has not reported it (for example right after pairing) or reported a
value the server does not understand. Clients treat `null` as "unknown".

### 3.3 `pedestal` state

```json
{
  "power": true,
  "mode": "manual",
  "fan_speed": 2,
  "pm25": 12,
  "air_quality": "good",
  "filter_life_percent": 75,
  "filter_remaining_minutes": 97200,
  "filter_replace_needed": false,
  "filter_cover_open": false,
  "fault": "none"
}
```

| Field | Type | Values |
|---|---|---|
| `power` | boolean | Purifier running. |
| `mode` | string | `auto`, `sleep` (device-managed) or `manual` (fixed `fan_speed`). Reported also while `power` is false. |
| `fan_speed` | integer | 1 (lowest) to 4 (highest) when `mode` is `manual`, else `null`. |
| `pm25` | integer | PM2.5 concentration in ug/m3 (0-500). |
| `air_quality` | string | `good`, `moderate`, `poor`, `very_poor`. |
| `filter_life_percent` | integer | Remaining filter life, 0-100. |
| `filter_remaining_minutes` | integer | Remaining filter time in minutes. |
| `filter_replace_needed` | boolean | The device asks for a filter replacement. |
| `filter_cover_open` | boolean | The filter cover is open. |
| `fault` | string | `none`, `fan`, `pm25_sensor`, `overcurrent`. |

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
| `include` | `state` | none | Also return each device's `state` and `fetched_at`. |

Response `200`:

```json
{
  "devices": [
    {
      "id": "5b0c9e7d2a4f4e1c8a3d6b90",
      "name": "Living Room Purifier",
      "model": "pedestal",
      "model_name": "Birdie Pedestal",
      "online": true,
      "hardware_version": "1.0.0",
      "state": { "power": true, "mode": "auto", "fan_speed": null, "pm25": 12, "...": "..." },
      "fetched_at": "2026-10-17T12:00:00Z"
    }
  ]
}
```

`GET /v1/devices?include=state` is the **polling endpoint**: one request
returns everything Home Assistant needs for all of a user's devices.

### 4.4 `GET /v1/devices/{device_id}`

One device with its state. Same object as an element of
`GET /v1/devices?include=state`.

Errors: `404 device_not_found` if the device does not exist **or** does not
belong to the user (the bridge does not reveal which).

### 4.5 `POST /v1/devices/{device_id}/commands`

Sends one or more commands, applied together. Request:

```json
{
  "commands": [
    { "name": "set_power", "params": { "on": true } },
    { "name": "set_fan_speed", "params": { "speed": 2 } }
  ]
}
```

* 1 to 5 commands per request. `params` may be omitted when a command takes
  none.
* Commands of the `pedestal` model:

  | `name` | `params` | Effect |
  |---|---|---|
  | `set_power` | `on`: boolean | Turn the purifier on or off. |
  | `set_mode` | `mode`: `auto` or `sleep` | Select a device-managed mode. |
  | `set_fan_speed` | `speed`: integer 1-4 | Select manual mode at that speed. |
  | `reset_filter` | none | Reset the filter life after replacing the filter. |

* Two commands that change the same setting (for example `set_mode` and
  `set_fan_speed`) in one request are rejected.

Response `202`:

```json
{ "accepted": true }
```

`202` means the device cloud accepted the command for delivery; it does not
prove the device has applied it. Clients confirm by reading state afterwards.

Errors: `400 invalid_request` (malformed body), `404 device_not_found`,
`409 device_offline`, `422 command_rejected` (unknown command, invalid
parameter, or not supported by this device).

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
| 422 | `command_rejected` | Unknown command, invalid parameter, or rejected by the device | Show error; do not retry |
| 404 | `not_found` | Unknown endpoint | Bug or version mismatch; log |
| 405 | `method_not_allowed` | Wrong HTTP method | Bug; log |
| 429 | `rate_limited` | Too many requests from this user | Back off for `Retry-After` seconds |
| 502 | `upstream_error` | Tuya returned an unexpected error | Treat as transient polling failure |
| 503 | `upstream_unavailable` | Tuya unreachable, or Birdie's Tuya plan/quota problem | Treat as transient; respect `Retry-After` if present |
| 500 | `internal_error` | Bridge bug | Treat as transient |

`429` and `503` responses may include `Retry-After` (seconds).

## 6. Client guidance (Home Assistant)

* Poll `GET /v1/devices?include=state` once per config entry, default every
  60 seconds, never more often than every 30 seconds.
* After a successful command, request one extra refresh after about 2 seconds.
  The client may show the commanded `power`, `mode` and `fan_speed` until then.
* Treat `online: false` as "entities unavailable", not as an error.

## 7. Server requirements (bridge)

This section describes the reference bridge on Tuya Cloud; another backend
implementing the API only has to meet the same guarantees.

* **Device ids:** the reference bridge derives `id` as a truncated SHA-256 of
  the Tuya device id, so no mapping table is needed. A backend that replaces
  it must keep returning the same ids, or Home Assistant users lose their
  device and entity history.
* **Translation:** each model module (`bridge/birdie_bridge/pedestal.py`)
  converts Tuya status to the state object and commands to Tuya DPs. The
  meaning of `filter_replace_needed`, `filter_cover_open` and `reset_filter`
  is still assumed from the product schema; correcting it after a real-device
  test is a bridge-only change.
* **Command validation:** commands are checked against the model's command
  table and against the writable DPs Tuya reports for the device (cached for
  an hour), so unsupported modes are rejected with `422` before reaching Tuya.
* **Ownership:** every device-scoped request checks that the device belongs to
  the token's Tuya user. The ownership list may be cached for up to 60 seconds.
* **Allow-lists:** only products in the bridge's product map are listed, and
  only the model's commands are forwarded.
* **Secrets:** Tuya credentials come from the deployment's secret store. Tokens,
  secrets and `local_key` are never logged or returned.
* **Quota:** state may be served from a cache of at most 10 seconds;
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

1. Confirm the Birdie app uses Firebase Authentication and which providers
   (section 2.1, `sign-in.md` section 8).
2. Where the Birdie user to Tuya UID mapping lives (Birdie backend, or derived
   from Tuya UID login).
3. Deployment target (Cloud Run in Birdie's Google Cloud project assumed),
   region and public host name for the sign-in page.
4. Final product name for `model_name` ("Pedestal" in the Tuya console,
   "Podium" in public material).
