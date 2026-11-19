# SPDX-License-Identifier: GPL-3.0-only
"""The station's own Meshtastic radio, over USB, TCP (Wi-Fi) or Bluetooth.

The Meshtastic library runs its own threads. Received text is handed to the asyncio loop
with call_soon_threadsafe so the rest of the station never sees another thread.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)
TRANSPORTS = ("none", "serial", "tcp", "ble")
BROADCAST = 0xFFFFFFFF


def node_id(num: int) -> str:
    return f"!{num:08x}"


class MeshtasticLink:
    def __init__(self, settings: dict[str, Any], on_text: Callable[[dict[str, Any]], None] | None = None,
                 iface_factory: Callable[[dict[str, Any]], Any] | None = None):
        self.settings = settings
        self.on_text = on_text
        self._factory = iface_factory
        self.iface = None
        self.state = "off"
        self.error: str | None = None
        self.connected_at: float | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lost = False

    @property
    def transport(self) -> str:
        t = self.settings.get("transport", "none")
        return t if t in TRANSPORTS else "none"

    @property
    def connected(self) -> bool:
        return self.iface is not None and self.state == "connected"

    # ---- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        if self.transport == "none":
            self.state = "off"
            return
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(), name="meshtastic")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self._close()
        self.state = "off"

    def _close(self) -> None:
        iface, self.iface = self.iface, None
        if iface is not None:
            try:
                from pubsub import pub

                pub.unsubscribe(self._on_receive, "meshtastic.receive.text")
                pub.unsubscribe(self._on_lost, "meshtastic.connection.lost")
            except Exception:  # noqa: BLE001
                pass
            try:
                iface.close()
            except Exception:  # noqa: BLE001
                pass

    def _open(self):
        if self._factory:
            return self._factory(self.settings)
        t = self.transport
        if t == "serial":
            from meshtastic.serial_interface import SerialInterface

            return SerialInterface(self.settings.get("serial_port") or None)
        if t == "tcp":
            from meshtastic.tcp_interface import TCPInterface

            return TCPInterface(self.settings.get("tcp_host") or "meshtastic.local", portNumber=int(self.settings.get("tcp_port") or 4403))
        if t == "ble":
            from meshtastic.ble_interface import BLEInterface

            return BLEInterface(self.settings.get("ble_address") or None)
        raise RuntimeError("No Meshtastic connection type is set.")

    async def _run(self) -> None:
        delay = 5
        while True:
            self.state, self._lost = "connecting", False
            try:
                iface = await asyncio.to_thread(self._open)
                self.iface = iface
                try:
                    from pubsub import pub

                    pub.subscribe(self._on_receive, "meshtastic.receive.text")
                    pub.subscribe(self._on_lost, "meshtastic.connection.lost")
                except ImportError:  # pragma: no cover - test fakes
                    pass
                self.state, self.error, self.connected_at = "connected", None, time.time()
                delay = 5
                log.info("Meshtastic radio connected: %s", self.my_node().get("long_name"))
                while not self._lost:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc) or exc.__class__.__name__
                log.warning("Meshtastic link failed: %s", self.error)
            self._close()
            self.state = "disconnected"
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    # ---- events from the library's threads -------------------------------------
    def _on_lost(self, interface=None, **_kw) -> None:
        if interface is None or interface is self.iface:
            self._lost = True

    def _on_receive(self, packet: dict[str, Any], interface=None, **_kw) -> None:
        if interface is not None and interface is not self.iface:
            return
        if self.on_text and self._loop:
            self._loop.call_soon_threadsafe(self.on_text, packet)

    # ---- what the radio knows ------------------------------------------------------
    def my_num(self) -> int | None:
        info = getattr(self.iface, "myInfo", None)
        return getattr(info, "my_node_num", None) if info is not None else None

    def my_node(self) -> dict[str, Any]:
        num = self.my_num()
        if num is None or self.iface is None:
            return {}
        n = (self.iface.nodesByNum or {}).get(num, {})
        u = n.get("user", {})
        pub_key = None
        try:
            sec = self.iface.localNode.localConfig.security
            if sec.public_key:
                pub_key = base64.b64encode(sec.public_key).decode()
        except Exception:  # noqa: BLE001
            pass
        return {"num": num, "id": node_id(num), "long_name": u.get("longName"), "short_name": u.get("shortName"),
                "hw_model": u.get("hwModel"), "public_key": pub_key,
                "firmware": getattr(getattr(self.iface, "metadata", None), "firmware_version", None)}

    def nodes(self) -> list[dict[str, Any]]:
        if self.iface is None:
            return []
        me = self.my_num()
        out = []
        for num, n in (self.iface.nodesByNum or {}).items():
            if num == me:
                continue
            u = n.get("user", {})
            pos = n.get("position", {})
            out.append({
                "num": num, "id": node_id(num), "long_name": u.get("longName"), "short_name": u.get("shortName"),
                "hw_model": u.get("hwModel"), "role": u.get("role", "CLIENT"), "has_key": bool(u.get("publicKey")),
                "last_heard": n.get("lastHeard"), "snr": n.get("snr"), "hops": n.get("hopsAway"),
                "lat": pos.get("latitude"), "lon": pos.get("longitude"), "favorite": bool(n.get("isFavorite")),
            })
        out.sort(key=lambda x: -(x["last_heard"] or 0))
        return out

    def channels(self) -> list[dict[str, Any]]:
        if self.iface is None:
            return []
        out = []
        for ch in getattr(self.iface.localNode, "channels", None) or []:
            role = getattr(ch, "role", 0)
            if role == 0:  # DISABLED
                continue
            name = ch.settings.name or ("Primary" if role == 1 else f"Channel {ch.index}")
            out.append({"index": ch.index, "name": name, "primary": role == 1})
        return out

    def send_text(self, text: str, channel_index: int) -> None:
        if not self.connected:
            raise RuntimeError("The Meshtastic radio isn't connected.")
        self.iface.sendText(text, channelIndex=int(channel_index))

    def info(self) -> dict[str, Any]:
        return {"transport": self.transport, "state": self.state, "error": self.error,
                "connected_at": self.connected_at, "me": self.my_node() if self.connected else None}
