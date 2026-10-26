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
- Birdie Wi-Fi air purifier over the local network (in development, see
  below).
- Birdie Pro firmware `1.6.0` and `1.6.1` BLE UUID compatibility.

## Birdie Pro Entities (Bluetooth)

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

## Birdie Podium Air Purifier (In Development)

The Birdie Podium Wi-Fi air purifier is controlled directly over the local network. No
cloud service, Tuya account or Birdie sign-in is involved: copy the purifier's
**device ID** and **local key** from the Birdie app, then add it with
**Add integration > Birdie > Podium air purifier (Wi-Fi, local network)**. Home
Assistant finds the purifier on the network, or you enter its IP address.

Entities: fan (on/off, 4 speeds, presets `auto` and `sleep`), PM2.5, air
quality, filter life and time remaining, filter replacement, filter cover,
device status and a reset filter button.

Setup, network requirements, key changes and troubleshooting:
**[docs/air-purifier.md](docs/air-purifier.md)**.

A first test on a real purifier confirmed the local connection, status
updates, power and all modes and speeds; the filter replacement and reset
are not confirmed yet ([docs/remote-testing.md](docs/remote-testing.md)).
Design notes:
[docs/architecture.md](docs/architecture.md). Tools:
`tools/purifier_probe.py` checks a real purifier without Home Assistant,
`tools/fake_purifier.py` runs a simulated one.

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

- Birdie Wi-Fi air purifier over the local network (Tuya LAN protocol 3.1-3.5
  through [tinytuya](https://github.com/jasonacox/tinytuya)): discovery, UI
  setup, local key updates, fan, sensors, binary sensors and filter reset.
  Partly verified on a real purifier (see docs/remote-testing.md).

Not implemented:

- OTA update.
- Data log service support.
- Home Assistant Core submission.
- Device firmware changes.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
