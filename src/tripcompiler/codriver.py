"""Live WRC pace-note delivery with optional cached WAV playback."""

from __future__ import annotations

import importlib
import json
import queue
import socket
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tripcompiler.pacenotes import PaceNoteSet
from tripcompiler.scheduler import PaceNoteScheduler, ScheduledCall
from tripcompiler.schema import PacketDecoder, PacketSizeError


@dataclass(frozen=True, slots=True)
class CoDriverStats:
    """Live-session counters returned when the listener stops."""

    decoded_packets: int
    malformed_packets: int
    emitted_calls: int
    duration_s: float


class AudioQueue:
    """Play cached WAV files sequentially without blocking UDP reception."""

    def __init__(self) -> None:
        self._items: queue.Queue[Path | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="pace-note-audio", daemon=True)
        self._thread.start()

    def submit(self, path: Path) -> None:
        self._items.put(path)

    def close(self) -> None:
        self._items.put(None)
        self._thread.join(timeout=5.0)

    def _run(self) -> None:
        try:
            winsound: Any = importlib.import_module("winsound")
        except ImportError:
            return
        while (path := self._items.get()) is not None:
            winsound.PlaySound(str(path), winsound.SND_FILENAME)


def run_live_codriver(
    decoder: PacketDecoder,
    note_set: PaceNoteSet,
    *,
    language: str = "en",
    audio_dir: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 20780,
    lead_time_s: float = 5.0,
    duration_s: float | None = None,
    max_packets: int | None = None,
) -> CoDriverStats:
    """Listen for WRC updates and emit locally scheduled calls until interrupted."""

    if duration_s is not None and duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if max_packets is not None and max_packets <= 0:
        raise ValueError("max_packets must be positive")
    scheduler = PaceNoteScheduler(note_set, language=language, lead_time_s=lead_time_s)
    audio_files = _audio_files(audio_dir)
    player = AudioQueue() if audio_files else None
    started = time.monotonic()
    decoded = 0
    malformed = 0
    emitted = 0
    route_checked = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1_048_576)
            udp_socket.bind((host, port))
            udp_socket.settimeout(0.25)
            try:
                while True:
                    if duration_s is not None and time.monotonic() - started >= duration_s:
                        break
                    if max_packets is not None and decoded >= max_packets:
                        break
                    try:
                        payload, _ = udp_socket.recvfrom(max(65_535, decoder.size))
                    except TimeoutError:
                        continue
                    try:
                        channels = decoder.decode(payload)
                    except PacketSizeError:
                        malformed += 1
                        continue
                    decoded += 1
                    if not route_checked:
                        _check_route(note_set, channels.get("route_id"))
                        route_checked = True
                    distance = _number(channels.get("stage_current_distance"))
                    speed = abs(_number(channels.get("vehicle_speed")))
                    for call in scheduler.update(distance, speed):
                        emitted += 1
                        print(json.dumps(asdict(call), ensure_ascii=False), flush=True)
                        audio_path = audio_files.get(call.note_id)
                        if player is not None and audio_path is not None:
                            player.submit(audio_path)
            except KeyboardInterrupt:
                pass
    finally:
        if player is not None:
            player.close()
    return CoDriverStats(decoded, malformed, emitted, time.monotonic() - started)


def preview_codriver(
    packets: list[dict[str, object]],
    note_set: PaceNoteSet,
    *,
    language: str = "en",
    lead_time_s: float = 5.0,
) -> list[ScheduledCall]:
    """Run the same scheduler deterministically over a recorded capture."""

    scheduler = PaceNoteScheduler(note_set, language=language, lead_time_s=lead_time_s)
    calls: list[ScheduledCall] = []
    for channels in packets:
        calls.extend(
            scheduler.update(
                _number(channels.get("stage_current_distance")),
                abs(_number(channels.get("vehicle_speed"))),
            )
        )
    return calls


def _audio_files(audio_dir: Path | None) -> dict[str, Path]:
    if audio_dir is None:
        return {}
    manifest_path = audio_dir / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read audio cache manifest {manifest_path}: {exc}") from exc
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        raise ValueError("Audio cache manifest requires an entries object")
    result: dict[str, Path] = {}
    for note_id, raw in entries.items():
        if isinstance(note_id, str) and isinstance(raw, dict) and isinstance(raw.get("file"), str):
            path = audio_dir / raw["file"]
            if path.is_file():
                result[note_id] = path
    return result


def _check_route(note_set: PaceNoteSet, value: object) -> None:
    if note_set.route_id is None or value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("WRC route_id must be numeric")
    if int(value) != note_set.route_id:
        raise ValueError(
            f"Pace notes are for route {note_set.route_id}, received route {int(value)}"
        )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
