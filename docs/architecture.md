# Architecture: Birdie Air Purifier in Home Assistant

Status: local LAN control selected (2026-10-26); first real-device probe 2026-10-26. Last reviewed 2026-10-26.

The purifier is a Tuya-based Wi-Fi device. Birdie calls it "Pedestal" in its
Tuya console and "Podium" in its Homey work; the integration uses the neutral
name "Birdie Air Purifier" until the product name is confirmed
(`purifier/model.py`, `MODEL_NAME`).

## Decision

Home Assistant talks to the purifier **directly over the local network** with
the Tuya LAN protocol, using the device ID and local key the user copies from
the Birdie app. Normal operation needs no cloud API, no Tuya IoT Core
subscription, no Tuya cloud credentials and no Birdie backend.

```
Birdie app ── BLE pairing, Wi-Fi setup ──> Purifier ── (own firmware traffic) ──> Tuya Cloud
   │ shows device ID + local key              ▲
   ▼                                          │ Tuya LAN protocol (TCP 6668, encrypted)
User ── enters them once ──> Home Assistant ──┘ + UDP 6666/6667 announcements
```

The purifier keeps talking to the Tuya cloud for the Birdie app as before;
Home Assistant does not depend on that.

Birdie (Tobias) validated this route on Homey with an existing Tuya Local
app (power on/off). The probe tool then confirmed on a real purifier that
status, pushes, power and every mode work over the LAN (see Data points).

### Why the cloud route was dropped

An earlier design (kept in git: branch `feature/pedestal-cloud`, tag
`pedestal-cloud-final`) used a "Birdie Cloud Bridge" holding the Tuya cloud
project secret, with OAuth sign-in for Birdie accounts. Birdie does not want
the paid Tuya IoT Core service that route needs (polling every user's devices
exceeds the trial quota), and it needs hosting plus a Firebase identity
integration. The LAN route needs none of that. Kept from that work: the
Home Assistant entities, the fan/preset mapping, the DP decoding and the
tests around them.

## Components

| Path | Role | Home Assistant imports |
|---|---|---|
| `custom_components/birdie/tuya_lan/` | LAN client: connection, status, DP writes, reconnect, discovery, log redaction | none |
| `custom_components/birdie/purifier/model.py` | The only place that knows the purifier's DPs: decode state, build DP writes | none |
| `custom_components/birdie/purifier/` (rest) | Coordinator, entities, config flow helpers, shared discovery | yes |
| `custom_components/birdie/config_flow.py` | UI setup for Bluetooth devices and purifiers | yes |
| `tools/purifier_probe.py` | Real-device checks on the customer's network | none |
| `tools/fake_purifier.py`, `tests/tuya_lan/fake_device.py` | Simulated purifier speaking the LAN protocol | none |

The Bluetooth (Birdie Pro) part of the integration is unchanged.

### Tuya LAN library

| Project | License | Use |
|---|---|---|
| [tinytuya](https://github.com/jasonacox/tinytuya) 1.20.0 | MIT | **Dependency.** Protocol 3.1-3.5 framing, AES-ECB/GCM, 3.4/3.5 session key negotiation, UDP announcement decryption. Same pinned version as Tuya Local, so both can be installed together. |
| [make-all/tuya-local](https://github.com/make-all/tuya-local) | MIT | Reference for how to drive tinytuya from Home Assistant (persistent connection, heartbeats, protocol rotation). No code copied. |
| [rospogrigio/localtuya](https://github.com/rospogrigio/localtuya) | GPL-3.0 | Reference only (license incompatible with copying into this MIT project). No code copied. |

The protocol and its encryption are not reimplemented; only the device side
used by the test simulator is assembled from tinytuya's primitives.

### LAN client (`tuya_lan.TuyaLanDevice`)

* tinytuya does blocking socket I/O. Each device gets one worker thread; all
  tinytuya calls run there, so they are serialised without locks and never
  block the event loop.
* `async_connect()` opens a persistent connection and reads the full status.
  With protocol "auto" it tries the version from the device's announcement
  (or the last working one) first, then 3.3, 3.5, 3.4, 3.1. 3.2 (tinytuya's
  "device22" variant) is used only when configured.
* A background loop sends a heartbeat every 10 s, receives pushed status
  changes, reads the full status every 60 s and 3 s after a command, treats
  35 s without any frame as a lost connection and reconnects with backoff
  (5 s doubling to 60 s).
* Commands are DP writes (`{"1": true, "3": "manual2"}`); the device confirms
  them with a status push.
* Errors: `DeviceUnreachableError` (nothing answers at the address, timeout)
  and `InvalidLocalKeyError` (the device answered but rejected the key: wrong
  key, or the protocol version does not match).
* tinytuya logs the local key in some debug messages; a filter on the
  tinytuya loggers replaces registered keys with `**REDACTED**`.

### Discovery

Tuya devices announce themselves every few seconds (UDP 6666 plain, 6667
encrypted with a published key; some 3.5 devices only answer a request sent
to UDP 7000). The announcement carries device ID, IP, protocol version and
product ID (`productKey`). The integration recognises Birdie purifiers by
product ID `a2zf7q0zxcutn5dy`.

* Config flow: searches for 6 s and lists Birdie purifiers; manual entry is
  always possible. Without an IP address, manual entry searches for the given
  device ID.
* While a purifier is set up, one shared listener follows configured
  purifiers to a new IP address and offers newly seen Birdie purifiers as
  discovered devices in the UI.
* Sockets use SO_REUSEADDR/SO_REUSEPORT to coexist with other Tuya
  integrations. Containers need host networking to receive broadcasts.

### Home Assistant behaviour

* One config entry per purifier (unique ID = Tuya device ID). Entry data:
  device ID, host, local key, configured protocol ("auto" by default) and the
  detected version as a hint for the next start.
* Setup connects once: unreachable -> setup retried by Home Assistant; key
  rejected -> re-authentication flow asking for the new key.
* At runtime the device pushes changes; entities go unavailable when the
  connection is lost and come back with it. Three consecutive key rejections
  while reconnecting start re-authentication (a single rejection can also be
  a rebooting device).
* Each entity declares the DPs it shows and stays unavailable until the
  device has reported them, because the LAN protocol may not expose every DP
  of the product definition. The filter reset button is not tied to a
  reported DP (a trigger DP may never be reported).
* Reconfigure changes the key, IP address or protocol version.
* Diagnostics contain the raw DPs (for mapping verification) with key and IP
  address redacted.

## Data points

Product schema from the Tuya console, and what a real purifier did over the
LAN in the 2026-10-26 probe (protocol 3.4, hardware version HW1.0.0, one unit).

| DP | Code | Schema | Entity | Real device |
|---|---|---|---|---|
| 1 | switch | bool, rw | Fan on/off | Reported; on/off writes work; app changes pushed |
| 2 | pm25 | value 0-500 ug/m3, ro | PM2.5 sensor | Reported (int); stayed at 1 while DP 111 reported a PM2.5 sensor fault |
| 3 | mode | enum auto/sleep/manual1-4, rw | Fan presets (auto, sleep) and speed (manual1-4 = 25-100 %) | Reported; every value written and confirmed; app changes pushed |
| 21 | air_quality | enum, ro | Air quality sensor | Reported; only `good` seen |
| 105 | filter_total_minutes | value, ro | Filter life %, time remaining | Reported: 129600 (90 days) |
| 106 | filter_used_minutes | value, ro | Filter life %, time remaining | Reported; counts up while running, pushed every minute |
| 107 | filter_status | bool, ro | Filter replacement (assumed true = replace) | Reported: false at 90 % filter life |
| 108 | reset_filter | bool, rw | Reset filter button (assumed: write true) | Not reported; not tested |
| 109 | filter_cover | bool, ro | Filter cover (false = open) | Reported: true normally, false for a few seconds while the cover was opened |
| 110 | hw_version | string, ro | Device hardware version | Reported: `HW1.0.0` |
| 111 | device_error | enum normal/fan/pm25/ocp, ro | Device status sensor | Reported: `pm25` throughout (see open points) |

## Open points

| Topic | Owner |
|---|---|
| Can the Birdie app show the device ID and local key? (Birdie's app developer is checking) | Birdie |
| The test unit reports device_error `pm25` and PM2.5 = 1: real sensor fault on that unit, or does the value mean something else? | Birdie |
| filter_cover: confirm false = open | Birdie |
| filter_status true when a replacement is due, reset_filter behaviour, air_quality values other than `good` | Birdie / later test |
| Product ID in the purifier's LAN broadcast (the probe was run with a fixed IP address, so discovery was not recorded) | Birdie / later test |
| Home Assistant on Birdie's network: key change, IP change, power loss, app and Home Assistant connected together | Later test |
| Final product name (Pedestal / Podium) and whether other product IDs exist | Birdie |
