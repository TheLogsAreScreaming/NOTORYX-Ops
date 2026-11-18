"""Observer packets from MQTT brokers.

Observers are radios that log every packet they hear and publish it to a broker. Topics
usually look like meshcore/<region>/<observer key>/packets with a JSON body that carries
the raw packet as hex. Plain hex bodies work too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .config import MqttSource
from .ingest import Ingest

log = logging.getLogger(__name__)

RAW_FIELDS = ("raw", "hex", "payload", "data", "packet")


def _num(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def parse_message(topic: str, body: bytes) -> dict[str, Any] | None:
    """Pull the raw packet, signal and observer out of one MQTT message."""
    parts = topic.split("/")
    observer_key = parts[2] if len(parts) >= 4 and len(parts[2]) >= 8 else None
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return None
    if text[0] != "{":
        try:
            bytes.fromhex(text)
        except ValueError:
            return None
        return {"raw": text, "observer": observer_key or topic, "observer_key": observer_key}
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(msg, dict):
        return None
    if str(msg.get("direction", "rx")).lower() == "tx":
        return None  # the observer's own transmissions are not reception data
    raw = next((msg[f] for f in RAW_FIELDS if isinstance(msg.get(f), str) and msg[f]), None)
    if raw is None:
        return None
    key = msg.get("origin_id") or observer_key
    name = msg.get("origin") or msg.get("observer") or (key[:8] if key else topic)
    return {
        "raw": raw,
        "observer": str(name),
        "observer_key": key,
        "snr": _num(msg.get("SNR", msg.get("snr"))),
        "rssi": _num(msg.get("RSSI", msg.get("rssi"))),
    }


class MqttFeed:
    def __init__(self, source: MqttSource, ingest: Ingest):
        self.source = source
        self.ingest = ingest
        self.state = "connecting"
        self.error: str | None = None
        self.messages = 0
        self.client = None

    def info(self) -> dict[str, Any]:
        return {"name": self.source.name, "host": self.source.host, "port": self.source.port,
                "topics": self.source.topics, "state": self.state, "error": self.error,
                "messages": self.messages}

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        import paho.mqtt.client as mqtt

        s = self.source
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"notoryx-ops-{int(time.time())}")
        if s.username:
            c.username_pw_set(s.username, s.password or None)
        if s.tls:
            c.tls_set()
        c.reconnect_delay_set(min_delay=2, max_delay=120)

        def on_connect(client, _ud, _flags, reason, _props=None):
            if getattr(reason, "is_failure", False):
                self.state, self.error = "error", str(reason)
                return
            self.state, self.error = "connected", None
            for t in s.topics:
                client.subscribe(t)

        def on_disconnect(_client, _ud, _flags=None, reason=None, _props=None):
            self.state = "disconnected"
            if reason is not None and getattr(reason, "is_failure", False):
                self.error = str(reason)

        def on_message(_client, _ud, msg):
            parsed = parse_message(msg.topic, msg.payload)
            if parsed is None:
                return
            self.messages += 1
            loop.call_soon_threadsafe(self._handle, parsed)

        c.on_connect, c.on_disconnect, c.on_message = on_connect, on_disconnect, on_message
        try:
            c.connect_async(s.host, s.port, keepalive=60)
            c.loop_start()
        except Exception as exc:  # noqa: BLE001
            self.state, self.error = "error", str(exc)
        self.client = c

    def _handle(self, p: dict[str, Any]) -> None:
        self.ingest.handle(p["raw"], p["observer"], f"mqtt:{self.source.name}", p.get("snr"), p.get("rssi"),
                           observer_key=p.get("observer_key"))

    def stop(self) -> None:
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
