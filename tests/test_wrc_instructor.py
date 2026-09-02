from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

import pytest

from tripcompiler.analysis import normalize_packets
from tripcompiler.codriver import preview_codriver
from tripcompiler.compiler import compile_trip
from tripcompiler.localization import (
    available_pace_note_languages,
    load_pace_note_dictionary,
    localize_pace_note_phrases,
)
from tripcompiler.pacenotes import (
    PaceNote,
    PaceNoteSet,
    generate_pace_notes,
    import_zendrive_pace_notes,
    load_pace_notes,
    write_pace_notes,
)
from tripcompiler.scheduler import PaceNoteScheduler
from tripcompiler.track import (
    TrackProfileError,
    build_track_profile,
    load_track_profile,
    write_track_profile,
)
from tripcompiler.tts import CommandTtsProvider, TtsError, prepare_audio_cache
from tripcompiler.wrc_catalog import enrich_wrc_metadata, load_wrc_catalog


def _curved_packets() -> list[dict[str, float | int]]:
    packets: list[dict[str, float | int]] = []
    radius = 50.0
    for index, distance in enumerate(range(0, 102, 2)):
        angle = distance / radius
        packets.append(
            {
                "packet_uid": index + 1,
                "game_total_time": 100.0 + distance / 20.0,
                "game_frame_count": index,
                "stage_current_time": distance / 20.0,
                "stage_current_distance": float(distance),
                "stage_length": 100.0,
                "stage_progress": distance / 100.0,
                "vehicle_speed": 20.0,
                "vehicle_position_x": radius * (1.0 - math.cos(angle)),
                "vehicle_position_y": distance * 0.02,
                "vehicle_position_z": radius * math.sin(angle),
                "vehicle_forward_direction_x": math.sin(angle),
                "vehicle_forward_direction_y": 0.0,
                "vehicle_forward_direction_z": math.cos(angle),
                "vehicle_left_direction_x": math.cos(angle),
                "vehicle_left_direction_y": 0.0,
                "vehicle_left_direction_z": -math.sin(angle),
                "vehicle_up_direction_x": 0.0,
                "vehicle_up_direction_y": 1.0,
                "vehicle_up_direction_z": 0.0,
                "location_id": 27,
                "route_id": 360,
                "vehicle_id": 5,
                "vehicle_class_id": 21,
                "vehicle_manufacturer_id": 5,
            }
        )
    return packets


def _catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "versions": {"schema": 1, "data": {"build": 2, "major": 3, "minor": 1}},
                "vehicles": [{"id": 5, "name": "Ford Fiesta Rally2"}],
                "vehicle_classes": [{"id": 21, "name": "WRC2"}],
                "vehicle_manufacturers": [{"id": 5, "name": "Ford"}],
                "locations": [{"id": 27, "name": "FANATEC RALLY OCEANIA"}],
                "routes": [{"id": 360, "name": "Taipuha"}],
                "game_mode": [{"id": 2, "name": "Time Trial"}],
                "stage_result_status": [{"id": 1, "name": "Finished"}],
            }
        ),
        encoding="utf-8",
    )


def _native_notes() -> PaceNoteSet:
    return PaceNoteSet(
        schema_version=1,
        source="test",
        location_id=27,
        route_id=360,
        languages=("en", "ru"),
        notes=(
            PaceNote(
                note_id="n1",
                distance_m=50.0,
                kind="corner",
                direction="left",
                severity=4,
                modifiers=(),
                texts={
                    "en": "left four",
                    "ru": "\u043b\u0435\u0432\u044b\u0439 \u0447\u0435\u0442\u044b\u0440\u0435",
                },
                confidence=1.0,
            ),
            PaceNote(
                note_id="n2",
                distance_m=100.0,
                kind="corner",
                direction="right",
                severity=3,
                modifiers=("tightens",),
                texts={
                    "en": "right three tightens",
                    "ru": "\u043f\u0440\u0430\u0432\u044b\u0439 \u0442\u0440\u0438 \u0441\u0443\u0436\u0430\u0435\u0442\u0441\u044f",
                },
                confidence=1.0,
            ),
        ),
        provenance={"test": True},
    )


def test_catalog_resolves_wrc_ids(tmp_path: Path) -> None:
    path = tmp_path / "ids.json"
    _catalog(path)

    catalog = load_wrc_catalog(path)
    metadata = enrich_wrc_metadata(
        {
            "vehicle_id": 5,
            "vehicle_class_id": 21,
            "vehicle_manufacturer_id": 5,
            "location_id": 27,
            "route_id": 360,
            "game_mode": 2,
            "stage_result_status": 1,
        },
        catalog,
    )

    assert metadata["route"] == "Taipuha"
    assert metadata["vehicle"] == "Ford Fiesta Rally2"
    assert metadata["id_catalog_version"]["data"]["major"] == 3


def test_catalog_supports_game_generated_utf16(tmp_path: Path) -> None:
    utf8_path = tmp_path / "ids-utf8.json"
    _catalog(utf8_path)
    path = tmp_path / "ids.json"
    path.write_text(utf8_path.read_text(encoding="utf-8"), encoding="utf-16")

    assert load_wrc_catalog(path).lookup("routes", 360) == {"id": 360, "name": "Taipuha"}


def test_packaged_pace_note_locales_cover_zendrive_vocabulary() -> None:
    english = load_pace_note_dictionary("en")
    russian = load_pace_note_dictionary("ru")

    assert available_pace_note_languages() == ("en", "ru")
    assert len(english) == 216
    assert english.keys() == russian.keys()
    assert localize_pace_note_phrases(["four right", "tightens", "opens"], "ru") == (
        "\u043f\u0440\u0430\u0432\u043e 4 "
        "\u0441\u0443\u0436\u0435\u043d\u0438\u0435 "
        "\u0440\u0430\u0441\u043a\u0440\u044b\u0442\u0438\u0435"
    )


def test_track_profile_and_draft_notes_round_trip(tmp_path: Path) -> None:
    profile = build_track_profile(
        normalize_packets(_curved_packets()),
        {
            "location_id": 27,
            "location": "FANATEC RALLY OCEANIA",
            "route_id": 360,
            "route": "Taipuha",
            "stage_length_m": 100.0,
        },
    )

    assert profile.width["status"] == "unknown"
    assert profile.points[-1].distance_m == pytest.approx(100.0)
    assert max(point.curvature_rad_m for point in profile.points) == pytest.approx(0.02, rel=0.3)

    profile_path = tmp_path / "track_profile.json"
    write_track_profile(profile_path, profile)
    assert load_track_profile(profile_path).route_name == "Taipuha"

    note_set = generate_pace_notes(profile)
    assert note_set.languages == ("en", "ru")
    assert note_set.notes
    assert note_set.notes[0].direction == "left"
    assert note_set.notes[0].texts["ru"].startswith("\u043b\u0435\u0432\u043e 3")

    notes_path = tmp_path / "pace_notes.json"
    write_pace_notes(notes_path, note_set)
    assert load_pace_notes(notes_path).notes[0].texts["en"].startswith("left")


def test_stationary_trace_is_rejected() -> None:
    packets = _curved_packets()
    for packet in packets:
        packet["vehicle_position_x"] = 0.0
        packet["vehicle_position_y"] = 0.0
        packet["vehicle_position_z"] = 0.0
    with pytest.raises(TrackProfileError, match="moving reference"):
        build_track_profile(normalize_packets(packets), {"stage_length_m": 100.0})


def test_zendrive_import_is_user_supplied_and_distance_indexed(tmp_path: Path) -> None:
    source = tmp_path / "27-360.json"
    source.write_text(
        json.dumps(
            [
                [16, ["slight right", "40"]],
                [64, ["ice now"], {"winter": True}],
            ]
        ),
        encoding="utf-8",
    )

    note_set = import_zendrive_pace_notes(source)

    assert note_set.location_id == 27
    assert note_set.route_id == 360
    assert note_set.languages == ("en", "ru")
    assert note_set.notes[0].texts["en"] == "slight right 40"
    assert note_set.notes[0].texts["ru"] == (
        "\u043b\u0451\u0433\u043a\u043e\u0435 \u043f\u0440\u0430\u0432\u043e 40"
    )
    assert note_set.notes[1].texts["ru"] == "\u043b\u0451\u0434"
    assert note_set.notes[1].modifiers == ("condition:winter=true",)
    assert note_set.provenance["requires_user_license_review"] is True


def test_scheduler_supports_languages_and_stage_restart() -> None:
    scheduler = PaceNoteScheduler(_native_notes(), language="ru", lead_time_s=2.0)

    assert scheduler.update(0.0, 5.0) == []
    first = scheduler.update(30.0, 5.0)
    assert [call.note_id for call in first] == ["n1"]
    assert first[0].text == "\u043b\u0435\u0432\u044b\u0439 \u0447\u0435\u0442\u044b\u0440\u0435"
    assert [call.note_id for call in scheduler.update(80.0, 10.0)] == ["n2"]
    assert scheduler.update(0.0, 5.0) == []
    assert [call.note_id for call in scheduler.update(30.0, 5.0)] == ["n1"]


def test_preview_and_tts_cache_use_same_note_ids(tmp_path: Path) -> None:
    note_set = _native_notes()
    calls = preview_codriver(
        [
            {"stage_current_distance": 0.0, "vehicle_speed": 5.0},
            {"stage_current_distance": 30.0, "vehicle_speed": 5.0},
            {"stage_current_distance": 80.0, "vehicle_speed": 10.0},
        ],
        note_set,
        lead_time_s=2.0,
    )
    assert [call.note_id for call in calls] == ["n1", "n2"]

    provider = _FakeTts()
    manifest = prepare_audio_cache(note_set, "en", provider, tmp_path / "audio")
    assert set(manifest["entries"]) == {"n1", "n2"}
    assert provider.calls == 2
    prepare_audio_cache(note_set, "en", provider, tmp_path / "audio")
    assert provider.calls == 2


def test_wrc_compile_enriches_summary_and_creates_track_artifacts(tmp_path: Path) -> None:
    capture = tmp_path / "telemetry.jsonl"
    capture.write_text(
        "".join(json.dumps({"channels": packet}) + "\n" for packet in _curved_packets()),
        encoding="utf-8",
    )
    ids = tmp_path / "ids.json"
    _catalog(ids)
    pacenotes_dir = tmp_path / "pacenotes"
    pacenotes_dir.mkdir()
    (pacenotes_dir / "27-360.json").write_text(
        json.dumps([[1, ["120"]], [50, ["left four"]]]),
        encoding="utf-8",
    )
    output = tmp_path / "compiled"

    summary = compile_trip(
        "wrc",
        capture,
        output,
        wrc_ids_path=ids,
        wrc_pacenotes_dir=pacenotes_dir,
    )

    assert summary["source_metadata"]["route"] == "Taipuha"
    assert summary["track_profile"] == "generated"
    assert summary["pace_notes"] == "zendrive_imported"
    assert (output / "track_profile.json").is_file()
    assert (output / "pace_notes.draft.json").is_file()
    imported = load_pace_notes(output / "pace_notes.json")
    assert imported.source == "user_supplied_zendrive_compatible"
    assert len(imported.notes) == 2


class _FakeTts:
    provider_id = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, language: str, output_path: Path) -> None:
        self.calls += 1
        output_path.write_bytes(f"{language}:{text}".encode())


def test_command_tts_reports_subprocess_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(
            1,
            ["piper"],
            stderr="traceback\nONNX model Protobuf parsing failed",
        )

    monkeypatch.setattr("tripcompiler.tts.subprocess.run", fail)
    provider = CommandTtsProvider(("piper", "-f", "{output}"), name="piper:test")

    with pytest.raises(TtsError, match="ONNX model Protobuf parsing failed"):
        provider.synthesize("test", "en", tmp_path / "test.wav")
