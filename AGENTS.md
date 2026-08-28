# AI Driving Instructor

This is the local working directory for AI Driving Instructor.

## Project instructions

Project: AI Driving Instructor

Goal:
Build an AI driving instructor system for everyday drivers, driving schools, advanced driving courses, and motorsport.

The system must:
— collect OBD-II, GPS, IMU, and video data;
— synchronize telemetry by timestamp;
— retain trip history;
— detect harsh braking, rapid acceleration, dangerous cornering, and driver errors;
— generate an understandable post-trip analysis;
— compare trips and driver progress;
— synchronize events with video recordings;
— support trip playback and simulation in BeamNG;
— provide real-time voice guidance at a later stage.

Initial vehicle:
2021 Toyota Land Cruiser Prado 150, diesel.

Available hardware:
— Raspberry Pi 5;
— Teyes 360 head unit;
— dashboard camera;
— Vgate vLinker MC+ planned.

Settings:

- Source logs are stored in `drive_logs/`.
- Do not modify source CSV files.
- Keep the unified TripCompiler code at the project root; select the source with the `obd` or `wrc` argument.
- Store processing results in `compiled_trips/`.
- Use Python 3.10.
- Run tests before packaging.


First MVP priority:
Record OBD-II and GPS data, generate a post-trip report, and automatically identify significant events.

Working style:
Provide concrete engineering decisions, architecture, code, data schemas, and complexity and cost estimates; avoid unnecessary motivational language.
