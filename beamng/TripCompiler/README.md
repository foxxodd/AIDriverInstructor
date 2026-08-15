# WRC TripCompiler — MVP

Python 3.10 project for recording the official EA Sports WRC UDP telemetry and analyzing a
rally stage after the drive. The layout follows the same `src`/tests/tooling approach as the
Pianoteq MIDI Tutor project.

## MVP output

- immutable decoded capture in `drive_logs/wrc/<session>/telemetry.jsonl`;
- capture diagnostics and estimated UDP loss in `capture.json`;
- normalized `telemetry.csv` suitable for later BeamNG conversion;
- deterministic event intervals in `events.json`;
- machine-readable `summary.json` and standalone `report.html`;
- Ruff, strict Mypy, Pytest, branch coverage gate at 75%, pre-commit, and GitHub Actions.

## How EA Sports WRC exports data

The PC game creates `%USERPROFILE%\Documents\My Games\WRC\telemetry` after it has been started
once. Its `readme/channels.json` contains channel types and units. A JSON file in `udp/` selects
the channels and their packed order. `config.json` maps that structure and packet type to an
IP address, port, and rate. Values are packed little-endian and update packets can be emitted
once per frame or at a configured rate.

This project uses only that documented interface. It does not read process memory or modify
game binaries.

## Install

From `beamng/TripCompiler` in Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Configure the game

1. Start EA Sports WRC, reach the first interactive screen, and quit.
2. Copy
   `src/wrc_trip_compiler/config/wrc_ai_instructor.json` to
   `%USERPROFILE%\Documents\My Games\WRC\telemetry\udp\wrc_ai_instructor.json`.
3. Add this item to `udp.packets` in the game's `config.json`:

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

Keep any existing packet entries: several consumers can use different ports. Never edit files
under the game's `telemetry/readme` directory; EA regenerates them. Restart the game after any
configuration change and inspect its `telemetry/log.txt` if packets do not arrive.

## Validate before driving

```powershell
wrc-trip validate
```

The command prints the expected byte length and all decoded fields. The exact structure used by
the game must match the `--structure` passed to the recorder.

## Record a stage

Start the recorder before entering the stage:

```powershell
wrc-trip record
```

Stop with Ctrl+C after the result screen. A timestamped directory is created under
`drive_logs/wrc/`. For a short test:

```powershell
wrc-trip record --duration 30 --output drive_logs/wrc/udp_smoke_test
```

If the game or receiver is on another computer, set the config IP to the receiver and use
`--host 0.0.0.0`; allow the selected UDP port through the receiver firewall.

## Compile a capture

```powershell
wrc-trip compile drive_logs/wrc/20260815_120000/telemetry.jsonl \
  --output compiled_trips/20260815_120000
```

Output directories must be new. This prevents accidental overwriting of a previous analysis.

## Current limitations

- EA's export is PC-only according to its telemetry guide.
- UDP cannot guarantee delivery; loss is measured but packets are not retransmitted.
- No route centreline, corner calls, surface type, weather, damage model, or video sync is
  inferred in this MVP.
- Thresholds are starting values and require calibration against representative clean and poor
  runs for each car/class and controller.
- The report evaluates measured dynamics and control use; it is not yet a racing-line or pace
  comparison.

## Development checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for coordinates, event definitions, and loss
handling.

