import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

from app.schemas.telemetry import TelemetryRecord, SensorValidity


def generate_records(
    count: int,
    interval_seconds: float,
    *,
    start_time: datetime | None = None,
    seed: int | None = None,
    device_id: str = "simulated-wearable-01",
) -> Iterator[TelemetryRecord]:
    if count < 1:
        raise ValueError("count must be at least 1")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")

    rng = random.Random(seed)
    first_timestamp = start_time or datetime.now(timezone.utc)
    if first_timestamp.tzinfo is None or first_timestamp.utcoffset() is None:
        raise ValueError("start_time must be timezone-aware")
    first_timestamp = first_timestamp.astimezone(timezone.utc)

    for sequence in range(count):
        mpu_valid = sequence % 50 != 49 and rng.random() > 0.01
        max_valid = sequence % 30 != 29 and rng.random() > 0.02
        ds_valid = sequence % 40 != 39 and rng.random() > 0.02
        pulse_rate = (
            round(min(115, max(48, rng.gauss(74, 9))), 1)
            if max_valid and rng.random() > 0.08
            else None
        )
        temperature = round(min(37.5, max(29, rng.gauss(33.5, 1))), 2) if ds_valid else None

        yield TelemetryRecord(
            schema_version="1.0",
            source="simulated",
            device_id=device_id,
            sequence=sequence,
            timestamp=first_timestamp + timedelta(seconds=sequence * interval_seconds),
            acceleration_x_m_s2=round(rng.gauss(0, 0.25), 3) if mpu_valid else None,
            acceleration_y_m_s2=round(rng.gauss(0, 0.25), 3) if mpu_valid else None,
            acceleration_z_m_s2=round(rng.gauss(9.81, 0.15), 3) if mpu_valid else None,
            angular_velocity_x_rad_s=round(rng.gauss(0, 0.05), 4) if mpu_valid else None,
            angular_velocity_y_rad_s=round(rng.gauss(0, 0.05), 4) if mpu_valid else None,
            angular_velocity_z_rad_s=round(rng.gauss(0, 0.05), 4) if mpu_valid else None,
            pulse_rate_bpm=pulse_rate,
            skin_temperature_c=temperature,
            room_temperature_c=None,
            room_humidity_percent=None,
            pillbox_open=None,
            sensor_validity=SensorValidity(
                mpu6050=mpu_valid,
                max30102=max_valid,
                ds18b20=ds_valid,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit simulated telemetry as NDJSON.")
    parser.add_argument("--count", type=int, default=20, help="Number of records")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="Seconds between records"
    )
    parser.add_argument("--device-id", default="simulated-wearable-01")
    parser.add_argument("--seed", type=int, help="Optional random seed")
    args = parser.parse_args()
    if args.count < 1 or args.interval <= 0:
        parser.error("--count must be positive and --interval must be greater than zero")

    try:
        for index, record in enumerate(
            generate_records(
                args.count, args.interval, seed=args.seed, device_id=args.device_id
            )
        ):
            print(record.model_dump_json())
            if index + 1 < args.count:
                time.sleep(args.interval)
    except (BrokenPipeError, OSError) as exc:
        print(f"Simulator output error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
