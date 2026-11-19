# Meshtastic add-on for NOTORYX Ops

An optional add-on that gives a NOTORYX Ops station its own Meshtastic radio:

- **Nodes:** the Meshtastic nodes your radio has heard.
- **Remote admin** over the air with PKI admin (firmware 2.5 and newer): read device, LoRa, position, power and other settings, change them after seeing exactly what will change, rename a node, mark one relay as a favourite on another, and reboot.
- **Templates and compliance checks:** save the settings your relays should have, check a set of nodes against them, and apply them one node at a time with a pause between nodes to save airtime.
- **Bridge (off by default):** pass channel messages between a Meshtastic channel and a MeshCore channel. Only channel messages cross, never direct messages. Bridged text is tagged `[MT]` or `[MC]`, tagged text is never forwarded again, and a per-minute limit protects airtime.

## Install

Install it in the same Python environment as NOTORYX Ops, then restart the station:

```
pip install notoryx-ops-meshtastic
```

It shows up as **Meshtastic** in the dashboard's sidebar. To turn it off without uninstalling, add `disabled = ["meshtastic"]` under `[addons]` in the station's config file.

## Remote admin setup

1. Connect the station's Meshtastic radio on the add-on's page (USB, Wi-Fi/TCP or Bluetooth).
2. Copy the station radio's public key from the page.
3. On each node you want to manage, add that key under **Security → Admin keys** (do this on the node itself, once).

Some settings are never changed from afar because a mistake could lock you out: admin keys, the node's own keys, managed mode, the admin channel, LoRa region and transmit on/off. Private keys, Wi-Fi passwords and MQTT passwords are never read back to the browser.

## Licence

GPL-3.0-only, because it uses the Meshtastic Python library (GPL-3.0). NOTORYX Ops itself is MIT and works without this add-on. A build that bundles this add-on is distributed under the GPL.

Meshtastic® is a registered trademark of Meshtastic LLC. This add-on is not affiliated with or endorsed by Meshtastic LLC.
