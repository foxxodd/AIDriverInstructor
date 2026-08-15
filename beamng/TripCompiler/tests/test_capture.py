from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from wrc_trip_compiler.capture import JsonlCaptureWriter, capture_udp
from wrc_trip_compiler.models import FieldSpec
from wrc_trip_compiler.schema import PacketDecoder


def _decoder() -> PacketDecoder:
    return PacketDecoder(
        packet_id="session_update",
        fields=(FieldSpec("packet_uid", "uint64"), FieldSpec("vehicle_speed", "float32")),
        _struct=struct.Struct("<Qf"),
    )


def test_jsonl_writer_adds_receive_metadata() -> None:
    stream = io.StringIO()
    JsonlCaptureWriter(stream).write({"packet_uid": 7}, ("127.0.0.1", 20779), 1234)
    record = json.loads(stream.getvalue())
    assert record["channels"] == {"packet_uid": 7}
    assert record["sender_port"] == 20779
    assert record["received_monotonic_ns"] == 1234
    assert record["received_at_utc"].endswith("+00:00")


class _FakeSocket:
    def __init__(self, *_: object, **__: object) -> None:
        self.payloads = [b"bad", struct.pack("<Qf", 9, 18.5)]

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def setsockopt(self, *_: object) -> None:
        pass

    def bind(self, address: tuple[str, int]) -> None:
        assert address == ("127.0.0.1", 20779)

    def settimeout(self, timeout: float) -> None:
        assert timeout > 0

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        assert size >= 65_535
        return self.payloads.pop(0), ("127.0.0.1", 30000)


def test_capture_udp_counts_malformed_and_writes_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wrc_trip_compiler.capture.socket.socket", _FakeSocket)
    output = tmp_path / "session"

    stats = capture_udp(_decoder(), output, max_packets=1)

    assert stats.received_datagrams == 2
    assert stats.decoded_packets == 1
    assert stats.malformed_packets == 1
    assert stats.first_packet_uid == stats.last_packet_uid == 9
    record = json.loads((output / "telemetry.jsonl").read_text(encoding="utf-8"))
    assert record["channels"]["vehicle_speed"] == 18.5
    metadata = json.loads((output / "capture.json").read_text(encoding="utf-8"))
    assert metadata["decoder_packet_size"] == 12


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"duration_s": 0.0}, "duration_s"), ({"max_packets": 0}, "max_packets")],
)
def test_capture_rejects_invalid_limits(
    tmp_path: Path, kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        capture_udp(_decoder(), tmp_path / "session", **kwargs)
