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
                self.state, self.error, self.connected_at = "connected", None, time.time()
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
            if self.mc:
                try:
                    await self.mc.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                self.mc = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

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
