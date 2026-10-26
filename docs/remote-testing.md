# Testing With a Real Birdie Air Purifier

Everything so far is verified against a simulated purifier only. The LAN
protocol works only inside the purifier's own network: knowing the device ID
and local key is not enough to reach it over the internet. Real-device
testing therefore runs on a machine in Birdie's office network.

## What Birdie needs to provide

1. A purifier paired with the Birdie app, on the office Wi-Fi.
2. Its **device ID** and **local key**. If the app cannot show them yet, the
   Tuya Developer console of the product owner can (device details), for
   testing only. The key changes when the purifier is paired again.
3. A machine on the same network (wired or Wi-Fi, same subnet, broadcasts not
   blocked), one of:
   * Birdie's Home Assistant instance with this integration installed, or
   * any computer with Python 3.13+ for the probe tool, optionally reachable
     by us through a remote session Birdie sets up and controls.
4. Someone near the purifier to watch it react and to change settings in the
   app while the probe watches.

Close Homey's Tuya Local app (or any other local Tuya client) during the test:
the purifier accepts only a few local connections.

## Step 1: Probe (no Home Assistant needed)

```bash
git clone <this repository> && cd <repository>
python -m venv .venv && . .venv/bin/activate
pip install tinytuya==1.20.0

# Which Tuya devices announce themselves? (the purifier should be listed
# as "Birdie purifier"; note the IP and protocol version)
python -m tools.purifier_probe discover

# Read-only: connect, print all DPs, watch changes for 60 s.
# Change mode/speed in the Birdie app and open the filter cover meanwhile.
export BIRDIE_LOCAL_KEY='<local key>'   # or leave unset to be prompted
python -m tools.purifier_probe read --device-id <device id> --watch 60 --report read.json

# Changes the purifier state (power off/on, every mode and speed), then
# restores power and mode. --include-filter-reset also resets the filter counter.
python -m tools.purifier_probe test --device-id <device id> --yes --report test.json
```

The reports contain no local key and no IP address (unless `--keep-ip`).
Send `read.json` and `test.json` to the developers.

## Step 2: Home Assistant

Install the integration (HACS custom repository or copy
`custom_components/birdie`), restart, then **Add integration > Birdie >
Air purifier (Wi-Fi, local network)**. Home Assistant OS and host-network
Docker installs can discover the purifier; otherwise enter its IP address.
Download the diagnostics from the device page afterwards (key and IP are
redacted).

## Checklist

| # | Check | Result |
|---|---|---|
| 1 | `discover` lists the purifier with product ID `a2zf7q0zxcutn5dy`; protocol version: ___ | |
| 2 | `read` connects with protocol "auto"; all DPs and their types recorded | |
| 3 | DPs present: 1, 2, 3, 21, 105, 106, 107, 108, 109, 110, 111 (note missing/extra) | |
| 4 | Changes made in the app arrive as pushes within a few seconds | |
| 5 | Power off/on from the probe / Home Assistant | |
| 6 | Modes auto, sleep and speeds manual1-4 | |
| 7 | PM2.5 value plausible, unit/scale | |
| 8 | air_quality values seen: ___ | |
| 9 | filter_total/used minutes plausible; filter life % matches the app | |
| 10 | filter_status value when a replacement is due (if it can be triggered) | |
| 11 | filter_cover value with the cover open | |
| 12 | reset_filter: accepted? counter reset? (only with consent) | |
| 13 | device_error values (if a fault can be simulated) | |
| 14 | Unplug the purifier: entities unavailable; plug in: back without restart | |
| 15 | Pair the purifier again in the app: Home Assistant asks for the new key, works after entering it | |
| 16 | Birdie app still works while Home Assistant is connected | |
| 17 | Router assigns a new IP: Home Assistant follows (or Reconfigure works) | |

## After testing

Update `purifier/model.py` (DPs, assumptions marked ASSUMPTION) and the tests
from the reports, and record here which checks passed **on the real device**.
