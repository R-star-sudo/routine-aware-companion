# Routine-Aware Companion

Hackathon prototype for a routine-aware companion for older adults. This is
not a medical device. A pillbox opening is an interaction observation and does
not establish that medicine was taken. Skin temperature is not core body
temperature.

## Current software status

Implemented and locally testable: versioned telemetry validation, a FastAPI
service with CSV storage, stdin/serial NDJSON collector, telemetry simulator,
synthetic routine dataset generator, chronological offline model evaluation,
saved synthetic scoring artifacts, a feature-validated experimental scoring
API, conservative reminder policy, React dashboard, CSV import, and optional
ElevenLabs MP3 generation/playback through the browser.

Autonomous model reminders are disabled for all three targets. Synthetic held
out evaluation produced low precision, and synthetic performance is not
evidence of real-world accuracy. The scoring endpoint is for an explicitly
labelled engineering demonstration using caller-supplied approved features; it
does not derive schedule or history features from live telemetry. Voice is
optional and works only when backend credentials are configured. Physical
hardware-to-laptop integration has not been verified. Firmware source exists,
but it is not established that deployed devices match the laptop contract.

## Backend setup

From the repository root in PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Optional voice setup: copy `backend/.env.example` to `backend/.env` and set
`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, and optionally
`ELEVENLABS_MODEL_ID`. Keep `.env` private. Start the API in the backend
directory:

```powershell
uvicorn app.main:app --reload
```

The local service has no authentication or rate limiting. Keep it on localhost;
do not expose it publicly. The voice endpoint needs access controls before any
public deployment. Telemetry persists to `backend/data/telemetry.csv` by
default; `TELEMETRY_CSV_PATH` can select another location.

## Frontend setup

In a second terminal from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

The dashboard defaults to demo data. Select LIVE to read telemetry from
`http://127.0.0.1:8000`; set `VITE_API_BASE_URL` at frontend build/dev time to
use another local API origin. Browser audio may require pressing Play after
generation. Audio is played by the browser device, not an ESP32 speaker.

## Telemetry simulation and collection

From `backend` with its virtual environment active:

```powershell
python -m app.simulator --count 20 --interval 0.5 | python -m app.collector
```

The collector accepts stdin NDJSON or a serial port (`--serial-port COM3
--baudrate 115200`) and posts records to the local API. Serial integration
requires the base station to emit newline-delimited JSON matching schema 1.0;
that device path has not been physically verified.

## Dataset, evaluation and model artifacts

Synthetic routine histories can be generated without hardware:

```powershell
python -m app.dataset_generator --days 14 --interval-minutes 5 --seed 20261014 --scenario-rate 0.15 --start-date 2026-01-01 --timezone UTC --output data\synthetic_datasets\routine_dataset.csv
```

Evaluate the baseline and write reports/artifacts:

```powershell
python -m app.model_evaluation --input data\synthetic_datasets\routine_dataset.csv --output-dir data\model_comparison_run1 --seed 42 --validation-fraction 0.2 --test-fraction 0.2
```

The evaluator compares the decision tree with fixed logistic-regression and
random-forest baselines. It reports validation and held-out test precision,
recall, F1, confusion matrix, average precision, Brier score, and false
positives per held-out profile-day. Imputation/scaling are fitted only on
training rows. It writes versioned joblib envelopes containing target meaning,
feature names/order, data mode and estimator. For a fresh run that preserves
existing artifacts, choose a new output folder, for example
`data\model_comparison_run1`. Synthetic evaluation remains pipeline evidence
only, not real-world accuracy or grounds to enable reminders. ML reminder
targets are explicitly false by default in `app.services.reminder_policy`.

`POST /api/v1/routines/baseline` accepts a timezone and explicitly observed
per-day wake times, optional sleep-onset times, and optional activity summaries
by local hour. It does not infer sleep from movement; unsupported sleep-duration
and activity summaries are returned unavailable. Data is processed statelessly
and is not persisted by this route.

The checked-in code also includes a validation-only candidate-selection
experiment in `app.model_selection_experiment`; generated datasets, models,
and reports remain in ignored `backend/data/`. The selected model files used
by `/api/v1/predictions` are under
`data/model_evaluation/experiments/validation_selection_20261014/models/`.
The feature allowlist is the seven `feature_*` fields listed in the report;
labels, metadata, identifiers, and future data are excluded. Splits keep whole
profile-days together and use earlier days for training, then validation, then
test. Validation selects model configuration and threshold; the held-out test
set is not used for selection.

Do not use generated values as if they came from a real person. See
`backend/data/model_evaluation/report.md` and
`backend/data/model_evaluation/experiments/validation_selection_20261014/report.md`
for local measured results. They are engineering results on synthetic data,
not real-world or clinical evidence.

## API routes

- `GET /health` — local API health.
- `POST /api/v1/telemetry` — validate and append one telemetry record.
- `GET /api/v1/telemetry/latest` and `/api/v1/telemetry/history?limit=100` — stored records.
- `POST /api/v1/predictions` — score a selected target using all seven approved, precomputed features. Returns probability, model identity, missingness, and an explicit synthetic-only flag. Models do not autonomously trigger reminders.
- `POST /api/v1/features/prepare` — prepare those seven causal features from up to 500 telemetry observations plus an explicit timezone (`UTC` or a fixed offset such as `+05:30`) and expected wake time; returns unavailable features and suppresses scoring if any input, recent history, or schedule context is missing, invalid, or stale. It never computes future labels.
- `POST /api/v1/routines/baseline` — calculate conservative personal routine summaries from explicit observation history; sleep duration requires explicit sleep-onset and wake annotations.
- `POST /api/v1/reminders/evaluate` — evaluate a prediction against freshness, input-quality, enablement, and cooldown policy. All target enablement defaults to off.
- `POST /api/v1/demo/scenarios` — run the named scripted schedule-check-in rule through the same policy and cooldown service. This is an explicit simulation, not sensor data or a model result.
- `GET /api/v1/reminders/events` — retrieve recent policy decisions and speech-delivery states. The prototype event history is in memory and resets when the backend restarts.
- `POST /api/v1/voice/speech` — optional server-side ElevenLabs MP3. Pass an eligible reminder's `event_id` to associate speech generation and track browser playback. API keys stay on the backend.
- `POST /api/v1/reminders/events/{event_id}/delivery` — record browser playback states after audio generation.

## Tests

From the backend directory:

```powershell
python -m pytest
```

Tests use temporary storage and mocked voice-provider responses. No physical
hardware or paid provider request is needed.

## Hardware status

Wearable and base-station firmware sources and PlatformIO manifests are
present. Source inspection is not a hardware test. The wearable writes
diagnostic serial output. The base station emits startup/status log lines and
a nested record shaped as `{"src":"unified","wearable":{...},"base":{...},"rf":{...}}`,
which does not match the collector's flat version-1 `TelemetryRecord` contract.
The collector includes a small adapter for this exact nested shape. It filters
firmware log lines and validates mapped measurements. Because firmware supplies
uptime instead of UTC and no configured stable device ID, adapter mode requires
operator-supplied values, for example:

```powershell
python -m app.collector --serial-port COM3 --firmware-base-station --device-id station-a --boot-time-utc "2026-10-14T07:00:00Z"
```

Run that command from `backend`. The boot-time value must truly correspond to
the wearable's uptime zero and be updated after each wearable restart; the
adapter does not handle 32-bit uptime rollover. Fixture mapping and null
handling are tested; this command was not tested on a physical device. The
reliable fallback is simulator → API → dashboard.
