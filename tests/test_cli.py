from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tripcompiler.cli import build_parser, main


def _schemas(tmp_path: Path) -> tuple[Path, Path]:
    telemetry = tmp_path / "telemetry"
    readme = telemetry / "readme"
    readme.mkdir(parents=True)
    (readme / "channels.json").write_text(
        json.dumps({"channels": [{"id": "packet_uid", "type": "uint64"}]}),
        encoding="utf-8",
    )
    structure = tmp_path / "structure.json"
    structure.write_text(
        json.dumps(
            {
                "header": {"channels": []},
                "packets": [{"id": "session_update", "channels": ["packet_uid"]}],
            }
        ),
        encoding="utf-8",
    )
    return telemetry, structure


def test_validate_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    telemetry, structure = _schemas(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tripcompiler",
            "validate-wrc",
            "--telemetry-dir",
            str(telemetry),
            "--structure",
            str(structure),
        ],
    )
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["packet_size"] == 8
    assert output["fields"] == ["packet_uid"]


def test_compile_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capture = tmp_path / "telemetry.jsonl"
    capture.write_text(
        json.dumps({"channels": {"packet_uid": 1, "vehicle_speed": 2.0}}) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "result"
    monkeypatch.setattr(
        sys,
        "argv",
        ["tripcompiler", "compile", "wrc", str(capture), "--output", str(output)],
    )
    assert main() == 0
    assert (output / "summary.json").is_file()


def test_prepare_voice_uses_openai_quality_defaults() -> None:
    args = build_parser().parse_args(
        [
            "prepare-wrc-voice",
            "pace_notes.json",
            "--output",
            "audio",
        ]
    )

    assert args.provider == "openai"
    assert args.model == "gpt-4o-mini-tts"
    assert args.voice == "cedar"
    assert args.speed == 1.5
    assert "highly intelligible" in args.instructions


def test_batch_catalog_commands_use_language_defaults() -> None:
    notes_args = build_parser().parse_args(["prepare-wrc-pacenotes", "--language", "ru"])
    audio_args = build_parser().parse_args(
        ["prepare-wrc-audio", "--language", "ru", "--route", "27-360"]
    )

    assert notes_args.output is None
    assert notes_args.source is None
    assert audio_args.notes_dir is None
    assert audio_args.output is None
    assert audio_args.route == "27-360"
    assert audio_args.env_file == ".env.dev"
    assert audio_args.speed == 1.5
    with pytest.raises(SystemExit):
        build_parser().parse_args(["prepare-wrc-audio", "--language", "ru"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "prepare-wrc-audio",
                "--language",
                "ru",
                "--route",
                "27-360",
                "--speed",
                "4.1",
            ]
        )


def test_existing_output_reports_concise_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    source = tmp_path / "27-360.json"
    source.write_text("[]", encoding="utf-8")
    output = tmp_path / "pace_notes.json"
    output.write_text("existing user data", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tripcompiler",
            "import-wrc-notes",
            str(source),
            "--output",
            str(output),
        ],
    )

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "File exists" in error
    assert "Traceback" not in error
    assert output.read_text(encoding="utf-8") == "existing user data"
