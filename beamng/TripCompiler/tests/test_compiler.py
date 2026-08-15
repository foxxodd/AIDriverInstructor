from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from wrc_trip_compiler.compiler import CaptureFormatError, compile_trip, load_capture


def _record(uid: int, time_s: float, accel_z: float = 0.0) -> dict[str, object]:
    return {
        "received_at_utc": "2026-08-15T00:00:00+00:00",
        "channels": {
            "packet_uid": uid,
            "game_total_time": 100.0 + time_s,
            "game_frame_count": uid,
            "stage_current_time": time_s,
            "stage_current_distance": 25.0 * time_s,
            "vehicle_speed": 25.0,
            "vehicle_engine_rpm_current": 6000.0,
            "vehicle_acceleration_z": accel_z,
            "vehicle_forward_direction_z": 1.0,
            "vehicle_left_direction_x": 1.0,
            "vehicle_up_direction_y": 1.0,
        },
    }


def _write_capture(path: Path) -> None:
    records = [_record(10, 1.0), _record(12, 1.1, -7.0), _record(13, 1.2)]
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")


def test_compile_trip_creates_complete_artifact_set(tmp_path: Path) -> None:
    capture = tmp_path / "telemetry.jsonl"
    _write_capture(capture)
    output = tmp_path / "compiled"

    summary = compile_trip(capture, output)

    assert summary["samples"] == 3
    assert summary["data_quality"]["estimated_dropped_packets"] == 1
    assert summary["data_quality"]["packet_completeness"] == pytest.approx(0.75)
    assert summary["event_counts"] == {"hard_braking": 1}
    assert {path.name for path in output.iterdir()} == {
        "telemetry.csv",
        "events.json",
        "summary.json",
        "report.html",
    }
    with (output / "telemetry.csv").open(encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert rows[1]["longitudinal_accel_mps2"] == "-7.0"
    assert "hard_braking" in (output / "report.html").read_text(encoding="utf-8")

    with pytest.raises(FileExistsError):
        compile_trip(capture, output)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "no packets"),
        ("not-json\n", "Invalid JSON"),
        (json.dumps({"wrong": {}}) + "\n", "Missing channels"),
    ],
)
def test_capture_format_errors(tmp_path: Path, content: str, message: str) -> None:
    capture = tmp_path / "telemetry.jsonl"
    capture.write_text(content, encoding="utf-8")
    with pytest.raises(CaptureFormatError, match=message):
        load_capture(capture)


def test_missing_capture_is_wrapped(tmp_path: Path) -> None:
    with pytest.raises(CaptureFormatError, match="Cannot open"):
        load_capture(tmp_path / "missing.jsonl")
