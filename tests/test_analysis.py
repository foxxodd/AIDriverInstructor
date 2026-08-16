from __future__ import annotations

import pytest

from tripcompiler.analysis import detect_events, normalize_packets


def _packet(
    uid: int,
    time_s: float,
    *,
    acceleration_x: float = 0.0,
    acceleration_z: float = 0.0,
    speed: float = 20.0,
    lateral_velocity: float = 0.0,
    throttle: float = 0.0,
    brake: float = 0.0,
    handbrake: float = 0.0,
    wheel_speed: float | None = None,
) -> dict[str, bool | int | float | str]:
    packet: dict[str, bool | int | float | str] = {
        "packet_uid": uid,
        "game_total_time": 100.0 + time_s,
        "game_frame_count": uid * 2,
        "stage_current_time": time_s,
        "stage_current_distance": time_s * speed,
        "stage_progress": time_s / 100.0,
        "vehicle_speed": speed,
        "vehicle_engine_rpm_current": 5000.0,
        "vehicle_gear_index": 4,
        "vehicle_throttle": throttle,
        "vehicle_brake": brake,
        "vehicle_clutch": 0.0,
        "vehicle_steering": 0.2,
        "vehicle_handbrake": handbrake,
        "vehicle_position_x": 1.0,
        "vehicle_position_y": 2.0,
        "vehicle_position_z": time_s * speed,
        "vehicle_velocity_x": lateral_velocity,
        "vehicle_velocity_y": 0.0,
        "vehicle_velocity_z": speed,
        "vehicle_acceleration_x": acceleration_x,
        "vehicle_acceleration_y": 0.0,
        "vehicle_acceleration_z": acceleration_z,
        "vehicle_left_direction_x": 1.0,
        "vehicle_left_direction_y": 0.0,
        "vehicle_left_direction_z": 0.0,
        "vehicle_forward_direction_x": 0.0,
        "vehicle_forward_direction_y": 0.0,
        "vehicle_forward_direction_z": 1.0,
        "vehicle_up_direction_x": 0.0,
        "vehicle_up_direction_y": 1.0,
        "vehicle_up_direction_z": 0.0,
    }
    if wheel_speed is not None:
        for corner in ("bl", "br", "fl", "fr"):
            packet[f"vehicle_cp_forward_speed_{corner}"] = wheel_speed
    return packet


def test_normalization_projects_vectors_and_derives_slip() -> None:
    samples = normalize_packets(
        [
            _packet(1, 0.0),
            _packet(
                2,
                0.1,
                acceleration_x=8.0,
                acceleration_z=-7.0,
                lateral_velocity=10.0,
                wheel_speed=30.0,
            ),
        ]
    )

    sample = samples[1]
    assert sample.longitudinal_accel_mps2 == -7.0
    assert sample.lateral_accel_mps2 == 8.0
    assert sample.vertical_accel_mps2 == 0.0
    assert sample.slip_angle_deg == pytest.approx(26.565, rel=1e-3)
    assert sample.wheel_slip_ratio == 0.5
    assert sample.yaw_rate_deg_s == 0.0


def test_event_detection_merges_adjacent_samples_and_finds_rule_types() -> None:
    samples = normalize_packets(
        [
            _packet(1, 1.0, acceleration_z=-7.0, brake=0.8, throttle=0.4),
            _packet(
                2,
                1.1,
                acceleration_x=8.0,
                acceleration_z=-8.0,
                brake=0.7,
                throttle=0.7,
                handbrake=0.8,
                lateral_velocity=10.0,
                wheel_speed=30.0,
            ),
            _packet(3, 2.0, acceleration_z=6.0, throttle=0.8),
        ]
    )

    events = detect_events(samples)
    by_type = {event.event_type: event for event in events}
    assert set(by_type) == {
        "brake_throttle_overlap",
        "excessive_slip_angle",
        "handbrake_at_speed",
        "hard_acceleration",
        "hard_braking",
        "high_lateral_acceleration",
        "wheelspin",
    }
    assert by_type["hard_braking"].sample_count == 2
    assert by_type["hard_braking"].peak_value == 8.0
    assert by_type["hard_braking"].duration_s == pytest.approx(0.1)


def test_missing_direction_channels_use_identity_axes() -> None:
    samples = normalize_packets(
        [
            {
                "packet_uid": 1,
                "game_total_time": 12.0,
                "vehicle_speed": 1.0,
                "vehicle_acceleration_z": 2.5,
            }
        ]
    )
    assert samples[0].time_s == 0.0
    assert samples[0].longitudinal_accel_mps2 == 2.5
    assert detect_events([]) == []
