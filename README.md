# Routine-Aware Companion

Routine-Aware Companion is a Hacktivate 2026 predictive-systems prototype for
an elderly user's routine. It combines two ESP32 firmware projects, a laptop
collector/API, and a React dashboard. Hardware operation still needs bench
verification.

## Current status

**Implemented in code:** wearable sensor acquisition, ESP-NOW transmission,
and double-tap event reporting; base-station ESP-NOW reception, local
DHT11/morning Hall readings, mapped LEDs, compact JSON serial output, simulated
clock, and serial command handling; a collector that adapts telemetry, sends
startup parameters, and routes cached voice-cue commands to the laptop audio
player; FastAPI ingestion/history with CSV storage; synthetic-data tools; and
DEMO, LIVE, and local CSV dashboard modes.

**Still to integrate or verify:** physical sensor/radio/LED behavior, cached
voice-file availability and playback, live data collection, and base-station
prediction logic. The serial/action path is present, but predictive triggers
are not implemented. Offline model training/evaluation is separate from runtime
firmware. No live forecast is claimed by the dashboard.

See [docs/architecture.md](docs/architecture.md) for architecture and the
telemetry field definitions. See
[docs/real_data_collection.md](docs/real_data_collection.md) for the documented
hardware data-collection workflow and ground-truth requirements.

## Backend setup

Run these commands in Windows PowerShell from the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the API (keep it running in its terminal):

```powershell
uvicorn app.main:app --reload
```

The API writes to `backend/data/telemetry.csv` by default, creating the
directory when needed. Override the location before starting the API with
`$env:TELEMETRY_CSV_PATH = "C:\path\to\telemetry.csv"`.

## Simulate and collect

In a second PowerShell terminal, from the `backend` directory, pipe a
simulated newline-delimited JSON stream through the same collector interface
used for serial input:

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.simulator --count 20 --interval 0.5 | python -m app.collector
```

To collect base-station telemetry, set a timezone-aware simulated start time.
Opening the serial port may reset the ESP32; the collector waits for boot,
sends the clock command, ignores human-readable status lines, adapts each
telemetry packet, and posts it to the API. Replace `COM3` with the base
station's port:

```powershell
python -m app.collector --serial-port COM3 --baudrate 115200 --simulation-start "2026-10-14T07:00:00+05:30" --simulation-rate 60
```

The rate is virtual seconds per real second (1–3600). At `60`, one simulated
day takes 24 real minutes. Hardware sampling and ESP-NOW timing remain real
time; only the record timestamps use the simulated clock. A clock-only check
that prints serial output without API ingestion is available with `--clock-only`.
Hardware records are skipped when the base-station clock is unset.

The collector owns the USB serial port while it runs. It can send the learned
parameter line (`P,mu_g,sigma_g,n,p_break`) once at startup and save those
values in Base Station flash. This stores parameters for later runtime-model
work; the current firmware does not use them to make predictions. To try the
serial-to-audio route, put the cached ElevenLabs clip at
`backend/app/audio/V01.mp3`, route Windows audio to the AUX-connected speaker,
then run:

```powershell
python -m app.collector --serial-port COM3 --speak-test V01 --audio-dir app/audio
```

The collector sends `TEST_SPEAK,V01`; the Base Station emits `SPEAK,V01`, and
the laptop opens the matching cached MP3 with its default audio player. The
clip is not currently included in the repository. Other Base Station serial
commands can be sent at startup with repeatable `--base-command`, for example
`--base-command LED,NIGHT,ON`.

## Firmware

Build/upload both PlatformIO projects with the PlatformIO CLI:

```powershell
pio run -d firmware/wearable
pio run -d firmware/base_station
pio run -d firmware/wearable -t upload
pio run -d firmware/base_station -t upload
```

The base station and wearable must use the same ESP-NOW channel and compatible
packet layouts. The packet now includes a double-tap event sequence, so upload
both firmware projects together. Pin assignments and hardware setup are
documented in their source files and `docs/architecture.md`.

## Frontend

In another terminal, run:

```powershell
cd frontend
npm install
npm run dev
```

Open **Settings → Data source → Live dashboard API** to view collected
observations. The default API address is `http://127.0.0.1:8000`; set
`VITE_API_BASE_URL` in `frontend/.env.local` only if it differs. Live mode
shows observations and leaves forecasts/events empty until those services are
implemented. The Settings CSV import accepts backend CSV fields such as
`pulse_rate_bpm`, `skin_temperature_c`, `room_temperature_c`,
`room_humidity_percent`, acceleration axes, `pillbox_open`, and
`sensor_validity`.

## Prepare real telemetry for future model work

Real hardware must first be collected to a separate raw file using
`TELEMETRY_CSV_PATH`, then accompanied by the daily schedule and independent
event-annotation CSVs described in
[docs/real_data_collection.md](docs/real_data_collection.md). From `backend`,
prepare a new, not-yet-existing output file without overwriting raw telemetry
or prior prepared data:

```powershell
.\.venv\Scripts\python.exe -m app.real_data_preparation --telemetry data\hardware_telemetry.csv --schedules data\real_context\routine_schedules.csv --annotations data\real_context\event_annotations.csv --output data\prepared_real\training.csv --max-gap-seconds 600
```

The annotation file is optional; without it, waking labels remain unavailable.
Preparation validates raw records and adds profile-day identity, causal model
features, future-event labels, quality flags, and input-hash provenance. The
prepared-real evaluator mode is explicit and never selected by default:

```powershell
.\.venv\Scripts\python.exe -m app.model_evaluation --input data\prepared_real\training.csv --output-dir data\model_evaluation\prepared_real --dataset-mode prepared-real --seed 42 --validation-fraction 0.2 --test-fraction 0.2
```

The second command is documented for a future dataset only; it has **not**
been run on real records. Base-station serial output and real data collection
are not yet verified. See the collection guide for schedule/annotation
formats, target requirements, missing-data behavior, privacy, and limitations.

## Generate a synthetic routine dataset

The dataset generator is separate from the live telemetry simulator. It
creates multi-day, multi-profile histories with future-window labels, without
starting the API or requiring hardware:

```powershell
Set-Location backend
.\.venv\Scripts\Activate.ps1
python -m app.dataset_generator --days 14 --interval-minutes 5 --seed 20261014 --scenario-rate 0.15 --start-date 2026-01-01 --timezone UTC --output data\synthetic_datasets\routine_dataset.csv
```

Defaults are 14 days, a five-minute interval, seed `20261014`, scenario rate
`0.15`, start date `2026-01-01`, timezone `UTC`, and
`data\synthetic_datasets\routine_dataset.csv`. Use exactly one of
`--interval-minutes` or `--interval-seconds`; the interval must divide evenly
into 24 hours and cannot exceed 60 minutes. Scenario rate is a probability
from 0 through 1 applied independently to each profile-day for each synthetic
scenario. The timezone accepts `UTC` or a fixed offset such as `+05:30`;
daylight-saving rules are not modeled. Generated training data is ignored by
Git under `backend/data/synthetic_datasets/`.

The generator uses three fixed synthetic profiles with expected wake times of
06:30, 07:00, and 07:30 local time. The row count is
`days × 3 profiles × (86,400 / interval_seconds)`.

The CSV groups columns by prefix: identity and existing version-1 telemetry
measurements/validity flags; `feature_*` inputs computed from current or past
observations; `label_*` future targets; and `metadata_*` scenario-audit fields.
Do not use labels or audit metadata as model features. Labels are:

- `label_unusual_wake_within_60m`: an unusually early waking event starts in
  the next 60 minutes, at least 30 minutes before the profile baseline.
- `label_delayed_medication_access_within_30m`: the expected pillbox-access
  schedule is in the next 30 minutes and access is delayed by more than the
  15-minute grace period or absent. This is access only, not proof of
  medication ingestion.
- `label_room_temp_out_of_range_within_30m`: simulated room temperature first
  crosses outside the configurable comfort range in the next 30 minutes
  (defaults: 18–26 °C).

Labels are `0`, `1`, or blank in the final hour of each profile-day sequence,
where the full 60-minute maximum horizon is unavailable. Generation reports
positive, negative, and unavailable counts for each label. Synthetic values support engineering and
initial pipeline evaluation only; they do not establish real-world predictive
accuracy or medical conclusions. Future evaluation should split by complete
profile-day sequences, not randomly split neighboring rows.

## Train and evaluate baseline models

After generating the synthetic dataset, run the offline evaluator from
`backend`:

```powershell
python -m app.model_evaluation --input data\synthetic_datasets\routine_dataset.csv --output-dir data\model_evaluation --seed 42 --validation-fraction 0.2 --test-fraction 0.2
```

The evaluator trains one shallow decision tree per existing future-event
label. Its explicit predictor allowlist is:
`feature_hour_sin`, `feature_hour_cos`,
`feature_expected_wake_hour_local`,
`feature_minutes_from_expected_wake`,
`feature_motion_intensity_m_s2`,
`feature_recent_motion_mean_30m_m_s2`, and
`feature_minutes_since_pillbox_open`. Labels, metadata, raw identifiers,
telemetry measurements, and future observations are not predictors. Missing
allowed feature values are median-imputed by a preprocessing pipeline fit on
training rows only. Prepared-real mode uses this existing allowlist plus the
causal `feature_room_temperature_c`, derived from the current validated room
reading; this change does not alter the synthetic feature contract.

The split is chronological within each profile: latest complete profile-day
sequences are held out for testing; the immediately preceding complete
sequences are validation; and earlier complete sequences are training. By
default, validation and test are each 20% of each profile's days. Complete
sequences never cross partitions. The current baseline model is fit only on
training rows; validation is reserved for future model selection and is not
used to tune this baseline. Results on identical test rows compare precision,
recall, F1, confusion matrix, and average precision (when both test classes
are present) against a baseline that predicts the training majority class
using a 0.5 prevalence threshold. Average precision is computed with
`sklearn.metrics.average_precision_score`, not trapezoidal PR-curve area.
Per-target train/validation/test class counts and limitations are reported.
Undefined precision, recall, or F1 is reported as null, not zero, when its
denominator is empty.
Artifacts are written to `data\model_evaluation\evaluation.json`,
`data\model_evaluation\report.md`, and `data\model_evaluation\models\`.

These results are based on synthetic data and **do not establish real-world
accuracy**. Synthetic patterns may be simpler than real routines, sensors, and
missingness. Evaluation on representative real data and eventual validation
on the base-station target are still required. This laptop pipeline does not
move final predictive decisions or hardware actions off the base station.

## API endpoints

- `GET /` — project welcome message.
- `GET /health` — API health.
- `POST /api/v1/telemetry` — validate and append a telemetry record.
- `GET /api/v1/telemetry/latest` — retrieve the newest record (404 when empty).
- `GET /api/v1/telemetry/history?limit=100` — retrieve newest records first;
  limit must be from 1 to 500.

The dashboard reads the history endpoint directly. The API enables browser
requests only from localhost development origins.

## Tests

From the `backend` directory with the virtual environment activated:

```powershell
python -m pytest
```

Tests use temporary CSV paths and do not require physical hardware.
