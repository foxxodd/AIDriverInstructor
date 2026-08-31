# WRC instructor MVP

## Evidence and scope

EA Sports WRC exposes vehicle state, stage distance, position, route ID, and location ID through
its configurable UDP telemetry. The installed channel registry does not expose pace-note events,
road boundaries, or a complete route mesh. TripCompiler therefore treats the game-provided IDs
as catalog keys and the driven position trace as an observed trajectory, not as authoritative
road geometry.

The current increment provides a useful first co-driver without depending on BeamNG:

1. resolve EA numeric IDs with the game's generated `ids.json`;
2. build a distance-indexed track profile from a completed recording;
3. generate conservative draft notes and automatically import a matching local compatible file;
4. verify call timing by replaying a recording;
5. pre-generate English or Russian WAV files;
6. schedule and play cached calls from live EA UDP packets.

## Existing pace-note datasets

The [Zendrive repository](https://github.com/zendulu/zendrive) contains distance-indexed pace-note
files for EA Sports WRC routes and is technically compatible with this design. Its files contain
a stage distance, one or more spoken phrases,
and optional conditions. The repository currently has no declared software or dataset license.
TripCompiler consequently does not download, copy, package, or redistribute Zendrive content.
The user may place a local checkout under the ignored root `pacenotes/` directory. During WRC
compilation, TripCompiler selects `<location_id>-<route_id>.json` from that checkout and records
provenance plus `requires_user_license_review: true` in the output.

The audited local Zendrive snapshot contains 264 JSON files, 39,162 note records, and 216 distinct
spoken phrases. All files and records match the supported structure. One source record is stored
out of distance order; the importer therefore sorts normalized notes by stage distance without
changing the source file.

## Pace-note localization

Exact-phrase dictionaries are packaged under `src/tripcompiler/locales/pacenotes/`. English and
Russian dictionaries cover all 216 phrases in the audited snapshot. The importer discovers these
files automatically, so another language can be added as a separate JSON dictionary without
changing the import algorithm. If an updated source introduces an unknown phrase, English falls
back to the original text and incomplete translations are omitted.

Russian terminology uses concise rally-style directions and noun-form modifiers. Maintained JSON
stores non-English values as Unicode escapes to keep source and documentation technically English.

Other open-source co-driver projects can inform algorithms but do not automatically grant rights
to their route data. Code licenses and dataset licenses must be reviewed separately before any
catalog becomes a project dependency.

## Road width

Road width is not required for the first phase. Distance and curvature are enough for basic
left/right severity calls and call timing. A single run records where the car travelled, not
where both road edges are, and cannot distinguish a deliberate racing line from the centerline.

The track schema therefore records width explicitly as unknown. It never derives `cut`, `don't
cut`, shoulder, ditch, obstacle, or safe-corridor instructions from one trajectory. A later width
model should use one or more authoritative sources:

- manually surveyed route annotations;
- computer vision with synchronized video and calibration;
- repeated runs that include edge evidence, not merely ordinary racing lines;
- game assets or an official route API if EA exposes them in the future.

Until such evidence exists, width-dependent coaching remains disabled.

## Speech architecture

Python is fast enough for UDP decoding, distance scheduling, and audio queueing. Speech synthesis
is deliberately moved out of the real-time loop:

- the OpenAI provider uses the Python SDK and writes WAV files before the stage;
- the Piper provider invokes a separately installed local executable and ONNX voice model;
- content-addressed filenames prevent repeated synthesis when text, language, and provider are
  unchanged;
- the live process performs no model inference and no network request.

This design avoids a premature rewrite in another language. If profiling later shows that Python
cannot meet a measured latency target, only the small UDP scheduler/player boundary needs to move.
Rust is the preferred fallback for a compact native service, predictable latency, memory safety,
and straightforward Python interoperability. C++ remains appropriate only when direct integration
with a native TTS engine or game plugin makes it necessary.

English (`en`) and Russian (`ru`) are first-class MVP languages. The note schema stores language
variants under the same stable note ID, so timing is language-independent. Voice model licenses
vary and must be checked before distribution.

## Operational safety

Generated notes are drafts. Always replay them against a recording and perform a human recce before
live use. Incorrect direction, distance, severity, or missing hazards can invalidate a run. The
real-time scheduler is deterministic and resets when stage distance moves backwards enough to
indicate a restart; an LLM is never responsible for the moment a call is emitted.

An LLM may later improve wording, summarize a completed run, or propose coaching explanations.
Those outputs must remain downstream from recorded evidence and outside the live timing loop.
