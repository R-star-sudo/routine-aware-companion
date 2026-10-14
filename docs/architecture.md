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
