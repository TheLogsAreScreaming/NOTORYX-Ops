import struct

import pytest

from notoryx_ops import decoder
from notoryx_ops.channels import (
    PUBLIC_CHANNEL_KEY,
    channel_hash,
    decrypt_group_text,
    encrypt_group_text,
    hashtag_key,
    parse_channel,
)

KEY = bytes(range(32))


def advert_payload(name="Ridge", node_type=2, lat=44.123456, lon=-72.654321):
    app = decoder.encode_advert_app_data(node_type, name, lat, lon)
    return KEY + struct.pack("<I", 1_700_000_000) + bytes(64) + app


def test_header_route_and_type():
    raw = decoder.encode_packet(1, 0x04, [b"\xaa", b"\xbb"], advert_payload())
    p = decoder.decode(raw)
    assert p.route == "flood"
    assert p.payload_type == "advert"
    assert p.path == ["aa", "bb"]
    assert p.hops == 2
    assert p.hash_size == 1
    assert p.advert.name == "Ridge"
    assert p.advert.node_type == "repeater"
    assert p.advert.lat == pytest.approx(44.123456)
    assert p.advert.lon == pytest.approx(-72.654321)
    assert p.advert.public_key == KEY.hex()


def test_hex_input_and_known_vector():
    # header 0x11 = flood (1) + advert (4 << 2); one hop "7f"; then a short payload
    p = decoder.decode("11017f" + "00" * 4)
    assert (p.route, p.payload_type, p.path) == ("flood", "advert", ["7f"])
    assert p.advert is None  # too short to be a real advert


def test_transport_codes():
    raw = decoder.encode_packet(0, 0x05, [], b"\x11\x22\x33" + bytes(16), transport=(0x1234, 0xBEEF))
    p = decoder.decode(raw)
    assert p.route == "transport_flood"
    assert p.transport_codes == (0x1234, 0xBEEF)
    assert p.channel_hash == "11"


def test_multibyte_hashes():
    raw = decoder.encode_packet(2, 0x02, [b"\x01\x02", b"\x03\x04", b"\x05\x06"], b"\xaa\xbb" + bytes(18))
    p = decoder.decode(raw)
    assert p.hash_size == 2
    assert p.path == ["0102", "0304", "0506"]
    assert (p.dest_hash, p.src_hash) == ("aa", "bb")


def test_same_packet_same_hash_on_any_path():
    payload = b"\x01\x02" + bytes(20)
    a = decoder.decode(decoder.encode_packet(1, 0x02, [b"\x01"], payload))
    b = decoder.decode(decoder.encode_packet(1, 0x02, [b"\x01", b"\x09", b"\x07"], payload))
    assert a.packet_hash == b.packet_hash
    c = decoder.decode(decoder.encode_packet(1, 0x02, [b"\x01"], b"\x01\x03" + bytes(20)))
    assert c.packet_hash != a.packet_hash


@pytest.mark.parametrize("bad", ["", "zz", "01", "11" + "3f" + "00" * 4, "c0"])
def test_bad_packets_raise(bad):
    with pytest.raises(decoder.DecodeError):
        decoder.decode(bad)


def test_reserved_hash_size():
    with pytest.raises(decoder.DecodeError):
        decoder.decode(bytes([0x09, 0xC1, 0, 0, 0, 0]))


def test_advert_without_location_or_name():
    app = bytes([0x01])
    p = decoder.decode(decoder.encode_packet(1, 0x04, [], KEY + bytes(4) + bytes(64) + app))
    assert p.advert.node_type == "companion"
    assert p.advert.name is None and p.advert.lat is None


def test_ack_and_trace():
    ack = decoder.decode(decoder.encode_packet(2, 0x03, [], b"\xde\xad\xbe\xef"))
    assert ack.ack_crc == "deadbeef"
    trace_payload = struct.pack("<IIB", 7, 9, 0) + b"\x4f\xa2\x1c"
    tr = decoder.decode(decoder.encode_packet(2, 0x09, [b"\x10", b"\xf8"], trace_payload))
    assert tr.extra["trace_route"] == ["4f", "a2", "1c"]
    assert tr.extra["trace_snrs"] == [4.0, -2.0]


def test_hashtag_channel_hash_and_roundtrip():
    key = hashtag_key("testing")
    assert key == hashtag_key("#testing")
    assert len(key) == 16
    payload = encrypt_group_text(key, 1_700_000_000, "Hiker", "hello there")
    assert payload[0:1].hex() == channel_hash(key)
    msg = decrypt_group_text(payload, [parse_channel("public"), parse_channel("#testing")])
    assert msg.channel == "#testing"
    assert (msg.sender, msg.text, msg.timestamp) == ("Hiker", "hello there", 1_700_000_000)


def test_wrong_key_or_tampered_mac_gives_nothing():
    payload = encrypt_group_text(PUBLIC_CHANNEL_KEY, 1, "A", "b")
    assert decrypt_group_text(payload, [parse_channel("#other")]) is None
    tampered = payload[:1] + bytes([payload[1] ^ 1]) + payload[2:]
    assert decrypt_group_text(tampered, [parse_channel("public")]) is None


def test_parse_channel_forms():
    assert parse_channel("public").key == PUBLIC_CHANNEL_KEY
    c = parse_channel("Team=" + "00" * 16)
    assert c.name == "Team" and c.key == bytes(16)
    with pytest.raises(ValueError):
        parse_channel("Team=00")
    with pytest.raises(ValueError):
        parse_channel("nonsense")


def test_advert_with_position_off_the_globe_is_dropped():
    # A damaged advert seen on a real station decoded to 2019.17, -1981.93 and blanked the map.
    app = decoder.encode_advert_app_data(1, "Bogus", 2019.174185, -1981.933379)
    p = decoder.decode(decoder.encode_packet(1, 0x04, [], KEY + bytes(4) + bytes(64) + app))
    assert p.advert.lat is None and p.advert.lon is None
    assert p.advert.name == "Bogus"
    good = decoder.encode_advert_app_data(1, "Shelton", 41.327695, -73.093825)
    q = decoder.decode(decoder.encode_packet(1, 0x04, [], KEY + bytes(4) + bytes(64) + good))
    assert (q.advert.lat, q.advert.lon) == (41.327695, -73.093825)


def test_valid_position_edges():
    assert decoder.valid_position(90, 180) and decoder.valid_position(-90, -180)
    assert not decoder.valid_position(90.1, 0) and not decoder.valid_position(0, -180.5)
    assert not decoder.valid_position(None, 1) and not decoder.valid_position(1, None)
