# NOTORYX Ops

Network dashboard and infrastructure station for MeshCore.

NOTORYX Ops runs on a Raspberry Pi, a Mac or in Docker, next to a MeshCore companion radio. It has two sides:

- **Analyzer:** decodes every packet heard by the station radio and by observers on MQTT brokers. It shows a live route map with a 24-hour replay, a packet feed with a decoder, channel messages, observer health, coverage hexes and analytics, including a one-byte prefix collision grid.
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
| `src/notoryx_ops/server.py` | Web API and live updates |
| `web/` | Dashboard (React, MapLibre, uPlot) |
| `packaging/` | Docker and Raspberry Pi installer |
| `macos/` | Mac app launcher |

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

---
🛠️ AI-assisted: built with Anthropic's Claude, directed and reviewed by [BaconDisk22](https://github.com/BaconDisk22). Checked by automated tests and CI; review before relying on it.
