# Signing in to a Birdie Account from Home Assistant

Status: **implemented with a development identity provider**. Last updated
2026-10-17.

| Part | State |
|---|---|
| OAuth server, sign-in page, token rotation and revocation (`bridge/birdie_bridge/oauth/`) | Implemented and tested; in-memory store |
| Home Assistant OAuth flow, token refresh, re-authentication, revoke on removal | Implemented and tested; verified in Home Assistant 2026.9.2 against `tools/mock_cloud.py` |
| Development identity provider (e-mail/password from `BRIDGE_DEV_USERS`) | Implemented, development mode only |
| Firebase identity provider (section 5.2) | Not started; waits for Birdie to confirm A1/A2 |
| Shared token store (Firestore) for several bridge instances | Not started; waits for the hosting decision |

How a Home Assistant user connects their Birdie account to the integration.
The wire protocol is specified in `bridge-api.md` section 2.1; this document
explains the design behind it, what each side implements and what Birdie has
to provide.

## 1. Summary

* OAuth 2.0 authorization code grant with PKCE. The bridge is the
  authorization server; Home Assistant is a public client.
* The user signs in on a Birdie-branded page served by the bridge, with the
  same methods as in the Birdie app. The page uses Birdie's existing identity
  provider (assumed: Firebase Authentication), so there are no new accounts
  or passwords.
* Home Assistant never sees the password and stores only bridge-issued
  tokens. Access tokens live one hour; refresh tokens rotate and follow the
  Birdie account (disabled, deleted or password changed -> sign in again).
* Home Assistant's built-in OAuth2 support handles the browser round trip,
  token refresh and re-authentication.

Why not a username/password form in Home Assistant: the Birdie privacy policy
lists Google, Apple and Facebook sign-in. Those users have no Birdie password
and could not use such a form, and the form would route passwords through
Home Assistant.

## 2. Assumptions

| # | Assumption | Status |
|---|---|---|
| A1 | The Birdie app signs users in with Firebase Authentication (e-mail/password, Google, Apple, Facebook) **[INF]** from the privacy policy listing Firebase | **[OPEN]** asked Birdie |
| A2 | Each Firebase user maps to exactly one Tuya user (UID) whose devices they see in the app | **[OPEN]** where the mapping lives |
| A3 | The bridge runs in Birdie's Google Cloud project (same project as Firebase) | **[OPEN]** hosting decision |

Section 9 covers what changes if A1 is wrong.

## 3. Components

```
 Home Assistant                 Browser                        Bridge (Cloud Run)              Google / Birdie
 --------------                 -------                        ------------------              ---------------
 config flow  --open URL-->  /oauth/authorize  --page-->  sign-in page (Firebase Web SDK) --> Firebase Auth
                                                                 |  ID token
                                                                 v
                             <--302 my.home-assistant.io-- /oauth/session: verify ID token,
 /auth/external/callback <-- redirect to user's own HA        map Firebase UID -> Tuya UID,
        |                                                      issue code
        +--POST /oauth/token (code + PKCE verifier)------->  tokens  ---- grant stored in Firestore
        +--GET /v1/... Bearer access token --------------->  devices ---- Tuya Cloud
```

* **Home Assistant integration:** OAuth2 client (section 6).
* **Bridge:** authorization server, sign-in page and the existing `/v1` API.
* **Firebase Authentication:** Birdie's identity provider; unchanged.
* **Token store:** Firestore in Birdie's project (any store with TTL and
  transactions works). Needed because Cloud Run runs several stateless
  instances.

## 4. Sign-in sequence

1. In Home Assistant: **Add integration > Birdie > Birdie account**. The
   config flow generates a PKCE verifier and opens
   `/oauth/authorize?response_type=code&client_id=home-assistant&redirect_uri=https://my.home-assistant.io/redirect/oauth&state=<signed by HA>&code_challenge=<S256>&code_challenge_method=S256`
   in a new browser tab.
2. The bridge checks `client_id`, `redirect_uri` (exact match) and PKCE
   parameters, stores an *authorization request* (random id, 10 minutes TTL)
   and returns the sign-in page with that id. A `SameSite=Strict`,
   `HttpOnly` cookie binds the request to this browser.
3. The page signs the user in with the Firebase Web SDK: e-mail and password
   (with "forgot password"), Google, Apple, Facebook, whatever the app offers.
   Provider sign-in uses `signInWithPopup`, which avoids the third-party
   storage problems of redirect sign-in on a domain other than `authDomain`.
4. The page posts the Firebase **ID token** and the request id to
   `POST /oauth/session` (same origin, JSON, cookie required).
5. The bridge verifies the ID token (section 5.2), resolves the Tuya UID
   (A2) and shows a consent step: "Home Assistant will be able to see and
   control your Birdie devices." Allow / Cancel.
6. On Allow, the bridge creates a **grant** and a single-use authorization
   code (60 seconds), bound to the client, redirect URI, code challenge and
   grant, and redirects the browser to
   `https://my.home-assistant.io/redirect/oauth?code=...&state=...`.
7. my.home-assistant.io forwards the browser to the user's own Home Assistant
   (`/auth/external/callback`). Home Assistant does not need to be reachable
   from the internet; the redirect happens in the user's browser.
8. Home Assistant posts the code and PKCE verifier to `/oauth/token` and
   receives an access token (1 hour) and a refresh token.
9. The config flow calls `GET /v1/account` with the new access token, uses the
   account id as the entry's unique id and creates the entry.

Re-authentication runs steps 1-9 again; the flow aborts with
`wrong_account` if the user signs in to a different Birdie account.

## 5. Bridge design

### 5.1 Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /oauth/authorize` | Validate the request, show the sign-in page |
| `POST /oauth/session` | Internal to the sign-in page: ID token in, consent step / redirect URL out |
| `POST /oauth/consent` | Internal: Allow / Cancel |
| `POST /oauth/token` | Code exchange and refresh |
| `POST /oauth/revoke` | Revoke a grant |
| `/v1/...` | Existing API; bearer access token |

### 5.2 Verifying the Firebase ID token

With the Firebase Admin SDK (`verify_id_token(token, check_revoked=True)`)
or equivalently by hand **[DOC]**:

* RS256 signature with Google's published keys
  (`securetoken@system.gserviceaccount.com`), keys cached per their
  `Cache-Control`.
* `aud` = Birdie's Firebase project id, `iss` =
  `https://securetoken.google.com/<project id>`, `sub` (the Firebase UID)
  non-empty, `exp` in the future, `iat` and `auth_time` in the past.
* `auth_time` at most 5 minutes old, so a stolen long-lived browser session
  cannot silently authorize a new client.
* If Birdie requires verified e-mail addresses in the app, require
  `email_verified` for password accounts too.

### 5.3 Accounts

* `account_id` (returned by `/v1/account`, used by Home Assistant as unique
  id) = truncated SHA-256 of `birdie-account:<Firebase UID>`. Opaque and stable.
* `display_name` = the e-mail address from the ID token, if any (shown as the
  config entry title).
* The Firebase UID -> Tuya UID mapping (A2) is resolved at sign-in and stored
  in the grant, then re-checked on every refresh.

### 5.4 Codes, tokens and grants

* Tokens are 32 random bytes, base64url, with prefixes `bac_` (code),
  `bat_` (access token), `brt_` (refresh token) so secret scanners recognise
  them. Only their SHA-256 is stored.
* **Grant** (one per sign-in): account id, Firebase UID, Tuya UID, client id,
  `auth_time`, created / last used, current refresh token hash, previous
  refresh token hash and rotation time, revoked flag.
* **Code exchange:** checks expiry, single use, `client_id`, `redirect_uri`
  and `S256(code_verifier) == code_challenge`. A second use of a code
  revokes the grant it created (RFC 6749 section 4.1.2).
* **Refresh:** issues a new access token and a new refresh token. The previous
  refresh token is still accepted for 2 minutes (Home Assistant may have
  crashed before saving the response); later reuse is treated as theft and
  revokes the grant. Refresh tokens expire after 90 days without use.
* **On every refresh** the bridge loads the Firebase user (Admin SDK
  `get_user`) and answers `invalid_grant` if the user is deleted, disabled,
  or `tokens_valid_after` is later than the grant's `auth_time`. Firebase
  sets this when refresh tokens are revoked **[DOC]**; that a password or
  e-mail change in the Birdie app also sets it is **[INF]** and is checked
  in testing (section 10). One Admin SDK call per account per hour.
* **Access tokens** are looked up in the store on use, with a per-instance
  cache of at most 60 seconds, so a revocation takes effect within a minute.
* Firestore TTL policies delete expired requests, codes, access tokens and
  idle grants.

### 5.5 Failure behaviour

Home Assistant starts re-authentication on **any** 4xx from `/oauth/token`.
So:

* `invalid_grant` only when the grant is really unusable.
* Firebase, Firestore or Tuya problems -> `503` with `Retry-After`, never 4xx.
* Rate limiting -> `429`.

### 5.6 Sign-in page

* Static HTML and JavaScript served by the bridge, Birdie branding, the
  Firebase Web SDK pinned to an exact version.
* `Content-Security-Policy` that allows only the bridge and Google/Firebase
  origins, `frame-ancestors 'none'` (no clickjacking), HSTS.
* Shows the requesting client ("Home Assistant") and what it will be able to
  do, with a link to Birdie's privacy policy.
* Errors (popup blocked, wrong password, account not linked to any devices)
  are shown on the page; the user can retry without restarting the flow.

### 5.7 Logging and limits

* Never log tokens, codes, ID tokens, PKCE verifiers or e-mail addresses. Log
  the grant id and request id.
* Per-IP rate limits on `/oauth/authorize`, `/oauth/session` and
  `/oauth/token`; per-account limits on `/v1` stay as today.

## 6. Home Assistant design

* **OAuth implementation:** `async_setup` registers a
  `LocalOAuth2ImplementationWithPkce` with `client_id` `home-assistant`, the
  production authorize and token URLs, and no secret. Users never enter
  client credentials (no `application_credentials`). A small subclass returns
  `https://my.home-assistant.io/redirect/oauth` as redirect URI even when the
  `my` integration is not loaded, because the bridge accepts only that URI.
* **Config flow:** the existing `BirdieConfigFlow` (Bluetooth steps included)
  becomes a subclass of `AbstractOAuth2FlowHandler`; the menu option
  "Birdie account" shows one form, and submitting it starts the OAuth steps. `async_oauth_create_entry` calls
  `GET /v1/account`, sets the unique id and handles re-authentication.
* **Entry data:** `entry_type: cloud`, `auth_implementation`, `token`
  (access token, refresh token, `expires_at`), `account_id`. Diagnostics
  redact `token`.
* **Token provider:** an `OAuth2Session`-backed implementation of the
  existing `TokenProvider` protocol (`cloud/bridge.py`): it calls
  `async_ensure_token_valid()` before each request and, on a `401` from
  `/v1`, forces one refresh and retries.
* **Errors:** a failed refresh with 4xx -> `ConfigEntryAuthFailed`
  (re-authentication); 429/5xx/network -> `UpdateFailed` or
  `ConfigEntryNotReady`. Since about HA 2026.8 the helper raises
  `OAuth2TokenRequestReauthError` for this; HA 2026.1 raises a plain
  `aiohttp.ClientResponseError`. Both are `ClientResponseError`s, so the
  token provider maps by HTTP status and works on either.
* **Removal:** `async_remove_entry` calls `POST /oauth/revoke` (best effort).
* **Other clouds and development tokens:** a collapsed "Advanced" section
  of the Birdie account form takes another cloud URL and, for development
  bridges, a static token instead of signing in (Home Assistant deprecates
  advanced-mode-only steps). It is expanded while `DEFAULT_BRIDGE_URL` is
  unset.
* No migration is needed: Birdie account entries have not been released.

## 7. Threats and mitigations

| Threat | Mitigation |
|---|---|
| Authorization code intercepted | PKCE S256; single-use 60-second codes; exact redirect URI |
| Phishing link with the attacker's code challenge and redirect | Only `my.home-assistant.io` is accepted, which delivers the code to the victim's own Home Assistant |
| Login CSRF / request swapping | Authorization request id bound to a `SameSite=Strict` cookie; HA's signed `state` |
| Clickjacking the consent step | `frame-ancestors 'none'` |
| Stolen refresh token | Rotation with reuse detection; revocation on password change; 90-day idle expiry |
| Stolen access token | One-hour lifetime; revocable within a minute |
| Token store leak | Only hashes stored |
| Tokens in logs | Never logged; prefixes allow secret scanning |
| Revoked Birdie account keeps access | Firebase user checked on every refresh |
| Brute force on sign-in | Firebase's own protections plus per-IP limits on the bridge |

## 8. What Birdie needs to provide

1. Confirmation of A1, and the Firebase project id and enabled providers.
2. The answer to A2: where the Firebase UID -> Tuya UID mapping lives, and
   read access to it for the bridge.
3. A public host name for the bridge and sign-in page, e.g.
   `connect.<birdie domain>`.
4. In the Firebase console: add that host to **Authorized domains**; share the
   web app config (`apiKey`, `authDomain`, `projectId`; public values).
5. Provider web setup if the app only uses native sign-in today: Google
   (usually automatic), Apple (Services ID and return URL
   `https://<authDomain>/__/auth/handler`), Facebook (valid OAuth redirect
   URI, same handler).
6. A service account for the bridge with Firebase Authentication read access
   and Firestore access (on Cloud Run: the runtime service account).
7. Logo and the wording for the consent step.

## 9. If the assumptions are wrong

* **Birdie uses Tuya's own account system (no Firebase):** Tuya offers no web
  sign-in for App SDK users. Replace step 3 with an **e-mail one-time code**:
  the page asks for the e-mail address, the bridge looks up the Tuya user and
  sends a 6-digit code (Birdie's mail sender), the user types it in. The
  OAuth protocol and Home Assistant side stay the same.
* **E-mail and password only, no social sign-in:** the design above still
  works (only the e-mail form on the page). A username/password form in Home
  Assistant would be quicker to build but is not recommended.
* **Birdie has its own backend with device APIs:** it can implement the API
  (including section 2.1) itself, or issue tokens the bridge accepts. The Home
  Assistant side does not change.

## 10. Testing

* **Bridge, unit and HTTP tests:** a fake Firebase (test RSA key, locally
  signed ID tokens, injectable user records). Cover PKCE, code reuse,
  rotation and the grace period, reuse detection, revocation, disabled and
  password-changed users (`invalid_grant`), and identity provider outage
  (`503`, not 4xx).
* **Home Assistant:** the standard OAuth config flow tests
  (`hass_client_no_auth` for the callback, `aioclient_mock` for the token
  endpoint), refresh and re-authentication, and removal revoking the grant.
* **End to end before Birdie's Firebase is available:** `tools/mock_cloud.py`
  gains a development identity provider (a sign-in page that accepts a test
  user) so the full browser flow can run in a real Home Assistant container.
* **With Birdie:** one sign-in each with e-mail, Google, Apple and Facebook
  accounts, then a password change to confirm re-authentication.
