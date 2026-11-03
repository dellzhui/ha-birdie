# Birdie Home Assistant Integration

[![HACS validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hacs.yml)
[![Hassfest validation](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml/badge.svg)](https://github.com/dellzhui/ha-birdie/actions/workflows/hassfest.yml)

Birdie is a Home Assistant custom integration for Birdie products: Birdie BLE
air quality monitors, including Birdie Pro, and (beta) the Birdie Podium Wi-Fi
air purifier.

## Status

Current release: `v0.2.0b4`. It adds the Birdie Podium air purifier (beta);
Birdie Pro setups carry on unchanged.

Birdie is in the HACS default repository: search for **Birdie** in HACS to
install it.

## Features

- UI setup flow using Home Assistant Bluetooth discovery.
- Native Home Assistant Bluetooth support.
- Tested ESPHome Bluetooth Proxy support when active connections are enabled.
- Initial BLE reads and live updates through GATT notifications.
- Disconnect handling with unavailable entity states.
- Writable configuration entities for CO2 threshold and cool down period.
- Standalone BLE probe tool for setup verification and diagnostics.
- Birdie Podium Wi-Fi air purifier (beta, see below).
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

## Birdie Podium Air Purifier (Beta)

Home Assistant controls the purifier inside your home network; no cloud
service or Birdie sign-in is involved.

### What you need

- The purifier set up in the **Birdie app** and connected to your Wi-Fi.
- Its **device ID** and **local key** from the Birdie app (long-press a value
  to copy it).
- Home Assistant on the same network as the purifier (not a guest network or
  a separate VLAN).
- Recommended: a fixed IP address for the purifier (DHCP reservation in your
  router).

The local key works like a password for the purifier: do not share it or
paste it into forum posts or issue reports.

### Add the purifier

1. **Settings > Devices & services > Add integration > Birdie**.
2. Choose **Podium air purifier (Wi-Fi, local network)**.
3. If the purifier is listed, select it and paste its local key. If it is
   not, choose *Enter manually*, paste the device ID and local key and leave
   the IP address empty: Home Assistant looks for the purifier itself, which
   can take up to a minute. Enter the IP address only if it is still not
   found.

### Entities

| Entity | Type | Notes |
| --- | --- | --- |
| Purifier | Fan | On/off, speeds 1-4, presets **Auto** and **Sleep** |
| PM2.5 | Sensor | ug/m3 |
| Air quality | Sensor | Good, Moderate, Poor, Very poor |
| Filter life | Sensor | Remaining filter life in % |
| Filter time remaining | Sensor | Remaining filter time |
| Filter replacement | Binary sensor | On when the filter should be replaced |
| Filter cover | Binary sensor | Diagnostic; on when the filter cover is open |
| Device status | Sensor | Diagnostic |
| Reset filter | Button | Resets the filter counter after a filter change |

Changes made in the Birdie app or on the purifier show up in Home Assistant
within seconds.

### When the local key or IP address changes

The local key changes when the purifier is set up again in the Birdie app.
Home Assistant then asks for the new key under **Settings > Devices &
services** (**Re-authentication required**). You can also change the key or
the IP address at any time with **Reconfigure** in the purifier's
integration entry.

When the purifier gets a new IP address, Home Assistant finds it again on its
own; if your router hides the purifier, this can take about 10 minutes. A
fixed IP address avoids the wait.

### Troubleshooting

| Problem | What to check |
| --- | --- |
| The purifier is not listed | Use *Enter manually* with the device ID and local key (see above). |
| Cannot connect | The purifier is on and on the same network as Home Assistant. Close other local controllers of this purifier (another integration or hub); it accepts only a few connections. |
| The key is not accepted | Copy the current key from the Birdie app again; it changes when the purifier is set up again. |
| Entities are unavailable | The purifier is off or offline. After a new IP address Home Assistant finds it again on its own, which can take about 10 minutes; you can also enter the new address with **Reconfigure**. |

When reporting a problem, attach the diagnostics (**Download diagnostics** on
the purifier's device page); the local key and IP address are removed from
them.

## Requirements

Home Assistant 2026.3.0 or newer.

## Installation

### HACS

1. Open HACS.
2. Search for **Birdie** and open it.
3. Select **Download**.
4. Restart Home Assistant.
5. Add the integration from **Settings > Devices & services**.

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
python tools/birdie_probe.py --address AA:BB:CC:11:22:33
```

Connect and subscribe to notifications for CO2, temperature, humidity, state,
and battery:

```bash
python tools/birdie_probe.py --address AA:BB:CC:11:22:33 --notify
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

- Birdie Podium Wi-Fi air purifier (beta): discovery, UI setup, local key
  updates, fan, sensors, binary sensors and filter reset.

Not implemented:

- OTA update.
- Data log service support.
- Device firmware changes.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
