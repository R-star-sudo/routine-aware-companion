import argparse
import json
import sys
from collections.abc import Iterable
from typing import TextIO

import httpx


def post_lines(
    lines: Iterable[str],
    client: httpx.Client,
    api_url: str,
    error_stream: TextIO = sys.stderr,
) -> int:
    errors = 0
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
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
    args = parser.parse_args()
    if args.baudrate < 1:
        parser.error("--baudrate must be positive")

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
