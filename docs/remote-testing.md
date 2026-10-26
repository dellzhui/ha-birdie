# Testing With a Real Birdie Podium

A first probe ran on a real purifier on 2026-10-26 (results in the checklist
below); Home Assistant itself has run against a simulated purifier only. The LAN
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
   * any computer with Python 3.11+ for the probe tool, optionally reachable
     by us through a remote session Birdie sets up and controls.
4. Someone near the purifier to watch it react and to change settings in the
   app while the probe watches.

Close Homey's Tuya Local app (or any other local Tuya client) during the test:
the purifier accepts only a few local connections.

## Step 1: Probe (no Home Assistant needed)

The developers send a probe kit (`python -m tools.build_probe_kit` builds
`dist/birdie-purifier-probe-<version>.zip`; its `README.txt` has the same
steps for macOS, Linux and Windows). From a clone of this repository instead:

```bash
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

Preferably use a purifier with a working PM2.5 sensor.

1. Install the beta (`v0.2.0b1` or later): in HACS open **Birdie**, choose
   **Redownload** in the menu and select the beta version (pre-releases may
   have to be enabled for the repository first). Without HACS, copy
   `custom_components/birdie` from the release into the Home Assistant
   `config/custom_components` folder. Restart Home Assistant.
2. Close other local controllers of the purifier (Homey Tuya Local, the
   probe tool).
3. **Add integration > Birdie > Podium air purifier (Wi-Fi, local network)**. Home
   Assistant OS and host-network Docker installs should list the purifier
   (checklist item 1); otherwise choose manual entry and give the IP address.
4. Work through checklist items 5-9 and 14-17 from Home Assistant.
5. Download the diagnostics from the device page (key and IP are redacted)
   and send them with notes on each item.

## Checklist

| # | Check | Result |
|---|---|---|
| 1 | `discover` lists the purifier with product ID `a2zf7q0zxcutn5dy`; protocol version: ___ | Protocol 3.4; product ID not recorded (probe run with a fixed IP) |
| 2 | `read` connects with protocol "auto"; all DPs and their types recorded | Pass (with protocol 3.4 set; "auto" not tried), connects in 0.05 s |
| 3 | DPs present: 1, 2, 3, 21, 105, 106, 107, 108, 109, 110, 111 (note missing/extra) | All except 108; no extra DPs |
| 4 | Changes made in the app arrive as pushes within a few seconds | Pass (mode, power, filter cover) |
| 5 | Power off/on from the probe / Home Assistant | Pass (probe) |
| 6 | Modes auto, sleep and speeds manual1-4 | Pass (probe), each confirmed in ~0.3 s |
| 7 | PM2.5 value plausible, unit/scale | Not checked: that unit's PM2.5 sensor is broken (value stays 1) |
| 8 | air_quality values seen: ___ | `good` only |
| 9 | filter_total/used minutes plausible; filter life % matches the app | Plausible (129600 / 13311 min = 90 %); app value not compared |
| 10 | filter_status value when a replacement is due (if it can be triggered) | Not triggered (false at 90 %) |
| 11 | filter_cover value with the cover open | false while open, true when closed (Birdie app showed the same) |
| 12 | reset_filter: accepted? counter reset? (only with consent) | Not tested |
| 13 | device_error values (if a fault can be simulated) | `pm25` throughout: that unit's PM2.5 sensor is broken |
| 14 | Unplug the purifier: entities unavailable; plug in: back without restart | |
| 15 | Pair the purifier again in the app: Home Assistant asks for the new key, works after entering it | |
| 16 | Birdie app still works while Home Assistant is connected | App changes worked while the probe was connected |
| 17 | Router assigns a new IP: Home Assistant follows (or Reconfigure works) | |

## After testing

Update `purifier/model.py` (DPs, assumptions marked ASSUMPTION) and the tests
from the reports, and record here which checks passed **on the real device**.
Results above: 2026-10-26 probe (`read` and `test`, one unit, hardware
version HW1.0.0). Checks 14, 15 and 17 need Home Assistant on Birdie's
network.
