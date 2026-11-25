"""Link to a MeshCore companion radio over USB, Bluetooth or TCP.

The radio is both an observer (its raw receive log feeds the analyzer) and the way the
station talks to repeaters you own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .config import Config
from .ingest import Ingest

log = logging.getLogger(__name__)


class RadioError(RuntimeError):
    pass


class RadioLink:
    def __init__(self, cfg: Config, ingest: Ingest):
        self.cfg = cfg
        self.ingest = ingest
        self.mc = None
        self.state = "off" if cfg.radio_transport == "none" else "connecting"
        self.error: str | None = None
        self.self_info: dict[str, Any] = {}
        self.connected_at: float | None = None
        self.disconnected_since: float | None = time.time() if self.enabled else None
        self.reconnects = 0
        self._health: dict[str, Any] = {}
        self._health_at = 0.0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self.cfg.radio_transport != "none"

    @property
    def connected(self) -> bool:
        return self.mc is not None and self.state == "connected"

    def info(self) -> dict[str, Any]:
        return {
            "transport": self.cfg.radio_transport,
            "state": self.state,
            "error": self.error,
            "name": self.self_info.get("name"),
            "public_key": self.self_info.get("public_key"),
            "lat": self.self_info.get("adv_lat"),
            "lon": self.self_info.get("adv_lon"),
            "connected_at": self.connected_at,
        }

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name="radio")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
        if self.mc:
            try:
                await self.mc.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def _connect(self):
        from meshcore import MeshCore

        t = self.cfg.radio_transport
        if t == "serial":
            return await MeshCore.create_serial(self.cfg.serial_port, self.cfg.baudrate)
        if t == "ble":
            return await MeshCore.create_ble(self.cfg.ble_address or None)
        if t == "tcp":
            return await MeshCore.create_tcp(self.cfg.tcp_host, self.cfg.tcp_port)
        raise RadioError(f"unknown radio transport {t!r}")

    async def _run(self) -> None:
        from meshcore import EventType

        delay = 5
        while not self._stop.is_set():
            self.state = "connecting"
            try:
                mc = await self._connect()
                if mc is None:
                    raise RadioError("no answer from the radio")
                self.mc = mc
                self.self_info = dict(mc.self_info or {})
                async def on_rx(event, observer=self.cfg.observer_name):
                    p = event.payload or {}
                    if p.get("payload"):
                        self.ingest.handle(p["payload"], observer, "radio", p.get("snr"), p.get("rssi"),
                                           observer_key=self.self_info.get("public_key"))

                async def on_disconnect(_event):
                    self.state = "disconnected"

                mc.subscribe(EventType.RX_LOG_DATA, on_rx)
                mc.subscribe(EventType.DISCONNECTED, on_disconnect)
                await mc.ensure_contacts()
                await mc.start_auto_message_fetching()
                if self.connected_at is not None:
                    self.reconnects += 1
                self.state, self.error, self.connected_at = "connected", None, time.time()
                self.disconnected_since = None
                self._health_at = 0.0
                delay = 5
                log.info("radio connected: %s", self.self_info.get("name"))
                while not self._stop.is_set() and self.state == "connected" and mc.is_connected:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc) or exc.__class__.__name__
                log.warning("radio link failed: %s", self.error)
            self.state = "disconnected"
            if self.disconnected_since is None:
                self.disconnected_since = time.time()
            if self.mc:
                try:
                    await self.mc.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.mc = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    async def health(self, max_age: float = 60) -> dict[str, Any]:
        """Battery, firmware, uptime and radio stats of the station radio, asked at most once a minute."""
        base = {"connected": self.connected, "reconnects": self.reconnects, "connected_at": self.connected_at,
                "disconnected_since": self.disconnected_since, "error": self.error}
        if not self.connected:
            return {**base, **{k: v for k, v in self._health.items() if k in ("model", "firmware")}}
        if time.time() - self._health_at < max_age:
            return {**self._health, **base}
        mc, out = self.mc, {}

        async def ask(fn):
            try:
                ev = await asyncio.wait_for(fn(), timeout=6)
                return dict(ev.payload or {}) if ev is not None and isinstance(getattr(ev, "payload", None), dict) else {}
            except Exception:  # noqa: BLE001 - older firmware may not know a command
                return {}

        dev = await ask(mc.commands.send_device_query)
        out["model"], out["firmware"] = dev.get("model") or None, dev.get("ver") or None
        bat = await ask(mc.commands.get_bat)
        if bat.get("level"):
            out["battery_mv"] = bat["level"]
        core = await ask(mc.commands.get_stats_core)
        if core:
            out["battery_mv"] = core.get("battery_mv") or out.get("battery_mv")
            out["uptime_s"] = core.get("uptime_secs")
            out["queue"] = core.get("queue_len")
        rs = await ask(mc.commands.get_stats_radio)
        if rs:
            out.update(noise_floor=rs.get("noise_floor"), last_rssi=rs.get("last_rssi"), last_snr=rs.get("last_snr"),
                       tx_air_s=rs.get("tx_air_secs"), rx_air_s=rs.get("rx_air_secs"))
        self._health, self._health_at = out, time.time()
        return {**out, **base}

    # ---- repeater operations ----------------------------------------------
    def _require(self):
        if not self.connected:
            raise RadioError("The station radio isn't connected.")
        return self.mc

    async def contact(self, public_key: str) -> dict[str, Any]:
        mc = self._require()
        c = mc.get_contact_by_key_prefix(public_key[:12])
        if c is None:
            await mc.ensure_contacts(follow=True)
            c = mc.get_contact_by_key_prefix(public_key[:12])
        if c is None:
            raise RadioError("This repeater isn't in the radio's contacts yet. Wait for its advert, then try again.")
        return c

    async def login(self, public_key: str, password: str) -> None:
        mc = self._require()
        c = await self.contact(public_key)
        ev = await mc.commands.send_login_sync(c, password, min_timeout=8)
        if ev is None:
            raise RadioError("Login got no answer. Check the password and that the repeater is in range.")

    async def status(self, public_key: str) -> dict[str, Any]:
        mc = self._require()
        c = await self.contact(public_key)
        st = await mc.commands.req_status_sync(c, min_timeout=8)
        if not st:
            raise RadioError("No status reply.")
        return st

    async def neighbours(self, public_key: str) -> list[dict[str, Any]]:
        mc = self._require()
        c = await self.contact(public_key)
        res = await mc.commands.req_neighbours_sync(c, min_timeout=8)
        return list((res or {}).get("neighbours") or [])

    async def command(self, public_key: str, cmd: str, timeout: float = 15) -> str:
        """Send a CLI command to a repeater you are logged in to and wait for its reply."""
        from meshcore import EventType

        mc = self._require()
        c = await self.contact(public_key)
        prefix = c.get("public_key", public_key)[:12]
        waiter = asyncio.create_task(
            mc.dispatcher.wait_for_event(EventType.CONTACT_MSG_RECV, {"pubkey_prefix": prefix}, timeout=timeout)
        )
        await asyncio.sleep(0)
        sent = await mc.commands.send_cmd(c, cmd)
        if sent is None or sent.type == EventType.ERROR:
            waiter.cancel()
            raise RadioError("The radio refused the command.")
        ev = await waiter
        if ev is None:
            raise RadioError(f"No reply to '{cmd}'.")
        return (ev.payload or {}).get("text", "").strip()

    # ---- channels -----------------------------------------------------------
    async def channel_slots(self) -> list[dict[str, Any]]:
        """The radio's channel slots: index, name and key (hex). Empty slots have an empty name."""
        from meshcore import EventType

        mc = self._require()
        out = []
        for idx in range(int(self.self_info.get("max_channels") or 8)):
            ev = await mc.commands.get_channel(idx)
            if ev is None or ev.type == EventType.ERROR:
                break
            p = ev.payload or {}
            sec = p.get("channel_secret") or b""
            out.append({"idx": idx, "name": p.get("channel_name") or "",
                        "key": sec.hex() if isinstance(sec, bytes | bytearray) else str(sec)})
        return out

    async def find_slot(self, key: bytes) -> int | None:
        for s in await self.channel_slots():
            if s["key"] == key.hex():
                return s["idx"]
        return None

    async def add_channel(self, name: str, key: bytes) -> int:
        """Put a channel in the first empty slot on the radio. Returns the slot."""
        from meshcore import EventType

        slots = await self.channel_slots()
        for s in slots:
            if s["key"] == key.hex():
                return s["idx"]
        free = [s["idx"] for s in slots if not s["name"] and s["key"].strip("0") == ""]
        if not free:
            raise RadioError("Every channel slot on the station radio is in use. Free one in your MeshCore app first.")
        ev = await self._require().commands.set_channel(free[0], name[:31], key)
        if ev is None or ev.type == EventType.ERROR:
            raise RadioError("The radio refused the new channel.")
        return free[0]

    async def send_channel(self, key: bytes, text: str) -> int:
        """Send a message on the channel with this key. Returns the slot used."""
        from meshcore import EventType

        idx = await self.find_slot(key)
        if idx is None:
            raise RadioError("That channel isn't set up on the station radio. Add it there (or with Add to radio) first.")
        ev = await self._require().commands.send_chan_msg(idx, text)
        if ev is None or ev.type == EventType.ERROR:
            raise RadioError("The radio didn't send the message.")
        return idx
