"""Station settings, read from a TOML file."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .nodestate import DEFAULT_STALE_HOURS, STALE_CHOICES_HOURS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_CONFIG = """\
# NOTORYX Ops station settings

[station]
name = "NOTORYX Ops station"
# Where the database and logs live. Relative paths are next to this file.
data_dir = "data"
# Web dashboard. Use 0.0.0.0 to reach it from other computers on your network.
host = "127.0.0.1"
port = 8484
# Days of packets to keep. Fleet history is kept for fleet_history_days.
packet_retention_days = 14
fleet_history_days = 365
# Fill the dashboard with made-up traffic, for trying it without a radio.
demo = false
# Needed for changes (fleet, bulk settings, alerts) when the dashboard is reachable
# from other computers. Pick a long random string. Empty = changes only from this computer.
admin_token = ""
# Mark nodes and observers stale when they haven't been heard for this many hours.
# One of 0 (off), 1, 6, 24, 72 or 168.
stale_after_hours = 24

[radio]
# "serial", "ble", "tcp" or "none"
transport = "none"
serial_port = "/dev/ttyUSB0"
baudrate = 115200
ble_address = ""
tcp_host = "192.168.1.50"
tcp_port = 5000
# Name for this radio when it shows up as an observer.
observer_name = "Station radio"

[channels]
# "public", "#hashtag" or "name=32 hex digit key"
keys = ["public", "#testing"]

[fleet]
# Minutes between status polls of each repeater. Keep this long to save airtime.
poll_minutes = 30
# Seconds to wait between repeaters during a poll.
stagger_seconds = 20
# Alert when a repeater has not been heard for this many hours.
quiet_hours = 3
# Alert when battery drops under this many volts.
low_battery_volts = 3.6
# Firmware version you expect. Repeaters reporting an older one get an alert. Empty = off.
expected_firmware = ""

[alerts]
# Discord webhook URL, generic webhook URL, or both. Empty = off.
discord_webhook = ""
webhook_url = ""
# Shared secret to sign generic webhooks (HMAC-SHA256 in X-Notoryx-Signature).
webhook_secret = ""

# Add one block per MQTT broker that carries observer packets.
# [[mqtt]]
# name = "Local broker"
# host = "127.0.0.1"
# port = 1883
# username = ""
# password = ""
# tls = false
# topics = ["meshcore/+/+/packets"]
"""


@dataclass
class MqttSource:
    name: str
    host: str
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    topics: list[str] = field(default_factory=lambda: ["meshcore/+/+/packets"])


@dataclass
class Config:
    path: Path
    name: str = "NOTORYX Ops station"
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8484
    packet_retention_days: int = 14
    fleet_history_days: int = 365
    demo: bool = False
    admin_token: str = ""
    stale_after_hours: float = 24
    radio_transport: str = "none"
    serial_port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    ble_address: str = ""
    tcp_host: str = ""
    tcp_port: int = 5000
    observer_name: str = "Station radio"
    channel_keys: list[str] = field(default_factory=lambda: ["public"])
    poll_minutes: int = 30
    stagger_seconds: int = 20
    quiet_hours: float = 3
    low_battery_volts: float = 3.6
    expected_firmware: str = ""
    discord_webhook: str = ""
    webhook_url: str = ""
    webhook_secret: str = ""
    mqtt: list[MqttSource] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ops.sqlite"


def default_config_path() -> Path:
    env = os.environ.get("NOTORYX_OPS_CONFIG")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/NOTORYX Ops/config.toml"
    return Path.home() / ".config/notoryx-ops/config.toml"


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG)
    os.chmod(path, 0o600)


def load(path: Path | None = None) -> Config:
    path = (path or default_config_path()).expanduser()
    if not path.exists():
        write_default(path)
    raw = tomllib.loads(path.read_text())
    st, rd = raw.get("station", {}), raw.get("radio", {})
    fl, al = raw.get("fleet", {}), raw.get("alerts", {})
    data_dir = Path(st.get("data_dir", "data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir
    cfg = Config(
        path=path,
        name=st.get("name", Config.name),
        data_dir=data_dir,
        host=st.get("host", Config.host),
        port=int(st.get("port", Config.port)),
        packet_retention_days=int(st.get("packet_retention_days", 14)),
        fleet_history_days=int(st.get("fleet_history_days", 365)),
        demo=bool(st.get("demo", False)),
        admin_token=str(st.get("admin_token", "")),
        stale_after_hours=float(st.get("stale_after_hours", DEFAULT_STALE_HOURS)),
        radio_transport=rd.get("transport", "none"),
        serial_port=rd.get("serial_port", Config.serial_port),
        baudrate=int(rd.get("baudrate", 115200)),
        ble_address=rd.get("ble_address", ""),
        tcp_host=rd.get("tcp_host", ""),
        tcp_port=int(rd.get("tcp_port", 5000)),
        observer_name=rd.get("observer_name", Config.observer_name),
        channel_keys=list(raw.get("channels", {}).get("keys", ["public"])),
        poll_minutes=int(fl.get("poll_minutes", 30)),
        stagger_seconds=int(fl.get("stagger_seconds", 20)),
        quiet_hours=float(fl.get("quiet_hours", 3)),
        low_battery_volts=float(fl.get("low_battery_volts", 3.6)),
        expected_firmware=str(fl.get("expected_firmware", "")),
        discord_webhook=al.get("discord_webhook", ""),
        webhook_url=al.get("webhook_url", ""),
        webhook_secret=al.get("webhook_secret", ""),
        mqtt=[MqttSource(**m) for m in raw.get("mqtt", [])],
    )
    if cfg.poll_minutes < 5:
        cfg.poll_minutes = 5
    if cfg.stale_after_hours not in STALE_CHOICES_HOURS:
        cfg.stale_after_hours = DEFAULT_STALE_HOURS
    if cfg.stagger_seconds < 5:
        cfg.stagger_seconds = 5
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
