# Birdie Home Assistant Custom Integration

Home Assistant custom integration for Birdie and Birdie Pro BLE devices.

The integration domain is `birdie`. `Birdie Pro` is a device model name and is
not used as the Home Assistant domain.

## Features

- UI setup flow using Home Assistant Bluetooth discovery.
- BLE connection, initial reads, notifications, disconnect handling, and writes.
- CO2, temperature, humidity, Birdie state, and battery sensors.
- CO2 alarm binary sensor.
- CO2 threshold and cool down period number entities.
- A standalone `tools/birdie_probe.py` script for BLE discovery, service dumps,
  characteristic reads, and notification debugging.

## Installation

### HACS custom repository

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

### Manual installation

Copy `custom_components/birdie` into your Home Assistant `custom_components`
directory and restart Home Assistant.

## Bluetooth Notes

For HAOS or native Bluetooth setups, first add the Home Assistant-discovered
Bluetooth adapter, such as `hci0`, from **Settings > Devices & services**. This
enables Home Assistant's Bluetooth scanner. You do not need to pair Birdie and
you do not need to enter the Birdie address manually when discovery works.

For ESPHome Bluetooth Proxy setups, the proxy must support active connections:

```yaml
bluetooth_proxy:
  active: true
```

Passive-only proxies may forward advertisements but cannot reliably connect to
Birdie for GATT reads and notifications.

## Entities

The integration creates:

- CO2 sensor
- Temperature sensor
- Humidity sensor
- Birdie state sensor
- CO2 alarm binary sensor
- Battery sensor
- CO2 threshold number
- Cool down period number

## Debug Logging

To enable integration debug logs:

```yaml
logger:
  default: warning
  logs:
    custom_components.birdie: debug
```

## BLE Probe

Install `bleak` in the Python environment where you run the tool:

```bash
python -m pip install bleak
```

Scan for nearby BLE devices and highlight Birdie candidates:

```bash
python tools/birdie_probe.py --scan
```

Connect to a known BLE address, dump services, and read known characteristics:

```bash
python tools/birdie_probe.py --address 4C:5B:B3:43:A8:19
```

Connect and subscribe to notifications for CO2, temperature, humidity, Birdie
state, and battery:

```bash
python tools/birdie_probe.py --address 4C:5B:B3:43:A8:19 --notify
```

The test address above is only for probe/debug usage. The Home Assistant
integration stores the selected Bluetooth address in a config entry and does not
hard-code test hardware.

## Planned Later Phases

- Add diagnostics and deeper error handling after more real-device testing.
- Add tests for config flow, parser behavior, and entity state updates.

## Out of Scope

- OTA update.
- Data log service support.
- Home Assistant Core submission.
- Device firmware changes.
