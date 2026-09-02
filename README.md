# TripCompiler

TripCompiler is one Python 3.10-3.13 application for processing two driving-data sources:

- `obd` — long-form CSV exported by Car Scanner (`OBD-II + GPS`);
- `wrc` — JSONL recorded from the official EA Sports WRC UDP telemetry interface on PC.

Both sources use the same `tripcompiler compile <source>` command and produce a common trip model.
The current WRC increment also builds a route profile, imports local Zendrive pace notes, provides
English and Russian calls, pre-generates speech, and runs an external real-time co-driver.

## Repository layout

```text
src/tripcompiler/
  cli.py                       unified command-line interface
  compiler.py                  OBD/WRC dispatcher and common output contract
  obd.py                       Car Scanner adapter
  capture.py                   WRC UDP recorder
  schema.py                    configurable WRC packet decoder
  analysis.py                  normalization and event detection
  track.py                     distance-indexed WRC route profile
  pacenotes.py                 draft generation and external note import
  localization.py              packaged pace-note dictionary loader
  locales/pacenotes/en.json    English Zendrive phrase dictionary
  locales/pacenotes/ru.json    Russian Zendrive phrase dictionary
  scheduler.py                 deterministic real-time call scheduler
  tts.py                       cached OpenAI or Piper speech generation
tests/
docs/
drive_logs/                    immutable source captures, ignored by Git
compiled_trips/                generated artifacts, ignored by Git
pacenotes/                     local third-party pace-note data, ignored by Git
```

## Installation

Run these commands from PowerShell:

```powershell
cd C:\projects\AIDriverInstructor
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

All command examples below are intentionally shown on one line. A PowerShell backtick is therefore
not required.

## EA Sports WRC workflow

### 1. Configure WRC telemetry

After the first game launch, EA Sports WRC creates:

```text
%USERPROFILE%\Documents\My Games\WRC\telemetry
```

Copy the TripCompiler packet structure:

```text
src\tripcompiler\config\wrc_ai_instructor.json
```

to:

```text
%USERPROFILE%\Documents\My Games\WRC\telemetry\udp\wrc_ai_instructor.json
```

Add this entry to the `udp.packets` array in the game's `config.json`:

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

Validate the installed EA channel registry and packet structure:

```powershell
tripcompiler validate-wrc
```

### 2. Install Zendrive pace notes locally

Clone Zendrive into the ignored root `pacenotes/` directory:

```powershell
git clone --depth 1 https://github.com/zendulu/zendrive.git pacenotes\zendrive
```

The expected Taipuha source file is then:

```text
pacenotes\zendrive\pacenotes\27-360.json
```

Here `27` is the EA location ID and `360` is the EA route ID. TripCompiler uses this naming scheme
to select the correct file automatically. The upstream checkout remains local and is not committed.
Until Zendrive publishes an explicit license, its data must not be redistributed with this project.

To update an existing local checkout:

```powershell
git -C pacenotes\zendrive pull --ff-only
```

### 3. Record a stage

Start the recorder before entering the stage and stop it with Ctrl+C after the finish:

```powershell
tripcompiler record-wrc
```

The default recording directory is:

```text
drive_logs\wrc\<timestamp>\
```

For the current Taipuha example, the source recording is:

```text
drive_logs\wrc\20260830_124128\telemetry.jsonl
```

Source files under `drive_logs/` are immutable. Do not edit, rename, or replace them.

### 4. Compile the recording

Compile the Taipuha recording:

```powershell
tripcompiler compile wrc "drive_logs\wrc\20260830_124128\telemetry.jsonl" --output "compiled_trips\wrc_20260830_124128"
```

The output directory must not already exist. TripCompiler refuses to overwrite a previous analysis.
Use a new name such as `wrc_20260830_124128_v2` when recompiling the same source.

During WRC compilation TripCompiler:

1. reads EA's generated `readme\ids.json` when available;
2. resolves vehicle, class, manufacturer, location, and route names;
3. builds `track_profile.json` and `pace_notes.draft.json` from geometry;
4. looks for `pacenotes\zendrive\pacenotes\<location_id>-<route_id>.json`;
5. writes the matching Zendrive data as the primary `pace_notes.json`.

Use another local catalog explicitly when necessary:

```powershell
tripcompiler compile wrc "drive_logs\wrc\20260830_124128\telemetry.jsonl" --output "compiled_trips\wrc_20260830_124128_v2" --wrc-pacenotes-dir "D:\wrc-data\pacenotes"
```

### 5. Preview co-driver timing

Preview uses the original recording for speed and stage-distance timing, and the compiled primary
pace-note file for calls:

```powershell
tripcompiler preview-wrc-codriver "drive_logs\wrc\20260830_124128\telemetry.jsonl" "compiled_trips\wrc_20260830_124128\pace_notes.json" --language ru
```

Use `--language en` for English. Preview does not synthesize or play audio; it prints every scheduled
call so its note distance and trigger distance can be reviewed first.

### 6. Generate speech before the stage

Speech is generated once and cached as WAV files. No TTS request runs in the live scheduling loop.

For OpenAI speech, install the optional SDK and configure `OPENAI_API_KEY` in the environment:

```powershell
python -m pip install -e ".[tts]"
tripcompiler prepare-wrc-voice "compiled_trips\wrc_20260830_124128\pace_notes.json" --output "compiled_trips\wrc_20260830_124128\audio-ru" --provider openai --language ru
```

For local Piper speech, install the official Python package into the active virtual environment:

```powershell
python -m pip install piper-tts
python -m piper.download_voices --data-dir "C:\voices" ru_RU-denis-medium
```

The download command creates both `ru_RU-denis-medium.onnx` and its required adjacent
`ru_RU-denis-medium.onnx.json` configuration. Verify the installation:

```powershell
.\.venv\Scripts\piper.exe --help
Test-Path "C:\voices\ru_RU-denis-medium.onnx"
Test-Path "C:\voices\ru_RU-denis-medium.onnx.json"
```

Both `Test-Path` commands must print `True`. Then build the Russian audio cache:

```powershell
tripcompiler prepare-wrc-voice "compiled_trips\wrc_20260830_124128\pace_notes.json" --output "compiled_trips\wrc_20260830_124128\audio-ru" --provider piper --language ru --piper-executable ".\.venv\Scripts\piper.exe" --piper-model "C:\voices\ru_RU-denis-medium.onnx"
```

If Piper reports `INVALID_PROTOBUF` or `Protobuf parsing failed`, the ONNX download is incomplete.
Force both voice files to be downloaded again, then repeat the cache command:

```powershell
python -m piper.download_voices --force-redownload --data-dir "C:\voices" ru_RU-denis-medium
```

Piper and its voice models are not bundled. Piper is GPL-3.0; the Denis voice model card identifies
its source dataset as CC0. Review the engine and selected voice licenses before redistribution.

### 7. Run the external live co-driver

Add a second WRC UDP `session_update` entry with the same `wrc_ai_instructor` structure and port
`20780`. Keep port `20779` for recording and port `20780` for the live co-driver.

Run the co-driver with the prepared cache:

```powershell
tripcompiler run-wrc-codriver "compiled_trips\wrc_20260830_124128\pace_notes.json" --language ru --audio-dir "compiled_trips\wrc_20260830_124128\audio-ru"
```

Mute the built-in WRC co-driver when testing the external one. The live process only decodes UDP,
schedules calls by stage distance and speed, and plays cached WAV files.

## When manual pace-note import is needed

Manual import is not required when `tripcompiler compile wrc` finds the local Zendrive catalog.
Use it only for a standalone compatible JSON file or when adding notes to an existing compiled
trip without recompiling it.

For example, this one-line command imports Taipuha under a new filename:

```powershell
tripcompiler import-wrc-notes "pacenotes\zendrive\pacenotes\27-360.json" --output "compiled_trips\wrc_20260830_124128\pace_notes.manual.json"
```

The destination file must not already exist. The command converts the upstream list format into
the native multilingual TripCompiler schema; it does not modify the source file.

## Pace-note languages

Packaged exact-phrase dictionaries live in:

```text
src\tripcompiler\locales\pacenotes\
```

The current `en.json` and `ru.json` dictionaries cover all 216 phrases found across the current
264-file Zendrive catalog. Russian calls use concise rally-style directions and noun-form
modifiers instead of inflected direction adjectives or verb phrases.

A future language is added as another `<language>.json` file with the same schema and phrase keys.
The importer discovers packaged language files automatically. If an updated upstream file contains
an unknown phrase, English remains available while that phrase is added to other dictionaries.

## Generated artifacts

Every OBD or WRC compilation produces:

- `telemetry.csv` — normalized telemetry used for detailed analysis;
- `events.json` — detected event intervals;
- `summary.json` — trip metrics, source metadata, and data-quality information;
- `report.html` — standalone human-readable report;
- `script_ai.json` — trajectory reserved for later BeamNG ScriptAI work;
- `road_centerline.json` — local route centerline.

A usable WRC recording additionally produces:

- `track_profile.json` — distance-indexed position, heading, curvature, and grade;
- `pace_notes.draft.json` — conservative geometry-only English/Russian draft;
- `pace_notes.json` — primary imported Zendrive notes when a matching local file exists.

The geometry draft cannot infer crests, jumps, hazards, road edges, or safe cuts from a single
vehicle trace. Zendrive data is therefore preferred when present, while the draft remains useful
for comparison and diagnostics.

## Compiling Car Scanner OBD data

```powershell
tripcompiler compile obd "drive_logs\2026-07-29 16-27-48.csv" --output "compiled_trips\obd_2026-07-29"
```

The OBD adapter:

- reads semicolon-delimited CSV with `SECONDS`, `PID`, `VALUE`, `UNITS`, `LATITUDE`, and
  `LONGTITUDE` columns;
- supports UTF-8, UTF-8 with BOM, and CP1251;
- converts GPS coordinates to local metres;
- normalizes speed, RPM, accelerator, brake, acceleration, and wheel-speed signals;
- preserves a PID catalog and recognized raw dynamic signals;
- never modifies the source CSV.

OBD compilation additionally creates `pid_catalog.csv` and `vehicle_dynamics_raw.csv`.

## Quality checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Test coverage must remain at or above 75 percent. GitHub Actions runs the checks on Python 3.10.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/WRC_INSTRUCTOR.md](docs/WRC_INSTRUCTOR.md) for design details and safety constraints.
