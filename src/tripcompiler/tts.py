"""Pre-generate and cache multilingual pace-note speech."""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from dotenv import load_dotenv

from tripcompiler.pacenotes import PaceNoteSet

DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "cedar"
DEFAULT_OPENAI_INSTRUCTIONS = (
    "Speak as a professional rally co-driver. Use a firm, energetic, highly intelligible "
    "delivery. Keep every call short and urgent, with crisp consonants and no conversational "
    "filler."
)

_LANGUAGE_INSTRUCTIONS = {
    "en": "Use natural English pronunciation.",
    "ru": "Use natural Russian pronunciation and articulate directions and numbers distinctly.",
}


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

    model: str = DEFAULT_OPENAI_MODEL
    voice: str = DEFAULT_OPENAI_VOICE
    instructions: str = DEFAULT_OPENAI_INSTRUCTIONS
    env_file: Path | None = Path(".env.dev")

    @property
    def provider_id(self) -> str:
        instruction_config = json.dumps(
            {"base": self.instructions, "languages": _LANGUAGE_INSTRUCTIONS},
            sort_keys=True,
        )
        instruction_digest = hashlib.sha256(instruction_config.encode()).hexdigest()[:12]
        return f"openai:{self.model}:{self.voice}:{instruction_digest}"

    def synthesize(self, text: str, language: str, output_path: Path) -> None:
        try:
            if self.env_file is not None:
                load_dotenv(dotenv_path=self.env_file, override=False)
            module = importlib.import_module("openai")
            client_class = module.OpenAI
            client: Any = client_class()
            request: dict[str, Any] = {
                "model": self.model,
                "voice": self.voice,
                "input": text,
                "response_format": "wav",
            }
            language_instruction = _LANGUAGE_INSTRUCTIONS.get(language)
            instructions = " ".join(
                part for part in (self.instructions, language_instruction) if part
            )
            if instructions:
                request["instructions"] = instructions
            response = client.audio.speech.create(**request)
            response.write_to_file(output_path)
        except (ImportError, AttributeError) as exc:
            raise TtsError("Install the optional 'tts' dependency to use OpenAI speech") from exc
        except Exception as exc:
            raise TtsError(
                f"OpenAI speech synthesis failed for language {language}: {exc}"
            ) from exc


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


def prepare_audio_catalog(
    note_sets: Mapping[str, PaceNoteSet],
    language: str,
    provider: TtsProvider,
    output_dir: Path,
) -> dict[str, Any]:
    """Generate deduplicated speech and one cache manifest per route."""

    if not note_sets:
        raise TtsError("Audio catalog requires at least one pace-note route")
    files_dir = output_dir / "files"
    routes_dir = output_dir / "routes"
    files_dir.mkdir(parents=True, exist_ok=True)
    routes_dir.mkdir(parents=True, exist_ok=True)

    route_summaries: list[dict[str, Any]] = []
    unique_files: set[str] = set()
    synthesized_files = 0
    for route_code, note_set in sorted(note_sets.items()):
        entries: dict[str, dict[str, Any]] = {}
        for note in note_set.notes:
            text = note.texts.get(language)
            if text is None:
                raise TtsError(f"Route {route_code} note {note.note_id} has no {language} text")
            digest = hashlib.sha256(
                f"{provider.provider_id}\0{language}\0{text}".encode()
            ).hexdigest()[:16]
            filename = f"{language}-{digest}.wav"
            audio_path = files_dir / filename
            if not audio_path.is_file():
                provider.synthesize(text, language, audio_path)
                synthesized_files += 1
            unique_files.add(filename)
            entries[note.note_id] = {
                "file": f"../../files/{filename}",
                "language": language,
                "text": text,
                "provider": provider.provider_id,
            }

        route_dir = routes_dir / route_code
        route_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "route_code": route_code,
            "location_id": note_set.location_id,
            "route_id": note_set.route_id,
            "language": language,
            "provider": provider.provider_id,
            "entries": entries,
        }
        (route_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        route_summaries.append(
            {
                "route_code": route_code,
                "manifest": f"routes/{route_code}/manifest.json",
                "notes": len(entries),
            }
        )

    catalog = {
        "schema_version": 1,
        "language": language,
        "provider": provider.provider_id,
        "routes": route_summaries,
        "unique_audio_files": len(unique_files),
        "synthesized_audio_files": synthesized_files,
    }
    (output_dir / "catalog.json").write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return catalog
