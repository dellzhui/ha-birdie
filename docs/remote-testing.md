# Remote Testing With a Real Birdie Pedestal

How we validate the integration against one of Birdie's real devices without
shipping hardware. Status: ready to run as soon as Birdie provides access.

## What Birdie needs to provide

1. **A Tuya cloud project that can see the Birdie app's devices.** In the
   Tuya Developer Platform account that owns the Birdie app:
   * Cloud > Development > Create Cloud Project, in the **same data center**
     as the Birdie app (for Europe usually "Central Europe").
   * Subscribe the **IoT Core** API service (normally offered on creation).
   * Devices > **Link My App** > select the Birdie app. (An app can be linked
     to at most two projects.)
2. **The project's Access ID and Access Secret**, shared through a secure
   channel (password manager share or similar, not chat or e-mail). Rotate
   the secret after testing.
3. **One Pedestal online** in a Birdie app account during the test window,
   and the **Tuya UID of that app account** (shown on the cloud project's
   device details or linked app users).
4. Someone who can look at the purifier for a few minutes to confirm what the
   device does when we send commands (optional but speeds things up).

## What we run

Nothing is deployed; everything runs on the developer machine.

```bash
# 1. Credentials (git-ignored file)
cp bridge/.env.example bridge/.env.local      # TUYA_ACCESS_ID / SECRET / REGION
set -a; . bridge/.env.local; set +a

# 2. Read-only checks with the CLI
birdie-tuya token
birdie-tuya user-devices <tuya_uid>          # device id, product id, online, status
birdie-tuya spec <device_id>                 # authoritative DP types and ranges

# 3. Bridge in development mode, mapped to that one test user
export BRIDGE_ENV=development
export BRIDGE_DEV_TOKENS="<random token>=<tuya_uid>"
python -m birdie_bridge

# 4. Home Assistant: Add integration > Birdie > Birdie account
#    URL http://<dev machine>:8080, token = the random token
```

The bridge only exposes that user's devices of known Birdie products and only
forwards `switch`, `mode` and `reset_filter` commands.

## Checklist

Record results (with the diagnostics download from the Home Assistant device
page) and `birdie-tuya status` for the raw DPs. Where an assumption was
wrong, update `bridge/birdie_bridge/pedestal.py`; the Home Assistant
integration does not see Tuya DPs and needs no change.

| # | Check | How | Expected / question |
|---|---|---|---|
| 1 | Product id | `user-devices` | Matches `a2zf7q0zxcutn5dy`; is it the production PID? |
| 2 | DP types match the console | `spec` | Boolean/Integer/Enum as in the console screenshot |
| 3 | Power on/off | Fan toggle in HA | Device turns on/off; `switch` reported back |
| 4 | Speeds | Fan 25/50/75/100 % | Tuya `mode` becomes `manual1`..`manual4`; airflow changes |
| 5 | Presets | Auto, Sleep | `mode` becomes `auto` / `sleep` |
| 6 | Mode while off | Set speed when off | Does the device accept `mode` while off, or must it be on first? |
| 7 | PM2.5 | Sensor vs app | Same value as the Birdie app; update interval? |
| 8 | Air quality | Sensor vs app | Same level as the app |
| 9 | Filter life | Sensor vs app | Total/used minutes plausible; matches app percentage |
| 10 | `filter_status` meaning | Ask / observe | Is `true` "replace filter" or "OK"? |
| 11 | `filter_cover` meaning | Open the cover | Is `true` "open" or "closed"? |
| 12 | Filter reset | Button in HA | Does `filter_used_minutes` go to 0? Does `reset_filter` report back `false`? |
| 13 | `device_error` | Ask Birdie | Meaning of `fan`, `pm25`, `ocp` |
| 14 | Offline | Unplug device | Entities become unavailable within one poll; commands show "device offline" |
| 15 | Back online | Plug in | Entities recover without reloading |
| 16 | Name | HA device name | Uses the name given in the Birdie app |

## After testing

* Ask Birdie to rotate the Access Secret if it was shared for testing.
* Save redacted real responses as `*_captured.json` fixtures (remove
  `local_key`, `ip`, `uid`, coordinates) and add tests for them.
