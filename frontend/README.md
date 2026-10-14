# Routine-Aware Companion frontend

This React and Vite dashboard is self-contained under `frontend/`.

## Run

```powershell
cd frontend
npm install
npm run dev
```

The dashboard opens in clearly labelled **DEMO** mode. Demo readings and
forecasts are illustrative and are never presented as live hardware data.
Choose **Settings → Local CSV import** to view timestamped observations
stored in this browser.

## Live API mode

Live mode requests `GET /api/v1/telemetry/history?limit=500` from FastAPI. By
default, the API base URL is `http://127.0.0.1:8000`. If it is elsewhere, set
`VITE_API_BASE_URL` in `frontend/.env.local` to its origin. FastAPI allows
browser requests from localhost development origins.

The frontend validates telemetry schema version 1.0 and maps its fields to
observation values. Records are sorted chronologically and the dashboard opens
on the latest observed date. Live mode contains no forecasts or action events
until those services are implemented; it shows an explicit error when the
service is missing or unavailable and never falls back to demo data.

Settings CSV import supports the backend telemetry export, including
`pulse_rate_bpm`, `skin_temperature_c`, `room_temperature_c`,
`room_humidity_percent`, acceleration axes, `pillbox_open`, and the serialized
`sensor_validity` object. It also accepts the shorter observation column names
listed in the Settings page.

Sensor records are observations, not diagnoses. A pillbox state change does
not confirm medication was taken. Demo action controls only add a labelled
local event and do not control hardware or generate/play audio.
