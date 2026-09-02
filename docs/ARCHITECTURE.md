# TripCompiler architecture

## Main boundary

TripCompiler is one application with two input adapters and one output contract:

```text
Car Scanner CSV -- obd.py -----+
                              +--> NormalizedSample --> event rules --> common artifacts
EA WRC UDP ------ capture.py --+
                  schema.py
```

The required `obd`/`wrc` argument selects only parsing and source-specific quality metadata.
It does not select a separate compiler implementation.

## Principles

1. **Preserve inputs.** Source CSV and WRC `telemetry.jsonl` are append-only inputs and are
   never rewritten by compilation.
2. **Normalize before analyzing.** Both adapters produce the same typed `NormalizedSample`.
   Event rules do not know whether a frame came from a physical vehicle or a simulator.
3. **Keep source evidence.** OBD produces a PID catalog and recognized raw signal table. WRC
   preserves every configured decoded channel and packet-loss counters.
4. **Keep measurement deterministic.** Vector projections, unit conversions, and thresholds
   are testable calculations. ML/LLM explanations can be added after this layer.

## OBD adapter

The importer reads the long-form Car Scanner format. GPS coordinates become local metres using
an equirectangular projection around the first point. X points east, Y is altitude, and Z points
north. Heading comes from neighbouring GPS points.

Speed uses vehicle speed when sufficiently complete and GPS speed otherwise. Explicit longitudinal and
lateral accelerations are used only when they pass signal-quality promotion; `g` is converted to m/s². Missing longitudinal
acceleration is derived from speed change. Missing lateral acceleration is estimated as
`speed * yaw_rate`.

Promotion requires at least 20 measurements, at least half-trip temporal coverage, and a median
interval no greater than ten seconds. Nearest promoted values are accepted only within ten seconds. Sparse values remain visible in
`vehicle_dynamics_raw.csv` and are not silently fabricated across long gaps.

## WRC adapter

EA's generated `channels.json` is the type registry and `wrc_ai_instructor.json` defines field
order. Packets are packed little-endian. Wrong-size datagrams are rejected rather than partly
decoded. `packet_uid` gaps estimate UDP loss.

EA defines X as left, Y as up, and Z as forward. World acceleration is projected onto the
vehicle forward/left/up vectors. Heading is `atan2(forward_x, forward_z)` and slip angle
compares velocity with vehicle orientation.

`time_s` is time since the first captured game frame. `stage_time_s` preserves EA's stage clock
including countdown and result-screen behaviour.

## WRC route and co-driver layer

The WRC instructor layer remains downstream from normalized telemetry:

```text
WRC capture --> track profile --> draft/imported pace notes --> local scheduler --> cached WAV
```

`track.py` filters the finish tail, makes stage distance monotonic, resamples the driven trace,
and derives heading, curvature, and grade. A single vehicle trajectory is not a road-boundary
measurement, so width is stored as `unknown` with no invented lane edges.

`pacenotes.py` either creates conservative curvature-based draft calls or converts a
user-supplied Zendrive-compatible file into the native schema. During WRC compilation, the CLI
looks for `<location_id>-<route_id>.json` in the ignored local Zendrive catalog and emits a primary
`pace_notes.json` when it finds a match. The geometry draft remains available for comparison. The
native schema stores stable note IDs, stage distances, structured corner fields, localized text,
confidence, and provenance. Imported third-party data retains a mandatory license-review marker.

`prepare-wrc-pacenotes --language <code>` converts every numeric source route into an ignored
`pacenotes_<language>/` catalog. An optional `<location_id>-<route_id>` filter limits the batch
to one route. Generated language catalogs contain only the selected language and can be refreshed.

`localization.py` discovers packaged exact-phrase dictionaries under
`locales/pacenotes/<language>.json`. English and Russian currently cover the complete vocabulary
of the audited Zendrive snapshot. Adding a language is a data-only change when its dictionary has
the same phrase keys. An unknown upstream phrase remains available in English and does not produce
a partial translation in another language.

`scheduler.py` owns safety-critical timing. It uses current stage distance, speed, and a bounded
lead time; it resets after a detected stage restart. Neither an LLM nor a network round trip is
in the live timing path. `tts.py` pre-generates content-addressed WAV files through the OpenAI
Python SDK. Because the complete Russian catalog contains 7,290 composed calls, the audio command
requires one `<location_id>-<route_id>` selection. It deduplicates identical calls on that route and
writes a shared WAV store plus its live-player manifest. The cache key includes model, voice,
delivery instructions, language, and note text. The live player reads only its route manifest;
speech synthesis and network access stay outside the live timing path.

## Common events

Initial defaults:

- hard braking: longitudinal acceleration <= -6 m/s² above 3 m/s;
- hard acceleration: longitudinal acceleration >= 5 m/s² above 3 m/s;
- high lateral acceleration: absolute value >= 7 m/s² above 5 m/s;
- handbrake at speed: input >= 0.5 above 5 m/s;
- excessive slip: at least 20 degrees above 10 m/s;
- wheelspin: wheel/contact speed exceeds body speed by at least 25%;
- brake/throttle overlap: both inputs >= 0.3.

Samples separated by no more than 0.30 seconds are consolidated into one interval. Thresholds
must later be calibrated separately by source, vehicle/class, controller, and road surface.

## Common outputs

Every compilation creates `telemetry.csv`, `events.json`, `summary.json`, `report.html`,
`script_ai.json`, and `road_centerline.json` in a new output directory. A WRC compilation with a
usable moving trace also creates `track_profile.json` and `pace_notes.draft.json`. Refusing to
overwrite an existing directory prevents accidental loss of an earlier analysis.
