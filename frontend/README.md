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

## Live API integration point

Create `frontend/.env.local` using `.env.example` as a guide and set
`VITE_API_BASE_URL` to the laptop dashboard API origin. The frontend requests
`GET /api/dashboard`, expecting JSON with `observations`, `forecasts`, and
`events` arrays. Observation records use `timestamp` and optional
`heartRate`, `heartRateValid`, `sensorTemperature`, `ambientTemperature`,
`humidity`, `movement`, `pillboxState`, and `routineState` fields. Forecasts
use `targetDate`, `generatedAt`, `status`, and optional forecast-time,
probability, `wakeConfidence`, and medication-window fields. The semicircular
prediction meter displays the model-supplied `wakeConfidence` value (0–1) as a
percentage; the demo forecast currently supplies an illustrative 74% score.
The frontend does not calculate a score or estimate model accuracy. Events use
`id`, `timestamp`,
`type`, `description`, `status`, and `device`. Live mode validates this
contract, shows an explicit error when the service is missing or unavailable,
and does not fall back to demonstration data. The API/backend and USB serial
receiver are not implemented by this frontend.

Sensor records are observations, not diagnoses. A pillbox state change does
not confirm medication was taken. Demo action controls only add a labelled
local event and do not control hardware or generate/play audio.
