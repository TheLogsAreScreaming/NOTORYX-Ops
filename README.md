# NOTORYX Ops

Network dashboard and infrastructure station for MeshCore.

NOTORYX Ops runs on a Raspberry Pi, a Mac or in Docker, next to a MeshCore companion radio. It has two sides:

- **Analyzer:** decodes every packet heard by the station radio and by observers on MQTT brokers. It shows a live route map with a 24-hour replay and range rings, a node list that marks stale nodes and lets you ignore noisy ones, a packet feed with a decoder, channel messages, observer health, coverage hexes and analytics, including a one-byte prefix collision grid.
- **Terrain planning:** coverage maps worked out from real elevation (Copernicus DEM), link profiles with line of sight, Fresnel zone and a link budget, high-ground finder, and "what if" relay planning that shows the area a new relay would add. Predictions can be calibrated against the links the station has measured.
- **Field work:** hazard reports (flooded road, fallen tree, power line down...) sent and received as plain channel messages any MeshCore app can read, teams on their own private channels with position tracks, and an offline survey mode for phones that saves each spot and uploads it when the station can be reached. CSV and GPX survey imports.
- **Accounts and roles:** optional sign-in with viewer, operator and admin roles. The admin token keeps working for scripts.
- **Integrations:** GeoJSON and GPX exports, Prometheus metrics at `/metrics`, a read-only public map at `/public` with rounded positions, Home Assistant sensors over MQTT discovery, and a PostgreSQL archive (`pip install "notoryx-ops[postgres]"`).
- **Fleet:** logs in to the repeaters you own and keeps their health over time: battery, noise floor, airtime, uptime, neighbours and firmware. It raises alerts when a repeater goes quiet, its battery is low or falling, its firmware is behind, or two repeaters share a prefix, and sends them to Discord or a webhook. Bulk settings change one setting on several repeaters after showing the current values.

Status: first test version, **not yet tested with a real radio**. See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) and [docs/PLAN.md](docs/PLAN.md).

## Try it without a radio

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
(cd web && npm ci && npm run build)
.venv/bin/notoryx-ops run --demo --open
```

Demo mode fills the dashboard with made-up traffic and a pretend fleet. Nothing is sent over the air.

## Layout

| Path | What it is |
|---|---|
| `src/notoryx_ops/decoder.py` | Packet decoder (written from the public packet format) |
| `src/notoryx_ops/channels.py` | Channel keys and group message decryption |
| `src/notoryx_ops/radio.py` | Companion radio link (USB, Bluetooth, TCP) |
| `src/notoryx_ops/mqtt_ingest.py` | Observer packets from MQTT brokers |
| `src/notoryx_ops/fleet.py` | Repeater polling, history, bulk settings |
| `src/notoryx_ops/alerts.py` | Alert rules, Discord and webhook delivery |
| `src/notoryx_ops/analytics.py` | Overview, heatmap, prefixes, links, coverage |
| `src/notoryx_ops/terrain.py` | Elevation tiles, radio path model, coverage, high points, relay planning |
| `src/notoryx_ops/planning.py` | Terrain API and calibration from measured links |
| `src/notoryx_ops/field.py` | Field reports, teams, position tracks, surveys |
| `src/notoryx_ops/addons.py` | Loader for optional add-on packages |
| `addons/meshtastic/` | Meshtastic add-on (GPL-3.0, its own package) |
| `src/notoryx_ops/auth.py` | Accounts, roles and sign-in sessions |
| `src/notoryx_ops/integrations.py` | Exports, metrics, public map, Home Assistant, PostgreSQL |
| `src/notoryx_ops/server.py` | Web API and live updates |
| `web/` | Dashboard (React, MapLibre, uPlot) |
| `packaging/` | Docker and Raspberry Pi installer |
| `macos/` | Native Mac app (Swift window around the dashboard) |

## Development

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
cd web && npm run dev     # dashboard on :5173, proxied to a station on :8484
```

## Downloads

Builds are published on the NOTORYX-Ops-Releases repo, not here. The **Release build** workflow makes the wheel, Raspberry Pi bundle, Docker bundle, Mac app and `SHA256SUMS`.

## Clean-room rule

All code here is written from scratch. Ideas may come from other mesh tools, but no code is copied from them, and they are not named in this repo. The MeshCore Python library (MIT) is used as a dependency for the radio link.

## License

MIT. See [LICENSE](LICENSE).

**Add-ons** are separate packages with their own licence. The optional [Meshtastic add-on](addons/meshtastic/) (remote admin, templates and compliance checks, and a Meshtastic⇄MeshCore bridge) is GPL-3.0 because it uses the Meshtastic Python library; NOTORYX Ops works without it, and its own code stays MIT.

Elevation data: Copernicus DEM, © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA. Tiles are downloaded on first use of an area; nothing is bundled.

---
🛠️ AI-assisted: built with Anthropic's Claude, directed and reviewed by [BaconDisk22](https://github.com/BaconDisk22). Checked by automated tests and CI; review before relying on it.
