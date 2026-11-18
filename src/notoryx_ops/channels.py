"""Group channel keys and message decryption.

Channel messages use AES-128 in ECB mode with a 2-byte HMAC-SHA256 tag. The one-byte
channel hash on the air is the first byte of SHA-256 over the key.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

from Crypto.Cipher import AES

PUBLIC_CHANNEL_KEY = bytes.fromhex("8b3387e9c5cdea6ac9e5edbaa115cd72")


@dataclass(frozen=True)
class Channel:
    name: str
    key: bytes

    @property
    def hash(self) -> str:
        return channel_hash(self.key)


@dataclass
class GroupMessage:
    channel: str
    timestamp: int
    sender: str | None
    text: str


def channel_hash(key: bytes) -> str:
    return hashlib.sha256(key).digest()[0:1].hex()


def hashtag_key(name: str) -> bytes:
    """Key for a hashtag channel such as #testing."""
    if not name.startswith("#"):
        name = "#" + name
    return hashlib.sha256(name.encode("utf-8")).digest()[:16]


def parse_channel(spec: str) -> Channel:
    """Parse a channel from config.

    Accepts "public", "#name", or "name=32 hex digit key".
    """
    spec = spec.strip()
    if spec.lower() == "public":
        return Channel("Public", PUBLIC_CHANNEL_KEY)
    if spec.startswith("#"):
        return Channel(spec, hashtag_key(spec))
    if "=" in spec:
        name, hexkey = spec.split("=", 1)
        key = bytes.fromhex(hexkey.strip())
        if len(key) != 16:
            raise ValueError(f"channel key for {name} must be 16 bytes")
        return Channel(name.strip(), key)
    raise ValueError(f"cannot read channel {spec!r}")


def _mac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key.ljust(32, b"\x00"), data, hashlib.sha256).digest()[:2]


def encrypt_group_text(key: bytes, timestamp: int, sender: str, text: str) -> bytes:
    """Build a group text payload. Used by tests and the demo feed."""
    plain = struct.pack("<IB", timestamp, 0) + f"{sender}: {text}".encode()
    plain = plain.ljust((len(plain) + 15) // 16 * 16, b"\x00")
    cipher = AES.new(key, AES.MODE_ECB).encrypt(plain)
    return bytes.fromhex(channel_hash(key)) + _mac(key, cipher) + cipher


def decrypt_group_text(payload: bytes, channels: list[Channel]) -> GroupMessage | None:
    if len(payload) < 3 + 16:
        return None
    ch_hash = payload[0:1].hex()
    tag, cipher = payload[1:3], payload[3:]
    if len(cipher) % 16:
        return None
    for ch in channels:
        if ch.hash != ch_hash or not hmac.compare_digest(_mac(ch.key, cipher), tag):
            continue
        plain = AES.new(ch.key, AES.MODE_ECB).decrypt(cipher)
        ts = struct.unpack_from("<I", plain, 0)[0]
        body = plain[5:].split(b"\x00", 1)[0].decode("utf-8", "replace")
        sender, text = (body.split(": ", 1) + [""])[:2] if ": " in body else (None, body)
        return GroupMessage(ch.name, ts, sender, text)
    return None
