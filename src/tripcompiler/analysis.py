"""Normalize WRC frames and detect deterministic driving events."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from tripcompiler.models import AnalysisConfig, DrivingEvent, NormalizedSample, Scalar

Vector3 = tuple[float, float, float]


def _number(channels: Mapping[str, Scalar], key: str) -> float:
    value = channels.get(key, 0.0)
    if isinstance(value, (bool, int, float)):
        return float(value)
    return 0.0


def _integer(channels: Mapping[str, Scalar], key: str) -> int:
    return int(_number(channels, key))


def _dot(a: Vector3, b: Vector3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vector(channels: Mapping[str, Scalar], prefix: str) -> Vector3:
    return (
        _number(channels, f"{prefix}_x"),
        _number(channels, f"{prefix}_y"),
        _number(channels, f"{prefix}_z"),
    )


def _angle_delta(current: float, previous: float) -> float:
    return (current - previous + 180.0) % 360.0 - 180.0


def normalize_packets(packets: Iterable[Mapping[str, Scalar]]) -> list[NormalizedSample]:
    """Convert dynamic EA channel dictionaries into the stable compiler schema."""

    result: list[NormalizedSample] = []
    previous_heading = 0.0
    previous_time = 0.0
    first_game_time: float | None = None

    for index, channels in enumerate(packets):
        game_time = _number(channels, "game_total_time")
        stage_time = _number(channels, "stage_current_time")
        if first_game_time is None:
            first_game_time = game_time
        time_s = (
            game_time - first_game_time if "game_total_time" in channels else max(0.0, stage_time)
        )

        velocity = _vector(channels, "vehicle_velocity")
        acceleration = _vector(channels, "vehicle_acceleration")
        forward = _vector(channels, "vehicle_forward_direction")
        left = _vector(channels, "vehicle_left_direction")
        up = _vector(channels, "vehicle_up_direction")
        if math.isclose(_dot(forward, forward), 0.0):
            forward = (0.0, 0.0, 1.0)
        if math.isclose(_dot(left, left), 0.0):
            left = (1.0, 0.0, 0.0)
        if math.isclose(_dot(up, up), 0.0):
            up = (0.0, 1.0, 0.0)

        heading = math.degrees(math.atan2(forward[0], forward[2]))
        dt = time_s - previous_time
        yaw_rate = _angle_delta(heading, previous_heading) / dt if index and dt > 0 else 0.0
        forward_speed = _dot(velocity, forward)
        lateral_speed = _dot(velocity, left)
        slip_angle = math.degrees(math.atan2(lateral_speed, abs(forward_speed) + 1e-9))

        wheel_speeds = [
            abs(_number(channels, f"vehicle_cp_forward_speed_{corner}"))
            for corner in ("bl", "br", "fl", "fr")
            if f"vehicle_cp_forward_speed_{corner}" in channels
        ]
        speed = abs(_number(channels, "vehicle_speed"))
        mean_wheel_speed = sum(wheel_speeds) / len(wheel_speeds) if wheel_speeds else speed
        wheel_slip = (mean_wheel_speed - speed) / max(speed, 1.0)

        result.append(
            NormalizedSample(
                time_s=time_s,
                stage_time_s=stage_time,
                packet_uid=_integer(channels, "packet_uid"),
                frame=_integer(channels, "game_frame_count"),
                speed_mps=speed,
                engine_rpm=_number(channels, "vehicle_engine_rpm_current"),
                gear=_integer(channels, "vehicle_gear_index"),
                throttle=_number(channels, "vehicle_throttle"),
                brake=_number(channels, "vehicle_brake"),
                clutch=_number(channels, "vehicle_clutch"),
                steering=_number(channels, "vehicle_steering"),
                handbrake=_number(channels, "vehicle_handbrake"),
                position_x_m=_number(channels, "vehicle_position_x"),
                position_y_m=_number(channels, "vehicle_position_y"),
                position_z_m=_number(channels, "vehicle_position_z"),
                velocity_x_mps=velocity[0],
                velocity_y_mps=velocity[1],
                velocity_z_mps=velocity[2],
                acceleration_x_mps2=acceleration[0],
                acceleration_y_mps2=acceleration[1],
                acceleration_z_mps2=acceleration[2],
                longitudinal_accel_mps2=_dot(acceleration, forward),
                lateral_accel_mps2=_dot(acceleration, left),
                vertical_accel_mps2=_dot(acceleration, up),
                heading_deg=heading,
                yaw_rate_deg_s=yaw_rate,
                slip_angle_deg=slip_angle,
                wheel_slip_ratio=wheel_slip,
                stage_distance_m=_number(channels, "stage_current_distance"),
                stage_progress=_number(channels, "stage_progress"),
            )
        )
        previous_heading = heading
        previous_time = time_s
    return result


@dataclass(frozen=True, slots=True)
class _Rule:
    event_type: str
    threshold: float
    value: Callable[[NormalizedSample], float]
    active: Callable[[NormalizedSample], bool]


def _rules(config: AnalysisConfig) -> tuple[_Rule, ...]:
    return (
        _Rule(
            "hard_braking",
            abs(config.hard_braking_mps2),
            lambda sample: abs(min(0.0, sample.longitudinal_accel_mps2)),
            lambda sample: (
                sample.longitudinal_accel_mps2 <= config.hard_braking_mps2
                and sample.speed_mps >= config.longitudinal_event_min_speed_mps
            ),
        ),
        _Rule(
            "hard_acceleration",
            config.hard_acceleration_mps2,
            lambda sample: max(0.0, sample.longitudinal_accel_mps2),
            lambda sample: (
                sample.longitudinal_accel_mps2 >= config.hard_acceleration_mps2
                and sample.speed_mps >= config.longitudinal_event_min_speed_mps
            ),
        ),
        _Rule(
            "high_lateral_acceleration",
            config.high_lateral_accel_mps2,
            lambda sample: abs(sample.lateral_accel_mps2),
            lambda sample: (
                abs(sample.lateral_accel_mps2) >= config.high_lateral_accel_mps2
                and sample.speed_mps >= config.high_lateral_min_speed_mps
            ),
        ),
        _Rule(
            "handbrake_at_speed",
            config.handbrake_min,
            lambda sample: sample.handbrake,
            lambda sample: (
                sample.handbrake >= config.handbrake_min
                and sample.speed_mps >= config.handbrake_min_speed_mps
            ),
        ),
        _Rule(
            "excessive_slip_angle",
            config.slip_angle_deg,
            lambda sample: abs(sample.slip_angle_deg),
            lambda sample: (
                abs(sample.slip_angle_deg) >= config.slip_angle_deg
                and sample.speed_mps >= config.slip_min_speed_mps
            ),
        ),
        _Rule(
            "wheelspin",
            config.wheelspin_ratio,
            lambda sample: max(0.0, sample.wheel_slip_ratio),
            lambda sample: (
                sample.wheel_slip_ratio >= config.wheelspin_ratio
                and sample.speed_mps >= config.wheelspin_min_speed_mps
                and sample.throttle >= config.pedal_overlap_min
            ),
        ),
        _Rule(
            "brake_throttle_overlap",
            config.pedal_overlap_min,
            lambda sample: min(sample.brake, sample.throttle),
            lambda sample: (
                sample.brake >= config.pedal_overlap_min
                and sample.throttle >= config.pedal_overlap_min
            ),
        ),
    )


def detect_events(
    samples: list[NormalizedSample], config: AnalysisConfig | None = None
) -> list[DrivingEvent]:
    """Detect and merge threshold exceedances without statistical black boxes."""

    cfg = config or AnalysisConfig()
    events: list[DrivingEvent] = []
    for rule in _rules(cfg):
        active: list[NormalizedSample] = []
        for sample in samples:
            if rule.active(sample):
                if active and sample.time_s - active[-1].time_s > cfg.merge_gap_s:
                    events.append(_build_event(rule, active))
                    active = []
                active.append(sample)
            elif active and sample.time_s - active[-1].time_s > cfg.merge_gap_s:
                events.append(_build_event(rule, active))
                active = []
        if active:
            events.append(_build_event(rule, active))
    return sorted(events, key=lambda event: (event.start_s, event.event_type))


def _build_event(rule: _Rule, samples: list[NormalizedSample]) -> DrivingEvent:
    peak_sample = max(samples, key=rule.value)
    peak = rule.value(peak_sample)
    start = samples[0].time_s
    end = samples[-1].time_s
    return DrivingEvent(
        event_type=rule.event_type,
        start_s=start,
        end_s=end,
        duration_s=max(0.0, end - start),
        peak_value=peak,
        threshold=rule.threshold,
        severity=peak / rule.threshold if rule.threshold else 0.0,
        stage_distance_m=peak_sample.stage_distance_m,
        sample_count=len(samples),
    )
