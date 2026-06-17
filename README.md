# Birdie Home Assistant Custom Integration

This repository contains a custom Home Assistant integration for Birdie and
Birdie Pro BLE devices.

## Current Status

Phase 2 implements the core Home Assistant custom integration:

- UI setup flow using Home Assistant Bluetooth discovery.
- BLE connection, initial reads, notifications, disconnect handling, and writes.
- CO2, temperature, humidity, Birdie state, and battery sensors.
- CO2 alarm binary sensor.
- CO2 threshold and cool down period number entities.
- Shared BLE UUID constants and parsing helpers.
- A standalone `tools/birdie_probe.py` script for BLE discovery, service dumps,
  characteristic reads, and notification debugging.

The integration domain is `birdie`. `Birdie Pro` is a device model name and is
not used as the Home Assistant domain.

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

- Add HACS/public release metadata and documentation.
- Add diagnostics and deeper error handling after more real-device testing.
- Add tests for config flow, parser behavior, and entity state updates.

## Out of Scope for Phase 1

- OTA update.
- Data log service support.
- Home Assistant Core submission.
- Device firmware changes.
- Complex UI.
- Full HACS release workflow.
- Complete Home Assistant entity implementation.
