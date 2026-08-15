"""Typed domain models shared by decoding, capture, and analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

Scalar = bool | int | float | str


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One channel in a packed UDP packet."""

    channel_id: str
    channel_type: str
    units: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class NormalizedSample:
    """Stable TripCompiler row derived from one WRC update packet."""

    time_s: float
    stage_time_s: float
    packet_uid: int
    frame: int
    speed_mps: float
    engine_rpm: float
    gear: int
    throttle: float
    brake: float
    clutch: float
    steering: float
    handbrake: float
    position_x_m: float
    position_y_m: float
    position_z_m: float
    velocity_x_mps: float
    velocity_y_mps: float
    velocity_z_mps: float
    acceleration_x_mps2: float
    acceleration_y_mps2: float
    acceleration_z_mps2: float
    longitudinal_accel_mps2: float
    lateral_accel_mps2: float
    vertical_accel_mps2: float
    heading_deg: float
    yaw_rate_deg_s: float
    slip_angle_deg: float
    wheel_slip_ratio: float
    stage_distance_m: float
    stage_progress: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DrivingEvent:
    """A consolidated interval where one driving condition was exceeded."""

    event_type: str
    start_s: float
    end_s: float
    duration_s: float
    peak_value: float
    threshold: float
    severity: float
    stage_distance_m: float
    sample_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Thresholds for deterministic MVP event detection."""

    hard_braking_mps2: float = -6.0
    hard_acceleration_mps2: float = 5.0
    high_lateral_accel_mps2: float = 7.0
    handbrake_min: float = 0.5
    handbrake_min_speed_mps: float = 5.0
    slip_angle_deg: float = 20.0
    slip_min_speed_mps: float = 10.0
    wheelspin_ratio: float = 0.25
    wheelspin_min_speed_mps: float = 5.0
    pedal_overlap_min: float = 0.3
    merge_gap_s: float = 0.30
