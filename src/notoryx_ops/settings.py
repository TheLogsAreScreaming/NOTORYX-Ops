"""Settings from the dashboard: read them without secrets, validate changes, write the file."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .channels import parse_channel
from .config import Config, MqttSource
from .nodestate import STALE_CHOICES_HOURS

TRANSPORTS = ("none", "serial", "ble", "tcp")
RESTART_FIELDS = ("host", "port")

# Secret fields: never sent to the browser. On save, None keeps the value, "" clears it.
SECRETS = ("admin_token", "discord_webhook", "webhook_secret")


def public_view(cfg: Config) -> dict[str, Any]:
    return {
        "station": {
            "name": cfg.name, "host": cfg.host, "port": cfg.port,
            "packet_retention_days": cfg.packet_retention_days, "fleet_history_days": cfg.fleet_history_days,
            "admin_token_set": bool(cfg.admin_token), "stale_after_hours": cfg.stale_after_hours,
        },
        "stale_choices_hours": list(STALE_CHOICES_HOURS),
        "radio": {
            "transport": cfg.radio_transport, "serial_port": cfg.serial_port, "baudrate": cfg.baudrate,
            "ble_address": cfg.ble_address, "tcp_host": cfg.tcp_host, "tcp_port": cfg.tcp_port,
            "observer_name": cfg.observer_name,
        },
        "channels": list(cfg.channel_keys),
        "fleet": {
            "poll_minutes": cfg.poll_minutes, "stagger_seconds": cfg.stagger_seconds, "quiet_hours": cfg.quiet_hours,
            "low_battery_volts": cfg.low_battery_volts, "expected_firmware": cfg.expected_firmware,
        },
        "alerts": {
            "discord_webhook_set": bool(cfg.discord_webhook), "webhook_url": cfg.webhook_url,
            "webhook_secret_set": bool(cfg.webhook_secret),
        },
        "mqtt": [
            {"name": m.name, "host": m.host, "port": m.port, "username": m.username, "password_set": bool(m.password),
             "tls": m.tls, "topics": list(m.topics)}
            for m in cfg.mqtt
        ],
        "demo": cfg.demo,
        "config_path": str(cfg.path),
    }


def _int(v: Any, lo: int, hi: int, name: str) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a whole number.") from exc
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}.")
    return n


def _num(v: Any, lo: float, hi: float, name: str) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}.")
    return n


def _text(v: Any, name: str, max_len: int = 200, allow_empty: bool = True) -> str:
    s = "" if v is None else str(v).strip()
    if not s and not allow_empty:
        raise ValueError(f"{name} can't be empty.")
    if len(s) > max_len or any(ord(c) < 32 for c in s):
        raise ValueError(f"{name} is too long or has odd characters.")
    return s


def _url(v: Any, name: str) -> str:
    s = _text(v, name, 500)
    if s and not re.match(r"^https?://[^\s]+$", s):
        raise ValueError(f"{name} must start with http:// or https://.")
    return s


def apply_changes(cfg: Config, data: dict[str, Any]) -> list[str]:
    """Validate everything first, then change cfg in place. Returns fields that need a restart."""
    st, rd, fl, al = (data.get(k) or {} for k in ("station", "radio", "fleet", "alerts"))
    new: dict[str, Any] = {}
    if st:
        new["name"] = _text(st.get("name", cfg.name), "Station name", 60, allow_empty=False)
        host = _text(st.get("host", cfg.host), "Listen address", 60, allow_empty=False)
        if host not in ("127.0.0.1", "0.0.0.0", "localhost", "::1", "::"):
            raise ValueError("Listen address must be 127.0.0.1 (this computer) or 0.0.0.0 (your network).")
        new["host"] = host
        new["port"] = _int(st.get("port", cfg.port), 1024, 65535, "Port")
        new["packet_retention_days"] = _int(st.get("packet_retention_days", cfg.packet_retention_days), 1, 365, "Days to keep packets")
        new["fleet_history_days"] = _int(st.get("fleet_history_days", cfg.fleet_history_days), 7, 3650, "Days to keep fleet history")
        stale = _num(st.get("stale_after_hours", cfg.stale_after_hours), 0, 168, "Mark nodes stale after")
        if stale not in STALE_CHOICES_HOURS:
            raise ValueError("Mark nodes stale after: pick Off, 1 h, 6 h, 24 h, 3 days or 7 days.")
        new["stale_after_hours"] = stale
        if "admin_token" in st and st["admin_token"] is not None:
            tok = _text(st["admin_token"], "Admin token", 200)
            if tok and len(tok) < 12:
                raise ValueError("Admin token must be at least 12 characters.")
            new["admin_token"] = tok
    if rd:
        t = rd.get("transport", cfg.radio_transport)
        if t not in TRANSPORTS:
            raise ValueError("Pick a radio connection type.")
        new["radio_transport"] = t
        new["serial_port"] = _text(rd.get("serial_port", cfg.serial_port), "Serial port", 200)
        new["baudrate"] = _int(rd.get("baudrate", cfg.baudrate), 1200, 3000000, "Baud rate")
        new["ble_address"] = _text(rd.get("ble_address", cfg.ble_address), "Bluetooth device", 100)
        new["tcp_host"] = _text(rd.get("tcp_host", cfg.tcp_host), "TCP host", 200)
        new["tcp_port"] = _int(rd.get("tcp_port", cfg.tcp_port), 1, 65535, "TCP port")
        new["observer_name"] = _text(rd.get("observer_name", cfg.observer_name), "Observer name", 60, allow_empty=False)
        if t == "serial" and not new["serial_port"]:
            raise ValueError("Pick a serial port.")
        if t == "tcp" and not new["tcp_host"]:
            raise ValueError("Enter the radio's address.")
    if "channels" in data:
        chans = [_text(c, "Channel", 120, allow_empty=False) for c in data["channels"] or []]
        if len(chans) > 40:
            raise ValueError("Up to 40 channels.")
        for c in chans:
            parse_channel(c)  # raises ValueError with a readable message
        new["channel_keys"] = chans
    if fl:
        new["poll_minutes"] = _int(fl.get("poll_minutes", cfg.poll_minutes), 5, 1440, "Minutes between polls")
        new["stagger_seconds"] = _int(fl.get("stagger_seconds", cfg.stagger_seconds), 5, 600, "Seconds between repeaters")
        new["quiet_hours"] = _num(fl.get("quiet_hours", cfg.quiet_hours), 0.5, 168, "Quiet after hours")
        new["low_battery_volts"] = _num(fl.get("low_battery_volts", cfg.low_battery_volts), 2.5, 5, "Low battery volts")
        new["expected_firmware"] = _text(fl.get("expected_firmware", cfg.expected_firmware), "Expected firmware", 30)
    if al:
        if al.get("discord_webhook") is not None:
            new["discord_webhook"] = _url(al["discord_webhook"], "Discord webhook")
        new["webhook_url"] = _url(al.get("webhook_url", cfg.webhook_url), "Webhook URL")
        if al.get("webhook_secret") is not None:
            new["webhook_secret"] = _text(al["webhook_secret"], "Webhook secret", 200)
    if "mqtt" in data:
        old = {m.name: m for m in cfg.mqtt}
        brokers = []
        for m in data["mqtt"] or []:
            name = _text(m.get("name"), "Broker name", 40, allow_empty=False)
            pw = m.get("password")
            if pw is None:
                pw = old[name].password if name in old else ""
            topics = [_text(t, "Topic", 200, allow_empty=False) for t in (m.get("topics") or ["meshcore/+/+/packets"])]
            brokers.append(MqttSource(
                name=name, host=_text(m.get("host"), "Broker address", 200, allow_empty=False),
                port=_int(m.get("port", 1883), 1, 65535, "Broker port"),
                username=_text(m.get("username"), "Broker username", 100), password=_text(pw, "Broker password", 200),
                tls=bool(m.get("tls")), topics=topics,
            ))
        if len({b.name for b in brokers}) != len(brokers):
            raise ValueError("Each broker needs its own name.")
        new["mqtt"] = brokers

    restart = [f for f in RESTART_FIELDS if f in new and new[f] != getattr(cfg, f)]
    for k, v in new.items():
        setattr(cfg, k, v)
    return restart


def _q(v: Any) -> str:
    return json.dumps(v)  # JSON strings, numbers and lists are valid TOML here


def to_toml(cfg: Config) -> str:
    data_dir = cfg.data_dir
    try:
        data_dir = cfg.data_dir.relative_to(cfg.path.parent)
    except ValueError:
        pass
    lines = [
        "# NOTORYX Ops station settings. The Settings page in the dashboard writes this file.",
        "",
        "[station]",
        f"name = {_q(cfg.name)}",
        f"data_dir = {_q(str(data_dir))}",
        f"host = {_q(cfg.host)}",
        f"port = {cfg.port}",
        f"packet_retention_days = {cfg.packet_retention_days}",
        f"fleet_history_days = {cfg.fleet_history_days}",
        f"demo = {'true' if cfg.demo else 'false'}",
        f"admin_token = {_q(cfg.admin_token)}",
        f"stale_after_hours = {float(cfg.stale_after_hours)}",
        "",
        "[radio]",
        f"transport = {_q(cfg.radio_transport)}",
        f"serial_port = {_q(cfg.serial_port)}",
        f"baudrate = {cfg.baudrate}",
        f"ble_address = {_q(cfg.ble_address)}",
        f"tcp_host = {_q(cfg.tcp_host)}",
        f"tcp_port = {cfg.tcp_port}",
        f"observer_name = {_q(cfg.observer_name)}",
        "",
        "[channels]",
        f"keys = {_q(list(cfg.channel_keys))}",
        "",
        "[fleet]",
        f"poll_minutes = {cfg.poll_minutes}",
        f"stagger_seconds = {cfg.stagger_seconds}",
        f"quiet_hours = {float(cfg.quiet_hours)}",
        f"low_battery_volts = {float(cfg.low_battery_volts)}",
        f"expected_firmware = {_q(cfg.expected_firmware)}",
        "",
        "[alerts]",
        f"discord_webhook = {_q(cfg.discord_webhook)}",
        f"webhook_url = {_q(cfg.webhook_url)}",
        f"webhook_secret = {_q(cfg.webhook_secret)}",
    ]
    for m in cfg.mqtt:
        lines += [
            "",
            "[[mqtt]]",
            f"name = {_q(m.name)}",
            f"host = {_q(m.host)}",
            f"port = {m.port}",
            f"username = {_q(m.username)}",
            f"password = {_q(m.password)}",
            f"tls = {'true' if m.tls else 'false'}",
            f"topics = {_q(list(m.topics))}",
        ]
    return "\n".join(lines) + "\n"


def save(cfg: Config) -> None:
    """Write the settings file atomically, readable only by this user."""
    tmp = cfg.path.with_suffix(".toml.tmp")
    tmp.write_text(to_toml(cfg))
    os.chmod(tmp, 0o600)
    os.replace(tmp, cfg.path)


def serial_ports() -> list[dict[str, str]]:
    from serial.tools import list_ports

    out = []
    for p in list_ports.comports():
        if any(x in p.device for x in ("Bluetooth-Incoming", "BLTH", "debug-console")):
            continue
        out.append({"device": p.device, "description": p.description or "", "manufacturer": p.manufacturer or ""})
    return out


async def ble_devices(timeout: float = 6) -> list[dict[str, Any]]:
    """Nearby Bluetooth devices that look like MeshCore radios. Listening only, nothing is sent."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    nus = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if nus in uuids or "meshcore" in name.lower():
            out.append({"address": dev.address, "name": name or dev.address, "rssi": adv.rssi})
    return sorted(out, key=lambda d: -(d["rssi"] or -999))
