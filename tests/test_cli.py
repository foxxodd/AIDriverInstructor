from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tripcompiler.cli import main


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
