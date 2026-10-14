# Architecture

## Status

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

## Safety and reliability boundaries

Sensor readings are observations, not confirmed medical facts. Changes should be described relative to the individual's usual baseline. Skin temperature is not core body temperature, and opening a medication container does not establish that medication was taken. Important events should be logged, and safe local behavior should not depend on internet availability.

## Current repository layout

- `firmware/wearable/`: PlatformIO project for wearable telemetry transmitter.
- `firmware/base_station/`: PlatformIO project for base station receiver and USB serial bridge.
- `backend/`: Placeholder structure for upcoming FastAPI services and routine models.
- `frontend/`: Placeholder structure for upcoming React/Vite dashboard.
- `docs/`: System documentation and architecture specifications.

