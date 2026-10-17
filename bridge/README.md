# Birdie Cloud Bridge

Server-side component that lets the Birdie Home Assistant integration reach
Birdie devices on Tuya Cloud without exposing Birdie's Tuya project secret.

```
Home Assistant (birdie integration) --> Birdie Cloud Bridge --> Tuya Cloud OpenAPI
                                        (holds the secret,
                                         per-user device scoping)
```

See `../docs/architecture.md` for the reasoning.

## Status

| Component | State |
|---|---|
| Tuya Cloud OpenAPI client (`birdie_bridge.tuya`) | Implemented; tested against an in-process fake cloud and Tuya's documented signing vectors |
| Developer CLI (`birdie-tuya`) | Implemented |
| HTTP API (`birdie_bridge.app`, docs/bridge-api.md) | Implemented: ownership scoping, product allow-list, command allow-list, status/spec caching, per-account rate limit, Tuya error mapping |
| Development authentication (static tokens) | Implemented, development mode only |
| Production authentication | Not started - depends on how the Birdie app signs users in |

Nothing here has been run against a real Birdie Pedestal yet.

## Layout

```
birdie_bridge/
  app.py        aiohttp application: routes, error envelope, auth, rate limit
  service.py    business logic: per-user device scoping, caching, validation
  products.py   Tuya PID -> Birdie model map and command allow-lists
  auth.py       authenticators (development tokens today)
  tuya/         Tuya Cloud OpenAPI client: signing, token lifecycle, typed errors
  devtool.py    CLI for probing a Tuya cloud project
  __main__.py   server entry point (python -m birdie_bridge)
tests/          pytest suite; fake_tuya.py is an in-process fake of the OpenAPI,
                test_contract.py runs the Home Assistant client against the bridge
```

## Development

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e 'bridge[dev]'
cd bridge && ../.venv/bin/pytest
```

## Running the bridge locally

```bash
set -a; . bridge/.env.local; set +a          # TUYA_* credentials
export BRIDGE_ENV=development
export BRIDGE_DEV_TOKENS='<pick-a-random-token>=<tuya_uid_of_test_user>'
export BRIDGE_EXTRA_PRODUCTS='<clone_pid>=pedestal'   # only for a cloned test product
.venv/bin/python -m birdie_bridge            # listens on 127.0.0.1:8080
```

Then in Home Assistant add **Birdie > Birdie account** with URL
`http://<host>:8080` and the development token. Development tokens are
refused unless `BRIDGE_ENV=development`; there is no production
authenticator yet, so the bridge cannot be deployed for real users.

The Tuya UID of a test user is shown in the Tuya console on the cloud
project's linked app account (Devices > Link App Account). Verify it with
`birdie-tuya user-devices <uid>`.

## Container image

`bridge/Dockerfile` builds a small image suitable for Google Cloud Run or any
container host. It listens on `$PORT` (default 8080) and reads all
configuration from environment variables; inject `TUYA_ACCESS_SECRET` from
the platform's secret manager. Without a production authenticator the
container refuses to start outside development mode.

```bash
docker build -t birdie-bridge bridge/
```

## Developer CLI

Credentials are read from environment variables only. Keep them in a
git-ignored file and never commit or paste them anywhere:

```bash
cp bridge/.env.example bridge/.env.local   # fill in TUYA_ACCESS_ID / TUYA_ACCESS_SECRET / TUYA_REGION
set -a; . bridge/.env.local; set +a

birdie-tuya token                          # verify credentials
birdie-tuya devices                        # devices visible to the project (localKey redacted)
birdie-tuya device <device_id>             # details (local_key, ip, lat/lon redacted)
birdie-tuya spec <device_id>               # DP types and ranges reported by the cloud
birdie-tuya status <device_id>             # raw DP values
birdie-tuya send <device_id> mode '"auto"' --yes
```

Commands that change device state require `--yes`.

The Tuya IoT Core *Trial Edition* has a small monthly API-call allowance and
suspends service when it is used up. Avoid tight polling loops.
