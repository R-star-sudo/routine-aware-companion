# routine-aware-companion

An assistive system in planning for elderly people that will learn individual daily routines and provide proactive reminders and caregiver alerts.

## Project status

The project is being developed in modular phases. **Hardware firmware for both ESP32 nodes is now implemented:**

- **Wearable Firmware (`firmware/wearable/`)**: ESP32 firmware collecting 6-axis IMU (MPU6050), pulse oximetry / heart rate (MAX30102), and peripheral skin temperature (DS18B20). Features live sensor health and staleness tracking, broadcasting validated, packed telemetry packets via ESP-NOW at 10 Hz (100ms interval) on Wi-Fi Channel 1.
- **Base Station Firmware (`firmware/base_station/`)**: ESP32 firmware receiving ESP-NOW telemetry via a FreeRTOS queue, detecting sequence gaps and node reboots, reading local ambient conditions (DHT11) and pillbox state (Hall effect sensor), and outputting unified JSON telemetry over USB Serial (115200 baud).

The laptop serial ingestion pipeline, backend service, database integration, frontend dashboard, and predictive routine services are planned for subsequent steps.

See [docs/architecture.md](docs/architecture.md) for full component boundaries, data protocols, and architectural status.

## Firmware Setup & Build

Both firmware projects are managed with PlatformIO and pinned to `espressif32@6.5.0` (Arduino-ESP32 2.0.14) for stable ESP-NOW API support:

```bash
# Build Wearable firmware
pio run -d firmware/wearable

# Build Base Station firmware
pio run -d firmware/base_station
```
