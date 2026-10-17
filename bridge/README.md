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
| HTTP API for Home Assistant | Not started |
| User authentication | Not started - depends on how the Birdie app signs users in |

Nothing here has been run against a real Birdie Pedestal yet.

## Layout

```
birdie_bridge/
  tuya/        Tuya Cloud OpenAPI client: signing, token lifecycle, typed errors
  devtool.py   CLI for probing a Tuya cloud project
tests/         pytest suite; fake_tuya.py is an in-process fake of the OpenAPI
```

## Development

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e 'bridge[dev]'
cd bridge && ../.venv/bin/pytest
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
