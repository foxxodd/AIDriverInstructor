from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from wrc_trip_compiler.schema import PacketSizeError, SchemaError, load_decoder


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_decoder_uses_header_order_and_scalar_types(tmp_path: Path) -> None:
    channels = tmp_path / "channels.json"
    structure = tmp_path / "packet.json"
    _write_json(
        channels,
        {
            "channels": [
                {"id": "packet_4cc", "type": "fourcc"},
                {"id": "packet_uid", "type": "uint64", "units": "count"},
                {"id": "valid", "type": "boolean"},
                {"id": "speed", "type": "float32"},
                {"id": "distance", "type": "float64"},
            ]
        },
    )
    _write_json(
        structure,
        {
            "header": {"channels": ["packet_4cc"]},
            "packets": [
                {
                    "id": "session_update",
                    "channels": ["packet_uid", "valid", "speed", "distance"],
                }
            ],
        },
    )

    decoder = load_decoder(channels, structure)
    payload = struct.pack("<4sQ?fd", b"SESU", 42, True, 12.5, 123.25)

    assert decoder.size == len(payload)
    assert decoder.field_names == ("packet_4cc", "packet_uid", "valid", "speed", "distance")
    assert decoder.decode(payload) == {
        "packet_4cc": "SESU",
        "packet_uid": 42,
        "valid": True,
        "speed": 12.5,
        "distance": 123.25,
    }
    with pytest.raises(PacketSizeError, match="Expected"):
        decoder.decode(payload[:-1])


@pytest.mark.parametrize(
    ("channels_value", "structure_value", "message"),
    [
        ({}, {"header": {"channels": []}, "packets": []}, "channels list"),
        (
            {"channels": [{"id": "value", "type": "complex128"}]},
            {"header": {"channels": []}, "packets": []},
            "Unsupported channel type",
        ),
        (
            {"channels": [{"id": "value", "type": "float32"}]},
            {"header": {"channels": []}, "packets": []},
            "not present",
        ),
        (
            {"channels": [{"id": "value", "type": "float32"}]},
            {
                "header": {"channels": []},
                "packets": [{"id": "session_update", "channels": ["missing"]}],
            },
            "absent",
        ),
    ],
)
def test_schema_validation_errors(
    tmp_path: Path, channels_value: object, structure_value: object, message: str
) -> None:
    channels = tmp_path / "channels.json"
    structure = tmp_path / "packet.json"
    _write_json(channels, channels_value)
    _write_json(structure, structure_value)

    with pytest.raises(SchemaError, match=message):
        load_decoder(channels, structure)


def test_invalid_json_is_schema_error(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    with pytest.raises(SchemaError, match="Cannot read JSON"):
        load_decoder(invalid, invalid)
