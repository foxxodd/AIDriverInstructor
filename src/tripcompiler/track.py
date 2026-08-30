"""Build a distance-indexed WRC reference trace from recorded telemetry."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from tripcompiler.models import NormalizedSample


class TrackProfileError(ValueError):
    """A usable track profile cannot be built from the supplied samples."""


@dataclass(frozen=True, slots=True)
class TrackPoint:
    """One resampled point on a route reference trace."""

    distance_m: float
    x_m: float
    y_m: float
    z_m: float
    heading_deg: float
    curvature_rad_m: float
    grade: float

    def to_dict(self) -> dict[str, float]:
        return {key: round(value, 6) for key, value in asdict(self).items()}


@dataclass(frozen=True, slots=True)
class TrackProfile:
    """Versioned route geometry derived from one reconnaissance trace."""

    schema_version: int
    source: str
    location_id: int | None
    location_name: str | None
    route_id: int | None
    route_name: str | None
    stage_length_m: float
    observed_length_m: float
    sample_step_m: float
    coordinate_system: str
    origin_world_m: tuple[float, float, float]
    width: dict[str, Any]
    points: tuple[TrackPoint, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["origin_world_m"] = list(self.origin_world_m)
        value["points"] = [point.to_dict() for point in self.points]
        return value


def build_track_profile(
    samples: list[NormalizedSample],
    metadata: dict[str, Any],
    *,
    sample_step_m: float = 2.0,
) -> TrackProfile:
    """Create a smoothed local trace indexed by official stage distance."""

    if sample_step_m <= 0:
        raise ValueError("sample_step_m must be positive")
    stage_length = _number(metadata.get("stage_length_m"))
    trace = _monotonic_trace(samples, stage_length)
    if len(trace) < 2 or trace[-1][0] - trace[0][0] < sample_step_m:
        raise TrackProfileError("At least two moving stage positions are required")

    origin = (trace[0][1], trace[0][2], trace[0][3])
    local_trace = [
        (distance, x - origin[0], y - origin[1], z - origin[2]) for distance, x, y, z in trace
    ]
    spatial_length = sum(
        math.sqrt(
            (current[1] - previous[1]) ** 2
            + (current[2] - previous[2]) ** 2
            + (current[3] - previous[3]) ** 2
        )
        for previous, current in pairwise(local_trace)
    )
    if spatial_length < sample_step_m:
        raise TrackProfileError("Recorded positions do not contain a moving reference trace")
    positions = _resample(local_trace, sample_step_m)
    positions = _smooth_positions(positions)
    points = _derive_geometry(positions)
    observed_length = points[-1].distance_m
    return TrackProfile(
        schema_version=1,
        source="wrc_reconnaissance_trace",
        location_id=_identifier(metadata.get("location_id")),
        location_name=_text(metadata.get("location")),
        route_id=_identifier(metadata.get("route_id")),
        route_name=_text(metadata.get("route")),
        stage_length_m=stage_length or observed_length,
        observed_length_m=observed_length,
        sample_step_m=sample_step_m,
        coordinate_system="EA WRC local metres: X left, Y up, Z forward",
        origin_world_m=origin,
        width={
            "status": "unknown",
            "left_m": None,
            "right_m": None,
            "method": "not_observable_from_single_vehicle_trace",
        },
        points=tuple(points),
    )


def write_track_profile(path: Path, profile: TrackProfile) -> None:
    """Write a profile without silently replacing an existing file."""

    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(profile.to_dict(), stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def load_track_profile(path: Path) -> TrackProfile:
    """Load and validate a profile produced by this package."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrackProfileError(f"Cannot read track profile {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise TrackProfileError("Unsupported track profile schema")
    raw_points = document.get("points")
    if not isinstance(raw_points, list):
        raise TrackProfileError("Track profile points must be a list")
    try:
        points = tuple(
            TrackPoint(
                distance_m=float(raw["distance_m"]),
                x_m=float(raw["x_m"]),
                y_m=float(raw["y_m"]),
                z_m=float(raw["z_m"]),
                heading_deg=float(raw["heading_deg"]),
                curvature_rad_m=float(raw["curvature_rad_m"]),
                grade=float(raw["grade"]),
            )
            for raw in raw_points
            if isinstance(raw, dict)
        )
        origin_raw = document["origin_world_m"]
        if not isinstance(origin_raw, list) or len(origin_raw) != 3:
            raise TrackProfileError("origin_world_m must contain three coordinates")
        width = document.get("width", {})
        if not isinstance(width, dict):
            raise TrackProfileError("Track profile width must be an object")
        return TrackProfile(
            schema_version=1,
            source=str(document["source"]),
            location_id=_identifier(document.get("location_id")),
            location_name=_text(document.get("location_name")),
            route_id=_identifier(document.get("route_id")),
            route_name=_text(document.get("route_name")),
            stage_length_m=float(document["stage_length_m"]),
            observed_length_m=float(document["observed_length_m"]),
            sample_step_m=float(document["sample_step_m"]),
            coordinate_system=str(document["coordinate_system"]),
            origin_world_m=(float(origin_raw[0]), float(origin_raw[1]), float(origin_raw[2])),
            width=dict(width),
            points=points,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, TrackProfileError):
            raise
        raise TrackProfileError(f"Invalid track profile {path}: {exc}") from exc


def _monotonic_trace(
    samples: list[NormalizedSample], stage_length_m: float
) -> list[tuple[float, float, float, float]]:
    trace: list[tuple[float, float, float, float]] = []
    finish_margin = max(5.0, stage_length_m * 0.002) if stage_length_m else math.inf
    for sample in samples:
        distance = sample.stage_distance_m
        values = (distance, sample.position_x_m, sample.position_y_m, sample.position_z_m)
        if not all(math.isfinite(value) for value in values) or distance < 0:
            continue
        if stage_length_m and distance > stage_length_m + finish_margin:
            continue
        if trace and distance < trace[-1][0] - 2.0:
            continue
        item = (distance, sample.position_x_m, sample.position_y_m, sample.position_z_m)
        if trace and distance <= trace[-1][0] + 0.25:
            trace[-1] = item
        else:
            trace.append(item)
    return trace


def _resample(
    trace: list[tuple[float, float, float, float]], sample_step_m: float
) -> list[tuple[float, float, float, float]]:
    start = trace[0][0]
    end = trace[-1][0]
    targets: list[float] = []
    target = start
    while target < end:
        targets.append(target)
        target += sample_step_m
    targets.append(end)

    result: list[tuple[float, float, float, float]] = []
    upper = 1
    for distance in targets:
        while upper < len(trace) - 1 and trace[upper][0] < distance:
            upper += 1
        before = trace[upper - 1]
        after = trace[upper]
        span = after[0] - before[0]
        fraction = (distance - before[0]) / span if span > 0 else 0.0
        result.append(
            (
                distance - start,
                before[1] + (after[1] - before[1]) * fraction,
                before[2] + (after[2] - before[2]) * fraction,
                before[3] + (after[3] - before[3]) * fraction,
            )
        )
    return result


def _smooth_positions(
    positions: list[tuple[float, float, float, float]], radius: int = 2
) -> list[tuple[float, float, float, float]]:
    smoothed: list[tuple[float, float, float, float]] = []
    for index, point in enumerate(positions):
        window = positions[max(0, index - radius) : min(len(positions), index + radius + 1)]
        count = len(window)
        smoothed.append(
            (
                point[0],
                sum(item[1] for item in window) / count,
                sum(item[2] for item in window) / count,
                sum(item[3] for item in window) / count,
            )
        )
    return smoothed


def _derive_geometry(positions: list[tuple[float, float, float, float]]) -> list[TrackPoint]:
    headings: list[float] = []
    for index in range(len(positions)):
        before = positions[max(0, index - 1)]
        after = positions[min(len(positions) - 1, index + 1)]
        headings.append(math.degrees(math.atan2(after[1] - before[1], after[3] - before[3])))

    points: list[TrackPoint] = []
    for index, position in enumerate(positions):
        before_index = max(0, index - 2)
        after_index = min(len(positions) - 1, index + 2)
        before = positions[before_index]
        after = positions[after_index]
        distance_span = after[0] - before[0]
        heading_delta = math.radians(_angle_delta(headings[after_index], headings[before_index]))
        curvature = heading_delta / distance_span if distance_span > 0 else 0.0
        grade = (after[2] - before[2]) / distance_span if distance_span > 0 else 0.0
        points.append(
            TrackPoint(
                distance_m=position[0],
                x_m=position[1],
                y_m=position[2],
                z_m=position[3],
                heading_deg=headings[index],
                curvature_rad_m=curvature,
                grade=grade,
            )
        )
    return points


def _angle_delta(current: float, previous: float) -> float:
    return (current - previous + 180.0) % 360.0 - 180.0


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _identifier(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
