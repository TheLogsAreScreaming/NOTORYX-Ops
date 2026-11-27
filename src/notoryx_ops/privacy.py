"""Everything that can leave this station, in one list.

Each flow says what it sends, whether that includes anything the radios heard, and the
setting that turns it on. The dashboard shows this list ("What leaves this station"),
and tools/privacy_check.py fails the build when code can reach the network from a
module that isn't declared here, or when a flow that carries radio data is on by default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import Config


@dataclass(frozen=True)
class Flow:
    id: str
    name: str
    sends: str
    radio_data: bool
    """True when what's sent includes packets, messages, node names, keys or positions."""
    modules: tuple[str, ...]
    on: Callable[[Config], bool]
    setting: str


FLOWS: tuple[Flow, ...] = (
    Flow("alerts", "Alerts to Discord or a webhook", "Alert text: repeater names, battery, how long a node has been quiet.",
         True, ("alerts",), lambda c: bool(c.discord_webhook or c.webhook_url), "Settings › Alerts"),
    Flow("mqtt_in", "MQTT observer feeds", "A sign-in to each broker you add. Packets only come in; nothing is published.",
         False, ("mqtt_ingest",), lambda c: bool(c.mqtt), "Settings › MQTT brokers"),
    Flow("homeassistant", "Home Assistant", "Station and fleet sensor values to your MQTT broker.",
         True, ("integrations",), lambda c: c.ha_enabled, "Settings › Home Assistant"),
    Flow("postgres", "PostgreSQL archive", "Copies of packets, messages, fleet history and alerts to your database.",
         True, ("integrations",), lambda c: c.pg_enabled, "Settings › PostgreSQL archive"),
    Flow("stats_push", "Live network stats", "Counts and short node ids every 15 minutes. No names, messages or positions.",
         True, ("netstats",), lambda c: c.stats_push_enabled, "Settings › Live network stats"),
    Flow("public_map", "Public map", "Repeater names and rounded positions to anyone who opens /public.",
         True, (), lambda c: c.public_map, "Settings › Public map"),
    Flow("share", "Radio sharing", "Lets one app on your network use the station radio over TCP.",
         True, ("share",), lambda c: c.share_enabled, "Settings › Station"),
    Flow("terrain", "Elevation tiles", "Asks a public elevation server for the 1° squares your coverage maps need.",
         False, ("terrain",), lambda c: True, "Used when you open Coverage"),
    Flow("maps", "Base map tiles", "Asks the map tile server for the map squares you look at or download.",
         False, ("maps",), lambda c: True, "Used when a map is on screen"),
    Flow("updates", "Update check", "Asks GitHub for the newest NOTORYX Ops release. Sends nothing about you.",
         False, ("updates",), lambda c: True, "Admins, every 6 hours"),
)

# Modules that talk to your own radio (USB, Bluetooth or TCP) and nothing else.
RADIO_LINK = ("radio", "settings")
# Helpers that hold no flow of their own.
HELPERS = ("net",)


def declared_modules() -> set[str]:
    out = set(RADIO_LINK) | set(HELPERS)
    for f in FLOWS:
        out.update(f.modules)
    return out


def view(cfg: Config) -> list[dict[str, Any]]:
    return [{"id": f.id, "name": f.name, "sends": f.sends, "radio_data": f.radio_data, "on": bool(f.on(cfg)),
             "setting": f.setting} for f in FLOWS]
