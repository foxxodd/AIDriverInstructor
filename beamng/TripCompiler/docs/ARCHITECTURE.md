# Architecture — EA Sports WRC MVP

## Principles

1. **Use the official configurable UDP export.** The game-generated `channels.json` is the
   type registry; the selected packet-structure JSON is the field order. No memory scanning,
   game modification, or guessed fixed offsets are used.
2. **Preserve raw captures.** `telemetry.jsonl` stores every decoded channel before analysis.
   TripCompiler never edits it, so improved algorithms can reprocess an old stage.
3. **Separate transport from analysis.** UDP loss, schema validation, coordinate conversion,
   and event thresholds can be tested independently.
4. **Keep measurement deterministic.** The first MVP uses transparent vector projections and
   thresholds. An ML or LLM layer may explain results later but does not measure them.

## Data flow

```text
EA Sports WRC
  -> configurable little-endian UDP packet (60 Hz recommended)
  -> PacketDecoder (channels.json + wrc_ai_instructor.json)
  -> drive_logs/wrc/<session>/telemetry.jsonl
  -> normalize_packets()
  -> event rules
  -> compiled_trips/<session>/
       telemetry.csv
       events.json
       summary.json
       report.html
```

## Coordinate system

EA defines X as left, Y as up, and Z as forward. Position, velocity, and acceleration are in
world coordinates. TripCompiler projects acceleration onto the car's forward, left, and up
unit vectors to obtain longitudinal, lateral, and vertical acceleration. Heading is
`atan2(forward_x, forward_z)`. Slip angle compares the velocity vector with the car's forward
direction.

`time_s` is monotonic time since the first captured game frame. `stage_time_s` preserves EA's
stage clock, including countdown/result-screen behavior. Keeping both avoids a time reset at
the start line and supports later video synchronization.

These positions are local game-world metres, not latitude/longitude.

## Event rules

The defaults are deliberately configurable and intended as initial rally-analysis thresholds:

- hard braking: longitudinal acceleration at or below -6 m/s²;
- hard acceleration: longitudinal acceleration at or above 5 m/s²;
- high lateral acceleration: absolute lateral acceleration at or above 7 m/s²;
- handbrake at speed: handbrake at least 0.5 above 5 m/s;
- excessive slip: at least 20 degrees above 10 m/s;
- wheelspin: driven contact-patch speed exceeds body speed by at least 25%;
- brake/throttle overlap: both inputs at least 0.3.

Consecutive samples separated by no more than 0.30 seconds are consolidated. These are
engineering flags, not claims that rally technique is unsafe or incorrect.

## Loss and failure behavior

UDP is lossy. `packet_uid` gaps estimate dropped packets and appear in `capture.json` and the
compiled quality summary. A datagram with a wrong byte length is counted as malformed and is
not partially decoded. Schema or JSONL errors fail with file and line context.

The standard `wrc` layout is backward-compatible but lacks newer channels. A custom layout is
used so capture and decoder share an explicit field contract. If EA changes a channel type,
`validate` detects the resulting schema problem before recording.
