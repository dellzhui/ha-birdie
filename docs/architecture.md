# Architecture: Birdie Pedestal in Home Assistant

Status: option B (Birdie cloud bridge) selected; option A3 dropped. Last reviewed 2026-10-17.

## Implementation status

| Part | State |
|---|---|
| Home Assistant: Birdie account entry, Pedestal entities, diagnostics | Implemented; unit-tested and verified end to end in HA 2026.9.2 against the bridge and a fake Tuya cloud |
| Bridge: HTTP API, scoping, caching, error mapping, container image | Implemented with development tokens |
| Sign-in (OAuth 2.0 + PKCE, Birdie sign-in page) | Implemented end to end with a development identity provider (`sign-in.md`); Firebase provider and shared token store wait for Birdie |
| Real device validation | Waiting for Birdie: cloud project access, see `remote-testing.md` |
| Deployment of the bridge | Waiting for Birdie: hosting decision |

Legend for facts: **[DOC]** official Tuya / Home Assistant documentation,
**[SRC]** official source code (Tuya SDKs, HA core), **[INF]** our inference,
**[OPEN]** must be confirmed by Birdie or Tuya.

## 1. System today

```
Birdie app (Tuya App SDK) --BLE pairing--> Pedestal --Wi-Fi/MQTT--> Tuya Cloud
        \___________________ user account & device binding _________/
```

* Users and device bindings live in Tuya's user pool for Birdie's App SDK app.
* Birdie does not (as far as we know) run its own backend. **[OPEN]**

## 2. Verified Tuya facts that drive the design

1. **Every Tuya OpenAPI request is signed with the cloud project's Access
   Secret** (HMAC-SHA256 over client_id, access_token, timestamp, nonce and
   request). Holding an access token alone is not enough. **[DOC]**
   https://developer.tuya.com/en/docs/iot/new-singnature?id=Kbw0q34cs2e5g
2. A cloud project sees Birdie app devices through *Devices -> Link My App*
   ("OEM App or an app built with Tuya app SDKs"; max two projects per app) or
   *Link SaaS* (all devices of a PID). Such a project can access the whole
   fleet. **[DOC]** https://developer.tuya.com/en/docs/iot/link-devices?id=Ka471nu1sfmkl
3. Consequence: **the Access Secret must never ship inside an open-source
   Home Assistant integration.** Anyone could extract it and control every
   linked Birdie device. **[INF, follows from 1+2]**
4. *Link Tuya App Account* (QR scan) works for Tuya Smart / Smart Life
   accounts, not for a custom App SDK app, so end users cannot attach their
   Birdie account to a personal developer project. **[DOC + INF]**
5. The official HA `tuya` integration uses `tuya-device-sharing-sdk` with a
   Tuya-issued HA client id (`HA_3y9q4ak7g4ephrvke`, schema `haauthorize`), a
   QR code scanned in the app, user-scoped encrypted `/v1.0/m/life/...` APIs
   and MQTT push. No secret is needed. **[SRC]** It officially supports only
   Tuya Smart and Smart Life; OEM-app users get "Please use the designated APP
   to scan the code" and there is no documented way for a brand to enable it.
   **[DOC + SRC]** home-assistant/core#120619, tuya/tuya-device-sharing-sdk#43
6. OAuth 2.0 authorization-code mode (`grant_type=2`) lets an app user grant a
   third party access via a Tuya-hosted H5 login page; the code exchange needs
   the client secret, i.e. a server. Whether an App SDK app can be selected
   is not documented. **[DOC + OPEN]**
   https://developer.tuya.com/en/docs/iot/authorization-code-page-usage?id=Kdkyz44dz6a7r
7. Legacy "authorized login" (`POST /v1.0/iot-01/associated-users/actions/authorized-login`,
   username + password + app schema) still exists but also requires the
   project secret. **[DOC]**
8. Real-time push (Message Service / Pulsar) is project-level and needs the
   Access Secret. **[DOC]**
9. Quotas: per-API frequency limits per project; the **Trial Edition** allows
   ~26k API calls per month and suspends service when exhausted (error
   28841004). Production cost scales with polling. **[DOC]**
   https://developer.tuya.com/en/docs/iot/membership-service?id=K9m8k45jwvg9j

## 3. Three access scenarios

| Scenario | Mechanism | Where the secret lives |
|---|---|---|
| Developer, own test devices | Own cloud project, simple-mode token | Developer machine (`.env.local`) |
| Birdie, its product fleet | Birdie cloud project + Link My App / Link SaaS | Birdie-controlled server only |
| Ordinary Birdie user in HA | See options below | Never on the user's machine |

## 4. Options for the end-user path

### A. Home Assistant calls Tuya OpenAPI directly

* A1 - user supplies their own developer project credentials: not possible
  for App SDK users (fact 4) and contrary to the requirement that users do not
  need Tuya developer accounts.
* A2 - integration embeds Birdie's credentials: rejected (fact 3).
* A3 - HA-style device sharing (QR) for Birdie's app: no server, push updates,
  best UX. **Depends on Tuya enabling it for Birdie's app** (undocumented).
  If granted with a Birdie client id, the integration would use
  `tuya-device-sharing-sdk` as transport.

### B. Thin Birdie cloud bridge

```
Home Assistant  --OAuth2 + small REST-->  Birdie bridge  --signed OpenAPI-->  Tuya Cloud
 (birdie_cloud)                            (holds secret,
                                            per-user scoping)
```

* User signs in with their Birdie app account (Tuya OAuth H5 page if available
  for the App SDK app, otherwise legacy authorized login through the bridge).
* Bridge maps HA sessions to the Tuya `uid`, only allows that user's devices,
  and exposes three endpoints: list devices, get state, send command.
* Can run serverless (one function + small key-value store).
* Costs: a hosted component Birdie must own (hosting, secret management,
  security responsibility); Tuya API calls billed to Birdie's project.
* Fully built on documented APIs. Later it can consume Message Service and
  serve cached state, cutting Tuya API calls.

### C. Not pursued now

* Matter: native and local, but requires firmware support (out of scope). **[OPEN]**
* Local LAN control with `local_key`: still needs a cloud step to hand out the
  key, plus community protocol code. Not simpler.

### Comparison

| | A3 Tuya device sharing | B Birdie bridge |
|---|---|---|
| Implementation effort | Low (reuse Tuya SDK) | Medium (+ bridge) |
| Security | Tuya-managed, user-scoped | Good if bridge scoping is correct; Birdie owns risk |
| User setup | Scan QR in Birdie app | Sign in with Birdie account |
| Maintenance | Low; depends on Tuya | Bridge to operate |
| Extra server | No | Yes (serverless) |
| Dependency | Tuya enablement + possibly app change | Birdie Tuya project, hosting |
| Real-time | MQTT push | Polling (push possible later) |
| Availability today | Unknown | Yes |

## 5. Decision

1. **Option B, the Birdie cloud bridge, is the delivery path.** Option A3
   (Tuya-enabled QR sharing) is not pursued.
2. The Pedestal is added to the **existing `birdie` integration** next to
   Birdie Pro, as a second config entry type ("Birdie account").
3. The Home Assistant side never talks to Tuya directly and never holds Tuya
   credentials. It talks only to the bridge (`custom_components/birdie/cloud/`,
   transport-agnostic via `CloudTransport`).
4. The Tuya OpenAPI client lives in the bridge (`bridge/birdie_bridge/tuya/`)
   and is also used for development and remote debugging (`birdie-tuya` CLI).
5. Open: how the bridge authenticates a Birdie user. The Birdie privacy
   policy lists email/password plus Google, Apple and Facebook sign-in, and
   Firebase; whether accounts live in Firebase Auth or in Tuya's user pool
   must be confirmed by Birdie.

### Repository layout

```
custom_components/birdie/        Home Assistant integration (published via HACS)
  cloud/                         Pedestal model, transport protocol (no Tuya secrets)
bridge/                          Birdie cloud bridge (deployed by/for Birdie)
  birdie_bridge/tuya/            Tuya Cloud OpenAPI client
docs/                            Architecture notes
tests/                           Integration-side tests
```

The HTTP contract between the integration and the bridge is specified in
`bridge-api.md`.

Before pushing `bridge/` to the public repository, confirm with Birdie that
publishing it is acceptable; otherwise move it to a private repository.

## 6. Home Assistant presentation

* Domain: the existing `birdie` integration (shared with Birdie Pro).
* Branding: since HA 2026.3 a custom integration ships `brand/icon.png`,
  `logo.png` (+ `@2x`, dark variants) in its own folder; the brands repo no
  longer accepts custom integrations. Set `hacs.json` minimum HA to 2026.3.0.
* The integration only sees the bridge API's Birdie terms (opaque device ids,
  state objects, named commands); the bridge translates to and from Tuya DPs.
* Device registry: manufacturer "Birdie", model "Birdie Pedestal",
  hw_version from the device's `hardware_version` (Tuya DP 110).
* Entities (HA guidance: air purifiers are `fan` entities; named manual speeds
  are percentages, presets are device-managed modes):

| Bridge state / command (Tuya DP) | Entity |
|---|---|
| `power`, `mode`, `fan_speed` / `set_power`, `set_mode`, `set_fan_speed` (switch, mode) | `fan`: on/off; speed 1-4 -> 25/50/75/100 %; presets `auto`, `sleep` |
| `pm25` (pm25) | `sensor` PM25, ug/m3, measurement |
| `air_quality` (air_quality) | `sensor` enum: good / moderate / poor / very_poor |
| `filter_life_percent`, `filter_remaining_minutes` (filter_total/used_minutes) | `sensor` filter life %; remaining duration |
| `filter_replace_needed` (filter_status) | `binary_sensor` problem (DP meaning TBC) |
| `reset_filter` (reset_filter) | `button` (config) |
| `filter_cover_open` (filter_cover) | `binary_sensor` opening, diagnostic (DP meaning TBC) |
| `fault` (device_error) | `sensor` enum diagnostic: none / fan / pm25_sensor / overcurrent |

* Coordinator: cloud polling (default 60 s, configurable), `ConfigEntryAuthFailed`
  for re-auth, `UpdateFailed` for transient errors, entities unavailable when
  the device is offline, refresh after each command.

## 7. Testing without Birdie's device

Our own Tuya developer account can reproduce the Pedestal's DP schema in a
self-created product and debug it with a Tuya *virtual device* (Product ->
Device Debugging; the web debugger can change DP values and report them to
the cloud). Linked to our own cloud project through a Smart Life account, it
exercises the real Tuya Cloud: signing, tokens, DP specification, status and
commands. It cannot validate firmware behaviour, Birdie's PID, or Birdie's
App SDK account model. See https://developer.tuya.com/en/docs/iot/device_debug?id=Kbrcqod1qa730
and https://developer.tuya.com/en/docs/iot/manage-virtual-devices?id=Ka4725tiyfhg0

## 8. Open questions

For Birdie:
1. Tuya developer account: is the App SDK app in Birdie's own Tuya account?
   Which data center(s)?
2. Product ID: `a2zf7q0zxcutn5dy` (console status "Developing"). Will
   production units keep this PID?
3. Can Birdie create a cloud project linked to its app (Link My App or Link
   SaaS) for remote debugging, and share time-limited credentials with us
   through a secure channel?
4. Does Birdie have or want any backend of its own? Is a small hosted bridge
   acceptable if needed?
5. DP semantics (types are now known from the console): does
   `filter_status = true` mean "replace filter" or "OK"? Does
   `filter_cover = true` mean open or closed? Does `reset_filter = true` reset
   `filter_used_minutes` to 0, and does the device report it back to false?
   What does `ocp` in `device_error` stand for?
6. Does the Wi-Fi module / firmware support Matter?

For Tuya (via Birdie's account manager):
1. Can Birdie's App SDK app be enabled for the Home Assistant QR device-sharing
   flow (`tuya-device-sharing-sdk`), ideally with a Birdie client id? What app
   changes would be required?
2. Can the App SDK app be selected for OAuth 2.0 authorization (H5 login)?
3. Does Link My App include future devices of all users automatically?
4. API call pricing / quotas for the expected fleet size; Message Service cost.
5. Is `authorized-login` deprecated, and is `schema` the App SDK channel id?
