# SPDX-License-Identifier: GPL-3.0-only
"""A pretend Meshtastic interface that answers admin requests like firmware would."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from meshtastic.protobuf import admin_pb2, config_pb2, localonly_pb2, mesh_pb2

ME = 0x0A0B0C0D
RELAY_A = 0x11111111
RELAY_B = 0x22222222
OLD = 0x33333333  # firmware too old for PKI admin
LOCKED = 0x44444444  # doesn't trust the station's key


class FakeIface:
    def __init__(self):
        now = time.time()
        self.myInfo = SimpleNamespace(my_node_num=ME)
        self.metadata = SimpleNamespace(firmware_version="2.7.15")
        self.nodesByNum = {
            ME: {"user": {"longName": "Station", "shortName": "STN", "hwModel": "HELTEC_V3"}},
            RELAY_A: {"user": {"longName": "Ridge relay", "shortName": "RR", "hwModel": "RAK4631", "role": "ROUTER", "publicKey": "a"},
                      "lastHeard": now - 60, "snr": 6.5, "hopsAway": 0, "position": {"latitude": 44.2, "longitude": -72.5}},
            RELAY_B: {"user": {"longName": "Valley relay", "shortName": "VR", "hwModel": "RAK4631", "role": "ROUTER", "publicKey": "b"},
                      "lastHeard": now - 600, "snr": -3.0, "hopsAway": 1},
            OLD: {"user": {"longName": "Old one", "shortName": "OLD", "hwModel": "TBEAM"}, "lastHeard": now - 3000},
            LOCKED: {"user": {"longName": "Locked", "shortName": "LK", "hwModel": "RAK4631", "publicKey": "c"}, "lastHeard": now - 30},
        }
        sec = config_pb2.Config.SecurityConfig(public_key=b"\x01" * 32)
        self.localNode = SimpleNamespace(
            localConfig=localonly_pb2.LocalConfig(security=sec),
            channels=[SimpleNamespace(index=0, role=1, settings=SimpleNamespace(name="")),
                      SimpleNamespace(index=1, role=2, settings=SimpleNamespace(name="ops")),
                      SimpleNamespace(index=2, role=0, settings=SimpleNamespace(name=""))],
        )
        self.configs = {}
        for n in (RELAY_A, RELAY_B, LOCKED):
            c = localonly_pb2.LocalConfig()
            c.device.role = config_pb2.Config.DeviceConfig.Role.ROUTER
            c.lora.hop_limit = 3
            c.lora.region = config_pb2.Config.LoRaConfig.RegionCode.US
            c.network.wifi_psk = "secret-wifi"
            c.security.private_key = b"\x02" * 32
            c.position.position_broadcast_secs = 900
            self.configs[n] = c
        self.sent_admin = []
        self.sent_text = []
        self.favorites = {}
        self.closed = False

    def _getOrCreateByNum(self, num):
        return self.nodesByNum.setdefault(num, {})

    def sendData(self, msg, dest, portNum=None, wantAck=False, wantResponse=False, onResponse=None,
                 onResponseAckPermitted=False, channelIndex=0, pkiEncrypted=False):
        assert pkiEncrypted, "admin must be PKI"
        self.sent_admin.append((dest, msg))
        reply = self._answer(dest, msg, wantResponse)
        threading.Timer(0.01, onResponse, args=(reply,)).start()

    def _answer(self, dest, msg, want):
        def nak(reason):
            return {"from": dest, "decoded": {"routing": {"errorReason": reason}}}

        if dest == LOCKED:
            return nak("ADMIN_PUBLIC_KEY_UNAUTHORIZED")
        r = admin_pb2.AdminMessage(session_passkey=b"pk")
        self.nodesByNum[dest]["adminSessionPassKey"] = b"pk"
        kind = msg.WhichOneof("payload_variant")
        if kind == "get_device_metadata_request":
            r.get_device_metadata_response.CopyFrom(mesh_pb2.DeviceMetadata(firmware_version="2.3.2" if dest == OLD else "2.7.15"))
        elif kind == "get_owner_request":
            u = self.nodesByNum[dest]["user"]
            r.get_owner_response.long_name = u["longName"]
            r.get_owner_response.short_name = u["shortName"]
        elif kind == "get_config_request":
            name = admin_pb2.AdminMessage.ConfigType.Name(msg.get_config_request).removesuffix("_CONFIG").lower()
            getattr(r.get_config_response, name).CopyFrom(getattr(self.configs[dest], name))
        elif kind == "set_config":
            sec = msg.set_config.WhichOneof("payload_variant")
            getattr(self.configs[dest], sec).CopyFrom(getattr(msg.set_config, sec))
            return {"from": dest, "decoded": {"routing": {"errorReason": "NONE"}}}
        elif kind == "set_owner":
            self.nodesByNum[dest]["user"]["longName"] = msg.set_owner.long_name or self.nodesByNum[dest]["user"]["longName"]
            return {"from": dest, "decoded": {"routing": {"errorReason": "NONE"}}}
        elif kind == "set_favorite_node":
            self.favorites.setdefault(dest, set()).add(msg.set_favorite_node)
            return {"from": dest, "decoded": {"routing": {"errorReason": "NONE"}}}
        elif kind == "remove_favorite_node":
            self.favorites.setdefault(dest, set()).discard(msg.remove_favorite_node)
            return {"from": dest, "decoded": {"routing": {"errorReason": "NONE"}}}
        elif kind == "reboot_seconds":
            return {"from": dest, "decoded": {"routing": {"errorReason": "NONE"}}}
        else:
            return nak("BAD_REQUEST")
        return {"from": dest, "decoded": {"admin": {"raw": r}}}

    def sendText(self, text, channelIndex=0):
        self.sent_text.append((channelIndex, text))

    def close(self):
        self.closed = True
