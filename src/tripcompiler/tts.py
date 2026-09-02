"""Pre-generate and cache multilingual pace-note speech."""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
