"""Unified command-line interface for OBD and WRC trip data."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import cast

from tripcompiler.analysis import normalize_packets
from tripcompiler.capture import capture_udp
from tripcompiler.codriver import preview_codriver, run_live_codriver
from tripcompiler.compiler import SourceKind, compile_trip, load_capture, wrc_metadata
from tripcompiler.pacenotes import (
    import_zendrive_pace_notes,
    load_pace_notes,
    write_pace_notes,
)
from tripcompiler.schema import PacketDecoder, load_decoder
from tripcompiler.track import build_track_profile, write_track_profile
from tripcompiler.tts import (
    CommandTtsProvider,
    OpenAITtsProvider,
    TtsProvider,
    prepare_audio_cache,
)
from tripcompiler.wrc_catalog import enrich_wrc_metadata, load_wrc_catalog


def _default_telemetry_dir() -> Path:
    return Path.home() / "Documents" / "My Games" / "WRC" / "telemetry"


def _default_structure() -> Path:
    resource = files("tripcompiler").joinpath("config/wrc_ai_instructor.json")
    return Path(str(resource))


def _default_ids() -> Path:
    return _default_telemetry_dir() / "readme" / "ids.json"


def _decoder(args: argparse.Namespace) -> PacketDecoder:
    telemetry_dir = Path(args.telemetry_dir)
    return load_decoder(
        telemetry_dir / "readme" / "channels.json",
        Path(args.structure),
    )


def _catalog_path(value: str | None) -> Path | None:
    if value:
        return Path(value)
    default = _default_ids()
    return default if default.is_file() else None


def _cmd_validate(args: argparse.Namespace) -> int:
    decoder = _decoder(args)
    print(
        json.dumps(
            {
                "source": "wrc",
                "packet_id": decoder.packet_id,
                "packet_size": decoder.size,
                "field_count": len(decoder.fields),
                "fields": list(decoder.field_names),
            },
            indent=2,
        )
    )
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    decoder = _decoder(args)
    output = Path(args.output) if args.output else _default_capture_dir()
    print(f"Listening on udp://{args.host}:{args.port}; packet size {decoder.size} bytes")
    print(f"Raw capture: {output}")
    stats = capture_udp(
        decoder,
        output,
        host=args.host,
        port=args.port,
        duration_s=args.duration,
        max_packets=args.max_packets,
    )
    print(json.dumps(asdict(stats), indent=2))
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    source = cast(SourceKind, args.source)
    input_path = Path(args.input)
    output = Path(args.output) if args.output else _default_compiled_dir(source, input_path)
    ids_path = _catalog_path(args.wrc_ids) if source == "wrc" else None
    summary = compile_trip(source, input_path, output, wrc_ids_path=ids_path)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _cmd_build_track(args: argparse.Namespace) -> int:
    packets = load_capture(Path(args.input))
    metadata = wrc_metadata(packets[-1])
    ids_path = _catalog_path(args.wrc_ids)
    if ids_path is not None:
        metadata = enrich_wrc_metadata(metadata, load_wrc_catalog(ids_path))
    profile = build_track_profile(
        normalize_packets(packets),
        metadata,
        sample_step_m=args.sample_step,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_track_profile(output, profile)
    print(json.dumps({"track_profile": str(output), "points": len(profile.points)}, indent=2))
    return 0


def _cmd_import_notes(args: argparse.Namespace) -> int:
    note_set = import_zendrive_pace_notes(
        Path(args.input),
        location_id=args.location_id,
        route_id=args.route_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_pace_notes(output, note_set)
    print(json.dumps({"pace_notes": str(output), "notes": len(note_set.notes)}, indent=2))
    return 0


def _cmd_prepare_voice(args: argparse.Namespace) -> int:
    note_set = load_pace_notes(Path(args.notes))
    provider: TtsProvider
    if args.provider == "openai":
        provider = OpenAITtsProvider(
            model=args.model,
            voice=args.voice,
            instructions=args.instructions,
        )
    else:
        if not args.piper_model:
            raise ValueError("--piper-model is required for the piper provider")
        provider = CommandTtsProvider(
            (
                args.piper_executable,
                "-m",
                args.piper_model,
                "-f",
                "{output}",
            ),
            name=f"piper:{Path(args.piper_model).name}",
        )
    manifest = prepare_audio_cache(
        note_set,
        args.language,
        provider,
        Path(args.output),
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def _cmd_preview_codriver(args: argparse.Namespace) -> int:
    packets: list[dict[str, object]] = [dict(packet) for packet in load_capture(Path(args.input))]
    notes = load_pace_notes(Path(args.notes))
    calls = preview_codriver(
        packets,
        notes,
        language=args.language,
        lead_time_s=args.lead_time,
    )
    print(json.dumps([asdict(call) for call in calls], indent=2, ensure_ascii=False))
    return 0


def _cmd_run_codriver(args: argparse.Namespace) -> int:
    stats = run_live_codriver(
        _decoder(args),
        load_pace_notes(Path(args.notes)),
        language=args.language,
        audio_dir=Path(args.audio_dir) if args.audio_dir else None,
        host=args.host,
        port=args.port,
        lead_time_s=args.lead_time,
        duration_s=args.duration,
        max_packets=args.max_packets,
    )
    print(json.dumps(asdict(stats), indent=2))
    return 0


def _default_capture_dir() -> Path:
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return Path("drive_logs") / "wrc" / stamp


def _default_compiled_dir(source: SourceKind, input_path: Path) -> Path:
    session_name = input_path.parent.name if source == "wrc" else input_path.stem
    return Path("compiled_trips") / f"{source}_{session_name}"


def _add_schema_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--telemetry-dir",
        default=str(_default_telemetry_dir()),
        help="EA WRC telemetry directory containing readme/channels.json",
    )
    parser.add_argument(
        "--structure",
        default=str(_default_structure()),
        help="Packet structure JSON used by both EA WRC and this decoder",
    )


def _add_language_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", choices=("en", "ru"), default="en")
    parser.add_argument("--lead-time", type=float, default=5.0, help="Target call lead time")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tripcompiler",
        description="Compile OBD or EA Sports WRC data into a common trip report",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    compile_command = sub.add_parser("compile", help="Compile OBD CSV or WRC JSONL")
    compile_command.add_argument("source", choices=("obd", "wrc"), help="Input data source")
    compile_command.add_argument("input", help="Car Scanner CSV or WRC telemetry.jsonl")
    compile_command.add_argument("--output", help="New output directory")
    compile_command.add_argument("--wrc-ids", help="EA WRC readme/ids.json")
    compile_command.set_defaults(func=_cmd_compile)

    validate = sub.add_parser("validate-wrc", help="Validate EA WRC channel and packet schemas")
    _add_schema_arguments(validate)
    validate.set_defaults(func=_cmd_validate)

    record = sub.add_parser("record-wrc", help="Record WRC UDP packets until Ctrl+C")
    _add_schema_arguments(record)
    record.add_argument("--host", default="127.0.0.1")
    record.add_argument("--port", type=int, default=20779)
    record.add_argument("--duration", type=float, help="Optional recording duration in seconds")
    record.add_argument("--max-packets", type=int, help="Optional decoded packet limit")
    record.add_argument("--output", help="New session directory; defaults to drive_logs/wrc/<time>")
    record.set_defaults(func=_cmd_record)

    track = sub.add_parser("build-wrc-track", help="Build a route profile from a WRC capture")
    track.add_argument("input", help="Recorded WRC telemetry.jsonl")
    track.add_argument("--output", required=True, help="New track_profile.json path")
    track.add_argument("--wrc-ids", help="EA WRC readme/ids.json")
    track.add_argument("--sample-step", type=float, default=2.0, help="Profile spacing in metres")
    track.set_defaults(func=_cmd_build_track)

    import_notes = sub.add_parser(
        "import-wrc-notes",
        help="Convert a user-supplied Zendrive-compatible pace-note file",
    )
    import_notes.add_argument("input")
    import_notes.add_argument("--output", required=True)
    import_notes.add_argument("--location-id", type=int)
    import_notes.add_argument("--route-id", type=int)
    import_notes.set_defaults(func=_cmd_import_notes)

    voice = sub.add_parser("prepare-wrc-voice", help="Pre-generate cached WAV pace-note calls")
    voice.add_argument("notes", help="Native pace_notes.json")
    voice.add_argument("--output", required=True, help="Audio cache directory")
    voice.add_argument("--language", choices=("en", "ru"), default="en")
    voice.add_argument("--provider", choices=("openai", "piper"), default="openai")
    voice.add_argument("--model", default="gpt-4o-mini-tts")
    voice.add_argument("--voice", default="coral")
    voice.add_argument(
        "--instructions",
        default="Speak quickly and clearly like a professional rally co-driver.",
    )
    voice.add_argument("--piper-executable", default="piper")
    voice.add_argument("--piper-model", help="Piper ONNX voice model")
    voice.set_defaults(func=_cmd_prepare_voice)

    preview = sub.add_parser(
        "preview-wrc-codriver",
        help="Replay scheduler decisions over a recorded WRC capture",
    )
    preview.add_argument("input", help="Recorded WRC telemetry.jsonl")
    preview.add_argument("notes", help="Native pace_notes.json")
    _add_language_arguments(preview)
    preview.set_defaults(func=_cmd_preview_codriver)

    codriver = sub.add_parser("run-wrc-codriver", help="Run the local realtime WRC co-driver")
    codriver.add_argument("notes", help="Native pace_notes.json")
    _add_schema_arguments(codriver)
    _add_language_arguments(codriver)
    codriver.add_argument("--audio-dir", help="Prepared audio cache directory")
    codriver.add_argument("--host", default="127.0.0.1")
    codriver.add_argument("--port", type=int, default=20780)
    codriver.add_argument("--duration", type=float)
    codriver.add_argument("--max-packets", type=int)
    codriver.set_defaults(func=_cmd_run_codriver)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
