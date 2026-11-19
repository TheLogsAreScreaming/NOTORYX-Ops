# SPDX-License-Identifier: GPL-3.0-only
"""Remote administration of Meshtastic nodes over the air (PKI admin, firmware 2.5 and newer).

The target node must list the station radio's public key under Security -> Admin keys.
Every request is a PKI-encrypted admin message; the library remembers the session passkey
each node hands back and adds it to later requests.
"""

from __future__ import annotations

import threading
from typing import Any

from google.protobuf import json_format
from meshtastic.protobuf import admin_pb2, localonly_pb2, portnums_pb2

CONFIG_SECTIONS = [f.name for f in localonly_pb2.LocalConfig.DESCRIPTOR.fields if f.name != "version"]
MODULE_SECTIONS = [f.name for f in localonly_pb2.LocalModuleConfig.DESCRIPTOR.fields if f.name != "version"]
# Module section name -> AdminMessage.ModuleConfigType value.
_MODULE_TYPE = {
    "mqtt": "MQTT_CONFIG", "serial": "SERIAL_CONFIG", "external_notification": "EXTNOTIF_CONFIG",
    "store_forward": "STOREFORWARD_CONFIG", "range_test": "RANGETEST_CONFIG", "telemetry": "TELEMETRY_CONFIG",
    "canned_message": "CANNEDMSG_CONFIG", "audio": "AUDIO_CONFIG", "remote_hardware": "REMOTEHARDWARE_CONFIG",
    "neighbor_info": "NEIGHBORINFO_CONFIG", "ambient_lighting": "AMBIENTLIGHTING_CONFIG",
    "detection_sensor": "DETECTIONSENSOR_CONFIG", "paxcounter": "PAXCOUNTER_CONFIG",
    "statusmessage": "STATUSMESSAGE_CONFIG", "traffic_management": "TRAFFICMANAGEMENT_CONFIG", "tak": "TAK_CONFIG",
}
MODULE_SECTIONS = [s for s in MODULE_SECTIONS if s in _MODULE_TYPE]
SECTIONS = CONFIG_SECTIONS + MODULE_SECTIONS

# Never read back to the browser, never written from it.
SECRET_FIELDS = {("security", "private_key"), ("network", "wifi_psk"), ("mqtt", "password")}
# Changing these from afar can cut the node off for good; refused.
LOCKOUT_FIELDS = {("security", "admin_key"), ("security", "public_key"), ("security", "is_managed"),
                  ("security", "admin_channel_enabled"), ("lora", "region"), ("lora", "tx_enabled")}
MIN_FIRMWARE = (2, 5)


class AdminError(RuntimeError):
    pass


def _to_dict(msg) -> dict[str, Any]:
    try:
        return json_format.MessageToDict(msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    except TypeError:  # older protobuf
        return json_format.MessageToDict(msg, preserving_proto_field_name=True, including_default_value_fields=True)


def redact(section: str, values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    for sec, field in SECRET_FIELDS:
        if sec == section and field in out:
            out[field] = "(hidden)" if out[field] else ""
    return out


def firmware_ok(version: str | None) -> bool:
    try:
        parts = tuple(int(x) for x in (version or "").split(".")[:2])
    except ValueError:
        return False
    return parts >= MIN_FIRMWARE


def section_message(section: str):
    """An empty protobuf message for one section, used to check field names and types."""
    if section in CONFIG_SECTIONS:
        return getattr(localonly_pb2.LocalConfig(), section).__class__()
    if section in MODULE_SECTIONS:
        return getattr(localonly_pb2.LocalModuleConfig(), section).__class__()
    raise AdminError(f"Unknown settings section {section!r}.")


def coerce(section: str, field: str, value: Any) -> Any:
    """Turn a value from the browser into what the protobuf field needs. Raises AdminError."""
    if (section, field) in SECRET_FIELDS:
        raise AdminError(f"{section}.{field} can't be changed from here.")
    if (section, field) in LOCKOUT_FIELDS:
        raise AdminError(f"{section}.{field} could lock you out of the node; change it on the node itself.")
    msg = section_message(section)
    fd = msg.DESCRIPTOR.fields_by_name.get(field)
    if fd is None:
        raise AdminError(f"{section} has no setting called {field!r}.")
    repeated = fd.is_repeated if hasattr(fd, "is_repeated") else fd.label == fd.LABEL_REPEATED  # protobuf 7 dropped .label
    if fd.message_type is not None or repeated:
        raise AdminError(f"{section}.{field} is a list or group; it can't be set here.")
    t = fd.type
    try:
        if fd.enum_type is not None:
            names = [v.name for v in fd.enum_type.values]
            if isinstance(value, str) and value in names:
                return value
            if isinstance(value, int) and value in [v.number for v in fd.enum_type.values]:
                return fd.enum_type.values_by_number[value].name
            raise AdminError(f"{section}.{field} must be one of: {', '.join(names)}.")
        if t == fd.TYPE_BOOL:
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "1", "yes", "on"):
                return True
            if str(value).lower() in ("false", "0", "no", "off"):
                return False
            raise ValueError
        if t in (fd.TYPE_FLOAT, fd.TYPE_DOUBLE):
            return float(value)
        if t == fd.TYPE_STRING:
            s = str(value)
            if len(s) > 200:
                raise AdminError(f"{section}.{field} is too long.")
            return s
        if t == fd.TYPE_BYTES:
            raise AdminError(f"{section}.{field} holds raw bytes and can't be set here.")
        n = int(value)
        if t in (fd.TYPE_UINT32, fd.TYPE_UINT64, fd.TYPE_FIXED32, fd.TYPE_FIXED64) and n < 0:
            raise ValueError
        return n
    except (TypeError, ValueError) as exc:
        raise AdminError(f"{section}.{field}: {value!r} isn't a valid value.") from exc


class RemoteAdmin:
    """Admin requests to remote nodes through a connected Meshtastic interface."""

    def __init__(self, iface, timeout: float = 45):
        self.iface = iface
        self.timeout = timeout

    # ---- plumbing ------------------------------------------------------------
    def _send(self, node_num: int, msg: admin_pb2.AdminMessage, want_reply: bool) -> dict[str, Any] | None:
        """Send one admin message and wait for its reply (or, for changes, the node's ACK)."""
        entry = self.iface._getOrCreateByNum(node_num)
        if entry.get("adminSessionPassKey"):
            msg.session_passkey = entry["adminSessionPassKey"]
        done = threading.Event()
        box: dict[str, Any] = {}

        def on_response(packet: dict[str, Any]) -> None:
            box["p"] = packet
            done.set()

        self.iface.sendData(
            msg, node_num, portNum=portnums_pb2.PortNum.ADMIN_APP, wantAck=True, wantResponse=want_reply,
            onResponse=on_response, onResponseAckPermitted=not want_reply, channelIndex=0, pkiEncrypted=True,
        )
        if not done.wait(self.timeout):
            raise AdminError("No answer from the node. It may be out of range, asleep, or not trust this station's key.")
        p = box["p"]
        routing = (p.get("decoded") or {}).get("routing")
        if routing and routing.get("errorReason", "NONE") != "NONE":
            reason = routing["errorReason"]
            hint = {
                "NOT_AUTHORIZED": "The node doesn't list this station's public key under Security → Admin keys.",
                "PKI_FAILED": "Key exchange failed. The node may not know this station's public key yet; wait for a node-info exchange.",
                "PKI_UNKNOWN_PUBKEY": "The node doesn't know this station's public key yet; wait for a node-info exchange.",
                "NO_RESPONSE": "The node didn't answer.",
                "ADMIN_BAD_SESSION_KEY": "The session expired. Try again.",
                "ADMIN_PUBLIC_KEY_UNAUTHORIZED": "The node doesn't list this station's public key under Security → Admin keys.",
            }.get(reason, "")
            raise AdminError(f"The node refused: {reason}. {hint}".strip())
        return p

    @staticmethod
    def _admin(p: dict[str, Any]) -> admin_pb2.AdminMessage:
        raw = ((p.get("decoded") or {}).get("admin") or {}).get("raw")
        if raw is None:
            raise AdminError("The node's answer wasn't an admin reply.")
        return raw

    # ---- reads ------------------------------------------------------------------
    def metadata(self, node_num: int) -> dict[str, Any]:
        m = admin_pb2.AdminMessage(get_device_metadata_request=True)
        return _to_dict(self._admin(self._send(node_num, m, True)).get_device_metadata_response)

    def owner(self, node_num: int) -> dict[str, Any]:
        m = admin_pb2.AdminMessage(get_owner_request=True)
        d = _to_dict(self._admin(self._send(node_num, m, True)).get_owner_response)
        d.pop("public_key", None)
        return d

    def get(self, node_num: int, section: str) -> dict[str, Any]:
        m = admin_pb2.AdminMessage()
        if section in CONFIG_SECTIONS:
            m.get_config_request = admin_pb2.AdminMessage.ConfigType.Value(section.upper() + "_CONFIG")
            reply = self._admin(self._send(node_num, m, True)).get_config_response
        elif section in MODULE_SECTIONS:
            m.get_module_config_request = admin_pb2.AdminMessage.ModuleConfigType.Value(_MODULE_TYPE[section])
            reply = self._admin(self._send(node_num, m, True)).get_module_config_response
        else:
            raise AdminError(f"Unknown settings section {section!r}.")
        if reply.WhichOneof("payload_variant") != section:
            raise AdminError("The node answered with a different section. Try again.")
        return _to_dict(getattr(reply, section))

    # ---- changes ----------------------------------------------------------------
    def plan(self, current: dict[str, Any], section: str, changes: dict[str, Any]) -> list[dict[str, Any]]:
        """What would change: [{field, before, after}] for fields that differ."""
        out = []
        for field, value in changes.items():
            v = coerce(section, field, value)
            before = current.get(field)
            if before != v:
                out.append({"field": field, "before": before, "after": v})
        return out

    def set(self, node_num: int, section: str, current: dict[str, Any], changes: dict[str, Any]) -> list[dict[str, Any]]:
        """Write changed fields on top of the section as last read. Returns the changes made."""
        diff = self.plan(current, section, changes)
        if not diff:
            return []
        # The firmware replaces the whole section, so hidden fields (Wi-Fi password, private key) go
        # back exactly as the node sent them. They're only hidden from the browser, never dropped.
        values = dict(current)
        for d in diff:
            values[d["field"]] = d["after"]
        sub = section_message(section)
        json_format.ParseDict(values, sub, ignore_unknown_fields=True)
        m = admin_pb2.AdminMessage()
        if section in CONFIG_SECTIONS:
            getattr(m.set_config, section).CopyFrom(sub)
        else:
            getattr(m.set_module_config, section).CopyFrom(sub)
        self._send(node_num, m, False)
        return diff

    def set_owner(self, node_num: int, long_name: str | None, short_name: str | None) -> None:
        m = admin_pb2.AdminMessage()
        if long_name is not None:
            if not 1 <= len(long_name) <= 39:
                raise AdminError("Long names are 1-39 characters.")
            m.set_owner.long_name = long_name
        if short_name is not None:
            if not 1 <= len(short_name.encode()) <= 4:
                raise AdminError("Short names are 1-4 bytes.")
            m.set_owner.short_name = short_name
        self._send(node_num, m, False)

    def favorite(self, node_num: int, target_num: int, on: bool = True) -> None:
        m = admin_pb2.AdminMessage()
        if on:
            m.set_favorite_node = target_num
        else:
            m.remove_favorite_node = target_num
        self._send(node_num, m, False)

    def reboot(self, node_num: int, seconds: int = 10) -> None:
        self._send(node_num, admin_pb2.AdminMessage(reboot_seconds=max(5, min(int(seconds), 600))), False)
