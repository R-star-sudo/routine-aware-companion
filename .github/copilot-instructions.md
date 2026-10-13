# Routine-Aware Companion: Project Guidelines

## Technology

- Use Python and FastAPI for the backend.
- Use React, TypeScript, and Vite for the frontend.
- Start with SQLite while keeping the database design adaptable to PostgreSQL.
- Hardware will use ESP32 devices and ESP-NOW communication.
- Keep critical local functions, such as pathway lighting and fallback reminders, functional without internet access.

## Safety and data quality

- Never generate medical diagnoses or present sensor readings as confirmed medical facts. Describe changes as deviations from the individual's usual baseline.
- Do not describe skin temperature as core body temperature.
- Medication-container opening does not prove that medication was taken.
- Clearly distinguish observed data, predictions, and confirmed actions.
- Validate incoming sensor data, handle missing or poor-quality readings, log important events, and use safe fallbacks.

## Engineering practices

- Use type hints, small modules, clear error handling, and automated tests.
- Never commit API keys, passwords, tokens, personal health data, or other secrets.
- Make incremental changes and do not modify unrelated files.
- Explain changed files and provide commands to test the changes.
