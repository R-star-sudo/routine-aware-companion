# Architecture

## Status

This document describes the intended architecture. At this foundation stage,
the components below are **planned**; no application services, user interface,
device firmware, database schema, or predictive services are implemented.

## Planned components

- **Backend (`backend/`)**: A Python and FastAPI service. Its API layer will
  expose application operations, schemas will define and validate data
  contracts, services will hold application logic, core will contain shared
  configuration and cross-cutting concerns, and db will manage persistence.
- **Frontend (`frontend/`)**: A React, TypeScript, and Vite application for
  older adults and caregivers to interact with the system, review information,
  and manage settings.
- **Wearable (`firmware/wearable/`)**: ESP32-based wearable firmware intended
  to collect supported sensor observations and communicate them to the base
  station using ESP-NOW.
- **Base station (`firmware/base_station/`)**: ESP32-based local coordination
  firmware intended to receive wearable messages and support local actions.
  Critical functions, including pathway lighting and fallback reminders, must
  remain available without internet access.
- **Database (`backend/app/db/`)**: Persistence will start with SQLite. Data
  access and schema choices should allow a future move to PostgreSQL without
  coupling application logic unnecessarily to SQLite.
- **Predictive services (`backend/app/services/`, planned)**: Services may
  learn an individual's usual routine and identify deviations for reminder
  and caregiver workflows. They must distinguish observations from
  predictions and confirmed actions, account for missing or poor-quality
  readings, and avoid medical diagnoses or claims that sensor data proves a
  medical condition or action.

## Safety and reliability boundaries

Sensor readings are observations, not confirmed medical facts. Changes should
be described relative to the individual's usual baseline. Skin temperature is
not core body temperature, and opening a medication container does not
establish that medication was taken. Important events should be logged, and
safe local behavior should not depend on internet availability.

## Current repository layout

The directories provide starting points for these responsibilities. They are
placeholders only; implementation and dependency setup are intentionally out
of scope for this foundation stage.
