import asyncio
from types import SimpleNamespace

from notoryx_ops.ingest import Hub
from notoryx_ops.share import RadioShare, allowed_peer


class FakeRadio:
    connected = True

    def __init__(self):
        self.sent, self.tap, self.shared, self.ended = [], None, False, 0

    async def begin_share(self, tap):
        self.tap, self.shared = tap, True

    async def end_share(self):
        self.tap, self.shared = None, False
        self.ended += 1

    async def share_send(self, frame):
        self.sent.append(frame)


def test_only_local_addresses_may_share():
    assert allowed_peer("192.168.1.20") and allowed_peer("10.0.0.5") and allowed_peer("127.0.0.1")
    assert allowed_peer("::ffff:192.168.1.20") and allowed_peer("fe80::1%en0")
    assert not allowed_peer("8.8.8.8") and not allowed_peer("not an ip")


def test_frames_pass_both_ways_and_one_app_at_a_time():
    async def run():
        radio = FakeRadio()
        cfg = SimpleNamespace(share_enabled=True, share_host="127.0.0.1", share_port=0)
        share = RadioShare(cfg, SimpleNamespace(radio=radio, hub=Hub()))
        await share.start()
        port = share.server.sockets[0].getsockname()[1]

        r, w = await asyncio.open_connection("127.0.0.1", port)
        for _ in range(50):
            if radio.shared:
                break
            await asyncio.sleep(0.01)
        assert radio.shared and share.client == "127.0.0.1"

        # app -> radio, split across writes and with junk in front
        w.write(b"\x00junk<\x03\x00ab")
        await w.drain()
        w.write(b"c<\x01\x00z")
        await w.drain()
        for _ in range(50):
            if len(radio.sent) == 2:
                break
            await asyncio.sleep(0.01)
        assert radio.sent == [b"abc", b"z"]

        # radio -> app
        radio.tap(b"\x88hello")
        assert await asyncio.wait_for(r.readexactly(9), 2) == b">\x06\x00\x88hello"

        # a second app is turned away while the first is connected
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(r2.read(10), 2) == b""
        w2.close()

        w.close()
        for _ in range(50):
            if radio.ended:
                break
            await asyncio.sleep(0.01)
        assert radio.ended == 1 and not radio.shared and share.client is None
        await share.stop()

    asyncio.run(run())


def test_station_radio_refuses_requests_while_shared(tmp_path):
    import pytest

    from notoryx_ops import config
    from notoryx_ops.ingest import Ingest
    from notoryx_ops.radio import RadioError, RadioLink
    from notoryx_ops.store import Store

    cfg = config.load(tmp_path / "config.toml")
    link = RadioLink(cfg, Ingest(Store(tmp_path / "ops.sqlite"), [], Hub()))
    link.mc, link.state, link.shared = object(), "connected", True
    with pytest.raises(RadioError, match="sharing"):
        link._require()


def test_busy_port_gives_a_clear_reason():
    import socket

    async def run():
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        cfg = SimpleNamespace(share_enabled=True, share_host="127.0.0.1", share_port=port)
        share = RadioShare(cfg, SimpleNamespace(radio=FakeRadio(), hub=Hub()))
        await share.start()
        holder.close()
        assert share.server is None and "another program is using it" in share.error

    asyncio.run(run())
