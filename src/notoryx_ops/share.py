"""Share the station radio with one app over the local network.

The station keeps its own link to the MeshCore companion radio and opens a TCP port that
speaks the MeshCore companion protocol (the same one Wi-Fi companion radios use). A phone
or desktop app connects to that port as if it were a Wi-Fi radio:

  app -> station   0x3C ('<') + length (2 bytes, little endian) + frame
  station -> app   0x3E ('>') + length (2 bytes, little endian) + frame

Every frame the radio sends is passed to the app, and every frame the app sends goes to
the radio unchanged. While an app is connected the station only listens: it stops fetching
messages (so the app gets them), and fleet checks, health reads and sending are paused so
the two never answer each other's questions. Only one app at a time, and only from
private or local addresses, because the companion protocol has no password.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from typing import Any

log = logging.getLogger(__name__)

MAX_FRAME = 300


def allowed_peer(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def local_addresses() -> list[str]:
    """This computer's addresses on the local network, to type into the app."""
    found = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # picks the outgoing interface; nothing is sent
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for a in socket.gethostbyname_ex(socket.gethostname())[2]:
            found.add(a)
    except OSError:
        pass
    return sorted(a for a in found if allowed_peer(a) and not a.startswith("127."))


class RadioShare:
    def __init__(self, cfg: Any, station: Any):
        self.cfg = cfg
        self.station = station
        self.server: asyncio.base_events.Server | None = None
        self.client: str | None = None
        self.client_since: float | None = None
        self.frames_in = self.frames_out = 0
        self.error: str | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def radio(self):
        return self.station.radio

    def info(self) -> dict[str, Any]:
        return {"enabled": self.cfg.share_enabled, "port": self.cfg.share_port, "listening": self.server is not None,
                "client": self.client, "client_since": self.client_since, "frames_to_app": self.frames_out,
                "frames_from_app": self.frames_in, "error": self.error,
                "addresses": local_addresses() if self.server else []}

    async def start(self) -> None:
        if not self.cfg.share_enabled or self.server is not None:
            return
        try:
            self.server = await asyncio.start_server(self._handle, host=self.cfg.share_host, port=self.cfg.share_port)
            self.error = None
            log.info("sharing the radio on port %s", self.cfg.share_port)
        except OSError as exc:
            why = exc.strerror or str(exc)
            if exc.errno in (48, 98):  # address already in use (macOS, Linux)
                why = "another program is using it"
                if self.cfg.share_port == 5000:
                    why += " (on a Mac that is usually AirPlay Receiver)"
                why += ". Pick another port, such as 5050"
            self.error = f"Couldn't open port {self.cfg.share_port}: {why}."
            log.warning("radio share: %s", self.error)

    async def stop(self) -> None:
        if self._writer:
            self._writer.close()
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), 3)
            except asyncio.TimeoutError:
                pass
            self.server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?", 0))[0]
        if not allowed_peer(peer):
            log.warning("radio share: refused %s (not on the local network)", peer)
            writer.close()
            return
        if self._writer is not None:
            log.info("radio share: refused %s, %s is already connected", peer, self.client)
            writer.close()
            return
        if not self.radio.connected or not hasattr(self.radio, "begin_share"):
            log.info("radio share: refused %s, the station radio isn't connected", peer)
            writer.close()
            return
        self._writer, self.client, self.client_since = writer, peer, time.time()
        self.frames_in = self.frames_out = 0
        self.station.hub.publish({"type": "share", "client": peer})
        log.info("radio share: %s connected", peer)

        def to_app(frame: bytes) -> None:
            if writer.is_closing():
                return
            writer.write(b">" + len(frame).to_bytes(2, "little") + bytes(frame))
            self.frames_out += 1

        try:
            await self.radio.begin_share(to_app)
            buf = b""
            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"<")
                    if start < 0:
                        buf = b""
                        break
                    buf = buf[start:]
                    if len(buf) < 3:
                        break
                    size = int.from_bytes(buf[1:3], "little")
                    if size == 0 or size > MAX_FRAME:
                        buf = buf[1:]  # not a real frame start, look for the next one
                        continue
                    if len(buf) < 3 + size:
                        break
                    frame, buf = buf[3:3 + size], buf[3 + size:]
                    if not self.radio.connected:
                        raise ConnectionError("the station radio went away")
                    await self.radio.share_send(frame)
                    self.frames_in += 1
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
            log.info("radio share: %s left (%s)", peer, exc)
        finally:
            await self.radio.end_share()
            self._writer, self.client, self.client_since = None, None, None
            writer.close()
            self.station.hub.publish({"type": "share", "client": None})
            log.info("radio share: %s disconnected, the station uses the radio again", peer)
