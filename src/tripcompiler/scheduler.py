"""Deterministic distance-based pace-note scheduling for live or replayed WRC data."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from tripcompiler.pacenotes import PaceNote, PaceNoteSet


@dataclass(frozen=True, slots=True)
class ScheduledCall:
    """A localized pace note released by the scheduler."""

    note_id: str
    note_distance_m: float
    trigger_distance_m: float
    language: str
    text: str


class PaceNoteScheduler:
    """Release notes using speed-adaptive look-ahead without network dependencies."""

    def __init__(
        self,
        note_set: PaceNoteSet,
        *,
        language: str = "en",
        lead_time_s: float = 5.0,
        minimum_lead_m: float = 20.0,
        maximum_lead_m: float = 180.0,
    ) -> None:
        if lead_time_s <= 0:
            raise ValueError("lead_time_s must be positive")
        if minimum_lead_m < 0 or maximum_lead_m < minimum_lead_m:
            raise ValueError("Invalid lead-distance limits")
        self._notes = tuple(
            note for note in note_set.notes if _text_for(note, language) is not None
        )
        self._distances = tuple(note.distance_m for note in self._notes)
        self._language = language
        self._lead_time_s = lead_time_s
        self._minimum_lead_m = minimum_lead_m
        self._maximum_lead_m = maximum_lead_m
        self._index = 0
        self._last_distance: float | None = None

    def update(self, distance_m: float, speed_mps: float) -> list[ScheduledCall]:
        """Return newly due calls for one telemetry update."""

        if distance_m < 0 or speed_mps < 0:
            return []
        if self._last_distance is None or distance_m < self._last_distance - 20.0:
            self._index = bisect_left(self._distances, max(0.0, distance_m - 5.0))

        lead_distance = min(
            self._maximum_lead_m,
            max(self._minimum_lead_m, speed_mps * self._lead_time_s),
        )
        horizon = distance_m + lead_distance
        calls: list[ScheduledCall] = []
        while self._index < len(self._notes) and self._notes[self._index].distance_m <= horizon:
            note = self._notes[self._index]
            text = _text_for(note, self._language)
            if text is not None:
                calls.append(
                    ScheduledCall(
                        note_id=note.note_id,
                        note_distance_m=note.distance_m,
                        trigger_distance_m=distance_m,
                        language=self._language,
                        text=text,
                    )
                )
            self._index += 1
        self._last_distance = distance_m
        return calls

    def reset(self) -> None:
        """Reset the scheduler for a restarted stage."""

        self._index = 0
        self._last_distance = None


def _text_for(note: PaceNote, language: str) -> str | None:
    text = note.texts.get(language)
    if text:
        return text
    fallback = note.texts.get("en")
    return fallback if fallback else None
