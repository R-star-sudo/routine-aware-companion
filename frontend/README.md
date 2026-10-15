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
not confirm medication was taken. The LED demo action records intent only and
does not control hardware.

## ElevenLabs voice prompts

The Predictions & Events page includes a **Voice companion** panel. Enter a
short reminder and select **Generate & play**. The frontend sends the text to
the FastAPI server, then plays the returned MP3 through the browser device's
selected audio output. If browser autoplay is blocked, use the audio player's
Play control.

Configure the backend from the repository root:

1. Copy `backend/.env.example` to `backend/.env`.
2. Set `ELEVENLABS_API_KEY` to your ElevenLabs API key and
   `ELEVENLABS_VOICE_ID` to the ID of the voice selected in your ElevenLabs
   account. The default model is `eleven_multilingual_v2`.
3. From PowerShell, install dependencies and start FastAPI:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   uvicorn app.main:app --reload
   ```

4. In another terminal, start the dashboard using the frontend instructions
   above and open **Predictions & Events → Voice companion**.

The ElevenLabs key stays on the backend and is never bundled into Vite. Keep
`backend/.env` out of Git and never place the key in a `VITE_*` variable.
The server limits reminder text to 1,200 characters and returns generated MP3
data without caching it. Before exposing this endpoint to a public network,
add authentication and rate limiting to prevent unauthorized usage.

**Hardware note:** this first integration plays on the device running the
browser; it does not yet stream audio to the ESP32 base station. A physical
ESP32 speaker needs an audio output stage (for example, an I2S amplifier and
speaker) and a separate firmware/backend command path. The existing base
station telemetry serial stream is not an audio transport.
