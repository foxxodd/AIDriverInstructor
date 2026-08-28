"""Import long-form Car Scanner OBD/GPS CSV into the common trip schema."""

from __future__ import annotations

import csv
import math
import re
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from statistics import median
from typing import Any

from tripcompiler.models import NormalizedSample

EARTH_RADIUS_M = 6_371_000.0


class ObdFormatError(ValueError):
    """The Car Scanner CSV is missing required data or cannot be decoded."""


@dataclass(frozen=True, slots=True)
class ObdRow:
    """One parsed row from the long-form OBD/GPS export."""

    seconds: float
    pid: str
    value: float
    units: str
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class ObdImportResult:
    """Normalized samples plus OBD-specific audit data."""

    samples: list[NormalizedSample]
    rows: list[ObdRow]
    pid_catalog: list[dict[str, Any]]
    metadata: dict[str, Any]


_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gps_altitude", (r"^\u0412\u044b\u0441\u043e\u0442\u0430 \(GPS\)$", r"GPS.*Altitude")),
    ("gps_speed", (r"^\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c \(GPS\)$", r"GPS.*Speed")),
    (
        "vehicle_speed",
        (
            r"^\u0421\u043a\u043e\u0440\u043e\u0441\u0442\u044c \u0430\u0432\u0442\u043e\u043c\u043e\u0431\u0438\u043b\u044f$",
            r"Vehicle.*Speed",
        ),
    ),
    (
        "engine_rpm",
        (
            r"^\u041e\u0431\u043e\u0440\u043e\u0442\u044b \u0434\u0432\u0438\u0433\u0430\u0442\u0435\u043b\u044f$",
            r"^Engine.*RPM",
        ),
    ),
    (
        "throttle",
        (
            r"^\u041f\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u043f\u0435\u0434\u0430\u043b\u0438 \u0430\u043a\u0441\u0435\u043b\u0435\u0440\u0430\u0442\u043e\u0440\u0430",
            r"^\u041f\u043e\u043b\u043e\u0436\u0435\u043d\u0438\u0435 \u0434\u0440\u043e\u0441\u0441\u0435\u043b\u044c\u043d\u043e\u0439 \u0437\u0430\u0441\u043b\u043e\u043d\u043a\u0438$",
            r"Throttle",
        ),
    ),
    (
        "brake",
        (
            r"\u0432\u044b\u043a\u043b\u044e\u0447\u0430\u0442\u0435\u043b\u044f \u0441\u0442\u043e\u043f-\u0441\u0438\u0433\u043d\u0430\u043b\u0430",
            r"Brake.*Switch",
        ),
    ),
    (
        "steering",
        (
            r"\u0423\u0433\u043e\u043b \u043f\u043e\u0432\u043e\u0440\u043e\u0442\u0430 \u0440\u0443\u043b\u0435\u0432\u043e\u0433\u043e \u043a\u043e\u043b\u0435\u0441\u0430",
            r"Steering.*Angle",
        ),
    ),
    (
        "longitudinal_accel",
        (
            r"\u041f\u0440\u043e\u0434\u043e\u043b\u044c\u043d\u0430\u044f \u0441\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044e\u0449\u0430\u044f \u0443\u0441\u043a\u043e\u0440\u0435\u043d\u0438\u044f",
            r"Longitudinal.*Accel",
        ),
    ),
    (
        "lateral_accel",
        (
            r"\u0411\u043e\u043a\u043e\u0432\u0430\u044f \u0441\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044e\u0449\u0430\u044f \u0443\u0441\u043a\u043e\u0440\u0435\u043d\u0438\u044f",
            r"Lateral.*Accel",
        ),
    ),
    (
        "yaw_rate",
        (
            r"^\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0440\u044b\u0441\u043a\u0430\u043d\u0438\u044f$",
            r"^\u0414\u0430\u0442\u0447\u0438\u043a \u0440\u044b\u0441\u043a\u0430\u043d\u0438\u044f$",
            r"Yaw.*Rate",
        ),
    ),
    (
        "wheel_speed_bl",
        (
            r"\u043b\u0435\u0432\u043e\u0433\u043e \u0437\u0430\u0434\u043d\u0435\u0433\u043e \u043a\u043e\u043b\u0435\u0441\u0430",
            r"Rear Left.*Wheel.*Speed",
        ),
    ),
    (
        "wheel_speed_br",
        (
            r"\u043f\u0440\u0430\u0432\u043e\u0433\u043e \u0437\u0430\u0434\u043d\u0435\u0433\u043e \u043a\u043e\u043b\u0435\u0441\u0430",
            r"Rear Right.*Wheel.*Speed",
        ),
    ),
    (
        "wheel_speed_fl",
        (
            r"\u043b\u0435\u0432\u043e\u0433\u043e \u043f\u0435\u0440\u0435\u0434\u043d\u0435\u0433\u043e \u043a\u043e\u043b\u0435\u0441\u0430",
            r"Front Left.*Wheel.*Speed",
        ),
    ),
    (
        "wheel_speed_fr",
        (
            r"\u043f\u0440\u0430\u0432\u043e\u0433\u043e \u043f\u0435\u0440\u0435\u0434\u043d\u0435\u0433\u043e \u043a\u043e\u043b\u0435\u0441\u0430",
            r"Front Right.*Wheel.*Speed",
        ),
    ),
)


def canonical_pid(pid: str) -> str | None:
    """Map a localized Car Scanner PID name to a stable source signal."""

    for canonical, patterns in _ALIASES:
        if any(re.search(pattern, pid, flags=re.IGNORECASE) for pattern in patterns):
            return canonical
    return None


def read_obd_csv(path: Path) -> list[ObdRow]:
    """Read a semicolon-separated Car Scanner export without modifying it."""

    last_error: UnicodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with path.open(encoding=encoding, newline="") as stream:
                reader = csv.DictReader(stream, delimiter=";")
                required = {"SECONDS", "PID", "VALUE", "LATITUDE", "LONGTITUDE"}
                if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                    missing = sorted(required - set(reader.fieldnames or ()))
                    raise ObdFormatError(f"Missing required OBD columns: {missing}")
                rows: list[ObdRow] = []
                for raw in reader:
                    try:
                        rows.append(
                            ObdRow(
                                seconds=float(raw["SECONDS"]),
                                pid=raw["PID"].strip(),
                                value=float(raw["VALUE"]),
                                units=raw.get("UNITS", "").strip(),
                                latitude=float(raw["LATITUDE"]),
                                longitude=float(raw["LONGTITUDE"]),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
            if not rows:
                raise ObdFormatError(f"OBD CSV contains no numeric rows: {path}")
            return sorted(rows, key=lambda row: row.seconds)
        except UnicodeError as exc:
            last_error = exc
        except OSError as exc:
            raise ObdFormatError(f"Cannot open OBD CSV {path}: {exc}") from exc
    raise ObdFormatError(f"Cannot decode OBD CSV {path}: {last_error}")


def import_obd_trip(path: Path) -> ObdImportResult:
    """Create common samples and an auditable PID catalog from an OBD export."""

    rows = read_obd_csv(path)
    signals: dict[str, list[ObdRow]] = defaultdict(list)
    pid_rows: dict[str, list[ObdRow]] = defaultdict(list)
    for row in rows:
        pid_rows[row.pid].append(row)
        canonical = canonical_pid(row.pid)
        if canonical is not None:
            signals[canonical].append(row)

    anchors = _trajectory_anchors(rows, signals)
    if not anchors:
        raise ObdFormatError("No GPS or speed timeline found in OBD CSV")
    promoted_signals = _promoted_signals(signals, anchors)
    samples = _normalize_anchors(anchors, promoted_signals)
    catalog = [
        {
            "pid": pid,
            "canonical_name": canonical_pid(pid),
            "samples": len(group),
            "units": sorted({row.units for row in group if row.units}),
            "first_time_s": group[0].seconds - rows[0].seconds,
            "last_time_s": group[-1].seconds - rows[0].seconds,
            "status": (
                "promoted"
                if canonical_pid(pid) in promoted_signals
                else "sparse"
                if canonical_pid(pid) is not None
                else "unrecognized"
            ),
        }
        for pid, group in sorted(pid_rows.items())
    ]
    gps_points = sum(_valid_gps(row) for row in anchors)
    return ObdImportResult(
        samples=samples,
        rows=rows,
        pid_catalog=catalog,
        metadata={
            "raw_rows": len(rows),
            "pid_count": len(pid_rows),
            "recognized_pid_count": sum(canonical_pid(pid) is not None for pid in pid_rows),
            "gps_points": gps_points,
            "recognized_signals": sorted(signals),
            "promoted_signals": sorted(promoted_signals),
        },
    )


def _valid_gps(row: ObdRow) -> bool:
    return (
        math.isfinite(row.latitude)
        and math.isfinite(row.longitude)
        and not (math.isclose(row.latitude, 0.0) and math.isclose(row.longitude, 0.0))
    )


def _trajectory_anchors(rows: list[ObdRow], signals: dict[str, list[ObdRow]]) -> list[ObdRow]:
    candidates = [row for row in signals.get("gps_altitude", []) if _valid_gps(row)]
    if not candidates:
        candidates = [row for row in rows if _valid_gps(row)]
    if not candidates:
        candidates = signals.get("gps_speed", []) or signals.get("vehicle_speed", [])
    unique: dict[float, ObdRow] = {}
    for row in candidates:
        unique.setdefault(row.seconds, row)
    return [unique[seconds] for seconds in sorted(unique)]


def _promoted_signals(
    signals: dict[str, list[ObdRow]], anchors: list[ObdRow]
) -> dict[str, list[ObdRow]]:
    always = {"gps_altitude", "gps_speed"}
    trip_start = anchors[0].seconds
    trip_end = anchors[-1].seconds
    trip_span = max(trip_end - trip_start, 1e-9)
    promoted: dict[str, list[ObdRow]] = {}
    for canonical, rows in signals.items():
        if canonical in always:
            promoted[canonical] = rows
            continue
        gaps = [current.seconds - previous.seconds for previous, current in pairwise(rows)]
        signal_span = rows[-1].seconds - rows[0].seconds if len(rows) > 1 else 0.0
        if len(rows) >= 20 and signal_span / trip_span >= 0.5 and gaps and median(gaps) <= 10.0:
            promoted[canonical] = rows
    return promoted


def _signal_value(
    rows: list[ObdRow] | None,
    seconds: float,
    *,
    max_gap_s: float = 10.0,
) -> tuple[float, str] | None:
    if not rows:
        return None
    times = [row.seconds for row in rows]
    index = bisect_left(times, seconds)
    choices = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(rows)]
    nearest = min(choices, key=lambda candidate: abs(rows[candidate].seconds - seconds))
    row = rows[nearest]
    if abs(row.seconds - seconds) > max_gap_s:
        return None
    return row.value, row.units


def _converted_signal(
    signals: dict[str, list[ObdRow]], canonical: str, seconds: float
) -> float | None:
    result = _signal_value(signals.get(canonical), seconds)
    if result is None:
        return None
    value, units = result
    normalized_units = units.casefold()
    if canonical in {
        "gps_speed",
        "vehicle_speed",
        "wheel_speed_bl",
        "wheel_speed_br",
        "wheel_speed_fl",
        "wheel_speed_fr",
    }:
        return value if "m/s" in normalized_units else value / 3.6
    if canonical in {"longitudinal_accel", "lateral_accel"} and normalized_units == "g":
        return value * 9.80665
    if canonical in {"throttle", "brake"} and ("%" in units or value > 1.0):
        return max(0.0, min(1.0, value / 100.0))
    return value


def _normalize_anchors(
    anchors: list[ObdRow], signals: dict[str, list[ObdRow]]
) -> list[NormalizedSample]:
    first = anchors[0]
    latitude_scale = math.pi * EARTH_RADIUS_M / 180.0
    longitude_scale = latitude_scale * math.cos(math.radians(first.latitude))
    altitude0 = _converted_signal(signals, "gps_altitude", first.seconds) or 0.0
    points: list[tuple[float, float, float, float]] = []
    for anchor in anchors:
        x_m = (anchor.longitude - first.longitude) * longitude_scale if _valid_gps(anchor) else 0.0
        z_m = (anchor.latitude - first.latitude) * latitude_scale if _valid_gps(anchor) else 0.0
        altitude = _converted_signal(signals, "gps_altitude", anchor.seconds)
        points.append(
            (anchor.seconds - first.seconds, x_m, (altitude or altitude0) - altitude0, z_m)
        )

    headings = _headings(points)
    cumulative_distance = _cumulative_distance(points)
    total_distance = cumulative_distance[-1] if cumulative_distance else 0.0
    samples: list[NormalizedSample] = []
    previous_speed = 0.0
    previous_heading = headings[0]
    previous_time = points[0][0]
    for index, (time_s, x_m, y_m, z_m) in enumerate(points):
        dt = time_s - previous_time
        heading = headings[index]
        heading_rad = math.radians(heading)
        forward = (math.sin(heading_rad), 0.0, math.cos(heading_rad))
        left = (-forward[2], 0.0, forward[0])
        speed = _converted_signal(signals, "vehicle_speed", anchors[index].seconds)
        if speed is None:
            speed = _converted_signal(signals, "gps_speed", anchors[index].seconds)
        if speed is None:
            speed = _derived_speed(points, index)
        longitudinal = _converted_signal(signals, "longitudinal_accel", anchors[index].seconds)
        if longitudinal is None:
            longitudinal = (speed - previous_speed) / dt if index and dt > 0 else 0.0
        yaw_rate = _angle_delta(heading, previous_heading) / dt if index and dt > 0 else 0.0
        lateral = _converted_signal(signals, "lateral_accel", anchors[index].seconds)
        if lateral is None:
            lateral = speed * math.radians(yaw_rate)
        throttle = _converted_signal(signals, "throttle", anchors[index].seconds) or 0.0
        brake = _converted_signal(signals, "brake", anchors[index].seconds) or 0.0
        engine_rpm = _converted_signal(signals, "engine_rpm", anchors[index].seconds) or 0.0
        steering = _converted_signal(signals, "steering", anchors[index].seconds) or 0.0
        acceleration_x = forward[0] * longitudinal + left[0] * lateral
        acceleration_z = forward[2] * longitudinal + left[2] * lateral
        wheel_slip = _wheel_slip(signals, anchors[index].seconds, speed)
        samples.append(
            NormalizedSample(
                time_s=time_s,
                stage_time_s=time_s,
                packet_uid=index + 1,
                frame=index,
                speed_mps=abs(speed),
                engine_rpm=engine_rpm,
                gear=0,
                throttle=throttle,
                brake=brake,
                clutch=0.0,
                steering=steering,
                handbrake=0.0,
                position_x_m=x_m,
                position_y_m=y_m,
                position_z_m=z_m,
                velocity_x_mps=forward[0] * speed,
                velocity_y_mps=0.0,
                velocity_z_mps=forward[2] * speed,
                acceleration_x_mps2=acceleration_x,
                acceleration_y_mps2=0.0,
                acceleration_z_mps2=acceleration_z,
                longitudinal_accel_mps2=longitudinal,
                lateral_accel_mps2=lateral,
                vertical_accel_mps2=0.0,
                heading_deg=heading,
                yaw_rate_deg_s=yaw_rate,
                slip_angle_deg=0.0,
                wheel_slip_ratio=wheel_slip,
                stage_distance_m=cumulative_distance[index],
                stage_progress=(
                    cumulative_distance[index] / total_distance if total_distance else 0.0
                ),
            )
        )
        previous_speed = speed
        previous_heading = heading
        previous_time = time_s
    return samples


def _headings(points: list[tuple[float, float, float, float]]) -> list[float]:
    headings: list[float] = []
    previous = 0.0
    for index in range(len(points)):
        before = points[max(0, index - 1)]
        after = points[min(len(points) - 1, index + 1)]
        dx = after[1] - before[1]
        dz = after[3] - before[3]
        if math.hypot(dx, dz) > 0.05:
            previous = math.degrees(math.atan2(dx, dz))
        headings.append(previous)
    return headings


def _cumulative_distance(points: list[tuple[float, float, float, float]]) -> list[float]:
    distances = [0.0]
    for previous, current in pairwise(points):
        distances.append(
            distances[-1]
            + math.sqrt(
                (current[1] - previous[1]) ** 2
                + (current[2] - previous[2]) ** 2
                + (current[3] - previous[3]) ** 2
            )
        )
    return distances


def _derived_speed(points: list[tuple[float, float, float, float]], index: int) -> float:
    if index == 0:
        return 0.0
    previous = points[index - 1]
    current = points[index]
    dt = current[0] - previous[0]
    if dt <= 0:
        return 0.0
    return math.hypot(current[1] - previous[1], current[3] - previous[3]) / dt


def _angle_delta(current: float, previous: float) -> float:
    return (current - previous + 180.0) % 360.0 - 180.0


def _wheel_slip(signals: dict[str, list[ObdRow]], seconds: float, speed: float) -> float:
    wheel_speeds = [
        value
        for corner in ("bl", "br", "fl", "fr")
        if (value := _converted_signal(signals, f"wheel_speed_{corner}", seconds)) is not None
    ]
    if not wheel_speeds:
        return 0.0
    return (sum(map(abs, wheel_speeds)) / len(wheel_speeds) - abs(speed)) / max(abs(speed), 1.0)
