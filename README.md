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
  tts.py                       cached OpenAI speech generation
tests/
docs/
requirements/                  production, test, and development install entry points
drive_logs/                    immutable source captures, ignored by Git
compiled_trips/                generated artifacts, ignored by Git
pacenotes/                     local third-party pace-note data, ignored by Git
pacenotes_<language>/          generated localized catalogs, ignored by Git
audio_<language>/              generated shared WAV catalogs, ignored by Git
```

## Installation

Run these commands from PowerShell:

```powershell
cd C:\projects\AIDriverInstructor
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements/dev.txt
```

The repository provides separate installation entry points:

- `requirements/prod.txt` — production runtime with OpenAI speech support;
- `requirements/test.txt` — production dependencies plus the test runner and coverage;
- `requirements/dev.txt` — editable application, tests, linting, type checking, pre-commit, and
  OpenAI speech support.

Each environment is installed completely with one `python -m pip install -r <requirements-file>`
command. Dependency versions remain defined once in `pyproject.toml`; the requirements files select
the appropriate extras.

## OpenAI credentials

For local development, copy `.env.dev.example` to the ignored `.env.dev` file and set the key there:

```powershell
Copy-Item .env.dev.example .env.dev
notepad .env.dev
```

TripCompiler loads `.env.dev` before creating the OpenAI client, but never overrides an existing
process environment variable. Do not commit `.env.dev`.

GitHub production uses the `production` environment and its encrypted `OPENAI_API_KEY` secret. Set
it interactively without placing the value in a command or repository file:

```powershell
gh secret set OPENAI_API_KEY --env production
```

The manually triggered `production configuration` workflow installs `requirements/prod.txt` in one
command and verifies that the secret exists without printing it or calling the OpenAI API.

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

Generate every Russian route in one command:

```powershell
tripcompiler prepare-wrc-pacenotes --language ru
```

The command converts every numeric source file into the native single-language schema under the
ignored `pacenotes_ru\` directory. It writes `catalog.json` plus one `<location_id>-<route_id>.json`
file per route. Re-running the command refreshes these generated files. To prepare only Taipuha:

```powershell
tripcompiler prepare-wrc-pacenotes --language ru --route 27-360
```

The `--route` value always uses the Zendrive/EA `<location_id>-<route_id>` filename code. English is
the upstream source and is not duplicated into a generated `pacenotes_en` catalog.

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

Preview uses the original recording for speed and stage-distance timing, and the generated localized
route file for calls:

```powershell
tripcompiler preview-wrc-codriver "drive_logs\wrc\20260830_124128\telemetry.jsonl" "pacenotes_ru\27-360.json" --language ru
```

For English, use the multilingual `pace_notes.json` produced by trip compilation. Preview does not
synthesize or play audio; it prints every scheduled call so its note distance and trigger distance
can be reviewed first.

### 6. Generate speech before the stage

Speech is generated once and cached as WAV files. No TTS request runs in the live scheduling loop.

OpenAI Speech API is the supported MVP speech backend. The complete SDK and local environment loader
are installed by both `requirements/prod.txt` and `requirements/dev.txt`.

The complete Russian catalog currently contains 7,290 unique composed calls. Generating all of them
would be expensive and slow, so `--route` is required for audio generation. Generate every call for
Taipuha in one command:

```powershell
tripcompiler prepare-wrc-audio --language ru --route 27-360
```

The ignored output is `audio_ru\`. The selected route receives a live-player manifest under
`audio_ru\routes\27-360\manifest.json`; identical calls within the route share one WAV.

The defaults are `gpt-4o-mini-tts`, `cedar`, and rally speech speed `1.5`. A previous
three-second call should be close to two seconds at this speed. Re-run the same route command to
generate a new cache; the speed is part of its cache key. For an even faster comparison:

```powershell
tripcompiler prepare-wrc-audio --language ru --route 27-360 --speed 1.7 --output audio_fast_ru
```

The [OpenAI speech API reference](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create)
accepts values from `0.25` to `4.0`. Values much above `2.0` are unlikely to remain clear for
dense rally calls. OpenAI also recommends `marin` for high voice quality:

```powershell
tripcompiler prepare-wrc-audio --language ru --route 27-360 --voice marin --output audio_marin_ru
```

Model, voice, speed, instructions, language, and note text contribute to the content-addressed
cache key. Re-running a command synthesizes only missing unique WAV files. The older `prepare-wrc-voice` command
remains available for one standalone compiled `pace_notes.json` file.

Speech is synthesized before the stage; the live loop makes no network request. API synthesis may
incur usage charges. Products exposing these recordings must clearly disclose that the voice is
AI-generated, as required by the [OpenAI text-to-speech guide](https://developers.openai.com/api/docs/guides/text-to-speech).

Piper is no longer supported because its available Russian voice did not meet the intelligibility
target. A future local backend must meet both multilingual quality and redistribution-license
requirements.

### 7. Run the external live co-driver

Add a second WRC UDP `session_update` entry with the same `wrc_ai_instructor` structure and port
`20780`. Keep port `20779` for recording and port `20780` for the live co-driver.

Run the co-driver with the prepared cache:

```powershell
tripcompiler run-wrc-codriver "pacenotes_ru\27-360.json" --language ru --audio-dir "audio_ru\routes\27-360"
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
