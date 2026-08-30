"""Generate, import, validate, and localize distance-indexed WRC pace notes."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tripcompiler.track import TrackPoint, TrackProfile


class PaceNoteError(ValueError):
    """A pace-note document is invalid or incompatible."""


@dataclass(frozen=True, slots=True)
class PaceNote:
    """One structured call anchored to stage distance."""

    note_id: str
    distance_m: float
    kind: str
    direction: str | None
    severity: int | None
    modifiers: tuple[str, ...]
    texts: dict[str, str]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["modifiers"] = list(self.modifiers)
        return value


@dataclass(frozen=True, slots=True)
class PaceNoteSet:
    """Editable multilingual note collection for one WRC route."""

    schema_version: int
    source: str
    location_id: int | None
    route_id: int | None
    languages: tuple[str, ...]
    notes: tuple[PaceNote, ...]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "location_id": self.location_id,
            "route_id": self.route_id,
            "languages": list(self.languages),
            "provenance": self.provenance,
            "notes": [note.to_dict() for note in self.notes],
        }


def generate_pace_notes(
    profile: TrackProfile,
    *,
    turn_threshold_rad_m: float = 0.003,
    minimum_heading_change_deg: float = 12.0,
) -> PaceNoteSet:
    """Generate conservative draft corner calls from reference-trace curvature."""

    if turn_threshold_rad_m <= 0:
        raise ValueError("turn_threshold_rad_m must be positive")
    groups = _corner_groups(profile.points, turn_threshold_rad_m)
    notes: list[PaceNote] = []
    for group in groups:
        heading_change = sum(abs(point.curvature_rad_m) * profile.sample_step_m for point in group)
        if math.degrees(heading_change) < minimum_heading_change_deg:
            continue
        peak_curvature = max(abs(point.curvature_rad_m) for point in group)
        radius = 1.0 / peak_curvature
        severity = _severity_for_radius(radius)
        direction = "left" if sum(point.curvature_rad_m for point in group) > 0 else "right"
        modifiers = _corner_modifiers(group)
        note_id = f"corner-{len(notes) + 1:03d}"
        texts = {
            "en": _english_corner(direction, severity, modifiers),
            "ru": _russian_corner(direction, severity, modifiers),
        }
        notes.append(
            PaceNote(
                note_id=note_id,
                distance_m=round(group[0].distance_m, 1),
                kind="corner",
                direction=direction,
                severity=severity,
                modifiers=modifiers,
                texts=texts,
                confidence=0.55,
            )
        )
    return PaceNoteSet(
        schema_version=1,
        source="geometry_draft",
        location_id=profile.location_id,
        route_id=profile.route_id,
        languages=("en", "ru"),
        notes=tuple(notes),
        provenance={
            "track_profile_schema": profile.schema_version,
            "automatic": True,
            "requires_human_review": True,
            "road_width_used": False,
            "warning": "Do not infer cut or hazard calls from a single vehicle trace.",
        },
    )


def import_zendrive_pace_notes(
    path: Path,
    *,
    location_id: int | None = None,
    route_id: int | None = None,
) -> PaceNoteSet:
    """Convert a user-supplied Zendrive-compatible file without redistributing its data."""

    if location_id is None or route_id is None:
        parsed_location, parsed_route = _ids_from_stem(path.stem)
        location_id = location_id if location_id is not None else parsed_location
        route_id = route_id if route_id is not None else parsed_route
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaceNoteError(f"Cannot read pace notes {path}: {exc}") from exc
    if not isinstance(document, list):
        raise PaceNoteError("Zendrive-compatible pace notes must be a list")

    notes: list[PaceNote] = []
    for index, raw in enumerate(document, start=1):
        if not isinstance(raw, list) or len(raw) < 2:
            raise PaceNoteError(f"Invalid pace note at index {index - 1}")
        distance = raw[0]
        phrases = raw[1]
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise PaceNoteError(f"Pace-note distance at index {index - 1} must be numeric")
        if not isinstance(phrases, list) or not all(isinstance(item, str) for item in phrases):
            raise PaceNoteError(f"Pace-note phrases at index {index - 1} must be strings")
        conditions = raw[2] if len(raw) > 2 and isinstance(raw[2], dict) else {}
        modifiers = tuple(
            f"condition:{key}={str(value).lower()}" for key, value in sorted(conditions.items())
        )
        notes.append(
            PaceNote(
                note_id=f"imported-{index:03d}",
                distance_m=float(distance),
                kind="imported_call",
                direction=None,
                severity=None,
                modifiers=modifiers,
                texts={"en": " ".join(phrases)},
                confidence=0.8,
            )
        )

    return PaceNoteSet(
        schema_version=1,
        source="user_supplied_zendrive_compatible",
        location_id=location_id,
        route_id=route_id,
        languages=("en",),
        notes=tuple(notes),
        provenance={
            "input": str(path),
            "redistribution_rights": "not_asserted_by_tripcompiler",
            "requires_user_license_review": True,
        },
    )


def write_pace_notes(path: Path, note_set: PaceNoteSet) -> None:
    """Write editable notes without replacing existing user corrections."""

    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(note_set.to_dict(), stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def load_pace_notes(path: Path) -> PaceNoteSet:
    """Load the native editable pace-note schema."""

    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaceNoteError(f"Cannot read pace notes {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise PaceNoteError("Unsupported pace-note schema")
    raw_notes = document.get("notes")
    if not isinstance(raw_notes, list):
        raise PaceNoteError("Pace-note document requires a notes list")
    notes: list[PaceNote] = []
    try:
        for raw in raw_notes:
            if not isinstance(raw, dict):
                raise PaceNoteError("Every pace note must be an object")
            texts = raw.get("texts")
            modifiers = raw.get("modifiers", [])
            if not isinstance(texts, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in texts.items()
            ):
                raise PaceNoteError("Pace-note texts must map language codes to strings")
            if not isinstance(modifiers, list) or not all(
                isinstance(item, str) for item in modifiers
            ):
                raise PaceNoteError("Pace-note modifiers must be strings")
            severity = raw.get("severity")
            if severity is not None and (
                isinstance(severity, bool) or not isinstance(severity, int)
            ):
                raise PaceNoteError("Pace-note severity must be an integer or null")
            notes.append(
                PaceNote(
                    note_id=str(raw["note_id"]),
                    distance_m=float(raw["distance_m"]),
                    kind=str(raw["kind"]),
                    direction=str(raw["direction"]) if raw.get("direction") is not None else None,
                    severity=severity,
                    modifiers=tuple(modifiers),
                    texts=dict(texts),
                    confidence=float(raw["confidence"]),
                )
            )
        languages = document.get("languages", [])
        provenance = document.get("provenance", {})
        if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
            raise PaceNoteError("Pace-note languages must be strings")
        if not isinstance(provenance, dict):
            raise PaceNoteError("Pace-note provenance must be an object")
        return PaceNoteSet(
            schema_version=1,
            source=str(document["source"]),
            location_id=_optional_int(document.get("location_id")),
            route_id=_optional_int(document.get("route_id")),
            languages=tuple(languages),
            notes=tuple(sorted(notes, key=lambda note: note.distance_m)),
            provenance=dict(provenance),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PaceNoteError):
            raise
        raise PaceNoteError(f"Invalid pace-note document {path}: {exc}") from exc


def _corner_groups(points: tuple[TrackPoint, ...], threshold: float) -> list[list[TrackPoint]]:
    groups: list[list[TrackPoint]] = []
    active: list[TrackPoint] = []
    active_sign = 0
    last_strong_distance = 0.0
    for point in points:
        sign = (
            1
            if point.curvature_rad_m >= threshold
            else -1
            if point.curvature_rad_m <= -threshold
            else 0
        )
        close = bool(active) and point.distance_m - last_strong_distance <= 12.0
        if sign and (not active or (sign == active_sign and close)):
            active.append(point)
            active_sign = sign
            last_strong_distance = point.distance_m
        elif sign:
            groups.append(active)
            active = [point]
            active_sign = sign
            last_strong_distance = point.distance_m
        elif active and close:
            active.append(point)
        elif active:
            groups.append(active)
            active = []
            active_sign = 0
    if active:
        groups.append(active)
    return groups


def _corner_modifiers(group: list[TrackPoint]) -> tuple[str, ...]:
    modifiers: list[str] = []
    length = group[-1].distance_m - group[0].distance_m
    if length >= 70.0:
        modifiers.append("long")
    split = max(1, len(group) // 2)
    first = sum(abs(point.curvature_rad_m) for point in group[:split]) / split
    second_group = group[split:] or group[-1:]
    second = sum(abs(point.curvature_rad_m) for point in second_group) / len(second_group)
    if second > first * 1.35:
        modifiers.append("tightens")
    elif first > second * 1.35:
        modifiers.append("opens")
    return tuple(modifiers)


def _severity_for_radius(radius_m: float) -> int:
    if radius_m < 20:
        return 1
    if radius_m < 35:
        return 2
    if radius_m < 55:
        return 3
    if radius_m < 85:
        return 4
    if radius_m < 130:
        return 5
    return 6


def _english_corner(direction: str, severity: int, modifiers: tuple[str, ...]) -> str:
    parts = [direction, str(severity)]
    parts.extend(modifiers)
    return " ".join(parts)


def _russian_corner(direction: str, severity: int, modifiers: tuple[str, ...]) -> str:
    translations = {
        "left": "\u043b\u0435\u0432\u044b\u0439",
        "right": "\u043f\u0440\u0430\u0432\u044b\u0439",
        "long": "\u0434\u043b\u0438\u043d\u043d\u044b\u0439",
        "tightens": "\u0441\u0443\u0436\u0430\u0435\u0442\u0441\u044f",
        "opens": "\u0440\u0430\u0441\u043a\u0440\u044b\u0432\u0430\u0435\u0442\u0441\u044f",
    }
    parts = [translations[direction], str(severity)]
    parts.extend(translations[modifier] for modifier in modifiers)
    return " ".join(parts)


def _ids_from_stem(stem: str) -> tuple[int | None, int | None]:
    parts = stem.split("-", maxsplit=1)
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
