# TripCompiler

A unified Python 3.10 project for post-trip analysis of two input formats:

- `obd` — long-form CSV exported by Car Scanner (`OBD-II + GPS`);
- `wrc` — JSONL captured from the official EA Sports WRC UDP telemetry interface on PC.

The entire project resides at the repository root. There are no separate WRC and OBD compilers:
the source is a required argument of the single `tripcompiler compile` command.

## Structure

```text
src/tripcompiler/
  cli.py          unified CLI
  compiler.py     source dispatcher and common result format
  obd.py          Car Scanner CSV adapter
  capture.py      WRC UDP capture
  schema.py       configurable WRC packet decoder
  analysis.py     common WRC normalization and event detectors
  track.py        distance-indexed WRC route profile
  pacenotes.py    draft generation and user-supplied note import
  scheduler.py    deterministic real-time call scheduler
  tts.py          cached OpenAI or Piper speech generation
tests/
docs/
drive_logs/       immutable source captures, excluded from Git
compiled_trips/   generated results, excluded from Git
```

## Installation

```powershell
cd C:\projects\AIDriverInstructor
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Compiling OBD data

```powershell
tripcompiler compile obd "drive_logs\2026-07-29 16-27-48.csv" `
  --output "compiled_trips\obd_2026-07-29"
```

The OBD adapter:

- reads semicolon-delimited CSV with `SECONDS`, `PID`, `VALUE`, `UNITS`,
  `LATITUDE`, and `LONGTITUDE` columns;
- supports UTF-8, UTF-8 with BOM, and CP1251;
- converts GPS coordinates to local metres;
- normalizes speed, RPM, accelerator, brake, acceleration, and wheel-speed signals;
- preserves a PID catalog and recognized raw dynamic signals;
- never modifies the source CSV.

## Configuring and recording WRC

After the first game launch, EA Sports WRC creates
`%USERPROFILE%\Documents\My Games\WRC\telemetry`.

1. Copy
   `src\tripcompiler\config\wrc_ai_instructor.json` to
   `%USERPROFILE%\Documents\My Games\WRC\telemetry\udp\wrc_ai_instructor.json`.
2. Add the following entry to the `udp.packets` array in the game's `config.json`:

```json
{
  "bEnabled": true,
  "frequencyHz": 60,
  "ip": "127.0.0.1",
  "packet": "session_update",
  "port": 20779,
  "structure": "wrc_ai_instructor"
}
```

3. Validate the schema against the installed game version:

```powershell
tripcompiler validate-wrc
```

4. Start recording before entering the stage and stop with Ctrl+C after the finish:

```powershell
tripcompiler record-wrc
```

By default, captures are stored in `drive_logs/wrc/<timestamp>/`.

5. Compile the capture with the same TripCompiler:

```powershell
tripcompiler compile wrc "drive_logs\wrc\20260816_120000\telemetry.jsonl" `
  --output "compiled_trips\wrc_20260816_120000"
```

When EA's generated `readme\ids.json` is available, TripCompiler resolves numeric vehicle,
class, manufacturer, location, route, game-mode, and result-status identifiers into names. Use
`--wrc-ids PATH` to select a catalog explicitly.

## Common output format

Both sources produce:

- `telemetry.csv` — unified normalized telemetry schema;
- `events.json` — detected event intervals;
- `summary.json` — trip metrics and data-quality information;
- `report.html` — standalone report;
- `script_ai.json` — common trajectory for subsequent BeamNG ScriptAI adaptation;
- `road_centerline.json` — local route centerline in metres.

A WRC capture with a usable moving position trace additionally produces:

- `track_profile.json` — a smoothed, distance-indexed route profile with heading, curvature,
  grade, provenance, and an explicit unknown-width model;
- `pace_notes.draft.json` — conservative English and Russian draft calls derived from curvature.

The draft calls require a human recce before competitive use. Road width and cut safety cannot
be inferred from a single driven center trace, so TripCompiler does not generate `cut` or
`don't cut` instructions automatically.

OBD compilation additionally produces:

- `pid_catalog.csv` — complete PID inventory, frequency, and common-schema mapping;
- `vehicle_dynamics_raw.csv` — recognized source measurements without rewriting the CSV.

Common detectors identify harsh braking and acceleration, high lateral acceleration,
handbrake use at speed, large slip angles, wheelspin, and simultaneous brake and throttle.
The default thresholds are initial engineering values and require separate calibration for
real-road driving and the rally simulator.

## WRC co-driver prototype

The first instructor increment keeps note timing local and deterministic. It can import a
user-supplied Zendrive-compatible distance-indexed JSON file, preview calls against a recording,
pre-generate speech, and play cached calls from live UDP telemetry.

```powershell
# Optional: import a legally obtained, user-supplied location-route note file.
tripcompiler import-wrc-notes "C:\data\27-360.json" `
  --output "compiled_trips\taipuha\pace_notes.json"

# Verify timing without audio.
tripcompiler preview-wrc-codriver `
  "drive_logs\wrc\20260830_124128\telemetry.jsonl" `
  "compiled_trips\taipuha\pace_notes.json" --language en

# Cloud speech. Install the optional Python dependency first.
python -m pip install -e ".[tts]"
tripcompiler prepare-wrc-voice "compiled_trips\taipuha\pace_notes.json" `
  --output "compiled_trips\taipuha\audio-en" --provider openai --language en

# Local speech through a separately installed Piper executable and voice model.
tripcompiler prepare-wrc-voice "compiled_trips\taipuha\pace_notes.json" `
  --output "compiled_trips\taipuha\audio-ru" --provider piper --language ru `
  --piper-model "C:\voices\ru_RU-voice.onnx"
```

For live use, add a second EA UDP packet entry using the same structure and port `20780`, then
run:

```powershell
tripcompiler run-wrc-codriver "compiled_trips\taipuha\pace_notes.json" `
  --language en --audio-dir "compiled_trips\taipuha\audio-en"
```

OpenAI and Piper generate WAV files before the stage; the live loop only schedules and plays
cached audio. English (`en`) and Russian (`ru`) are supported. The optional Zendrive-compatible
importer accepts user-supplied files from [zendulu/zendrive](https://github.com/zendulu/zendrive).
Keep these files under the ignored `pacenotes/` directory; they are not redistributed until the
upstream repository publishes an explicit license. See
[docs/WRC_INSTRUCTOR.md](docs/WRC_INSTRUCTOR.md) for design and safety details.

## Quality checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Minimum test coverage is 75%. GitHub Actions runs the same checks on Python 3.10.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details about time representation,
coordinate systems, and packet-loss handling.
