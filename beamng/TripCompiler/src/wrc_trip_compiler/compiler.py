"""Compile append-only WRC captures into durable analysis artifacts."""

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
from typing import Any

from wrc_trip_compiler.analysis import detect_events, normalize_packets
from wrc_trip_compiler.models import AnalysisConfig, Scalar


class CaptureFormatError(ValueError):
    """The raw JSONL stream cannot be compiled safely."""


def load_capture(path: Path) -> list[dict[str, Scalar]]:
    """Read raw packets while retaining an actionable source line on errors."""

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
    capture_path: Path,
    output_dir: Path,
    config: AnalysisConfig | None = None,
) -> dict[str, Any]:
    """Compile one raw capture into CSV, JSON, and a standalone HTML report."""

    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    packets = load_capture(capture_path)
    samples = normalize_packets(packets)
    events = detect_events(samples, config)
    output_dir.mkdir(parents=True)

    telemetry_path = output_dir / "telemetry.csv"
    rows = [sample.to_dict() for sample in samples]
    with telemetry_path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    event_payload = [event.to_dict() for event in events]
    (output_dir / "events.json").write_text(
        json.dumps(event_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    times = [sample.time_s for sample in samples]
    duration = max(times) - min(times) if len(times) > 1 else 0.0
    packet_uids = [sample.packet_uid for sample in samples if sample.packet_uid > 0]
    estimated_dropped = sum(
        max(0, current - previous - 1) for previous, current in pairwise(packet_uids)
    )
    expected_packets = len(samples) + estimated_dropped
    event_counts = dict(sorted(Counter(event.event_type for event in events).items()))
    speeds = [sample.speed_mps for sample in samples]
    final_channels = packets[-1]
    summary: dict[str, Any] = {
        "schema_version": 1,
        "source": "ea_sports_wrc_udp",
        "capture": str(capture_path),
        "samples": len(samples),
        "duration_s": duration,
        "sample_rate_hz": (len(samples) - 1) / duration if duration > 0 else 0.0,
        "stage_distance_m": max(sample.stage_distance_m for sample in samples),
        "max_speed_kph": max(speeds) * 3.6,
        "mean_speed_kph": fmean(speeds) * 3.6,
        "max_engine_rpm": max(sample.engine_rpm for sample in samples),
        "session": {
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
        },
        "events": len(events),
        "event_counts": event_counts,
        "data_quality": {
            "estimated_dropped_packets": estimated_dropped,
            "packet_completeness": len(samples) / expected_packets if expected_packets else 1.0,
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


def _render_report(summary: dict[str, Any], events: list[dict[str, Any]]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(event['event_type']))}</td>"
        f"<td>{event['start_s']:.2f}</td>"
        f"<td>{event['stage_distance_m']:.1f}</td>"
        f"<td>{event['peak_value']:.2f}</td>"
        f"<td>{event['severity']:.2f}</td>"
        "</tr>"
        for event in events
    )
    if not rows:
        rows = '<tr><td colspan="5">No threshold events detected</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EA Sports WRC trip report</title>
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
  <h1>EA Sports WRC trip report</h1>
  <div class="metrics">
    <div class="metric"><strong>{summary["duration_s"]:.1f} s</strong>duration</div>
    <div class="metric"><strong>{summary["stage_distance_m"]:.0f} m</strong>stage distance</div>
    <div class="metric"><strong>{summary["max_speed_kph"]:.1f} km/h</strong>maximum speed</div>
    <div class="metric"><strong>{summary["events"]}</strong>detected events</div>
  </div>
  <h2>Events</h2>
  <table><thead><tr><th>Type</th><th>Time, s</th><th>Distance, m</th><th>Peak</th><th>Severity</th></tr></thead><tbody>{rows}</tbody></table>
  <p><small>Threshold-based engineering report. It is not a safety rating and rally driving style can intentionally exceed the defaults.</small></p>
</body>
</html>
"""
