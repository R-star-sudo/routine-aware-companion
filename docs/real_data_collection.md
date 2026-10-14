# Real Sensor Data Collection

This guide describes the existing laptop API and collector as a boundary for
future base-station telemetry. The collector and API store records; they do not
generate routine features, attach event labels, or make final assistive
decisions. Local prediction and essential hardware actions remain a
base-station responsibility.

## Current pipeline and data boundary

The intended collection path is:

1. Wearable sensors report to the base station over ESP-NOW.
2. The base station emits one version-1 telemetry JSON object per line over
   USB serial.
3. `app.collector` reads each line from the selected serial port, checks that
   it is a JSON object, and POSTs it to the existing API.
4. `POST /api/v1/telemetry` validates the object against
   `TelemetryRecord` and appends it to the configured CSV.

The API accepts either `source="hardware"` or `source="simulated"`. Keep
hardware collection in its own CSV file as described below. The synthetic
dataset generator writes a separate labeled dataset. The real-data preparation
module described later converts raw hardware data plus separate schedules and
annotations into a distinct, provenance-marked prepared dataset. Raw telemetry
is never a training input.

The synthetic evaluator's allowlist is
`feature_hour_sin`, `feature_hour_cos`,
`feature_expected_wake_hour_local`,
`feature_minutes_from_expected_wake`,
`feature_motion_intensity_m_s2`,
`feature_recent_motion_mean_30m_m_s2`, and
`feature_minutes_since_pillbox_open`. The time-of-day and motion features can
be derived from time and valid accelerometer observations; expected waking
features require the separately recorded routine schedule; pillbox timing
requires observed openings. Prepared-real evaluation uses a separate,
explicitly validated allowlist that adds
`feature_room_temperature_c`, the valid room reading at the prediction time.
This causal feature is necessary to predict a future room-temperature
crossing; it does not change the synthetic feature contract. The evaluator
rejects raw hardware telemetry and requires the provenance and feature/label
fields produced by the preparation step.

The wearable/base-station firmware is not yet verified to emit the version-1
NDJSON contract below. The current wearable's serial output is diagnostic,
not an established base-station stream. The firmware team must implement and
test the JSON framing, field mapping, UTC clock handling, sequence behavior,
validity reporting, and room/pillbox observations before real serial
collection can work.

## Start the API and collector

Create and activate the backend environment and install the declared
dependencies once, from the repository root:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

In the API PowerShell terminal, from `backend`, select a hardware-only
destination and start the existing API:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
$env:TELEMETRY_CSV_PATH = (Join-Path (Get-Location) "data\hardware_telemetry.csv")
uvicorn app.main:app --reload
```

The environment variable applies only to this API process and its child
processes. The API creates the parent directory and CSV file as needed. Keep
this file separate from `data\synthetic_datasets\` and the model-evaluation
artifact directory. Do not point the hardware API at a synthetic data file or
overwrite a dataset.

In a second PowerShell terminal, from `backend`, connect to the base station's
USB serial port:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m app.collector --serial-port COM3 --baudrate 115200
```

Replace `COM3` and the baud rate with the values configured for the actual
base station. The collector uses the existing default endpoint
`http://127.0.0.1:8000/api/v1/telemetry`. It reports malformed lines, API
validation failures, and connection errors to standard error. Use Ctrl+C to
stop serial collection. The collector does not currently persist-and-retry
failed posts or deduplicate sequence numbers; preserve the base station's
original serial log if transmission gaps or API downtime are possible.

## Required USB serial NDJSON contract

Send exactly one UTF-8 JSON object followed by a newline for each sample.
Do not send a JSON array, Markdown fences, log prefixes, or multiple objects
on one line. A valid illustrative hardware line is:

```json
{"schema_version":"1.0","source":"hardware","device_id":"wearable-a17","sequence":1042,"timestamp":"2026-10-14T03:45:00Z","acceleration_x_m_s2":0.12,"acceleration_y_m_s2":-0.08,"acceleration_z_m_s2":9.79,"angular_velocity_x_rad_s":0.01,"angular_velocity_y_rad_s":-0.02,"angular_velocity_z_rad_s":0.0,"pulse_rate_bpm":72.0,"skin_temperature_c":33.4,"room_temperature_c":22.1,"room_humidity_percent":45.0,"pillbox_open":null,"sensor_validity":{"mpu6050":true,"max30102":true,"ds18b20":true}}
```

The values above are an example of the wire shape, not real sensor data. The
version-1 API contract is:

| Field | Required representation |
|---|---|
| `schema_version` | Exact string `"1.0"`. |
| `source` | Exact string `"hardware"` for real collection. Never label real records as simulated. |
| `device_id` | Stable, pseudonymous device identifier, 1–100 characters. |
| `sequence` | Non-negative integer. The firmware should increment it for each record; the API does not currently enforce uniqueness, monotonicity, or gap detection. |
| `timestamp` | ISO-8601 timestamp with timezone, preferably UTC `Z`. Use a synchronized UTC clock; the API normalizes accepted offsets to UTC. Naive/local timestamps without offsets are rejected. |
| `acceleration_{x,y,z}_m_s2` | All three keys required; numeric values or JSON `null`; acceleration in m/s². |
| `angular_velocity_{x,y,z}_rad_s` | All three keys required; numeric values or JSON `null`; angular velocity in rad/s. |
| `pulse_rate_bpm` | Beats/minute or `null` when unavailable; accepted range is greater than 0 through 300. |
| `skin_temperature_c` | Skin-sensor temperature in °C or `null`; it is not core body temperature. |
| `room_temperature_c` | Optional room temperature in °C or `null` when unavailable. |
| `room_humidity_percent` | Optional relative humidity percent or `null` when unavailable. |
| `pillbox_open` | `true`/`false` only when an opening state was observed; `null` when unknown/unavailable. An opening does not prove medication was taken. |
| `sensor_validity` | Required object with boolean keys `mpu6050`, `max30102`, and `ds18b20`. When an individual is invalid, its corresponding measurements must be `null`. |

JSON `null` means missing/unavailable; never replace a missing or invalid
reading with zero. The API rejects non-finite numbers, unknown fields, and
invalid measurements. It enforces null readings when the MPU6050, MAX30102, or
DS18B20 validity flag is false. Version 1 has no room-temperature/humidity or
pillbox validity flag; firmware should report those values as `null` when it
cannot provide a trustworthy observation. A future contract revision may be
needed if explicit quality flags are required for those sources.

Acceleration fields represent acceleration in m/s²; angular-velocity fields
represent rad/s; pulse rate is BPM; temperature fields are °C; humidity is
percent. Skin temperature must remain explicitly identified as skin
temperature, not core temperature. The contract does not provide room sensor
identity, sensor calibration metadata, or a base-station clock-quality flag;
firmware/device documentation must retain those details for data-quality
review.

## Identifier and privacy practices

- Assign opaque pseudonymous IDs (for example, a random study code) to
  `device_id` and keep them stable enough to join records across a collection
  period. Do not include names, initials, addresses, dates of birth, or
  account details in identifiers or serial logs.
- Version 1 has no `profile_id`; do not overload `device_id` with a person's
  identity. Maintain any device-to-profile mapping separately, encrypted and
  access-controlled, with access limited to authorized study staff.
- Treat timestamps and routine/event observations as sensitive personal data
  even when pseudonymous. Restrict access to CSVs, avoid sharing raw records,
  use approved storage/retention practices, and do not commit collected data
  to Git.
- Keep a record of consent/authorization and collection purpose according to
  the applicable project and institutional requirements.

## Ground truth for future labels

Raw telemetry does not by itself prove an event. Keep ground-truth annotations
separate from the ingestion CSV, with a pseudonymous profile/device reference,
event type, observed event timestamp, source of confirmation, confidence or
quality note, and annotation provenance. Also record schedule effective dates
and the applicable local timezone/UTC offset so changes in routines and
daylight-saving transitions can be reconstructed. Do not infer an event from
the model prediction or fill uncertain events as confirmed.

The existing synthetic targets describe future windows, but their real-data
labels require the following independent evidence:

- **Unusual waking within 60 minutes:** record the person's expected waking
  schedule for the applicable date and a reliable waking-event timestamp.
  The synthetic definition calls a wake unusual when it is at least 30
  minutes earlier than the expected time. Confirm whether that definition is
  clinically/study appropriate before using it for real labels; do not treat
  motion alone as confirmed waking.
- **Delayed medication access within 30 minutes:** record the scheduled
  pillbox-access time and observed pillbox-opening timestamps, including
  whether access was not observed. The synthetic definition treats access
  more than 15 minutes late or absent as delayed. Opening indicates access
  only; it does not prove medication was taken or swallowed.
- **Room temperature out of range within 30 minutes:** record valid,
  time-stamped room-temperature observations and the configured comfort
  range, including when that range applies. A crossing label requires
  evidence that temperature moved from within range to outside it. Missing,
  stale, invalid, or poorly calibrated room readings must remain unavailable,
  not be converted into a crossing or an in-range value.

The current telemetry contract can carry wake-related motion, temperature,
and pillbox-open observations, but it does not carry expected schedules,
confirmed wake events, comfort-range configuration, or ground-truth
annotations. Those must be collected and curated separately before the
corresponding labels can be derived.

## Prepare a real-data dataset

Preparation accepts the raw API CSV, a daily schedule CSV, and an optional
event annotation CSV. It writes a new file; input paths are read-only, and the
output path must differ from each input and must not already exist. Keep each
input and the prepared output separate from `data\synthetic_datasets\`,
model-evaluation outputs, and the runtime telemetry file. Raw data remains
unchanged.

The schedule CSV has one row for each profile/device local date covered by
raw telemetry, with this exact required header:

```csv
profile_id,device_id,local_date,timezone,expected_wake_at,scheduled_pillbox_at,room_min_c,room_max_c
study-7f2,wearable-a17,2026-10-14,UTC,2026-10-14T07:00:00Z,2026-10-14T08:00:00Z,18,26
```

Use a pseudonymous `profile_id`, the matching raw `device_id`, an ISO local
date, and `UTC` or a fixed `±HH:MM` timezone. One profile must use the same
fixed offset throughout a preparation run; daylight-saving transitions and
timezone-rule databases are not modeled. Do not combine data across a
timezone-offset change in one run. `expected_wake_at` and
`scheduled_pillbox_at` are optional, timezone-aware ISO-8601 instants that
must fall on `local_date` in the specified timezone. Leave a value blank when
that schedule is not known; the corresponding target remains unavailable.
`room_min_c` and `room_max_c` must either both be set to a valid configured
range or both be blank; a missing range leaves room-crossing labels
unavailable. Every local date represented in telemetry needs a schedule row
so each observation can be mapped to a profile-day. A profile must map to
exactly one device and timezone in a preparation run.

The optional annotation CSV contains independently confirmed wake times:

```csv
profile_id,event_type,event_timestamp,evidence
study-7f2,wake,2026-10-14T06:20:00Z,caregiver-confirmed observation
```

`event_timestamp` must be timezone-aware; it is converted to the profile's
schedule timezone to assign its local date. One wake annotation is permitted
per profile-day. `evidence` records the annotation source/provenance, not
model input. Other event types are rejected; pillbox openings are taken from
the telemetry `pillbox_open` observations. If annotations are absent, wake
labels are blank/unavailable, not negative. Keep identifying details outside
this file; protect raw annotations as sensitive data.

From `backend`, prepare a new derived CSV:

```powershell
.\.venv\Scripts\python.exe -m app.real_data_preparation --telemetry data\hardware_telemetry.csv --schedules data\real_context\routine_schedules.csv --annotations data\real_context\event_annotations.csv --output data\prepared_real\training.csv --max-gap-seconds 600
```

Omit `--annotations` only when no wake annotations are available and wake
labels should remain unavailable. The default maximum gap is 600 seconds.
Observation continuity beyond this threshold is insufficient to assign a
future label.

The prepared CSV retains validated hardware measurements and adds profile/day
identity, `feature_*` predictors, the three existing `label_*` targets,
`quality_*` flags, and preparation version/tool plus SHA-256 input provenance.
Its causal feature allowlist includes the existing routine/time/motion/pillbox
features and `feature_room_temperature_c`, the current valid room temperature
at prediction time. That additional real-data feature is not added to the
synthetic feature contract. Identifiers, labels, schedules as raw fields,
annotation contents, quality fields, provenance, and future observations are
not predictors.

Label rules follow the synthetic target meanings but require real evidence:

- Unusual wake: require both a scheduled expected wake and a confirmed wake
  annotation. Positive means the annotated wake is more than 30 minutes
  earlier than expected and occurs in the next 60 minutes. Without either
  schedule or annotation, the target remains blank.
- Delayed medication access: require a scheduled access time and continuous,
  non-null pillbox state observations through the 15-minute grace period.
  Positive means no observed opening occurred by that grace deadline for a
  scheduled access in the next 30 minutes. An opening is access only, not
  proof of ingestion.
- Room-temperature crossing: require a configured comfort range, an
  in-range starting observation, and valid room readings throughout the next
  30 minutes. Positive means a future observation crosses outside the range.

All targets also require the relevant observation horizon to be complete
within the configured maximum sample gap and within that local profile-day
sequence. Incomplete horizons, missing required readings, missing schedules,
and missing wake annotations remain blank; they are never converted to zero.
Invalid raw telemetry is rejected rather than repaired silently.

The evaluator defaults to synthetic mode. Prepared hardware input requires
explicit selection of the preparation mode:

```powershell
.\.venv\Scripts\python.exe -m app.model_evaluation --input data\prepared_real\training.csv --output-dir data\model_evaluation\prepared_real --dataset-mode prepared-real --seed 42 --validation-fraction 0.2 --test-fraction 0.2
```

That command is documented for later use only. No real hardware records have
been verified, and real-data model training/evaluation has not been performed.
The evaluator requires the prepared-data marker, hardware source, provenance,
quality fields, exact prepared-real feature allowlist, labels, and profile/day
identity; raw telemetry does not satisfy that contract. Chronological splits
keep whole profile-day sequences together. Review ground-truth provenance,
missingness, sample gaps, and per-split target coverage before any future
training.

## Validation before any training

Before preparing a hardware dataset for model work:

1. Preserve the immutable raw hardware CSV and its collection/configuration
   notes; work on a separate derived copy.
2. Confirm every raw row has `source="hardware"`, valid schema version,
   pseudonymous device identifier, timezone-aware timestamp, and valid
   sensor-validity/data-quality fields. Check timestamp ordering, duplicate
   or missing sequence numbers, collection gaps, clock resets, and timezone
   normalization per device.
3. Review sensor ranges, calibration, stale readings, null rates, and
   sensor-validity flags. Never convert nulls or invalid values to zero.
   Exclude or mark unreliable periods using explicit quality rules.
4. Join only authorized pseudonymous profile mappings and independently
   verified routine schedules/events. Check annotation provenance and
   timestamp alignment; retain unknown/uncertain ground truth as unavailable.
5. Derive predictor features only from observations available at the
   prediction time. Derive future-window labels only from the ground-truth
   sources above. Exclude identifiers, labels, audit metadata, future
   observations, and post-event information from predictors.
6. Keep complete profile-day sequences isolated in chronological
   train/validation/test partitions. Review class counts, unavailable labels,
   missingness, and coverage by device/profile before fitting.
7. Do not pass the raw hardware CSV directly to the evaluator. Prepare a
   separate dataset with `app.real_data_preparation`, then select
   `--dataset-mode prepared-real` explicitly. The evaluator checks the
   preparation provenance, required fields, hardware source, and allowlisted
   features. This path is implemented but has not been validated on verified
   hardware records; no real model results can yet be reported.

No real-data collection has been verified until base-station firmware emits
this contract and an end-to-end serial-to-API test confirms the emitted stream
and stored records.
