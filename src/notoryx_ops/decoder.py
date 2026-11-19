"""Decoder for raw MeshCore over-the-air packets.

Written from the public MeshCore packet format. A packet is:

    header (1) | transport codes (4, only for transport routes) | path length (1) | path | payload

Header bits: 0-1 route type, 2-5 payload type, 6-7 payload version.
Path length byte: bits 0-5 are the hop count, bits 6-7 are (hash size - 1).
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import asdict, dataclass, field
from typing import Any

ROUTE_TYPES = {0: "transport_flood", 1: "flood", 2: "direct", 3: "transport_direct"}

PAYLOAD_TYPES = {
    0x00: "request",
    0x01: "response",
    0x02: "text",
    0x03: "ack",
    0x04: "advert",
    0x05: "group_text",
    0x06: "group_data",
    0x07: "anon_request",
    0x08: "path",
    0x09: "trace",
    0x0A: "multipart",
    0x0B: "control",
    0x0F: "raw_custom",
}

NODE_TYPES = {0: "unknown", 1: "companion", 2: "repeater", 3: "room", 4: "sensor"}

ADVERT_HAS_LOCATION = 0x10
ADVERT_HAS_FEATURE1 = 0x20
ADVERT_HAS_FEATURE2 = 0x40
ADVERT_HAS_NAME = 0x80


class DecodeError(ValueError):
    """Raised when bytes cannot be read as a MeshCore packet."""


@dataclass
class Advert:
    public_key: str
    timestamp: int
    signature: str
    node_type: str
    name: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class Packet:
    raw: str
    route: str
    payload_type: str
    payload_version: int
    transport_codes: tuple[int, int] | None
    hash_size: int
    path: list[str]
    payload: str
    packet_hash: str
    advert: Advert | None = None
    channel_hash: str | None = None
    dest_hash: str | None = None
    src_hash: str | None = None
    ack_crc: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def hops(self) -> int:
        return len(self.path)

    @property
    def is_flood(self) -> bool:
        return self.route in ("flood", "transport_flood")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hops"] = self.hops
        return d


def packet_hash(payload_type: int, payload: bytes, trace_path_len: int | None = None) -> str:
    """A stable id for the same packet heard on different paths.

    The path changes at every hop, so it is left out. Trace packets add the path length
    because their payload is the same on each leg.
    """
    h = hashlib.sha256()
    h.update(bytes([payload_type]))
    if trace_path_len is not None:
        h.update(struct.pack("<H", trace_path_len))
    h.update(payload)
    return h.hexdigest()[:16]


def decode(data: bytes | str) -> Packet:
    if isinstance(data, str):
        try:
            data = bytes.fromhex(data.strip())
        except ValueError as exc:
            raise DecodeError("not hex") from exc
    if len(data) < 2:
        raise DecodeError("packet too short")

    header = data[0]
    route_id = header & 0x03
    ptype_id = (header >> 2) & 0x0F
    version = (header >> 6) & 0x03
    i = 1

    transport = None
    if route_id in (0, 3):
        if len(data) < i + 4 + 1:
            raise DecodeError("missing transport codes")
        transport = struct.unpack_from("<HH", data, i)
        i += 4

    path_byte = data[i]
    i += 1
    hops = path_byte & 0x3F
    hash_size = (path_byte >> 6) + 1
    if hash_size == 4:
        raise DecodeError("reserved hash size")
    path_bytes = hops * hash_size
    if len(data) < i + path_bytes:
        raise DecodeError("path longer than packet")
    path = [data[i + n * hash_size : i + (n + 1) * hash_size].hex() for n in range(hops)]
    i += path_bytes
    payload = data[i:]

    pkt = Packet(
        raw=data.hex(),
        route=ROUTE_TYPES[route_id],
        payload_type=PAYLOAD_TYPES.get(ptype_id, f"type_{ptype_id}"),
        payload_version=version,
        transport_codes=transport,
        hash_size=hash_size,
        path=path,
        payload=payload.hex(),
        packet_hash=packet_hash(ptype_id, payload, hops if ptype_id == 0x09 else None),
    )
    _decode_payload(pkt, ptype_id, payload)
    return pkt


def _decode_payload(pkt: Packet, ptype: int, p: bytes) -> None:
    if ptype == 0x04:
        pkt.advert = decode_advert(p)
    elif ptype in (0x05, 0x06) and len(p) >= 3:
        pkt.channel_hash = p[0:1].hex()
    elif ptype in (0x00, 0x01, 0x02, 0x08) and len(p) >= 4:
        pkt.dest_hash = p[0:1].hex()
        pkt.src_hash = p[1:2].hex()
    elif ptype == 0x07 and len(p) >= 33:
        pkt.dest_hash = p[0:1].hex()
        pkt.extra["sender_key"] = p[1:33].hex()
    elif ptype == 0x03 and len(p) >= 4:
        pkt.ack_crc = p[0:4].hex()
    elif ptype == 0x09 and len(p) >= 9:
        tag, auth, flags = struct.unpack_from("<IIB", p, 0)
        pkt.extra.update({"trace_tag": tag, "trace_flags": flags})
        # For traces, the packet path field holds the SNR of each hop (signed, x4).
        pkt.extra["trace_snrs"] = [((int(h, 16) ^ 0x80) - 0x80) / 4 for h in pkt.path]
        size = 1 << (flags & 3)
        hashes = p[9:]
        pkt.extra["trace_route"] = [hashes[n : n + size].hex() for n in range(0, len(hashes) - size + 1, size)]


def valid_position(lat: float | None, lon: float | None) -> bool:
    """Whether a position is a real place: both set and inside -90..90 and -180..180."""
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def decode_advert(p: bytes) -> Advert | None:
    if len(p) < 32 + 4 + 64 + 1:
        return None
    key = p[0:32]
    ts = struct.unpack_from("<I", p, 32)[0]
    sig = p[36:100]
    app = p[100:]
    flags = app[0]
    j = 1
    lat = lon = None
    if flags & ADVERT_HAS_LOCATION and len(app) >= j + 8:
        la, lo = struct.unpack_from("<ii", app, j)
        lat, lon = la / 1e6, lo / 1e6
        j += 8
    if flags & ADVERT_HAS_FEATURE1:
        j += 2
    if flags & ADVERT_HAS_FEATURE2:
        j += 2
    name = None
    if flags & ADVERT_HAS_NAME and len(app) > j:
        name = app[j:].split(b"\x00", 1)[0].decode("utf-8", "replace").strip() or None
    if (lat == 0 and lon == 0) or not valid_position(lat, lon):
        # 0,0 means "not set"; anything off the globe is a damaged or bogus advert.
        lat = lon = None
    return Advert(
        public_key=key.hex(),
        timestamp=ts,
        signature=sig.hex(),
        node_type=NODE_TYPES.get(flags & 0x0F, "unknown"),
        name=name,
        lat=lat,
        lon=lon,
    )


def encode_advert_app_data(node_type: int, name: str | None, lat: float | None, lon: float | None) -> bytes:
    """Build advert app data. Used by tests and the demo feed."""
    flags = node_type & 0x0F
    out = b""
    if lat is not None and lon is not None:
        flags |= ADVERT_HAS_LOCATION
        out += struct.pack("<ii", round(lat * 1e6), round(lon * 1e6))
    if name:
        flags |= ADVERT_HAS_NAME
        out += name.encode()
    return bytes([flags]) + out


def encode_packet(route: int, ptype: int, path: list[bytes], payload: bytes, transport=None, version=0) -> bytes:
    """Build a raw packet. Used by tests and the demo feed."""
    hash_size = len(path[0]) if path else 1
    header = (route & 3) | ((ptype & 0x0F) << 2) | ((version & 3) << 6)
    out = bytes([header])
    if route in (0, 3):
        out += struct.pack("<HH", *(transport or (0, 0)))
    out += bytes([len(path) | ((hash_size - 1) << 6)])
    out += b"".join(path)
    return out + payload
