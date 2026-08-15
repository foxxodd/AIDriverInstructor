"""UDP capture that preserves decoded packets as append-only JSONL."""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from wrc_trip_compiler.schema import PacketDecoder, PacketSizeError


@dataclass(frozen=True, slots=True)
class CaptureStats:
    """Recorder counters written next to the raw stream."""

    received_datagrams: int
    decoded_packets: int
    malformed_packets: int
    first_packet_uid: int | None
    last_packet_uid: int | None
    estimated_dropped_packets: int
    duration_s: float


class JsonlCaptureWriter:
    """Write one decoded packet per line without transforming its values."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write(
        self,
        channels: Mapping[str, object],
        sender: tuple[str, int],
        received_monotonic_ns: int,
    ) -> None:
        record = {
            "received_at_utc": datetime.now(timezone.utc).isoformat(),
            "received_monotonic_ns": received_monotonic_ns,
            "sender_ip": sender[0],
            "sender_port": sender[1],
            "channels": channels,
        }
        self._stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._stream.flush()


def capture_udp(
    decoder: PacketDecoder,
    output_dir: Path,
    host: str = "127.0.0.1",
    port: int = 20779,
    duration_s: float | None = None,
    max_packets: int | None = None,
    socket_timeout_s: float = 0.25,
) -> CaptureStats:
    """Listen until Ctrl+C, duration, or packet limit and return loss statistics."""

    if duration_s is not None and duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if max_packets is not None and max_packets <= 0:
        raise ValueError("max_packets must be positive")

    output_dir.mkdir(parents=True, exist_ok=False)
    raw_path = output_dir / "telemetry.jsonl"
    started = time.monotonic()
    received = 0
    decoded_count = 0
    malformed = 0
    first_uid: int | None = None
    last_uid: int | None = None
    dropped = 0

    with raw_path.open("x", encoding="utf-8", newline="\n") as stream:
        writer = JsonlCaptureWriter(stream)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1_048_576)
            udp_socket.bind((host, port))
            udp_socket.settimeout(socket_timeout_s)
            try:
                while True:
                    elapsed = time.monotonic() - started
                    if duration_s is not None and elapsed >= duration_s:
                        break
                    if max_packets is not None and decoded_count >= max_packets:
                        break
                    try:
                        payload, sender = udp_socket.recvfrom(max(65_535, decoder.size))
                    except TimeoutError:
                        continue
                    received += 1
                    try:
                        channels = decoder.decode(payload)
                    except PacketSizeError:
                        malformed += 1
                        continue
                    uid_value = channels.get("packet_uid")
                    if isinstance(uid_value, int):
                        if first_uid is None:
                            first_uid = uid_value
                        if last_uid is not None and uid_value > last_uid + 1:
                            dropped += uid_value - last_uid - 1
                        last_uid = uid_value
                    writer.write(channels, sender, time.monotonic_ns())
                    decoded_count += 1
            except KeyboardInterrupt:
                pass

    stats = CaptureStats(
        received_datagrams=received,
        decoded_packets=decoded_count,
        malformed_packets=malformed,
        first_packet_uid=first_uid,
        last_packet_uid=last_uid,
        estimated_dropped_packets=dropped,
        duration_s=time.monotonic() - started,
    )
    (output_dir / "capture.json").write_text(
        json.dumps(
            {
                **asdict(stats),
                "decoder_packet_id": decoder.packet_id,
                "decoder_packet_size": decoder.size,
                "decoder_fields": list(decoder.field_names),
                "listen_host": host,
                "listen_port": port,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return stats
