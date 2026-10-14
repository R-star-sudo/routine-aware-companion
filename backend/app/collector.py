import argparse
import json
import sys
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TextIO

import httpx
from app.services.firmware_adapter import FirmwareAdapterError, adapt_firmware_line


def post_lines(
    lines: Iterable[str],
    client: httpx.Client,
    api_url: str,
    error_stream: TextIO = sys.stderr,
    transform: Callable[[str], dict[str, object] | None] | None = None,
) -> int:
    errors = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if transform is not None:
            try:
                record = transform(line)
            except (FirmwareAdapterError, ValueError) as exc:
                print(f"Line {line_number}: firmware record rejected: {exc}", file=error_stream)
                errors += 1
                continue
            if record is None:
                continue
            try:
                response = client.post(api_url, json=record)
                if not response.is_success:
                    print(f"Line {line_number}: API returned {response.status_code}: {response.text}", file=error_stream)
                    errors += 1
            except httpx.RequestError as exc:
                print(f"Line {line_number}: API request failed: {exc}", file=error_stream)
                errors += 1
            continue
        try:
            record = json.loads(line, parse_constant=_reject_non_finite)
            if not isinstance(record, dict):
                raise ValueError("each JSON line must contain an object")
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"Line {line_number}: invalid JSON record: {exc}", file=error_stream)
            errors += 1
            continue

        try:
            response = client.post(api_url, json=record)
            if not response.is_success:
                print(
                    f"Line {line_number}: API returned {response.status_code}: "
                    f"{response.text}",
                    file=error_stream,
                )
                errors += 1
        except httpx.RequestError as exc:
            print(f"Line {line_number}: API request failed: {exc}", file=error_stream)
            errors += 1
    return errors


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"{value} is not a valid JSON number")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post newline-delimited telemetry to the local API."
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000/api/v1/telemetry",
        help="Telemetry ingestion endpoint",
    )
    parser.add_argument("--serial-port", help="Optional serial port, for example COM3")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--firmware-base-station", action="store_true",
        help="Adapt the current nested base-station JSON and ignore its diagnostic log lines.",
    )
    parser.add_argument("--device-id", help="Configured stable ID assigned to this base station.")
    parser.add_argument(
        "--boot-time-utc",
        help="Timezone-aware ISO-8601 UTC instant corresponding to wearable uptime zero.",
    )
    args = parser.parse_args()
    if args.baudrate < 1:
        parser.error("--baudrate must be positive")
    transform = None
    if args.firmware_base_station:
        if not args.device_id or not args.boot_time_utc:
            parser.error("--firmware-base-station requires --device-id and --boot-time-utc")
        try:
            boot_time = datetime.fromisoformat(args.boot_time_utc.replace("Z", "+00:00"))
            if boot_time.tzinfo is None or boot_time.utcoffset() is None:
                raise ValueError("timezone is required")
        except ValueError as exc:
            parser.error(f"--boot-time-utc must be timezone-aware ISO-8601: {exc}")
        transform = lambda line: adapt_firmware_line(
            line, device_id=args.device_id, boot_time_utc=boot_time
        )

    try:
        with httpx.Client(timeout=10.0) as client:
            if args.serial_port:
                import serial

                with serial.Serial(
                    args.serial_port, args.baudrate, timeout=1
                ) as serial_port:
                    errors = post_lines(
                        (line.decode("utf-8", errors="replace") for line in serial_port),
                        client,
                        args.api_url,
                        transform=transform,
                    )
            else:
                errors = post_lines(sys.stdin, client, args.api_url)
    except (httpx.RequestError, OSError) as exc:
        print(f"Collector connection failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Collector stopped.", file=sys.stderr)
        return 130
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
