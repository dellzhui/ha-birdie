# Birdie Home Assistant Integration

[![HACS validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml)

Birdie is a Home Assistant custom integration for Birdie BLE air quality
monitors, including Birdie Pro.

## Status

Current release: `v0.1.1`

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
