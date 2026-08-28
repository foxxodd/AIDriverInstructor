# AI Driving Instructor Repository Guidelines

## Current scope

The current MVP provides one Python application, TripCompiler, that converts either Car Scanner
OBD/GPS exports or EA Sports WRC telemetry into a common trip model and analysis artifacts.

Keep work within this MVP unless a task explicitly expands the scope. Long-term features such as
video synchronization, BeamNG playback, real-time coaching, and additional sensors are roadmap
items rather than current implementation requirements.

## Architecture

- Support Python 3.10 through 3.13.
- Keep application code in `src/tripcompiler/` and tests in `tests/`.
- Maintain one `tripcompiler` CLI and one compilation pipeline.
- Select the input adapter with the `obd` or `wrc` source argument.
- Keep source-specific parsing inside its adapter; share normalization, analysis, and output code.
- Preserve the common telemetry schema unless a task explicitly requires a schema migration.
- Keep documentation, comments, identifiers, user-facing messages, and test names in technical
  English.
- Preserve localized input compatibility when required. Represent non-English matching literals
  with Unicode escapes so that maintained source files remain English-only.

## Data handling

- Treat every file in `drive_logs/` as immutable source data. Do not edit, rename, or delete it.
- Write generated trip artifacts only to `compiled_trips/` or a test-provided temporary directory.
- Do not commit source logs, generated reports, credentials, tokens, or machine-specific files.
- Keep OBD and WRC inputs traceable to their original source; do not silently discard unsupported
  fields when an audit artifact can retain them.

## Change discipline

- Do not change algorithms, thresholds, public interfaces, schemas, or output filenames during a
  documentation-only or localization-only task.
- Keep changes focused and avoid unrelated formatting or refactoring.
- Add or update tests for every behavioral change and regression fix.
- Preserve backward compatibility unless the task explicitly authorizes a breaking change.
- Update `README.md` and `docs/ARCHITECTURE.md` when commands, architecture, or output contracts
  change.

## Required validation

Before committing or packaging, run:

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Test coverage must remain at or above 75 percent. Do not weaken lint, type-checking, test, or
coverage settings to make a change pass.

## Git workflow

- `main` is the stable branch.
- `dev` is the integration branch for completed development work.
- Create focused feature or fix branches from `dev` when a change requires review or spans more
  than one atomic commit.
- Use concise, imperative commit messages and keep each commit internally consistent.
- Do not force-push shared branches or rewrite published history unless explicitly requested.
