"""Made-up traffic and a pretend radio, for trying the dashboard without hardware.

Nothing here touches a real radio. Demo nodes are named "Demo ..." so they can't be
mistaken for a real network.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import struct
import time
from typing import Any

from . import decoder
from .channels import PUBLIC_CHANNEL_KEY, encrypt_group_text, hashtag_key
from .ingest import Ingest

CENTER = (44.26, -72.58)

REPEATERS = [
    ("Demo Ridge Top", 0.06, 0.05),
    ("Demo Water Tower", -0.02, 0.03),
    ("Demo Library Roof", 0.0, -0.01),
    ("Demo Fire Station", -0.05, -0.06),
    ("Demo Hilltop", 0.08, -0.07),
    ("Demo Barn", -0.09, 0.09),
]
COMPANIONS = ["Demo Hiker", "Demo Truck", "Demo Base", "Demo Kitchen", "Demo Tower Crew", "Demo Field Kit"]
CHATTER = ["checking in", "anyone copy?", "testing from the trail", "signal is good here", "heading out",
           "repeater check", "weather looks clear", "back at base"]


def _key(name: str) -> bytes:
    return hashlib.sha256(("notoryx-demo:" + name).encode()).digest()


class DemoNetwork:
    def __init__(self, ingest: Ingest, seed: int = 7):
        self.ingest = ingest
        self.rng = random.Random(seed)
        self.nodes: list[dict[str, Any]] = []
        for name, dlat, dlon in REPEATERS:
            self.nodes.append({"name": name, "key": _key(name), "type": 2,
                               "lat": CENTER[0] + dlat, "lon": CENTER[1] + dlon})
        # Two demo repeaters share a prefix so the clash check has something to show.
        self.nodes[4]["key"] = self.nodes[1]["key"][:1] + _key("clash")[1:]
        for i, name in enumerate(COMPANIONS):
            far = i == 5
            self.nodes.append({"name": name, "key": _key(name), "type": 1,
                               "lat": CENTER[0] + self.rng.uniform(-0.1, 0.1) + (0.25 if far else 0),
                               "lon": CENTER[1] + self.rng.uniform(-0.1, 0.1)})
        self.repeaters = [n for n in self.nodes if n["type"] == 2]
        self.observers = ["Demo Observer North", "Demo Observer South", "Station radio"]
        self._task: asyncio.Task | None = None

    @property
    def fleet(self) -> list[dict[str, Any]]:
        """Demo repeaters the station 'owns'. The Ridge Top one is offline."""
        return [self.repeaters[i] for i in (0, 1, 2, 3)]

    def advert(self, node: dict[str, Any], hops: int) -> bytes:
        app = decoder.encode_advert_app_data(node["type"], node["name"], node["lat"], node["lon"])
        payload = node["key"] + struct.pack("<I", int(time.time())) + bytes(64) + app
        return decoder.encode_packet(1, 0x04, self._path(hops), payload)

    def _path(self, hops: int) -> list[bytes]:
        live = [r for r in self.repeaters if r is not self.repeaters[0]]
        return [self.rng.choice(live)["key"][:1] for _ in range(hops)]

    def group_text(self) -> bytes:
        sender = self.rng.choice([n for n in self.nodes if n["type"] == 1])
        key = PUBLIC_CHANNEL_KEY if self.rng.random() < 0.5 else hashtag_key("#testing")
        payload = encrypt_group_text(key, int(time.time()), sender["name"], self.rng.choice(CHATTER))
        return decoder.encode_packet(1, 0x05, self._path(self.rng.randint(0, 4)), payload)

    def direct(self) -> bytes:
        a, b = self.rng.sample([n for n in self.nodes if n is not self.repeaters[0]], 2)
        payload = a["key"][:1] + b["key"][:1] + bytes(self.rng.getrandbits(8) for _ in range(18))
        ptype = self.rng.choice([0x02, 0x02, 0x00, 0x01])
        return decoder.encode_packet(2, ptype, self._path(self.rng.randint(1, 3)), payload)

    def ack(self) -> bytes:
        return decoder.encode_packet(2, 0x03, self._path(self.rng.randint(0, 2)), bytes(self.rng.getrandbits(8) for _ in range(4)))

    def emit(self, raw: bytes, at: float | None = None) -> None:
        n = self.rng.randint(1, 3)
        for obs in self.rng.sample(self.observers, n):
            self.ingest.handle(raw, obs, "demo", round(self.rng.uniform(-12, 11) * 4) / 4,
                               self.rng.randint(-120, -60), received_at=at)

    def one(self, at: float | None = None) -> None:
        roll = self.rng.random()
        if roll < 0.25:
            node = self.rng.choice([n for n in self.nodes if n is not self.repeaters[0]])
            self.emit(self.advert(node, 0 if self.rng.random() < 0.4 else self.rng.randint(1, 4)), at)
        elif roll < 0.55:
            self.emit(self.group_text(), at)
        elif roll < 0.85:
            self.emit(self.direct(), at)
        else:
            self.emit(self.ack(), at)

    def backfill(self, hours: int = 24, per_hour: int = 40) -> None:
        now = time.time()
        for node in self.nodes:
            self.emit(self.advert(node, 0), now - hours * 3600)
        for _ in range(hours * per_hour):
            self.one(now - self.rng.uniform(0, hours * 3600))

    def start(self) -> None:
        async def run():
            while True:
                await asyncio.sleep(self.rng.uniform(1.5, 5))
                self.one()

        self._task = asyncio.create_task(run(), name="demo")


class DemoRadio:
    """Stands in for RadioLink in demo mode. Answers fleet requests with made-up values."""

    def __init__(self, net: DemoNetwork):
        self.net = net
        self.state = "connected"
        self.cfg = None
        self._battery = {n["key"].hex(): 4150 - i * 220 for i, n in enumerate(net.fleet)}
        self._since = time.time()

    enabled = True
    connected = True
    error = None

    enabled = True
    shared = False
    disconnected_since = None
    reconnects = 0

    async def health(self, max_age: float = 60) -> dict[str, Any]:
        return {"connected": True, "reconnects": 0, "connected_at": self._since, "disconnected_since": None,
                "error": None, "model": "Demo radio", "firmware": "v1.11.0", "battery_mv": 4012, "uptime_s": 86400,
                "queue": 0, "noise_floor": -112, "last_rssi": -88, "last_snr": 7.5, "tx_air_s": 41, "rx_air_s": 1830}

    def info(self) -> dict[str, Any]:
        return {"transport": "demo", "state": "connected", "error": None, "name": "Demo station radio",
                "public_key": _key("station").hex(), "lat": CENTER[0], "lon": CENTER[1], "connected_at": self._since}

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def _check(self, key: str) -> None:
        from .radio import RadioError

        if key == self.net.repeaters[0]["key"].hex():
            raise RadioError("No status reply.")

    async def login(self, key: str, password: str) -> None:
        await asyncio.sleep(0.2)
        self._check(key)

    async def status(self, key: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        self._check(key)
        self._battery[key] = self._battery.get(key, 4000) - random.randint(0, 25)
        return {"bat": self._battery[key], "tx_queue_len": 0, "noise_floor": random.randint(-116, -104),
                "last_rssi": random.randint(-110, -70), "nb_recv": random.randint(5000, 9000),
                "nb_sent": random.randint(2000, 4000), "airtime": random.randint(600, 2400),
                "uptime": random.randint(86400, 40 * 86400), "last_snr": random.randint(-20, 40) / 4,
                "flood_dups": random.randint(100, 900), "direct_dups": random.randint(0, 50),
                "rx_airtime": random.randint(2000, 8000), "recv_errors": random.randint(0, 30)}

    async def neighbours(self, key: str) -> list[dict[str, Any]]:
        self._check(key)
        others = [n for n in self.net.repeaters if n["key"].hex() != key]
        return [{"pubkey": n["key"][:4].hex(), "secs_ago": random.randint(30, 3000),
                 "snr": random.randint(-40, 40) / 4} for n in random.sample(others, 3)]

    # ---- reach: made-up answers shaped like the real radio's -------------------
    async def trace(self, path: list[str], timeout: float = 20) -> dict[str, Any]:
        from .radio import RadioError

        await asyncio.sleep(0.4)
        known = {n["key"][: len(path[0]) // 2].hex() for n in self.net.repeaters} if path else set()
        if not path or any(h not in known for h in path):
            raise RadioError("The trace didn't come back. A repeater on the way may be out of range.")
        return {"hops": [{"hash": h, "snr": round(random.uniform(-6, 11), 2)} for h in path],
                "final_snr": round(random.uniform(-4, 10), 2)}

    async def path_discovery(self, public_key: str, timeout: float = 30) -> dict[str, Any]:
        await asyncio.sleep(0.5)
        hops = [n["key"][:1].hex() for n in random.sample(self.net.repeaters[1:], 2)]
        return {"out_path": hops, "in_path": list(reversed(hops))}

    async def who_hears_me(self, wait: float = 10) -> list[dict[str, Any]]:
        await asyncio.sleep(min(wait, 1))
        heard = self.net.repeaters[1:5]
        return sorted(({"key": n["key"].hex(), "node_type": 2, "snr_them": round(random.uniform(-8, 12), 2),
                        "snr_us": round(random.uniform(-8, 12), 2), "rssi_us": random.randint(-115, -70)}
                       for n in heard), key=lambda r: -r["snr_them"])

    # ---- channels: a pretend radio with 8 slots; sent messages come back as if heard -----
    _slots: list[dict[str, Any]] | None = None

    async def channel_slots(self) -> list[dict[str, Any]]:
        if self._slots is None:
            self._slots = [{"idx": 0, "name": "Public", "key": PUBLIC_CHANNEL_KEY.hex()},
                           {"idx": 1, "name": "#testing", "key": hashtag_key("#testing").hex()}]
            self._slots += [{"idx": i, "name": "", "key": "00" * 16} for i in range(2, 8)]
        return self._slots

    async def find_slot(self, key: bytes) -> int | None:
        return next((s["idx"] for s in await self.channel_slots() if s["key"] == key.hex()), None)

    async def add_channel(self, name: str, key: bytes) -> int:
        from .radio import RadioError

        slots = await self.channel_slots()
        found = await self.find_slot(key)
        if found is not None:
            return found
        free = next((s for s in slots if not s["name"]), None)
        if free is None:
            raise RadioError("Every channel slot on the station radio is in use.")
        free.update(name=name[:31], key=key.hex())
        return free["idx"]

    async def send_channel(self, key: bytes, text: str) -> int:
        from .radio import RadioError

        idx = await self.find_slot(key)
        if idx is None:
            raise RadioError("That channel isn't set up on the station radio.")
        payload = encrypt_group_text(key, int(time.time()), "Demo station", text)
        self.net.emit(decoder.encode_packet(1, 0x05, self.net._path(1), payload))
        return idx

    async def command(self, key: str, cmd: str, timeout: float = 15) -> str:
        self._check(key)
        if cmd == "ver":
            idx = [n["key"].hex() for n in self.net.fleet].index(key) if key in [n["key"].hex() for n in self.net.fleet] else 0
            return "v1.11.0" if idx != 3 else "v1.9.1"
        if cmd.startswith("get "):
            return "> 12"
        if cmd.startswith("set "):
            return "OK"
        return "Unknown command"
