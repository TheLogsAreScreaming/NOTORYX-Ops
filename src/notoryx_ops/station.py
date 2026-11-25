"""Wires the station parts together and runs them."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

from . import __version__, settings
from .alerts import Alerts
from .auth import Auth
from .channels import parse_channel
from .config import Config
from .demo import DemoNetwork, DemoRadio
from .field import Field
from .fleet import Fleet
from .ingest import Hub, Ingest
from .integrations import HomeAssistant, PostgresArchive
from .mqtt_ingest import MqttFeed
from .netstats import StatsPush
from .planning import Planner
from .radio import RadioLink
from .store import Store

log = logging.getLogger(__name__)


class Station:
    def __init__(self, cfg: Config, store: Store | None = None, terrain_fetch=None, addon_factories=None):
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
        self.field = Field(self.store, self.hub)
        self.ingest.field = self.field
        self.demo = DemoNetwork(self.ingest) if cfg.demo else None
        self.radio = DemoRadio(self.demo) if self.demo else RadioLink(cfg, self.ingest)
        self.mqtt = [MqttFeed(s, self.ingest) for s in cfg.mqtt]
        self.alerts = Alerts(cfg, self.store, self.hub)
        self.fleet = Fleet(cfg, self.store, self.radio, self.alerts, self.hub)
        self.fleet.field = self.field
        self.planner = Planner(cfg, self.store, fetch=terrain_fetch)
        self.auth = Auth(cfg, self.store)
        self.ha = HomeAssistant(cfg, self.store, self)
        self.pg = PostgresArchive(cfg, self.store)
        self.stats_push = StatsPush(cfg, self.store)
        from . import addons

        self.addons = addons.load(self, addon_factories)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self.demo:
            if not self.store.one("SELECT 1 x FROM packets LIMIT 1"):
                self.demo.backfill()
            for n in self.demo.fleet:
                if not self.store.one("SELECT 1 x FROM repeaters WHERE public_key = ?", (n["key"].hex(),)):
                    self.fleet.add(n["key"].hex(), n["name"], "demo")
            if not self.store.one("SELECT 1 x FROM reports LIMIT 1"):
                from .demo import CENTER

                now = time.time()
                self.field.on_message("Public", "Demo Truck", f"⚑ tree {CENTER[0] + 0.031:.5f},{CENTER[1] - 0.042:.5f} Across both lanes", now - 2400)
                self.field.on_message("Public", "Demo Hiker", f"⚑ flood {CENTER[0] - 0.024:.5f},{CENTER[1] + 0.018:.5f} Water over the bridge road", now - 900)
            self.demo.start()
        self.radio.start()
        for m in self.mqtt:
            m.start(loop)
        self.fleet.start()
        self.ha.start()
        self.pg.start()
        self.stats_push.start()
        self._watch = asyncio.create_task(self._watch_radio(), name="radio-watch")
        for a in self.addons:
            try:
                a.start()
            except Exception as exc:  # noqa: BLE001
                log.warning("add-on %s didn't start: %s", a.id, exc)

    async def _watch_radio(self) -> None:
        """Raise or clear the "station radio lost" alert every half minute."""
        while True:
            await asyncio.sleep(30)
            try:
                self.alerts.check_radio(self.radio)
            except Exception:  # noqa: BLE001
                log.exception("radio check failed")

    def _radio_key(self) -> tuple:
        c = self.cfg
        return (c.radio_transport, c.serial_port, c.baudrate, c.ble_address, c.tcp_host, c.tcp_port, c.observer_name)

    async def apply_settings(self, data: dict) -> list[str]:
        """Validate and save settings from the dashboard, then apply them without a restart where possible."""
        radio_before = self._radio_key()
        ha_before = (self.cfg.ha_enabled, self.cfg.ha_host, self.cfg.ha_port, self.cfg.ha_username, self.cfg.ha_password,
                     self.cfg.ha_tls, self.cfg.ha_prefix)
        pg_before = (self.cfg.pg_enabled, self.cfg.pg_dsn)
        push_before = (self.cfg.stats_push_enabled, self.cfg.stats_push_url, self.cfg.stats_push_token)
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
        ha_after = (self.cfg.ha_enabled, self.cfg.ha_host, self.cfg.ha_port, self.cfg.ha_username, self.cfg.ha_password,
                    self.cfg.ha_tls, self.cfg.ha_prefix)
        if ha_after != ha_before:
            self.ha.stop()
            self.ha.start()
        if (self.cfg.pg_enabled, self.cfg.pg_dsn) != pg_before:
            self.pg.stop()
            self.pg.start()
        if (self.cfg.stats_push_enabled, self.cfg.stats_push_url, self.cfg.stats_push_token) != push_before:
            self.stats_push.stop()
            self.stats_push.start()
        self.alerts.evaluate()
        return restart

    async def stop(self) -> None:
        if getattr(self, "_watch", None):
            self._watch.cancel()
        await self.radio.stop()
        for m in self.mqtt:
            m.stop()
        self.ha.stop()
        self.pg.stop()
        self.stats_push.stop()
        for a in self.addons:
            try:
                a.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("add-on %s didn't stop cleanly: %s", a.id, exc)

    def channel(self, name: str):
        """A configured channel by its name (as shown on the Channels page)."""
        for c in self.channels:
            if c.name.lower() == (name or "").lower():
                return c
        raise ValueError(f"The station doesn't have a channel called {name!r}. Add it under Settings → Channels.")

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
            "homeassistant": self.ha.info(),
            "postgres": self.pg.info(),
            "stats_push": self.stats_push.info(),
            "public_map": self.cfg.public_map,
            "addons": [{"id": a.id, "name": a.name, "version": a.version, "license": a.license} for a in self.addons],
        }
