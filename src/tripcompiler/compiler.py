"""Compile OBD CSV or WRC UDP captures into common trip artifacts."""

from __future__ import annotations

import csv
import html
import json
import math
from collections import Counter
from dataclasses import asdict
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from tripcompiler.analysis import detect_events, normalize_packets
from tripcompiler.models import AnalysisConfig, NormalizedSample, Scalar
from tripcompiler.obd import ObdImportResult, canonical_pid, import_obd_trip
from tripcompiler.pacenotes import (
    PaceNoteSet,
    generate_pace_notes,
    import_zendrive_pace_notes,
    write_pace_notes,
)
from tripcompiler.track import TrackProfileError, build_track_profile, write_track_profile
from tripcompiler.wrc_catalog import enrich_wrc_metadata, load_wrc_catalog

SourceKind = Literal["obd", "wrc"]


class CaptureFormatError(ValueError):
    """The raw WRC JSONL stream cannot be compiled safely."""


def load_capture(path: Path) -> list[dict[str, Scalar]]:
    """Read raw WRC packets while retaining source line context on errors."""

    packets: list[dict[str, Scalar]] = []
    try:
        stream = path.open(encoding="utf-8-sig")
    except OSError as exc:
        raise CaptureFormatError(f"Cannot open capture {path}: {exc}") from exc
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaptureFormatError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict) or not isinstance(record.get("channels"), dict):
                raise CaptureFormatError(f"Missing channels object at {path}:{line_number}")
            channels: dict[str, Scalar] = {}
            for key, value in record["channels"].items():
                if isinstance(key, str) and isinstance(value, (bool, int, float, str)):
                    channels[key] = value
            packets.append(channels)
    if not packets:
        raise CaptureFormatError(f"Capture contains no packets: {path}")
    return packets


def compile_trip(
    source: SourceKind,
    input_path: Path,
    output_dir: Path,
    config: AnalysisConfig | None = None,
    wrc_ids_path: Path | None = None,
    wrc_pacenotes_dir: Path | None = None,
) -> dict[str, Any]:
    """Compile one OBD or WRC input into the same normalized artifact contract."""

    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    obd_result: ObdImportResult | None = None
    imported_pace_notes: PaceNoteSet | None = None
    if source == "wrc":
        packets = load_capture(input_path)
        samples = normalize_packets(packets)
        source_name = "ea_sports_wrc_udp"
        source_metadata = wrc_metadata(packets[-1])
        if wrc_ids_path is not None:
            source_metadata = enrich_wrc_metadata(
                source_metadata,
                load_wrc_catalog(wrc_ids_path),
            )
        if wrc_pacenotes_dir is not None:
            location_id = source_metadata.get("location_id")
            route_id = source_metadata.get("route_id")
            if (
                isinstance(location_id, (int, float))
                and not isinstance(location_id, bool)
                and isinstance(route_id, (int, float))
                and not isinstance(route_id, bool)
            ):
                candidate = wrc_pacenotes_dir / f"{int(location_id)}-{int(route_id)}.json"
                if candidate.is_file():
                    imported_pace_notes = import_zendrive_pace_notes(candidate)
    elif source == "obd":
        obd_result = import_obd_trip(input_path)
        samples = obd_result.samples
        source_name = "car_scanner_obd_csv"
        source_metadata = obd_result.metadata
    else:
        raise ValueError(f"Unsupported source {source!r}; expected 'obd' or 'wrc'")

    events = detect_events(samples, config)
    output_dir.mkdir(parents=True)
    rows = _write_telemetry(output_dir, samples)
    _write_replay_outputs(output_dir, samples)
    track_profile_status = "not_applicable"
    pace_notes_status = "not_applicable"
    if source == "wrc":
        pace_notes_status = "none"
        try:
            profile = build_track_profile(samples, source_metadata)
            write_track_profile(output_dir / "track_profile.json", profile)
            write_pace_notes(
                output_dir / "pace_notes.draft.json",
                generate_pace_notes(profile),
            )
            track_profile_status = "generated"
            pace_notes_status = "geometry_draft_only"
        except TrackProfileError:
            track_profile_status = "insufficient_trace"
        if imported_pace_notes is not None:
            write_pace_notes(output_dir / "pace_notes.json", imported_pace_notes)
            pace_notes_status = "zendrive_imported"
    event_payload = [event.to_dict() for event in events]
    (output_dir / "events.json").write_text(
        json.dumps(event_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if obd_result is not None:
        _write_obd_audit(output_dir, obd_result)

    times = [sample.time_s for sample in samples]
    duration = max(times) - min(times) if len(times) > 1 else 0.0
    estimated_dropped = _estimated_packet_loss(samples) if source == "wrc" else 0
    expected_packets = len(samples) + estimated_dropped
    event_counts = dict(sorted(Counter(event.event_type for event in events).items()))
    speeds = [sample.speed_mps for sample in samples]
    summary: dict[str, Any] = {
        "schema_version": 1,
        "source": source_name,
        "input": str(input_path),
        "samples": len(samples),
        "duration_s": duration,
        "sample_rate_hz": (len(samples) - 1) / duration if duration > 0 else 0.0,
        "distance_m": max(sample.stage_distance_m for sample in samples),
        "max_speed_kph": max(speeds) * 3.6,
        "mean_speed_kph": fmean(speeds) * 3.6,
        "max_engine_rpm": max(sample.engine_rpm for sample in samples),
        "source_metadata": source_metadata,
        "track_profile": track_profile_status,
        "pace_notes": pace_notes_status,
        "events": len(events),
        "event_counts": event_counts,
        "data_quality": {
            "estimated_dropped_packets": estimated_dropped if source == "wrc" else None,
            "packet_completeness": (
                len(samples) / expected_packets if source == "wrc" and expected_packets else None
            ),
            "time_monotonic": all(current >= previous for previous, current in pairwise(times)),
            "finite_numeric_values": all(
                math.isfinite(value)
                for row in rows
                for value in row.values()
                if isinstance(value, float)
            ),
        },
        "analysis_config": asdict(config or AnalysisConfig()),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "report.html").write_text(
        _render_report(summary, event_payload), encoding="utf-8"
    )
    return summary


def _write_telemetry(output_dir: Path, samples: list[NormalizedSample]) -> list[dict[str, Any]]:
    rows = [sample.to_dict() for sample in samples]
    with (output_dir / "telemetry.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _write_replay_outputs(output_dir: Path, samples: list[NormalizedSample]) -> None:
    script = {
        "path": [
            {
                "x": round(sample.position_x_m, 3),
                "y": round(sample.position_y_m, 3),
                "z": round(sample.position_z_m, 3),
                "t": round(sample.time_s, 3),
            }
            for sample in samples
        ]
    }
    (output_dir / "script_ai.json").write_text(
        json.dumps(script, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    step = max(1, len(samples) // 500)
    road = {
        "format": "TripCompiler road centerline v1",
        "coordinate_system": "local metres",
        "nodes": [
            [
                round(sample.position_x_m, 3),
                round(sample.position_y_m, 3),
                round(sample.position_z_m, 3),
            ]
            for sample in samples[::step]
        ],
    }
    (output_dir / "road_centerline.json").write_text(
        json.dumps(road, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_obd_audit(output_dir: Path, result: ObdImportResult) -> None:
    catalog_rows = [{**row, "units": ", ".join(row["units"])} for row in result.pid_catalog]
    with (output_dir / "pid_catalog.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(catalog_rows[0]))
        writer.writeheader()
        writer.writerows(catalog_rows)

    recognized = [row for row in result.rows if canonical_pid(row.pid) is not None]
    with (output_dir / "vehicle_dynamics_raw.csv").open(
        "x", encoding="utf-8-sig", newline=""
    ) as stream:
        fieldnames = [
            "time_s",
            "canonical_name",
            "pid",
            "value",
            "units",
            "latitude",
            "longitude",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        start = result.rows[0].seconds
        writer.writerows(
            {
                "time_s": row.seconds - start,
                "canonical_name": canonical_pid(row.pid),
                "pid": row.pid,
                "value": row.value,
                "units": row.units,
                "latitude": row.latitude,
                "longitude": row.longitude,
            }
            for row in recognized
        )


def _estimated_packet_loss(samples: list[NormalizedSample]) -> int:
    packet_uids = [sample.packet_uid for sample in samples if sample.packet_uid > 0]
    return sum(max(0, current - previous - 1) for previous, current in pairwise(packet_uids))


def wrc_metadata(final_channels: dict[str, Scalar]) -> dict[str, Any]:
    return {
        "game_mode": final_channels.get("game_mode"),
        "vehicle_id": final_channels.get("vehicle_id"),
        "vehicle_class_id": final_channels.get("vehicle_class_id"),
        "vehicle_manufacturer_id": final_channels.get("vehicle_manufacturer_id"),
        "location_id": final_channels.get("location_id"),
        "route_id": final_channels.get("route_id"),
        "stage_length_m": final_channels.get("stage_length"),
        "stage_result_time_s": final_channels.get("stage_result_time"),
        "stage_penalty_s": final_channels.get("stage_result_time_penalty"),
        "stage_result_status": final_channels.get("stage_result_status"),
    }


def _render_report(summary: dict[str, Any], events: list[dict[str, Any]]) -> str:
    event_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(event['event_type']))}</td>"
        f"<td>{event['start_s']:.2f}</td>"
        f"<td>{event['stage_distance_m']:.1f}</td>"
        f"<td>{event['peak_value']:.2f}</td>"
        f"<td>{event['severity']:.2f}</td>"
        "</tr>"
        for event in events
    )
    if not event_rows:
        event_rows = '<tr><td colspan="5">No threshold events detected</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TripCompiler report</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #18212b; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 1rem; }}
    .metric {{ border: 1px solid #d8dee4; border-radius: .5rem; padding: 1rem; }}
    .metric strong {{ display: block; font-size: 1.5rem; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ border-bottom: 1px solid #d8dee4; padding: .6rem; text-align: left; }}
    small {{ color: #57606a; }}
  </style>
</head>
<body>
  <h1>TripCompiler report</h1>
  <p>Source: <strong>{html.escape(str(summary["source"]))}</strong></p>
  <div class="metrics">
    <div class="metric"><strong>{summary["duration_s"]:.1f} s</strong>duration</div>
    <div class="metric"><strong>{summary["distance_m"]:.0f} m</strong>distance</div>
    <div class="metric"><strong>{summary["max_speed_kph"]:.1f} km/h</strong>maximum speed</div>
    <div class="metric"><strong>{summary["events"]}</strong>detected events</div>
  </div>
  <h2>Events</h2>
  <table><thead><tr><th>Type</th><th>Time, s</th><th>Distance, m</th><th>Peak</th><th>Severity</th></tr></thead><tbody>{event_rows}</tbody></table>
  <p><small>Threshold-based engineering report. Source-specific calibration is required before interpreting events as driver errors.</small></p>
</body>
</html>
"""
