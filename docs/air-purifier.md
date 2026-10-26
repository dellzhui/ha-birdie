# Birdie Air Purifier

Home Assistant controls the Birdie Wi-Fi air purifier directly over your local
network. No cloud service, Tuya account or Birdie sign-in is involved: you
copy two values from the Birdie app once, and Home Assistant talks to the
purifier inside your home network.

> **Status: in development.** Everything below is tested against a simulated
> purifier. Which data points a real purifier reports, and some of their
> meanings, have not been confirmed on a real unit yet
> ([remote-testing.md](remote-testing.md)).

## What you need

* The purifier set up in the **Birdie app** and connected to your Wi-Fi.
* Its **device ID** and **local key**, shown in the Birdie app.
* Home Assistant 2026.3.0 or newer, with the Birdie integration installed
  (see the [README](../README.md#installation)). The protocol library
  (`tinytuya`) is installed by Home Assistant automatically.
* Home Assistant on the **same network** as the purifier:
  * Automatic discovery listens for the purifier's broadcasts on UDP ports
    6666 and 6667. This works on Home Assistant OS and on Docker with
    `network_mode: host`. Broadcasts do not cross routers or VLANs.
  * Control uses TCP port 6668 on the purifier. If Home Assistant cannot see
    the broadcasts (Docker bridge network, separate VLAN), enter the
    purifier's IP address during setup; only TCP 6668 then needs to be
    reachable.
* Recommended: a fixed IP address for the purifier (DHCP reservation in your
  router). Home Assistant also follows the purifier to a new address when it
  sees its broadcasts.

The device ID identifies the purifier. The **local key** is the password of
its local connection: anyone on your network who has it can control the
purifier. Do not share it, and do not paste it into forum posts or issue
reports. Home Assistant stores it in its configuration and leaves it out of
logs and diagnostics.

## Add the purifier

1. **Settings > Devices & services > Add integration > Birdie**.
2. Choose **Air purifier (Wi-Fi, local network)**. Home Assistant searches
   the network for a few seconds.
3. Then either:
   * **The purifier is listed:** select it and enter its **local key**.
   * **It is not listed** (or you choose *Enter manually*): enter the
     **device ID** and **local key**. Leave the IP address empty to search for
     that device ID, or enter the IP address if the search does not find it.
4. Home Assistant connects to the purifier to check the key before it adds
   the device. The protocol version is detected automatically; the
   **Advanced** section lets you choose one if detection fails (the
   `discover` command of the probe tool shows the version the purifier
   announces).

Once one purifier is set up, Home Assistant keeps listening for broadcasts:
another Birdie purifier that appears on the network is offered under
**Settings > Devices & services > Discovered**.

## Entities

| Entity | Type | Notes |
| --- | --- | --- |
| Purifier | Fan | On/off, speeds 1-4 as 25/50/75/100 %, presets **Auto** and **Sleep** |
| PM2.5 | Sensor | ug/m3 |
| Air quality | Sensor | Good, Moderate, Poor, Very poor |
| Filter life | Sensor | Remaining filter life in % |
| Filter time remaining | Sensor | Remaining filter time |
| Filter replacement | Binary sensor | On when the filter should be replaced |
| Filter cover | Binary sensor | Diagnostic; on when the filter cover is open |
| Device status | Sensor | Diagnostic; Normal, Fan fault, PM2.5 sensor fault, Overcurrent protection |
| Reset filter | Button | Resets the filter counter after a filter change |

How the fan maps to the purifier's modes:

* Setting a speed switches the purifier to manual mode at that speed;
  selecting a preset switches to auto or sleep mode. In a preset mode the
  fan shows no speed.
* Setting a speed or preset also turns the purifier on; speed 0 % turns it
  off.
* Changes made in the Birdie app or on the purifier show up in Home
  Assistant within seconds (the purifier pushes them).

An entity stays **unavailable** until the purifier has reported the value it
shows. If an entity stays unavailable while the others work, the purifier
probably does not report that value over the local network; please report it
with diagnostics attached (see [Troubleshooting](#troubleshooting)).

### Automation examples

Entity IDs depend on the device name; these use the default name.

```yaml
automation:
  - alias: "Purifier: auto mode when the air gets worse"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.birdie_air_purifier_pm2_5
        above: 35
        for: "00:05:00"
    actions:
      - action: fan.set_preset_mode
        target:
          entity_id: fan.birdie_air_purifier
        data:
          preset_mode: auto

  - alias: "Purifier: filter reminder"
    triggers:
      - trigger: state
        entity_id: binary_sensor.birdie_air_purifier_filter_replacement
        to: "on"
    actions:
      - action: persistent_notification.create
        data:
          message: "The Birdie air purifier filter should be replaced."
```

## When the local key changes

The local key changes whenever the purifier is set up again in the Birdie app
(for example after a reset or a new Wi-Fi network). Home Assistant then loses
the connection, the entities become unavailable, and after a few rejected
attempts it shows **Re-authentication required** for the purifier under
**Settings > Devices & services**. Open it and enter the new key from the
Birdie app.

You can also change the key at any time: open the purifier's integration
entry, select the three-dot menu and **Reconfigure**. Reconfigure also
changes the IP address and the protocol version; leave the key empty to keep
the current one.

## When the IP address changes

Home Assistant follows the purifier automatically when it receives the
purifier's broadcasts. Without broadcasts (Docker bridge network, separate
VLAN), use **Reconfigure** to enter the new address, or better, give the
purifier a fixed address in your router.

## Remove the purifier

Open **Settings > Devices & services > Birdie**, select the purifier's entry
and **Delete**. This only removes it from Home Assistant; the purifier and the
Birdie app are not affected.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| The purifier is not listed during setup | Home Assistant must receive UDP broadcasts from the purifier's network (Home Assistant OS, or Docker with host networking; same subnet/VLAN). Choose *Enter manually* and enter the IP address instead. A warning `Cannot listen for Tuya broadcasts on UDP 6666/6667` in the log means another program holds these ports without sharing them. |
| "The air purifier was not found on the local network" | Same as above: enter the IP address (shown in your router's client list). |
| "Cannot connect to the air purifier" | The purifier is on and on Wi-Fi; the IP address is correct; TCP port 6668 is reachable from Home Assistant (firewall, VLAN rules, guest Wi-Fi isolation). Close other local Tuya integrations for this purifier (Tuya Local, LocalTuya, a Homey Tuya Local app): the purifier accepts only a few local connections. |
| "The air purifier did not accept the local key" | Copy the current key from the Birdie app again; it changes when the purifier is set up again. It has exactly 16 characters and is case-sensitive. If the key is certainly right, choose the protocol version under **Advanced**. |
| Entities are unavailable | The purifier is off at the mains, offline, or has a new IP address (see above). Home Assistant reconnects on its own, retrying every 5 to 60 seconds. If a re-authentication notice appears, enter the new key. |
| One entity stays unavailable | The purifier has not reported that value (see [Entities](#entities)). Send diagnostics. |
| Setting a speed or preset fails with "has not reported its operating mode" | The purifier did not report its mode; on/off still works. Send diagnostics. |
| The Birdie app or another controller loses the purifier while Home Assistant is connected | Please report it; this has not been tested on a real purifier yet. |

### Diagnostics and debug logs

* **Diagnostics:** on the purifier's device page, select **Download
  diagnostics**. The file contains the raw data points the purifier reports
  and the connection state; the local key and IP address are removed.
* **Debug logs:** add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    default: warning
    logs:
      custom_components.birdie: debug
      tinytuya: debug
  ```

  The protocol library logs the local key in some debug messages; the
  integration replaces it with `**REDACTED**`. Still check logs before you
  share them, and turn debug logging off afterwards.
* **Probe tool:** `tools/purifier_probe.py` checks the purifier without Home
  Assistant (discovery, read-only status, a control test). See
  [remote-testing.md](remote-testing.md).
