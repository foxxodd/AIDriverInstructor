"""Unified command-line interface for OBD and WRC trip data."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import cast

from tripcompiler.capture import capture_udp
from tripcompiler.compiler import SourceKind, compile_trip
from tripcompiler.schema import PacketDecoder, load_decoder


def _default_telemetry_dir() -> Path:
    return Path.home() / "Documents" / "My Games" / "WRC" / "telemetry"


def _default_structure() -> Path:
    resource = files("tripcompiler").joinpath("config/wrc_ai_instructor.json")
    return Path(str(resource))


def _decoder(args: argparse.Namespace) -> PacketDecoder:
    telemetry_dir = Path(args.telemetry_dir)
    return load_decoder(
        telemetry_dir / "readme" / "channels.json",
        Path(args.structure),
    )


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
    summary = compile_trip(source, input_path, output)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
