"""Load EA-generated channel metadata and decode configurable UDP packets."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tripcompiler.models import FieldSpec, Scalar

_STRUCT_CODES = {
    "boolean": "?",
    "uint8": "B",
    "int8": "b",
    "uint16": "H",
    "int16": "h",
    "uint32": "I",
    "int32": "i",
    "uint64": "Q",
    "int64": "q",
    "float32": "f",
    "float64": "d",
    "fourcc": "4s",
}


class SchemaError(ValueError):
    """The EA channel or packet schema is invalid or incompatible."""


class PacketSizeError(ValueError):
    """A datagram length does not match the configured packet layout."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"Cannot read JSON schema {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaError(f"Schema root must be an object: {path}")
    return value


@dataclass(frozen=True, slots=True)
class PacketDecoder:
    """A decoder compiled from one EA packet structure."""

    packet_id: str
    fields: tuple[FieldSpec, ...]
    _struct: struct.Struct

    @property
    def size(self) -> int:
        return self._struct.size

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.channel_id for field in self.fields)

    def decode(self, payload: bytes) -> dict[str, Scalar]:
        if len(payload) != self.size:
            raise PacketSizeError(
                f"Expected {self.size} bytes for {self.packet_id}, received {len(payload)}"
            )
        values = self._struct.unpack(payload)
        decoded: dict[str, Scalar] = {}
        for field, value in zip(self.fields, values, strict=True):
            if field.channel_type == "fourcc":
                decoded[field.channel_id] = bytes(value).decode("ascii", errors="replace")
            else:
                decoded[field.channel_id] = value
        return decoded


def load_decoder(
    channels_path: Path,
    structure_path: Path,
    packet_id: str = "session_update",
) -> PacketDecoder:
    """Create a packed little-endian decoder from EA's generated JSON files."""

    channels_document = _load_object(channels_path)
    structure_document = _load_object(structure_path)

    raw_channels = channels_document.get("channels")
    if not isinstance(raw_channels, list):
        raise SchemaError("channels.json must contain a channels list")

    registry: dict[str, FieldSpec] = {}
    for raw in raw_channels:
        if not isinstance(raw, dict):
            raise SchemaError("Every channel must be an object")
        channel_id = raw.get("id")
        channel_type = raw.get("type")
        if not isinstance(channel_id, str) or not isinstance(channel_type, str):
            raise SchemaError("Every channel requires string id and type")
        if channel_type not in _STRUCT_CODES:
            raise SchemaError(f"Unsupported channel type {channel_type!r} for {channel_id}")
        registry[channel_id] = FieldSpec(
            channel_id=channel_id,
            channel_type=channel_type,
            units=str(raw.get("units", "")),
            description=str(raw.get("description", "")),
        )

    header = structure_document.get("header", {})
    if not isinstance(header, dict) or not isinstance(header.get("channels", []), list):
        raise SchemaError("Packet structure header.channels must be a list")
    header_ids = header.get("channels", [])

    raw_packets = structure_document.get("packets")
    if not isinstance(raw_packets, list):
        raise SchemaError("Packet structure must contain a packets list")
    selected: dict[str, Any] | None = None
    for packet in raw_packets:
        if isinstance(packet, dict) and packet.get("id") == packet_id:
            selected = packet
            break
    if selected is None:
        raise SchemaError(f"Packet {packet_id!r} is not present in {structure_path}")
    packet_ids = selected.get("channels")
    if not isinstance(packet_ids, list):
        raise SchemaError(f"Packet {packet_id!r} channels must be a list")

    channel_ids = [*header_ids, *packet_ids]
    if not all(isinstance(item, str) for item in channel_ids):
        raise SchemaError("Channel IDs must be strings")
    missing = [channel_id for channel_id in channel_ids if channel_id not in registry]
    if missing:
        raise SchemaError(f"Channels absent from channels.json: {', '.join(missing)}")

    fields = tuple(registry[channel_id] for channel_id in channel_ids)
    layout = "<" + "".join(_STRUCT_CODES[field.channel_type] for field in fields)
    return PacketDecoder(packet_id=packet_id, fields=fields, _struct=struct.Struct(layout))
