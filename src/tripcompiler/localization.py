"""Load extensible phrase dictionaries for pace-note languages."""

from __future__ import annotations

import json
from functools import cache
from importlib.resources import files
from typing import Any


class PaceNoteLocaleError(ValueError):
    """A packaged pace-note language dictionary is invalid."""


def available_pace_note_languages() -> tuple[str, ...]:
    """Return packaged language codes in stable order."""

    directory = files("tripcompiler").joinpath("locales/pacenotes")
    return tuple(
        sorted(
            item.name.removesuffix(".json")
            for item in directory.iterdir()
            if item.name.endswith(".json")
        )
    )


@cache
def load_pace_note_dictionary(language: str) -> dict[str, str]:
    """Load and validate one exact-phrase translation dictionary."""

    resource = files("tripcompiler").joinpath(f"locales/pacenotes/{language}.json")
    try:
        document: Any = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaceNoteLocaleError(f"Cannot read pace-note locale {language!r}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PaceNoteLocaleError(f"Unsupported pace-note locale schema for {language!r}")
    if document.get("language") != language:
        raise PaceNoteLocaleError(f"Pace-note locale language mismatch for {language!r}")
    phrases = document.get("phrases")
    if not isinstance(phrases, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in phrases.items()
    ):
        raise PaceNoteLocaleError(f"Pace-note locale {language!r} requires string phrases")
    return dict(phrases)


def localize_pace_note_phrases(phrases: list[str], language: str) -> str | None:
    """Translate exact upstream phrases, returning none if vocabulary is incomplete."""

    dictionary = load_pace_note_dictionary(language)
    localized: list[str] = []
    for phrase in phrases:
        translated = dictionary.get(phrase)
        if translated is None:
            return None
        localized.append(translated)
    return " ".join(localized)
