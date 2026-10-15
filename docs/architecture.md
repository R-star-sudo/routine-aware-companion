# Architecture and verified status

## Current end-to-end software path

```text
simulator or serial NDJSON
  -> collector (stdin/serial)
  -> FastAPI telemetry schema and CSV storage
  -> browser dashboard history view
```

The simulator and collector can use the same newline-delimited JSON contract.
The dashboard can retrieve stored telemetry from the local API. This software
path is separate from physical-device verification.

The offline ML path generates synthetic profile-day data, builds causal
features and future-event labels, splits whole profile-days chronologically,
fits/evaluates models, and writes local reports and joblib artifacts. The
prediction API accepts the existing seven-feature allowlist directly. Live
telemetry is not enough to derive all those inputs: schedule and observation
history features are not currently assembled by an online feature builder.
Therefore the endpoint is a feature-level demonstration, not an automatic
live prediction service. Synthetic-only results and low precision mean all
model-driven reminders are disabled by default.

The reminder policy is a separate service. It checks target enablement,
positive prediction, freshness, missing feature count, and per-target
cooldown. Current runtime configuration disables the three ML targets. Cooldown
behavior for those targets is unit-tested with an explicitly enabled test
policy; this does not enable model reminders in the running app.

The exception is a user-triggered, named `scheduled_check_in` demo rule. It is
labelled `simulated_demo` and `rule_based`, passes through the same policy and
cooldown, and produces an event with a reason and delivery state. The event
store is in memory and resets when the backend restarts. Eligible demo events
can request speech through the existing voice endpoint; event-linked playback
states are updated by the browser. No model target is enabled by this path.

The optional voice path is a user-entered dashboard prompt → backend
`POST /api/v1/voice/speech` → ElevenLabs MP3 → browser audio playback or visible
Play control. It is not connected to model-generated reminders, and there is
no ESP32 speaker output. Provider failures and missing configuration are
reported; no provider is called in tests.

## Component status

- **Telemetry schema and API:** implemented and covered by backend tests.
- **CSV persistence and collector:** implemented; simulator input is available.
- **React dashboard:** demo, CSV-import, and local API history views exist.
- **Synthetic data and evaluation:** implemented, reproducible with a fixed
  seed, and kept in ignored local output directories.
- **Inference:** saved synthetic validation-selected artifacts are loaded
  once per target and accept only the fixed feature allowlist. Artifacts must
  match the locally generated dataset/model environment.
- **Feature preparation:** `/api/v1/features/prepare` computes the same seven
  features from ordered validated observations and an explicit timezone and
  wake schedule. Stale records, inadequate 30-minute motion coverage, invalid
  motion, missing observed pillbox access, or missing routine context suppress
  scoring with per-feature reasons. No future labels are created at runtime.
- **Reminder policy:** implemented; all targets are disabled for autonomous
  reminders because measured synthetic test precision is inadequate.
- **Voice:** optional manual prompt generation and browser playback; provider
  credentials remain backend-only.
- **Firmware:** wearable and base-station source exists. No physical device was
  available for verification. Source inspection shows startup/status log lines
  plus a nested `{src, wearable, base, rf}` JSON record, which does not match the
  collector's flat version-1 telemetry schema. The serial collector now has an
  opt-in adapter with source-shaped fixture tests. It requires an operator
  supplied device ID and UTC boot-time correlation because firmware only emits
  uptime milliseconds. No physical serial run was performed.

## Telemetry and data quality

Schema version `1.0` requires timezone-aware timestamps, a source of
`simulated` or `hardware`, sequence/device identity, finite measurements, and
sensor validity. Optional readings can be null. Data are appended to a local
CSV store, not a multi-user database. `TELEMETRY_CSV_PATH` configures its
location; the default is ignored by Git.

The simulator emits telemetry-shaped NDJSON. Hardware serial input is only
usable if the base station emits newline-delimited objects that validate
against the same schema. Do not infer medical facts from sensor readings.

## Local safety and deployment boundary

The API is intended for localhost development. It has no authentication or
rate limiting. CORS permits the two local Vite origins. Do not expose it to a
network or public deployment without suitable authentication, authorization,
rate limits, and deployment review. Keep ElevenLabs credentials in
`backend/.env` or server environment only; never put them in frontend code.

## Future work

1. Verify base-station serial records against schema 1.0 using physical devices.
2. Build and test causal online feature construction from schedules and
   observation history before using models on live telemetry.
3. Collect representative, consented, independently labelled data and repeat
   evaluation before considering any target for reminders.
4. Connect policy-eligible reminders to a clear dashboard event and the
   existing optional voice path, retaining user playback and delivery state.
5. Add access controls before any network-exposed use.
## Current milestone

The repository contains both ESP32 firmware projects, a serial-to-API
collector, a versioned telemetry contract (`1.0`), FastAPI/CSV storage, and a
React dashboard. Firmware and hardware behavior have not yet been verified on
the assembled devices. Base Station LED command handling, a wearable double-
tap event, and the serial-to-cached-audio path are implemented in code, but
prediction triggers and hardware behavior still need integration and bench
verification.

## Intended data flow

```text
Wearable sensors and double-tap detector
  -> ESP-NOW packet
  -> Base station + DHT11 + morning pillbox Hall sensor + LEDs
  -> compact NDJSON/events over USB Serial
  -> collector maps telemetry, stores it through FastAPI, and routes SPEAK cues
  -> laptop default audio player -> AUX-connected speaker
  -> React dashboard history view

Laptop startup commands -> Base Station over that same USB serial connection
  (SIM_CLOCK, learned P parameters, LED commands, or a voice-path test)
```

## USB serial action protocol

The collector is the only laptop process that opens the Base Station COM port.
Every command and event is one newline-terminated ASCII line at 115200 baud.

| Direction | Line | Effect |
| --- | --- | --- |
| Laptop → base | `SIM_CLOCK,<unix_s>,<utc_offset_min>,<rate>` | Set accelerated simulated wall time. |
| Laptop → base | `P,<mu_g>,<sigma_g>,<n>,<p_break>` | Validate and save the latest parameter values in flash. Runtime prediction does not use them yet. |
| Laptop → base | `LED,MORNING,ON` / `LED,MORNING,OFF` | Control the morning reminder LED; a confirmed lid interaction turns it off. |
| Laptop → base | `LED,NIGHT,ON` / `LED,NIGHT,OFF` | Control the night LED; it turns off on double tap or after 120 seconds. |
| Laptop → base | `LED,CARETAKER,ON` / `LED,CARETAKER,OFF` | Control the caretaker LED; it turns off on double tap or after 120 seconds. |
| Laptop → base | `LED,AFTERNOON,ON` / `LED,AFTERNOON,OFF` | Control the afternoon reminder LED; a confirmed afternoon lid interaction turns it off. |
| Laptop → base | `LED,PATH_WC,<0–255>` / `LED,PATH_KIT,<0–255>` | Set path-light PWM brightness. |
| Laptop → base | `TEST_SPEAK,V01` | Test command. The base returns `SPEAK,V01` to the laptop. |
| Base → laptop | `SPEAK,V01` | Collector opens `V01.mp3` from the audio directory with the laptop's default audio player. |
| Base → laptop | `TAP,DOUBLE,<sequence>` | Report a wearable double-tap event; base also clears night and caretaker LEDs. |
| Base → laptop | `EVENT,PILLBOX_INTERACTION` | Report a morning lid open lasting at least 1.5 seconds followed by close. |
| Base → laptop | `EVENT,PILLBOX_AFTERNOON_INTERACTION` | Report an afternoon lid open lasting at least 1.5 seconds followed by close. |

Startup parameters can be supplied with `--model-parameters
"mu_g,sigma_g,n,p_break"`. Voice playback expects an existing cached MP3;
ElevenLabs generation and the audio asset are not part of the repository yet.

The simulator emits versioned schema 1.0 NDJSON. The base station emits a
compact nested JSON record alongside human-readable status and action lines.
The collector handles recognized action events, adapts base-station telemetry
to the versioned flat schema, and forwards records to the API. It also accepts
already versioned simulator records from stdin. The API validates every record
before appending it to CSV; latest/history endpoints expose the stored records
to the dashboard.

CSV is a local prototype store, not a multi-user database. Its location is
configurable using `TELEMETRY_CSV_PATH`; the default is
`backend/data/telemetry.csv`, which is excluded from Git.

The routine dataset generator is a distinct offline tool. It writes a flat CSV
to `backend/data/synthetic_datasets/routine_dataset.csv` by default and uses
the existing telemetry contract's measurement names, units, and validation
model. Its defaults are 14 days, five-minute samples, seed `20261014`, a
per-profile-day scenario probability of 0.15, UTC, and start date
`2026-01-01`. It accepts whole-second or whole-minute sampling intervals that
divide evenly into 24 hours, fixed-offset timezones, output path, and room
comfort bounds. Its output directory is ignored by Git.

The dataset is grouped into identity, telemetry, `feature_*`, `label_*`, and
`metadata_*` columns. Features are computed only from current/past observations
or clock/routine context. Scenario flags and event timestamps in
`metadata_*` exist for audit only; they are not predictors. The labels target
future events: unusual early waking in the next 60 minutes (at least 30
minutes ahead of a profile's expected wake time), delayed or absent pillbox
access associated with the scheduled access time in the next 30 minutes
(15-minute grace period; not medication ingestion), and first crossing beyond
the configurable room comfort bounds (18–26 °C by default) in the next 30
minutes. Rows without a complete 60-minute future horizon have blank labels.
The final hour of each profile-day `sequence_id` has blank labels. The
generator reports each label's positive, negative, and unavailable counts.

Synthetic data is useful for software development and initial evaluation, but
cannot establish real-world accuracy or medical conclusions. Model evaluation
should split by complete profile-day sequences to avoid leakage across
neighboring rows.

`backend/app/real_data_preparation.py` is a separate offline preparation step
for eventual hardware training data. It validates the API's raw hardware CSV
against `TelemetryRecord`, joins a daily schedule CSV and optional independently
confirmed wake annotations, and writes a distinct prepared CSV with causal
features, future labels, quality flags, and hashes identifying its inputs.
The preparation step never edits raw telemetry. Missing schedules,
annotations, readings, or incomplete sample horizons leave the affected labels
unavailable rather than negative. Schedule and annotation formats, label
requirements, data-quality assumptions, and commands are documented in
`docs/real_data_collection.md`.

The offline evaluator in `backend/app/model_evaluation.py` keeps synthetic
evaluation as its default. `--dataset-mode prepared-real` must be explicitly
selected for preparation-marked hardware rows; raw API telemetry is rejected
because it lacks the prepared feature/label contract and provenance. Synthetic
mode retains its existing explicit feature allowlist. Prepared-real mode has a
separate allowlist adding only the current valid room-temperature reading
(`feature_room_temperature_c`) so room-crossing prediction has a legitimate
current observation. Labels, annotation contents, metadata, quality fields,
identifiers, and future observations remain excluded. The replaceable
decision-tree implementation is in
`backend/app/models/decision_tree.py`. Median imputation is fitted inside each
model pipeline on training data only. Both modes split chronologically within
each profile by complete profile-day sequences; no sequence crosses
partitions. Results report class counts, precision, recall, F1, confusion
matrix, average precision where defined, and limitations. Undefined precision,
recall, and F1 values are null when denominators are empty. Decision-tree
pipelines are saved as joblib artifacts for offline experimentation.

The prepared-real path is implemented but has not been run on verified
hardware data. No real model is being trained or deployed in this milestone.
Laptop artifacts are not runtime models; final predictive decisions and
essential hardware actions remain the base station's responsibility.

## Telemetry contract version 1.0

All records include `schema_version` (`"1.0"`), `source` (`"simulated"` or
`"hardware"`), `device_id`, non-negative `sequence`, and an ISO-8601
timezone-aware `timestamp` normalized to UTC.

| Field(s) | Unit / meaning |
| --- | --- |
| `acceleration_x_m_s2`, `acceleration_y_m_s2`, `acceleration_z_m_s2` | MPU6050 acceleration, metres per second squared. |
| `angular_velocity_x_rad_s`, `angular_velocity_y_rad_s`, `angular_velocity_z_rad_s` | MPU6050 angular velocity, radians per second. |
| `pulse_rate_bpm` | MAX30102-derived pulse rate, beats per minute; `null` when unavailable. |
| `skin_temperature_c` | DS18B20 temperature in degrees Celsius. This is a peripheral/skin measurement, not core body temperature; `null` when unavailable. |
| `room_temperature_c` | Optional base-station room temperature in degrees Celsius; `null` when unavailable. |
| `room_humidity_percent` | Optional base-station relative humidity, percent (0–100); `null` when unavailable. |
| `pillbox_open` | Optional pillbox-open state; `null` when unavailable. Opening a container does not establish that medication was taken. |
| `sensor_validity` | Required boolean validity flags for `mpu6050`, `max30102`, and `ds18b20`. Measurements from an invalid sensor must be `null`. |

Motion and skin-temperature units reflect the wearable implementation:
MPU6050 acceleration is in m/s² and angular velocity is in rad/s; MAX30102
rate is BPM; DS18B20 and DHT11 temperatures are Celsius. The collector maps
`wearable.pkt` to `sequence`, `wearable` sensor fields to the versioned
measurements/validity flags, and `base` fields to room and pillbox measurements.
It converts `clock.unix_s` to a UTC ISO-8601 timestamp. If the simulated clock
is unset, the collector skips the hardware record instead of substituting the
laptop's real time. Invalid or unavailable sensor readings become `null`.

For activity displays, the frontend derives a simple normalized deviation of
acceleration magnitude from gravity. This is only a visualization value; it
is not the predictive model's motion feature.

## Component responsibilities and status

- **Wearable ESP32 — implemented in code, hardware not verified:**
  `firmware/wearable/` reads MPU6050, MAX30102, and DS18B20 data, validates
  sensor health, detects a two-impulse wrist gesture, and transmits packed
  ESP-NOW packets on channel 1. The double-tap thresholds need adjustment with
  the assembled wearable. GPIO 2 blinks as a heartbeat; GPIO 13 stays lit
  while the gesture detector is armed and briefly turns off after a detected
  double tap. GPIO 21/22 are I²C SDA/SCL; GPIO 4 is the 1-Wire pin. The optional
  vibration motor is not used by this integration.
- **Base station ESP32 — implemented in code, hardware not verified:**
  `firmware/base_station/` receives wearable packets, tracks packet/link
  health, reads DHT11 on GPIO 4 and the morning pillbox Hall sensor on GPIO 32,
  and emits compact NDJSON over USB serial. GPIO 33 is intentionally unused
  because the afternoon Hall sensor is not wired. GPIOs 16, 17, 18, 19, 23, 27,
  and 25 drive the mapped morning, afternoon, night, caretaker, path, and status
  LEDs. The caretaker indicator pulses, while night/caretaker reminders time
  out after 120 seconds. A confirmed morning-pillbox interaction means the lid stayed open for
  at least 1.5 seconds and then closed; it does not prove medication ingestion.
  Night/caretaker LEDs can be switched off by a wearable double tap. The
  non-blocking `SIM_CLOCK` command supplies accelerated virtual time. The
  firmware stores `P,mu_g,sigma_g,n,p_break` values in flash, but does not yet
  use them for predictions.
- **Laptop collector — implemented, hardware not verified:**
  `backend/app/collector.py` is the single USB serial owner. It reads
  versioned NDJSON, maps compact base-station telemetry to schema 1.0, and
  posts records to the API. At startup it can send the simulated clock, stored
  model parameters, and other base commands. It handles `SPEAK,V01` by opening
  `V01.mp3` from its audio directory with the laptop's default player. The
  cached clip is not included in the repository, and ElevenLabs generation is
  not implemented here. The collector performs no predictions.
- **Simulator — implemented:** `backend/app/simulator.py` generates
  timestamped, explicitly simulated records on stdout; diagnostic output is
  kept on stderr.
- **Routine dataset generator — implemented:** `backend/app/dataset_generator.py`
  generates reproducible synthetic profile/day histories and future labels in
  a separate CSV. It is not the live telemetry simulator and does not train or
  run a predictive model.
- **Real-data preparation — implemented, not yet validated on hardware:**
  `backend/app/real_data_preparation.py` validates raw hardware telemetry and
  joins separate schedules and wake annotations to produce a distinct
  provenance-marked dataset. It does not fabricate observations or ground
  truth; prepared-real evaluation requires an explicit evaluator mode.
- **FastAPI and CSV — implemented:** `backend/app/main.py` provides ingestion,
  latest, and bounded history endpoints. Local Vite origins can call the API;
  `backend/app/services/telemetry_storage.py` uses Python's standard `csv`
  module. There is no database in this milestone.
- **React caregiver dashboard — implemented:** `frontend/` provides clearly
  separated DEMO, LIVE, and browser-local CSV modes. LIVE reads the schema 1.0
  history endpoint and displays observations; it does not invent forecasts or
  claim physical actions.
- **Training and evaluation — synthetic baseline implemented; real-data
  preparation implemented:** the default chronological evaluation operates
  on synthetic data only. Prepared-real input validation exists, but no
  verified hardware dataset has been prepared or evaluated. Real model
  training/evaluation and deployment of a target-suitable model remain
  unperformed. Final runtime inference and decisions belong on the base
  station, not the laptop service.

Sensor readings are observations, not confirmed medical facts. The system
should describe changes as deviations from an individual's usual baseline,
not generate diagnoses, and distinguish observations, predictions, and
confirmed actions.
