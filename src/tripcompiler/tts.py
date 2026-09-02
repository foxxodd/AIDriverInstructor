"""Pre-generate and cache multilingual pace-note speech."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tripcompiler.pacenotes import PaceNoteSet


class TtsError(RuntimeError):
    """Speech synthesis failed or is not configured."""


class TtsProvider(Protocol):
    """Speech backend contract used by the cache builder."""

    @property
    def provider_id(self) -> str: ...

    def synthesize(self, text: str, language: str, output_path: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class OpenAITtsProvider:
    """OpenAI Speech API backend loaded only when the optional SDK is installed."""

    model: str = "gpt-4o-mini-tts"
    voice: str = "coral"
    instructions: str = "Speak quickly and clearly like a professional rally co-driver."

    @property
    def provider_id(self) -> str:
        return f"openai:{self.model}:{self.voice}"

    def synthesize(self, text: str, language: str, output_path: Path) -> None:
        try:
            module = importlib.import_module("openai")
            client_class = module.OpenAI
            client: Any = client_class()
            request: dict[str, Any] = {
                "model": self.model,
                "voice": self.voice,
                "input": text,
                "response_format": "wav",
            }
            if self.instructions:
                request["instructions"] = self.instructions
            response = client.audio.speech.create(**request)
            response.write_to_file(output_path)
        except (ImportError, AttributeError) as exc:
            raise TtsError("Install the optional 'tts' dependency to use OpenAI speech") from exc
        except Exception as exc:
            raise TtsError(
                f"OpenAI speech synthesis failed for language {language}: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True)
class CommandTtsProvider:
    """Offline backend that passes text on stdin to Piper or another executable."""

    command: tuple[str, ...]
    name: str = "command"

    @property
    def provider_id(self) -> str:
        return self.name

    def synthesize(self, text: str, language: str, output_path: Path) -> None:
        if not self.command:
            raise TtsError("TTS command cannot be empty")
        arguments = [
            item.replace("{output}", str(output_path)).replace("{language}", language)
            for item in self.command
        ]
        try:
            subprocess.run(
                arguments,
                input=text,
                text=True,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            details = exc.stderr or exc.stdout
            if isinstance(details, bytes):
                details = details.decode(errors="replace")
            if isinstance(details, str) and details.strip():
                detail = details.strip().splitlines()[-1]
            else:
                detail = f"exit status {exc.returncode}"
            raise TtsError(f"External TTS command failed: {detail}") from exc
        except OSError as exc:
            raise TtsError(f"External TTS command failed: {exc}") from exc
        if not output_path.is_file():
            raise TtsError("External TTS command did not create the requested output file")


def prepare_audio_cache(
    note_set: PaceNoteSet,
    language: str,
    provider: TtsProvider,
    output_dir: Path,
) -> dict[str, Any]:
    """Generate missing WAV calls and write a deterministic cache manifest."""

    output_dir.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, Any]] = {}
    for note in note_set.notes:
        text = note.texts.get(language) or note.texts.get("en")
        if not text:
            continue
        digest = hashlib.sha256(f"{provider.provider_id}\0{language}\0{text}".encode()).hexdigest()[
            :16
        ]
        filename = f"{note.note_id}-{language}-{digest}.wav"
        audio_path = output_dir / filename
        if not audio_path.exists():
            provider.synthesize(text, language, audio_path)
        entries[note.note_id] = {
            "file": filename,
            "language": language,
            "text": text,
            "provider": provider.provider_id,
        }
    manifest = {
        "schema_version": 1,
        "language": language,
        "provider": provider.provider_id,
        "entries": entries,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest
