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
SECRETS = ("admin_token", "discord_webhook", "webhook_secret", "metrics_token", "ha_password", "pg_dsn", "stats_push_token")


# Settings page key -> Config attribute for the [terrain] block.
TERRAIN_FIELDS = {
    "source": "terrain_source", "freq_mhz": "freq_mhz", "bandwidth_khz": "bandwidth_khz",
    "spreading_factor": "spreading_factor", "tx_power_dbm": "tx_power_dbm", "antenna_gain_dbi": "antenna_gain_dbi",
    "cable_loss_db": "cable_loss_db", "noise_figure_db": "noise_figure_db", "repeater_height_m": "repeater_height_m",
    "client_height_m": "client_height_m", "use_calibration": "use_calibration",
}
BANDWIDTHS = (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0)


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
            "webhook_secret_set": bool(cfg.webhook_secret), "radio_lost_minutes": cfg.radio_lost_minutes,
        },
        "mqtt": [
            {"name": m.name, "host": m.host, "port": m.port, "username": m.username, "password_set": bool(m.password),
             "tls": m.tls, "topics": list(m.topics)}
            for m in cfg.mqtt
        ],
        "share": {"enabled": cfg.share_enabled, "port": cfg.share_port},
        "terrain": {k: getattr(cfg, a) for k, a in TERRAIN_FIELDS.items()},
        "integrations": {
            "metrics_token_set": bool(cfg.metrics_token), "public_map": cfg.public_map, "public_title": cfg.public_title,
            "public_precision": cfg.public_precision, "public_show_companions": cfg.public_show_companions,
            "stats_push": cfg.stats_push_enabled, "stats_push_url": cfg.stats_push_url,
            "stats_push_token_set": bool(cfg.stats_push_token),
        },
        "homeassistant": {
            "enabled": cfg.ha_enabled, "host": cfg.ha_host, "port": cfg.ha_port, "username": cfg.ha_username,
            "password_set": bool(cfg.ha_password), "tls": cfg.ha_tls, "prefix": cfg.ha_prefix, "interval_s": cfg.ha_interval_s,
        },
        "postgres": {"enabled": cfg.pg_enabled, "dsn_set": bool(cfg.pg_dsn)},
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
        if al.get("radio_lost_minutes") is not None:
            new["radio_lost_minutes"] = _num(al["radio_lost_minutes"], 0, 1440, "Radio lost after minutes")
    sh = data.get("share") or {}
    if sh:
        if "enabled" in sh:
            new["share_enabled"] = bool(sh["enabled"])
        if "port" in sh:
            new["share_port"] = _int(sh["port"], 1024, 65535, "Sharing port")
    tr = data.get("terrain") or {}
    if tr:
        src = tr.get("source", cfg.terrain_source)
        if src not in ("glo90", "glo30"):
            raise ValueError("Elevation source must be GLO-90 or GLO-30.")
        new["terrain_source"] = src
        new["freq_mhz"] = _num(tr.get("freq_mhz", cfg.freq_mhz), 137, 2500, "Frequency (MHz)")
        bw = _num(tr.get("bandwidth_khz", cfg.bandwidth_khz), 7, 500, "Bandwidth (kHz)")
        if bw not in BANDWIDTHS:
            raise ValueError("Bandwidth must be a LoRa bandwidth such as 62.5, 125 or 250 kHz.")
        new["bandwidth_khz"] = bw
        new["spreading_factor"] = _int(tr.get("spreading_factor", cfg.spreading_factor), 5, 12, "Spreading factor")
        new["tx_power_dbm"] = _num(tr.get("tx_power_dbm", cfg.tx_power_dbm), -10, 37, "Transmit power (dBm)")
        new["antenna_gain_dbi"] = _num(tr.get("antenna_gain_dbi", cfg.antenna_gain_dbi), -10, 20, "Antenna gain (dBi)")
        new["cable_loss_db"] = _num(tr.get("cable_loss_db", cfg.cable_loss_db), 0, 20, "Cable loss (dB)")
        new["noise_figure_db"] = _num(tr.get("noise_figure_db", cfg.noise_figure_db), 0, 20, "Noise figure (dB)")
        new["repeater_height_m"] = _num(tr.get("repeater_height_m", cfg.repeater_height_m), 0, 300, "Repeater antenna height (m)")
        new["client_height_m"] = _num(tr.get("client_height_m", cfg.client_height_m), 0, 300, "Client antenna height (m)")
        new["use_calibration"] = bool(tr.get("use_calibration", cfg.use_calibration))
    ig = data.get("integrations") or {}
    if ig:
        if ig.get("metrics_token") is not None:
            tok = _text(ig["metrics_token"], "Metrics token", 200)
            if tok and len(tok) < 12:
                raise ValueError("Metrics token must be at least 12 characters.")
            new["metrics_token"] = tok
        new["public_map"] = bool(ig.get("public_map", cfg.public_map))
        new["public_title"] = _text(ig.get("public_title", cfg.public_title), "Public map title", 60)
        new["public_precision"] = _int(ig.get("public_precision", cfg.public_precision), 1, 4, "Public position detail")
        new["public_show_companions"] = bool(ig.get("public_show_companions", cfg.public_show_companions))
        new["stats_push_enabled"] = bool(ig.get("stats_push", cfg.stats_push_enabled))
        new["stats_push_url"] = _url(ig.get("stats_push_url", cfg.stats_push_url), "Stats relay address")
        if new["stats_push_url"].startswith("http://"):
            raise ValueError("Stats relay address must use https://.")
        if ig.get("stats_push_token") is not None:
            stok = _text(ig["stats_push_token"], "Stats relay token", 200)
            if stok and len(stok) < 24:
                raise ValueError("Stats relay token must be at least 24 characters.")
            new["stats_push_token"] = stok
        if new["stats_push_enabled"] and not (new["stats_push_url"] and new.get("stats_push_token", cfg.stats_push_token)):
            raise ValueError("Enter the stats relay address and token before turning stats sharing on.")
    ha = data.get("homeassistant") or {}
    if ha:
        new["ha_enabled"] = bool(ha.get("enabled", cfg.ha_enabled))
        new["ha_host"] = _text(ha.get("host", cfg.ha_host), "Home Assistant broker", 200)
        new["ha_port"] = _int(ha.get("port", cfg.ha_port), 1, 65535, "Home Assistant broker port")
        new["ha_username"] = _text(ha.get("username", cfg.ha_username), "Home Assistant broker username", 100)
        if ha.get("password") is not None:
            new["ha_password"] = _text(ha["password"], "Home Assistant broker password", 200)
        new["ha_tls"] = bool(ha.get("tls", cfg.ha_tls))
        prefix = _text(ha.get("prefix", cfg.ha_prefix), "Discovery prefix", 60, allow_empty=False)
        if not re.fullmatch(r"[A-Za-z0-9_/-]+", prefix):
            raise ValueError("Discovery prefix can use letters, numbers, _, - and /.")
        new["ha_prefix"] = prefix
        new["ha_interval_s"] = _int(ha.get("interval_s", cfg.ha_interval_s), 15, 3600, "Seconds between updates")
        if new["ha_enabled"] and not new["ha_host"]:
            raise ValueError("Enter the MQTT broker that Home Assistant uses.")
    pg = data.get("postgres") or {}
    if pg:
        new["pg_enabled"] = bool(pg.get("enabled", cfg.pg_enabled))
        if pg.get("dsn") is not None:
            dsn = _text(pg["dsn"], "PostgreSQL connection", 500)
            if dsn and not re.match(r"^(postgres(ql)?://|host=|dbname=)", dsn):
                raise ValueError("PostgreSQL connection must look like postgresql://user:pass@host/db.")
            new["pg_dsn"] = dsn
        if new["pg_enabled"] and not new.get("pg_dsn", cfg.pg_dsn):
            raise ValueError("Enter the PostgreSQL connection first.")
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
        f"radio_lost_minutes = {float(cfg.radio_lost_minutes)}",
        "",
        "[share]",
        f"enabled = {str(bool(cfg.share_enabled)).lower()}",
        f"host = {_q(cfg.share_host)}",
        f"port = {int(cfg.share_port)}",
        "",
        "[terrain]",
        f"source = {_q(cfg.terrain_source)}",
        f"freq_mhz = {float(cfg.freq_mhz)}",
        f"bandwidth_khz = {float(cfg.bandwidth_khz)}",
        f"spreading_factor = {int(cfg.spreading_factor)}",
        f"tx_power_dbm = {float(cfg.tx_power_dbm)}",
        f"antenna_gain_dbi = {float(cfg.antenna_gain_dbi)}",
        f"cable_loss_db = {float(cfg.cable_loss_db)}",
        f"noise_figure_db = {float(cfg.noise_figure_db)}",
        f"repeater_height_m = {float(cfg.repeater_height_m)}",
        f"client_height_m = {float(cfg.client_height_m)}",
        f"use_calibration = {'true' if cfg.use_calibration else 'false'}",
        "",
        "[integrations]",
        f"metrics_token = {_q(cfg.metrics_token)}",
        f"public_map = {'true' if cfg.public_map else 'false'}",
        f"public_title = {_q(cfg.public_title)}",
        f"public_precision = {int(cfg.public_precision)}",
        f"public_show_companions = {'true' if cfg.public_show_companions else 'false'}",
        f"stats_push = {'true' if cfg.stats_push_enabled else 'false'}",
        f"stats_push_url = {_q(cfg.stats_push_url)}",
        f"stats_push_token = {_q(cfg.stats_push_token)}",
        "",
        "[homeassistant]",
        f"enabled = {'true' if cfg.ha_enabled else 'false'}",
        f"host = {_q(cfg.ha_host)}",
        f"port = {int(cfg.ha_port)}",
        f"username = {_q(cfg.ha_username)}",
        f"password = {_q(cfg.ha_password)}",
        f"tls = {'true' if cfg.ha_tls else 'false'}",
        f"prefix = {_q(cfg.ha_prefix)}",
        f"interval_s = {int(cfg.ha_interval_s)}",
        "",
        "[postgres]",
        f"enabled = {'true' if cfg.pg_enabled else 'false'}",
        f"dsn = {_q(cfg.pg_dsn)}",
    ]
    if cfg.addons or cfg.addons_disabled:
        lines += ["", "[addons]", f"disabled = {_q(list(cfg.addons_disabled))}"]
    for name, table in sorted(cfg.addons.items()):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            continue
        lines += ["", f"[addons.{name}]"]
        for k, v in table.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", str(k)):
                continue
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, int | float | str):
                lines.append(f"{k} = {_q(v)}")
            elif isinstance(v, list) and all(isinstance(x, str | int | float) and not isinstance(x, bool) for x in v):
                lines.append(f"{k} = {_q(v)}")
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


MESHCORE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"  # Nordic UART service, used by MeshCore companions
MESHTASTIC_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"


async def ble_devices(timeout: float = 6, kind: str = "meshcore") -> list[dict[str, Any]]:
    """Nearby Bluetooth radios of one kind ("meshcore" or "meshtastic"). Listening only, nothing is sent."""
    from bleak import BleakScanner

    uuid, word = (MESHTASTIC_UUID, "meshtastic") if kind == "meshtastic" else (MESHCORE_UUID, "meshcore")
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if uuid in uuids or word in name.lower():
            out.append({"address": dev.address, "name": name or dev.address, "rssi": adv.rssi})
    return sorted(out, key=lambda d: -(d["rssi"] or -999))
