# NOTORYX Ops

Network dashboard and infrastructure tools for MeshCore.

NOTORYX Ops runs on a Raspberry Pi or a Mac next to a MeshCore radio. It has two sides:

- **Analyzer:** listens to the mesh through one or more observers, decodes packets, and shows them on a live route map with a 24-hour replay, a packet feed, channel views, coverage and analytics.
- **Fleet:** logs in to the repeaters you own and keeps their health over time: battery, noise floor, airtime, uptime and firmware. It sends alerts when a repeater goes quiet or its battery falls, and pushes one setting to several repeaters at once.

The NOTORYX Core app gets a matching Fleet tab that can sync from the station.

## Status

Planning. Nothing is built or released yet. See [docs/PLAN.md](docs/PLAN.md).

## Downloads

Builds are published on the NOTORYX-Ops-Releases repo, not here.

## Clean-room rule

All code here is written from scratch. Ideas may come from other mesh tools, but no code is copied from them, and they are not named in this repo.

---
🛠️ AI-assisted: built with Anthropic's Claude, directed and reviewed by [BaconDisk22](https://github.com/BaconDisk22). Checked by automated tests and CI; review before relying on it.
