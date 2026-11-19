"""Wires the station parts together and runs them."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from . import __version__, settings
from .alerts import Alerts
from .channels import parse_channel
from .config import Config
from .demo import DemoNetwork, DemoRadio
from .fleet import Fleet
from .ingest import Hub, Ingest
from .mqtt_ingest import MqttFeed
from .planning import Planner
from .radio import RadioLink
from .store import Store

log = logging.getLogger(__name__)


class Station:
    def __init__(self, cfg: Config, store: Store | None = None, terrain_fetch=None):
        self.cfg = cfg
        self.started_at = time.time()
        self.store = store or Store(cfg.db_path)
        self.hub = Hub()
        self.channels = []
        for spec in cfg.channel_keys:
            try:
                self.channels.append(parse_channel(spec))
            except ValueError as exc:
                log.warning("skipping channel: %s", exc)
        self.ingest = Ingest(self.store, self.channels, self.hub)
        self.demo = DemoNetwork(self.ingest) if cfg.demo else None
        self.radio = DemoRadio(self.demo) if self.demo else RadioLink(cfg, self.ingest)
        self.mqtt = [MqttFeed(s, self.ingest) for s in cfg.mqtt]
        self.alerts = Alerts(cfg, self.store, self.hub)
        self.fleet = Fleet(cfg, self.store, self.radio, self.alerts, self.hub)
        self.planner = Planner(cfg, self.store, fetch=terrain_fetch)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self.demo:
            if not self.store.one("SELECT 1 x FROM packets LIMIT 1"):
                self.demo.backfill()
            for n in self.demo.fleet:
                if not self.store.one("SELECT 1 x FROM repeaters WHERE public_key = ?", (n["key"].hex(),)):
                    self.fleet.add(n["key"].hex(), n["name"], "demo")
            self.demo.start()
        self.radio.start()
        for m in self.mqtt:
            m.start(loop)
        self.fleet.start()

    def _radio_key(self) -> tuple:
        c = self.cfg
        return (c.radio_transport, c.serial_port, c.baudrate, c.ble_address, c.tcp_host, c.tcp_port, c.observer_name)

    async def apply_settings(self, data: dict) -> list[str]:
        """Validate and save settings from the dashboard, then apply them without a restart where possible."""
        radio_before = self._radio_key()
        mqtt_before = [asdict(m) for m in self.cfg.mqtt]
        restart = settings.apply_changes(self.cfg, data)
        settings.save(self.cfg)

        self.channels = []
        for spec in self.cfg.channel_keys:
            try:
                self.channels.append(parse_channel(spec))
            except ValueError as exc:
                log.warning("skipping channel: %s", exc)
        self.ingest.channels = self.channels

        if not self.demo and self._radio_key() != radio_before:
            await self.radio.stop()
            self.radio = RadioLink(self.cfg, self.ingest)
            self.fleet.radio = self.radio
            self.radio.start()
        if [asdict(m) for m in self.cfg.mqtt] != mqtt_before:
            for m in self.mqtt:
                m.stop()
            self.mqtt = [MqttFeed(s, self.ingest) for s in self.cfg.mqtt]
            loop = asyncio.get_running_loop()
            for m in self.mqtt:
                m.start(loop)
        self.alerts.evaluate()
        return restart

    async def stop(self) -> None:
        await self.radio.stop()
        for m in self.mqtt:
            m.stop()

    def info(self) -> dict:
        return {
            "name": self.cfg.name,
            "version": __version__,
            "demo": self.cfg.demo,
            "started_at": self.started_at,
            "radio": self.radio.info(),
            "mqtt": [m.info() for m in self.mqtt],
            "fleet": self.fleet.info(),
            "channels": [{"name": c.name, "hash": c.hash} for c in self.channels],
            "decoded": self.ingest.counts,
            "retention_days": self.cfg.packet_retention_days,
            "config_path": str(self.cfg.path),
            "changes_need_token": bool(self.cfg.admin_token),
            "stale_after_hours": self.cfg.stale_after_hours,
        }
