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

[terrain]
# Elevation for coverage maps and link profiles: "glo90" (90 m, ~5 MB per 1° tile) or
# "glo30" (30 m, ~40 MB per tile). Tiles download on first use and stay in the data folder.
source = "glo90"
# Your LoRa settings, used to predict signal strength.
freq_mhz = 910.525
bandwidth_khz = 62.5
spreading_factor = 7
tx_power_dbm = 22
antenna_gain_dbi = 3
cable_loss_db = 1
noise_figure_db = 6
# Antenna heights above ground when nothing else is known.
repeater_height_m = 10
client_height_m = 1.5
# Correct predictions with the median error on links the station has measured.
use_calibration = true

[integrations]
# Bearer token that lets Prometheus read /metrics once accounts are on. Empty = sign-in needed.
metrics_token = ""
# A read-only public map at /public: repeaters (and optionally companions), positions rounded.
public_map = false
public_title = ""
# Decimal places kept in public positions: 2 is about 1 km, 3 about 100 m.
public_precision = 2
public_show_companions = false

[homeassistant]
# Publishes station and fleet sensors to Home Assistant over MQTT (discovery).
enabled = false
host = ""
port = 1883
username = ""
password = ""
tls = false
prefix = "homeassistant"
interval_s = 60

[postgres]
# Copies packets, messages, fleet history and alerts to PostgreSQL every minute.
# Needs: pip install "notoryx-ops[postgres]"
enabled = false
dsn = ""

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
    terrain_source: str = "glo90"
    freq_mhz: float = 910.525
    bandwidth_khz: float = 62.5
    spreading_factor: int = 7
    tx_power_dbm: float = 22
    antenna_gain_dbi: float = 3
    cable_loss_db: float = 1
    noise_figure_db: float = 6
    repeater_height_m: float = 10
    client_height_m: float = 1.5
    use_calibration: bool = True
    metrics_token: str = ""
    public_map: bool = False
    public_title: str = ""
    public_precision: int = 2
    public_show_companions: bool = False
    ha_enabled: bool = False
    ha_host: str = ""
    ha_port: int = 1883
    ha_username: str = ""
    ha_password: str = ""
    ha_tls: bool = False
    ha_prefix: str = "homeassistant"
    ha_interval_s: int = 60
    pg_enabled: bool = False
    pg_dsn: str = ""

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
    tr = raw.get("terrain", {})
    ig, ha, pg = raw.get("integrations", {}), raw.get("homeassistant", {}), raw.get("postgres", {})
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
        terrain_source=str(tr.get("source", "glo90")),
        freq_mhz=float(tr.get("freq_mhz", Config.freq_mhz)),
        bandwidth_khz=float(tr.get("bandwidth_khz", Config.bandwidth_khz)),
        spreading_factor=int(tr.get("spreading_factor", Config.spreading_factor)),
        tx_power_dbm=float(tr.get("tx_power_dbm", Config.tx_power_dbm)),
        antenna_gain_dbi=float(tr.get("antenna_gain_dbi", Config.antenna_gain_dbi)),
        cable_loss_db=float(tr.get("cable_loss_db", Config.cable_loss_db)),
        noise_figure_db=float(tr.get("noise_figure_db", Config.noise_figure_db)),
        repeater_height_m=float(tr.get("repeater_height_m", Config.repeater_height_m)),
        client_height_m=float(tr.get("client_height_m", Config.client_height_m)),
        use_calibration=bool(tr.get("use_calibration", True)),
        metrics_token=str(ig.get("metrics_token", "")),
        public_map=bool(ig.get("public_map", False)),
        public_title=str(ig.get("public_title", "")),
        public_precision=int(ig.get("public_precision", 2)),
        public_show_companions=bool(ig.get("public_show_companions", False)),
        ha_enabled=bool(ha.get("enabled", False)),
        ha_host=str(ha.get("host", "")),
        ha_port=int(ha.get("port", 1883)),
        ha_username=str(ha.get("username", "")),
        ha_password=str(ha.get("password", "")),
        ha_tls=bool(ha.get("tls", False)),
        ha_prefix=str(ha.get("prefix", "homeassistant")),
        ha_interval_s=int(ha.get("interval_s", 60)),
        pg_enabled=bool(pg.get("enabled", False)),
        pg_dsn=str(pg.get("dsn", "")),
    )
    cfg.public_precision = min(4, max(1, cfg.public_precision))
    if cfg.terrain_source not in ("glo90", "glo30"):
        cfg.terrain_source = "glo90"
    if cfg.poll_minutes < 5:
        cfg.poll_minutes = 5
    if cfg.stale_after_hours not in STALE_CHOICES_HOURS:
        cfg.stale_after_hours = DEFAULT_STALE_HOURS
    if cfg.stagger_seconds < 5:
        cfg.stagger_seconds = 5
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
