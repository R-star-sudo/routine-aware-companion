# Architecture

## Current milestone

<<<<<<< HEAD
Implemented software supports synthetic telemetry and laptop-side CSV logging
through one local FastAPI service. The data contract is versioned as `1.0`.
The separate `backend/app/dataset_generator.py` creates deterministic
multi-day synthetic routine sequences with future-event labels; it does not
call the API or train a model. Firmware-to-firmware and firmware-to-laptop
transport and the caregiver dashboard remain planned.

## Intended data flow

```text
Wearable sensors
  -> ESP-NOW
  -> Base station ESP32
  -> newline-delimited JSON over USB Serial
  -> Laptop collector
  -> existing FastAPI application
  -> CSV storage
  -> future React caregiver dashboard
```
=======
This document describes the intended system architecture. Current implementation status:

- **Firmware (`firmware/`)**: **Implemented**. Both the Wearable and Base Station ESP32 firmware nodes are implemented, configured with PlatformIO (`espressif32@6.5.0`), and ready for deployment.
- **Laptop Pipeline & Applications (`backend/`, `frontend/`)**: **Planned**. The serial data collection pipeline, FastAPI backend, SQLite database, React dashboard, and predictive routine services are scheduled for subsequent implementation steps.

## Components

- **Wearable (`firmware/wearable/`, implemented)**: ESP32-based wearable sensor node. Collects inertial motion (MPU6050 over I2C), heart rate / IR proximity (MAX30102 over I2C), and peripheral skin temperature (DS18B20 over 1-Wire). Includes live sensor health and staleness tracking (`mpu_ok`, `max_ok`, `ds_ok`). Broadcasts packed, byte-aligned telemetry (`SensorData` struct with `0xAA55` magic header and 8-bit XOR checksum) over ESP-NOW on fixed Wi-Fi Channel 1 at 10 Hz (100ms interval).
- **Base station (`firmware/base_station/`, implemented)**: ESP32-based local hub. Receives wearable ESP-NOW packets via a thread-safe FreeRTOS queue bridging the Core 0 Wi-Fi stack to the Core 1 main loop. Monitors RF statistics, packet drop accounting, sequence gaps, and transmitter reboot events. Reads local ambient temperature/humidity (DHT11 on GPIO 4) and pillbox status (Hall effect sensor on GPIO 5). Emits unified single-line JSON records over USB Serial at 115200 baud for laptop logging. Critical functions (pathway lighting, fallback reminders) are designed to function autonomously without internet access.
- **Backend (`backend/`, planned)**: A Python and FastAPI service. Its API layer will expose application operations, schemas will define and validate data contracts, services will hold application logic, core will contain shared configuration and cross-cutting concerns, and db will manage persistence.
- **Frontend (`frontend/`, planned)**: A React, TypeScript, and Vite application for older adults and caregivers to interact with the system, review information, and manage settings.
- **Database (`backend/app/db/`, planned)**: Persistence will start with SQLite. Data access and schema choices should allow a future move to PostgreSQL without coupling application logic unnecessarily to SQLite.
- **Predictive services (`backend/app/services/`, planned)**: Services may learn an individual's usual routine and identify deviations for reminder and caregiver workflows. They must distinguish observations from predictions and confirmed actions, account for missing or poor-quality readings, and avoid medical diagnoses or claims that sensor data proves a medical condition or action.
>>>>>>> 913503692b1d26c6fb28516d01a236cbd1b3f411

The simulator emits the same NDJSON record format expected from the future
base-station serial interface. The collector accepts stdin for hardware-free
simulation or an optional pyserial port for later integration. It validates
that each line contains a JSON object, forwards it to the local API, and
reports malformed input and request failures. The API validates the complete
telemetry schema before appending it to CSV. Latest/history endpoints expose
stored records to future visualization clients.

<<<<<<< HEAD
CSV is a local prototype store, not a multi-user database. Its location is
configurable using `TELEMETRY_CSV_PATH`; the default is
`backend/data/telemetry.csv`, which is excluded from Git.
=======
Sensor readings are observations, not confirmed medical facts. Changes should be described relative to the individual's usual baseline. Skin temperature is not core body temperature, and opening a medication container does not establish that medication was taken. Important events should be logged, and safe local behavior should not depend on internet availability.
>>>>>>> 913503692b1d26c6fb28516d01a236cbd1b3f411

The routine dataset generator is a distinct offline tool. It writes a flat CSV
to `backend/data/synthetic_datasets/routine_dataset.csv` by default and uses
the existing telemetry contract's measurement names, units, and validation
model. Its defaults are 14 days, five-minute samples, seed `20261014`, a
per-profile-day scenario probability of 0.15, UTC, and start date
`2026-01-01`. It accepts whole-second or whole-minute sampling intervals that
divide evenly into 24 hours, fixed-offset timezones, output path, and room
comfort bounds. Its output directory is ignored by Git.

<<<<<<< HEAD
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
| `room_temperature_c` | Optional base-station room temperature in degrees Celsius; `null` until integrated or when unavailable. |
| `room_humidity_percent` | Optional base-station relative humidity, percent (0–100); `null` until integrated or when unavailable. |
| `pillbox_open` | Optional pillbox-open state; `null` until integrated or when unavailable. Opening a container does not establish that medication was taken. |
| `sensor_validity` | Required boolean validity flags for `mpu6050`, `max30102`, and `ds18b20`. Measurements from an invalid sensor must be `null`. |

Motion and skin-temperature units reflect the current wearable implementation:
Adafruit MPU6050 event readings are acceleration in m/s² and angular velocity
in rad/s; the firmware reports the MAX30102-derived rate as BPM and reads
DS18B20 Celsius values. The firmware currently prints human-readable
diagnostics rather than NDJSON. Sequence, UTC wall-clock timestamp, the shared
JSON contract, explicit `null` serialization, and quality flags therefore
still require base-station/firmware integration. Room temperature, humidity,
and pillbox state are future optional inputs and are not currently produced by
the wearable code.

## Component responsibilities and status

- **Wearable ESP32 — partially implemented:** `firmware/wearable/` configures
  an `esp32dev` Arduino target and reads MPU6050, MAX30102, and DS18B20 data.
  GPIO 21/22 are I²C SDA/SCL; GPIO 4 is the 1-Wire data pin. It currently
  writes diagnostic text to USB serial. ESP-NOW transmission and NDJSON output
  are not implemented.
- **Base station ESP32 — planned:** `firmware/base_station/` is currently a
  placeholder. It must receive wearable messages and retain responsibility
  for final predictive decisions and essential local hardware actions,
  including safe fallback behavior, without relying on the laptop or internet.
  Its serial output must use the versioned NDJSON contract.
- **Laptop collector — implemented:** `backend/app/collector.py` reads NDJSON
  from stdin or optionally from a pyserial port and forwards records to the
  existing API. It performs no predictions or hardware actuation.
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
  latest, and bounded history endpoints. `backend/app/services/telemetry_storage.py`
  uses Python's standard `csv` module. There is no database in this milestone.
- **React caregiver dashboard — planned:** `frontend/` is currently a
  placeholder. It can later read validated API history for visualization.
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
=======
- `firmware/wearable/`: PlatformIO project for wearable telemetry transmitter.
- `firmware/base_station/`: PlatformIO project for base station receiver and USB serial bridge.
- `backend/`: Placeholder structure for upcoming FastAPI services and routine models.
- `frontend/`: Placeholder structure for upcoming React/Vite dashboard.
- `docs/`: System documentation and architecture specifications.

>>>>>>> 913503692b1d26c6fb28516d01a236cbd1b3f411
