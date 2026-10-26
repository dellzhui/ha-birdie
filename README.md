# Birdie Home Assistant Integration

[![HACS validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml)

Birdie is a Home Assistant custom integration for Birdie products: Birdie BLE
air quality monitors, including Birdie Pro, and (in development) the Birdie
Wi-Fi air purifier, controlled directly over the local network.

## Status

Current release: `v0.1.3`

The integration is available for installation through HACS as a custom
repository. Submission to the HACS default repository is in progress; until it is
approved, add this repository manually in HACS.

## Features

- UI setup flow using Home Assistant Bluetooth discovery.
- Native Home Assistant Bluetooth support.
- Tested ESPHome Bluetooth Proxy support when active connections are enabled.
- Initial BLE reads and live updates through GATT notifications.
- Disconnect handling with unavailable entity states.
- Writable configuration entities for CO2 threshold and cool down period.
- Standalone BLE probe tool for setup verification and diagnostics.
- Birdie Pro firmware `1.6.0` and `1.6.1` BLE UUID compatibility.

## Entities

| Entity | Platform | Unit | Notes |
| --- | --- | --- | --- |
| CO2 | Sensor | ppm | Carbon dioxide measurement |
| Temperature | Sensor | Celsius | Temperature measurement |
| Humidity | Sensor | % | Relative humidity measurement |
| State | Sensor | - | `up`, `down`, `cooldown`, or `unknown` |
| CO2 alarm | Binary sensor | - | On when Birdie reports alarm state |
| Battery | Sensor | % | Battery level mapped from the device enum |
| CO2 threshold | Number | ppm | Writable, 400-5000 ppm |
| Cool down period | Number | min | Writable, 1-240 min |

## Birdie Air Purifier (In Development)

The Wi-Fi air purifier (built on the Tuya platform) is controlled directly
over the local network with the Tuya LAN protocol (versions 3.1 to 3.5,
implemented by the MIT-licensed [tinytuya](https://github.com/jasonacox/tinytuya)
library). No cloud service, cloud credentials or account sign-in is involved;
the Birdie app is still used to pair the purifier with Wi-Fi.

Setup: **Add integration > Birdie > Air purifier (Wi-Fi, local network)**.
Home Assistant searches the network for Birdie purifiers; pick yours and enter
its **local key**, or enter the **device ID** and local key yourself (with the
IP address if the purifier cannot be found automatically). Both values are
shown in the Birdie app. The local key changes when the purifier is paired
again: Home Assistant then asks for the new key (or use **Reconfigure**).

Requirements and limits:

- Home Assistant must be on the same network as the purifier and receive its
  UDP broadcasts (ports 6666/6667) for automatic discovery; with a manual IP
  address only TCP port 6668 is needed. Docker installs need host networking
  for discovery.
- Tuya devices accept few simultaneous local connections. Do not control the
  same purifier from another local integration (for example Tuya Local or
  LocalTuya) at the same time.
- Give the purifier a fixed IP address (DHCP reservation) if possible. The
  integration also follows the purifier to a new address when it announces it.

Entities:

| Entity | Platform | Unit | Notes |
| --- | --- | --- | --- |
| Purifier | Fan | - | On/off, 4 speeds (25/50/75/100 %), presets `auto` and `sleep` |
| PM2.5 | Sensor | ug/m3 | Particulate matter |
| Air quality | Sensor | - | `good`, `moderate`, `poor`, `very_poor` |
| Filter life | Sensor | % | Remaining filter life |
| Filter time remaining | Sensor | days | Remaining filter time |
| Device status | Sensor | - | Diagnostic: `none` (normal), `fan`, `pm25_sensor`, `overcurrent` |
| Filter replacement | Binary sensor | - | On when the filter should be replaced |
| Filter cover | Binary sensor | - | Diagnostic: on when the filter cover is open |
| Reset filter | Button | - | Resets the filter counter after a filter change |

An entity stays unavailable until the purifier has reported the data point it
shows. The data point mapping follows the product definition and has **not
yet been verified on a real purifier**; see `docs/remote-testing.md`.

Tools: `tools/purifier_probe.py` discovers purifiers and records what a real
one reports (for verification on the purifier's network), and
`tools/fake_purifier.py` runs a simulated purifier for demos. See
`docs/architecture.md` for the design.

## Requirements

Home Assistant 2026.3.0 or newer (needed for the bundled brand images; the
air purifier support was verified on 2026.9.2 against a simulated purifier and
with the 2026.10 test framework).

## Installation

### HACS Custom Repository

1. Open HACS.
2. Go to **Integrations**.
3. Open the three-dot menu and choose **Custom repositories**.
4. Add this repository URL:

   ```text
   https://github.com/dellzhui/ha-birdie
   ```

5. Select category **Integration**.
6. Install **Birdie**.
7. Restart Home Assistant.
8. Add the integration from **Settings > Devices & services**.

### HACS Default Repository

Once the HACS default repository submission is approved, Birdie will be
installable by searching for **Birdie** directly in HACS.

### Manual Installation

Copy `custom_components/birdie` into your Home Assistant `custom_components`
directory and restart Home Assistant.

## Bluetooth Requirements

### Home Assistant OS / Native Bluetooth

Add the discovered Bluetooth adapter, such as `hci0`, from **Settings > Devices
& services**. This enables Home Assistant's Bluetooth scanner.

Birdie does not need to be paired with the operating system. When discovery is
working, the integration can be added from the Home Assistant UI without entering
the BLE address manually.

### ESPHome Bluetooth Proxy

For ESPHome Bluetooth Proxy deployments, active connections must be enabled:

```yaml
bluetooth_proxy:
  active: true
```

Passive-only proxies may forward advertisements, but they cannot perform the
GATT reads, writes, and notification subscriptions required by this integration.

## Compatibility

Tested connection paths:

- Home Assistant OS with native Bluetooth.
- ESPHome Bluetooth Proxy with active connections enabled.

Tested Birdie Pro firmware versions:

- `1.6.0`
- `1.6.1`

Firmware `1.6.1` changes the temperature, humidity, and current time UUIDs. The
integration supports both the current UUIDs and the legacy firmware `1.6.0`
UUIDs.

## Debug Logging

To enable debug logs for this integration:

```yaml
logger:
  default: warning
  logs:
    custom_components.birdie: debug
    tinytuya: debug  # air purifier protocol details; local keys are redacted
```

## BLE Probe Tool

The repository includes `tools/birdie_probe.py`, a standalone BLE diagnostic
tool based on `bleak`. It does not depend on Home Assistant.

Install `bleak` in the Python environment where you run the tool:

```bash
python -m pip install bleak
```

Scan for nearby BLE devices:

```bash
python tools/birdie_probe.py --scan
```

Connect to a known BLE address, dump GATT services, and read known
characteristics:

```bash
python tools/birdie_probe.py --address 4C:5B:B3:43:A8:19
```

Connect and subscribe to notifications for CO2, temperature, humidity, state,
and battery:

```bash
python tools/birdie_probe.py --address 4C:5B:B3:43:A8:19 --notify
```

The example address is for probe usage only. The Home Assistant integration uses
Bluetooth discovery and stores the selected address in the config entry.

## Scope

Implemented:

- Bluetooth discovery and UI setup flow.
- BLE reads, notifications, and writes for the supported characteristics.
- Sensors, binary sensor, and number entities for the current Birdie BLE data
  model.
- Native Bluetooth and ESPHome Bluetooth Proxy connection paths.
- Birdie Pro firmware `1.6.0` and `1.6.1` UUID compatibility.
- HACS-compatible repository structure and validation workflows.

Not implemented:

- OTA update.
- Data log service support.
- Home Assistant Core submission.
- Device firmware changes.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
