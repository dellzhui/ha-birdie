# Architecture: Birdie Podium Air Purifier in Home Assistant

Status: local network control; real-device probe 2026-10-26; Home Assistant on
a real Podium at Birdie 2026-11-02. Last reviewed 2026-11-03.

The purifier is a Wi-Fi device named **Podium** (confirmed by Birdie).
"Pedestal" was its development name and is still used in Birdie's product
definition and on early units (`purifier/model.py`, `MODEL_NAME`).

## Decision

Home Assistant talks to the purifier **directly over the local network**,
using the device ID and local key the user copies from the Birdie app.
Normal operation needs no cloud API, no cloud credentials and no Birdie
backend.

```
Birdie app ── BLE pairing, Wi-Fi setup ──> Purifier ── (own firmware traffic) ──> Birdie's cloud
   │ shows device ID + local key              ▲
   ▼                                          │ local protocol (TCP 6668, encrypted)
User ── enters them once ──> Home Assistant ──┘ + UDP 6666/6667 announcements
```

The purifier keeps talking to the cloud for the Birdie app as before; Home
Assistant does not depend on that.

Birdie validated local control on Homey first (power on/off). The probe tool
then confirmed on a real purifier that status, pushes, power and every mode
work over the LAN (see Data points), and Birdie ran this integration on a
real Podium.

## Components

| Path | Role | Home Assistant imports |
|---|---|---|
| `custom_components/birdie/lan/` | LAN client: connection, status, DP writes, reconnect, discovery, search by key, log redaction | none |
| `custom_components/birdie/purifier/model.py` | The only place that knows the purifier's DPs: decode state, build DP writes | none |
| `custom_components/birdie/purifier/` (rest) | Coordinator, entities, config flow helpers, shared discovery, network search | yes |
| `custom_components/birdie/config_flow.py` | UI setup for Bluetooth devices and purifiers | yes |
| `tools/purifier_probe.py` | Real-device checks on the customer's network | none |
| `tools/fake_purifier.py`, `tests/lan/fake_device.py` | Simulated purifier speaking the LAN protocol | none |

The Bluetooth (Birdie Pro) part of the integration is unchanged.

The protocol framing, its encryption (AES-ECB/GCM), the 3.4/3.5 session key
negotiation and the decryption of announcements come from an MIT-licensed
protocol library, pinned in `manifest.json` (`requirements`). They are not
reimplemented; only the device side used by the test simulator is assembled
from the library's primitives.

### LAN client (`lan.LanDevice`)

* The library does blocking socket I/O. Each device gets one worker thread;
  all library calls run there, so they are serialised without locks and never
  block the event loop.
* `async_connect()` opens a persistent connection and reads the full status.
  With protocol "auto" it tries the version from the device's announcement
  (or the last working one) first, then 3.3, 3.5, 3.4, 3.1. 3.2 (the
  library's "device22" variant) is used only when configured.
* A background loop sends a heartbeat every 10 s, receives pushed status
  changes, reads the full status every 60 s and 3 s after a command, treats
  35 s without any frame as a lost connection and reconnects with backoff
  (5 s doubling to 60 s).
* Commands are DP writes (`{"1": true, "3": "manual2"}`); the device confirms
  them with a status push.
* Errors: `DeviceUnreachableError` (nothing answers at the address, timeout)
  and `InvalidLocalKeyError` (the device answered but rejected the key: wrong
  key, or the protocol version does not match).
* The library logs the local key in some debug messages; a filter on its
  loggers replaces registered keys with `**REDACTED**`.

### Discovery

Devices announce themselves every few seconds (UDP 6666 plain, 6667
encrypted with a published key; some 3.5 devices only answer a request sent
to UDP 7000). The announcement carries device ID, IP, protocol version and
product ID (`productKey`). The integration recognises Birdie purifiers by
product ID `a2zf7q0zxcutn5dy`.

* Config flow: listens for 6 s and lists Birdie purifiers; manual entry is
  always possible. Without an IP address, manual entry listens for the given
  device ID, then searches by key (below).
* While a purifier is set up, one shared listener follows configured
  purifiers to a new IP address and offers newly seen Birdie purifiers as
  discovered devices in the UI.
* The discovery request (UDP 7000) is answered by every such device on the
  network, so it is broadcast only during a search: the 6 s config flow
  search and the address lookup when setup cannot reach the purifier. The
  shared listener never sends anything. The Podium (protocol 3.4) announces
  itself without a request.
* Sockets use SO_REUSEADDR/SO_REUSEPORT to coexist with other integrations.
  Containers need host networking to receive broadcasts.

### Search by key (`lan.search`)

Some routers drop broadcasts from wireless devices, so neither the
announcements nor the discovery request get through, while TCP 6668 works.
When a manual entry without IP address is not heard, the config flow
searches for it (progress step, up to about a minute):

1. Addresses: for each enabled IPv4 adapter of Home Assistant with a private
   address, its network, narrowed to the /24 around the address when larger;
   Home Assistant's own addresses, public, loopback and link-local addresses
   are skipped; at most 1024 addresses.
2. Open ports: a TCP connect to port 6668 on each address (64 at a time, 1 s
   timeout). Nothing is sent.
3. Key check: on each address that accepted, `LanDevice.async_connect()` with
   the entered device ID and key (8 at a time, 3 s per protocol version). The
   first device that answers with its status is the purifier. Other devices
   cannot decrypt what they receive and refuse the handshake, so they never
   get a command; all connections are closed.

Nothing is searched in the background: the search runs only when the user
asks for it during setup.

### Home Assistant behaviour

* One config entry per purifier (unique ID = device ID). Entry data: device
  ID, host, local key, configured protocol ("auto" by default) and the
  detected version as a hint for the next start.
* Setup connects once. Unreachable: look for the purifier's announcement and
  connect at its new address if it moved, otherwise Home Assistant retries
  setup. Key rejected: retried too, and only the third rejection in a row
  starts re-authentication, because with protocol 3.4/3.5 a busy or slow
  purifier fails the handshake exactly like a wrong key (library error 914).
* At runtime the device pushes changes; entities go unavailable when the
  connection is lost and come back with it. Three consecutive key rejections
  while reconnecting start re-authentication (a single rejection can also be
  a rebooting device); a re-authentication request is withdrawn when the
  stored key works again.
* Each entity declares the DPs it shows and stays unavailable until the
  device has reported them, because the LAN protocol may not expose every DP
  of the product definition. The filter reset button is not tied to a
  reported DP (a trigger DP may never be reported).
* Reconfigure changes the key, IP address or protocol version.
* Diagnostics contain the raw DPs (for mapping verification) with key and IP
  address redacted.

## Data points

Product schema from Birdie's product definition, and what a real purifier did over the
LAN in the 2026-10-26 probe (protocol 3.4, hardware version HW1.0.0, one unit).

| DP | Code | Schema | Entity | Real device |
|---|---|---|---|---|
| 1 | switch | bool, rw | Fan on/off | Reported; on/off writes work; app changes pushed |
| 2 | pm25 | value 0-500 ug/m3, ro | PM2.5 sensor | Reported (int); stayed at 1 on a unit with a broken sensor (DP 111 `pm25`), so the integration shows PM2.5 and air quality as unknown during that fault |
| 3 | mode | enum auto/sleep/manual1-4, rw | Fan presets (auto, sleep) and speed (manual1-4 = 25-100 %) | Reported; every value written and confirmed; app changes pushed |
| 21 | air_quality | enum, ro | Air quality sensor | Reported; only `good` seen |
| 105 | filter_total_minutes | value, ro | Filter life %, time remaining | Reported: 129600 (90 days) |
| 106 | filter_used_minutes | value, ro | Filter life %, time remaining | Reported; counts up while running, pushed every minute |
| 107 | filter_status | bool, ro | Filter replacement (assumed true = replace) | Reported: false at 90 % filter life |
| 108 | reset_filter | bool, rw | Reset filter button (assumed: write true) | Not reported; not tested |
| 109 | filter_cover | bool, ro | Filter cover (false = open) | Reported: true normally, false while the cover was open (confirmed by Birdie) |
| 110 | hw_version | string, ro | Device hardware version | Reported: `HW1.0.0` |
| 111 | device_error | enum normal/fan/pm25/ocp, ro | Device status sensor | Reported: `pm25` throughout; that unit's PM2.5 sensor is broken (confirmed by Birdie) |

## Open points

| Topic | Owner |
|---|---|
| filter_status true when a replacement is due, reset_filter behaviour, air_quality values other than `good` | Birdie / later test |
| Product ID in the purifier's LAN broadcast, and why Birdie's Home Assistant did not list the Podium (setup needed the IP address) | Birdie diagnostics |
| Values from a Podium with a working PM2.5 sensor | Birdie diagnostics |
| Following an IP address change when broadcasts never arrive (today: fixed address recommended) | Later |
| Whether other product IDs exist besides `a2zf7q0zxcutn5dy` | Birdie |
