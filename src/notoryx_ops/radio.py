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
        self.shared = False                      # an app is using the radio through the station
        self._tap = None                         # where a shared app gets the radio's frames
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

                async def on_telemetry(event):
                    p = event.payload or {}
                    lpp = p.get("lpp")
                    pre = p.get("pubkey_pre") or p.get("tag", "")
                    if lpp and pre:
                        self.ingest.handle_telemetry(str(pre), lpp, time.time())

                self._install_tap(mc)
                mc.subscribe(EventType.RX_LOG_DATA, on_rx)
                mc.subscribe(EventType.DISCONNECTED, on_disconnect)
                mc.subscribe(EventType.TELEMETRY_RESPONSE, on_telemetry)
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
        if not self.connected or self.shared:
            return {**base, "shared": self.shared, **{k: v for k, v in self._health.items() if k != "connected"}}
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
    # ---- sharing with an app ----------------------------------------------
    def _install_tap(self, mc) -> None:
        """Pass every frame the radio sends to a shared app as well as to the station."""
        link = self
        inner = mc.connection_manager.connection.reader

        class Tap:
            def __getattr__(self, name):
                return getattr(inner, name)

            async def handle_rx(self, data):
                if link._tap is not None:
                    try:
                        link._tap(bytes(data))
                    except Exception:  # noqa: BLE001 - a broken app connection must not stop the station
                        log.exception("radio share tap failed")
                return await inner.handle_rx(data)

        mc.connection_manager.set_reader(Tap())

    async def begin_share(self, tap) -> None:
        """An app takes over: the station stops fetching messages and asking the radio anything."""
        self.shared, self._tap = True, tap
        if self.mc:
            try:
                await self.mc.stop_auto_message_fetching()
            except Exception:  # noqa: BLE001
                pass

    async def end_share(self) -> None:
        self.shared, self._tap = False, None
        if self.mc and self.connected:
            try:
                await self.mc.start_auto_message_fetching()
            except Exception:  # noqa: BLE001
                pass

    async def share_send(self, frame: bytes) -> None:
        if not self.mc:
            raise ConnectionError("the station radio isn't connected")
        await self.mc.connection_manager.send(frame)

    def _require(self):
        if not self.connected:
            raise RadioError("The station radio isn't connected.")
        if self.shared:
            raise RadioError("An app is using the station radio through radio sharing right now. Try again when it disconnects.")
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

    # ---- reach: trace, path discovery, who can hear me ---------------------
    async def contacts(self) -> list[dict[str, Any]]:
        """The radio's own contact list: the only nodes it can find a route to."""
        mc = self._require()
        await mc.ensure_contacts(follow=True)
        kinds = {1: "companion", 2: "repeater", 3: "room", 4: "sensor"}
        out = []
        for c in (mc.contacts or {}).values():
            key = str(c.get("public_key") or "").lower()
            if not key:
                continue
            lat, lon = c.get("adv_lat"), c.get("adv_lon")
            has_pos = isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat or lon)
            out.append({"public_key": key, "name": c.get("adv_name") or None,
                        "node_type": kinds.get(c.get("type")), "lat": lat if has_pos else None,
                        "lon": lon if has_pos else None})
        return sorted(out, key=lambda c: ((c["name"] or "~").lower(), c["public_key"]))

    async def trace(self, path: list[str], timeout: float = 20) -> dict[str, Any]:
        """Send a trace along these repeater hashes (all the same length) and wait for it to come back.

        Every repeater on the way adds the SNR it heard the trace at. Returns
        {"hops": [{"hash", "snr"}...], "final_snr"}.
        """
        import random

        from meshcore import EventType

        mc = self._require()
        if not path:
            raise RadioError("Pick at least one repeater to trace through.")
        size = len(path[0])
        if size not in (2, 4, 8) or any(len(h) != size for h in path):
            raise RadioError("Every hop needs a hash of the same length (1, 2 or 4 bytes).")
        tag = random.randint(1, 0xFFFFFFFF)
        flags = {2: 0, 4: 1, 8: 2}[size]
        waiter = asyncio.create_task(mc.dispatcher.wait_for_event(EventType.TRACE_DATA, {"tag": tag}, timeout=timeout))
        await asyncio.sleep(0)
        sent = await mc.commands.send_trace(auth_code=0, tag=tag, flags=flags, path=bytes.fromhex("".join(path)))
        if sent is None or sent.type == EventType.ERROR:
            waiter.cancel()
            raise RadioError("The radio refused the trace.")
        ev = await waiter
        if ev is None:
            raise RadioError("The trace didn't come back. A repeater on the way may be out of range.")
        nodes = list((ev.payload or {}).get("path") or [])
        hops = [{"hash": n.get("hash"), "snr": n.get("snr")} for n in nodes if n.get("hash")]
        final = next((n.get("snr") for n in reversed(nodes) if "hash" not in n), None)
        return {"hops": hops, "final_snr": final}

    async def path_discovery(self, public_key: str, timeout: float = 30) -> dict[str, Any]:
        """Ask the mesh for a fresh route to a node and back. Returns both paths as hash lists."""
        mc = self._require()
        c = await self.contact(public_key)
        ev = await mc.commands.send_path_discovery_sync(c, min_timeout=min(timeout, 12), timeout=0)
        if ev is None:
            raise RadioError("No route came back. The node may be asleep or out of reach.")
        p = ev.payload or {}

        def split(hexpath: str, size: int) -> list[str]:
            n = max(1, int(size or 1)) * 2
            return [hexpath[i:i + n] for i in range(0, len(hexpath or ""), n)]

        return {"out_path": split(p.get("out_path", ""), p.get("out_path_hash_len", 1)),
                "in_path": split(p.get("in_path", ""), p.get("in_path_hash_len", 1))}

    async def who_hears_me(self, wait: float = 10) -> list[dict[str, Any]]:
        """Ask nearby repeaters to answer (zero hop) and collect who did, with signal both ways."""
        from meshcore import EventType

        mc = self._require()
        found: dict[str, dict[str, Any]] = {}
        tag_hex: list[str] = []

        async def on_resp(event):
            p = event.payload or {}
            if tag_hex and p.get("tag") != tag_hex[0]:
                return
            key = p.get("pubkey") or ""
            if key:
                found[key] = {"key": key, "node_type": p.get("node_type"), "snr_them": p.get("SNR_in"),
                              "snr_us": p.get("SNR"), "rssi_us": p.get("RSSI")}

        sub = mc.subscribe(EventType.DISCOVER_RESPONSE, on_resp)
        try:
            ev = await mc.commands.send_node_discover_req(1 << 2, prefix_only=False)
            if ev is None or ev.type == EventType.ERROR:
                raise RadioError("The radio refused the request. It may need newer MeshCore firmware (1.10 or later).")
            tag_hex.append(int(ev.payload["tag"]).to_bytes(4, "little").hex())
            await asyncio.sleep(wait)
        finally:
            try:
                mc.unsubscribe(sub)
            except Exception:  # noqa: BLE001
                pass
        return sorted(found.values(), key=lambda r: -(r.get("snr_them") or -99))

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
