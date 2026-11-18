# NOTORYX Ops plan

Status: 2026-11-18. Milestones 1-5 built in the first version (0.1.0), not yet tested with a real radio.

## Parts

1. **Ops station** (this repo): a headless service plus a local web dashboard. It runs on a Pi or a Mac with a MeshCore companion radio over USB, BLE or TCP, and can also read observer packets from MQTT brokers.
2. **Fleet tab in NOTORYX Core** (Core repo): the phone view of your repeaters. It works alone over the companion radio, and syncs history from a station when one is on the network.

## Dashboard pages

| Page | What it shows |
|---|---|
| Live map | Packet routes for all heard traffic, your repeaters ringed, 24 h replay slider |
| Packets | Filterable live feed: time, type, hops, SNR at each observer, decoded detail |
| Channels | Group messages for channels you have keys for |
| My fleet | Admin status of repeaters you own: battery, noise, airtime, uptime, firmware, history graphs |
| Observers | Each observer's health and what it hears |
| Coverage | Hex coverage from field captures, gaps, suggested repeater spots |
| Analytics | Activity heatmap, top nodes, prefix collision grid |
| Alerts | Repeater quiet, battery low or falling, firmware behind, prefix clash; to Discord or webhooks |
| Station | Radio link, brokers, polling schedule, retention, backups |

## Rules

- **Airtime first.** Fleet polling uses long intervals and staggered requests. Passive listening and observers come before active polls.
- **Admin passwords stay local.** Stored on the station or phone only, never sent to a cloud service.
- **Bulk settings show a diff and ask before sending.**
- **Clean-room.** Write our own code. Borrow ideas, never code, from other mesh dashboards, and never name them in this repo.
- **Releases** go to NOTORYX-Ops-Releases only.

## Decisions

- Station: Python (MeshCore Python library for the radio, FastAPI, SQLite, MQTT client).
- Dashboard: React + TypeScript + Vite, MapLibre with free OpenStreetMap tiles, uPlot charts.
- License: MIT, so code can move into the MIT NOTORYX apps.
- Order: the station first; the Core Fleet tab comes next and syncs from it.

## Still to do

- Test with a real companion radio (USB, Bluetooth, TCP) and real repeaters you own.
- Test against a real MQTT observer feed.
- Fleet tab in NOTORYX Core that syncs from a station.
- Signed Mac app.

## Milestones

1. Station skeleton: radio link, SQLite store, packet decode, web server with the Live map and Packets pages.
2. Fleet: repeater login, scheduled status polls, history, alerts.
3. Observers over MQTT, Analytics, prefix collision grid.
4. Coverage and planning.
5. Mac app and Pi installer, first release on NOTORYX-Ops-Releases.
