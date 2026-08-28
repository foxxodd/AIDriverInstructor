from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tripcompiler.compiler import compile_trip
from tripcompiler.obd import ObdFormatError, canonical_pid, import_obd_trip, read_obd_csv


def _write_obd(path: Path) -> None:
    fieldnames = ["SECONDS", "PID", "VALUE", "UNITS", "LATITUDE", "LONGTITUDE"]
    rows: list[dict[str, object]] = []
    measurements = [(0.0, 800.0), (36.0, 2500.0), *[(18.0, 1800.0)] * 19]
    for index, (speed, rpm) in enumerate(measurements):
        seconds = 100.0 + index
        common = {
            "SECONDS": seconds,
            "LATITUDE": 43.0,
            "LONGTITUDE": 133.0 + index * 0.0001,
        }
        rows.extend(
            [
                {
                    **common,
                    "PID": "\u0412\u044b\u0441\u043e\u0442\u0430 (GPS)",
                    "VALUE": 100.0 + index,
                    "UNITS": "m",
                },
                {
                    **common,
                    "PID": "\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c (GPS)",
                    "VALUE": speed,
                    "UNITS": "km/h",
                },
                {
                    **common,
                    "PID": "\u041e\u0431\u043e\u0440\u043e\u0442\u044b \u0434\u0432\u0438\u0433\u0430\u0442\u0435\u043b\u044f",
                    "VALUE": rpm,
                    "UNITS": "rpm",
                },
                {
                    **common,
                    "PID": "\u041f\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043f\u0435\u0434\u0430\u043b\u0438 \u0430\u043a\u0441\u0435\u043b\u0435\u0440\u0430\u0442\u043e\u0440\u0430 E",
                    "VALUE": 50.0,
                    "UNITS": "%",
                },
            ]
        )
        if index < 2:
            rows.append(
                {
                    **common,
                    "PID": "\u0411\u043e\u043a\u043e\u0432\u0430\u044f \u0441\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044e\u0449\u0430\u044f \u0443\u0441\u043a\u043e\u0440\u0435\u043d\u0438\u044f",
                    "VALUE": 2.0,
                    "UNITS": "g",
                }
            )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def test_import_obd_normalizes_gps_signals_and_catalog(tmp_path: Path) -> None:
    source = tmp_path / "trip.csv"
    _write_obd(source)

    result = import_obd_trip(source)

    assert len(result.samples) == 21
    assert result.samples[1].speed_mps == 10.0
    assert result.samples[1].engine_rpm == 2500.0
    assert result.samples[1].throttle == 0.5
    assert result.samples[-1].stage_distance_m > 10.0
    assert result.samples[-1].stage_progress == 1.0
    assert result.metadata["pid_count"] == 5
    assert result.metadata["recognized_signals"] == [
        "engine_rpm",
        "gps_altitude",
        "gps_speed",
        "lateral_accel",
        "throttle",
    ]
    assert result.metadata["promoted_signals"] == [
        "engine_rpm",
        "gps_altitude",
        "gps_speed",
        "throttle",
    ]
    assert (
        canonical_pid(
            "\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c \u0430\u0432\u0442\u043e\u043c\u043e\u0431\u0438\u043b\u044f"
        )
        == "vehicle_speed"
    )
    assert (
        canonical_pid("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 PID")
        is None
    )


def test_compile_obd_uses_common_output_and_audit_files(tmp_path: Path) -> None:
    source = tmp_path / "trip.csv"
    _write_obd(source)
    output = tmp_path / "compiled"

    summary = compile_trip("obd", source, output)

    assert summary["source"] == "car_scanner_obd_csv"
    assert summary["samples"] == 21
    assert summary["max_speed_kph"] == 36.0
    assert summary["data_quality"]["estimated_dropped_packets"] is None
    assert summary["source_metadata"]["gps_points"] == 21
    assert {path.name for path in output.iterdir()} == {
        "events.json",
        "pid_catalog.csv",
        "report.html",
        "summary.json",
        "telemetry.csv",
        "vehicle_dynamics_raw.csv",
        "script_ai.json",
        "road_centerline.json",
    }


def test_obd_without_gps_uses_speed_timeline(tmp_path: Path) -> None:
    source = tmp_path / "speed_only.csv"
    source.write_text(
        "SECONDS;PID;VALUE;UNITS;LATITUDE;LONGTITUDE\n"
        "1;\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c (GPS);0;km/h;0;0\n"
        "2;\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c (GPS);36;km/h;0;0\n",
        encoding="utf-8",
    )
    result = import_obd_trip(source)
    assert len(result.samples) == 2
    assert result.samples[1].position_x_m == 0.0
    assert result.samples[1].longitudinal_accel_mps2 == 10.0


def test_obd_format_errors_include_missing_columns_and_rows(tmp_path: Path) -> None:
    missing = tmp_path / "missing.csv"
    missing.write_text("SECONDS;PID\n", encoding="utf-8")
    with pytest.raises(ObdFormatError, match="Missing required"):
        read_obd_csv(missing)

    empty = tmp_path / "empty.csv"
    empty.write_text(
        "SECONDS;PID;VALUE;UNITS;LATITUDE;LONGTITUDE\nnot-a-number;x;y;;0;0\n",
        encoding="utf-8",
    )
    with pytest.raises(ObdFormatError, match="no numeric rows"):
        read_obd_csv(empty)

    with pytest.raises(ObdFormatError, match="Cannot open"):
        read_obd_csv(tmp_path / "absent.csv")


def test_compile_rejects_unknown_source_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported source"):
        compile_trip("invalid", tmp_path / "input", tmp_path / "output")  # type: ignore[arg-type]
