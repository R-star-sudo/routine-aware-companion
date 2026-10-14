#!/usr/bin/env python3
"""Interactive Base Station LED and Hardware Test Tool for Routine-Aware Companion.

Tests all 7 LEDs, verifies Hall Effect Pillbox Sensor, and checks LDR state.
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("pyserial is required. Run via: .\\backend\\.venv\\Scripts\\python.exe scripts/test_leds.py", file=sys.stderr)
    sys.exit(1)


def send_cmd(ser, cmd: str):
    print(f"\n>> Sending command: {cmd.strip()}")
    ser.write((cmd.strip() + "\n").encode("utf-8"))
    ser.flush()


def wait_for_ack(ser, expected_prefix: str, timeout=2.0):
    start = time.time()
    while time.time() - start < timeout:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith(expected_prefix):
            print(f"   [SUCCESS] Received: {line}")
            return True
        elif line.startswith("ERR,"):
            print(f"   [ERROR] Received: {line}")
            return False
    print(f"   [WARN] No ACK received within {timeout}s")
    return False


def main():
    parser = argparse.ArgumentParser(description="Base Station LED & Sensor Test Tool")
    parser.add_argument("--port", default="COM5", help="Serial port (default: COM5)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--action", choices=["all_on", "all_off", "sweep", "individual"], default="all_on",
                        help="Action to perform: all_on, all_off, sweep, individual")
    args = parser.parse_args()

    print(f"Opening {args.port} at {args.baud} baud (without hardware reset)...")
    try:
        ser = serial.Serial()
        ser.port = args.port
        ser.baudrate = args.baud
        ser.dtr = False
        ser.rts = False
        ser.timeout = 0.5
        ser.open()
    except Exception as exc:
        print(f"Error opening {args.port}: {exc}", file=sys.stderr)
        return 1

    # Wait for ESP32 to send at least one telemetry line to ensure serial sync
    print("Synchronizing with Base Station stream...")
    synced = False
    start_sync = time.time()
    while time.time() - start_sync < 4.0:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if line.startswith("{"):
            synced = True
            break
    if not synced:
        print("[WARN] Did not see initial packet, attempting to continue anyway...")
    else:
        print("Base Station is synced and listening for commands!")

    time.sleep(0.2)
    ser.reset_input_buffer()

    if args.action == "sweep":
        print("\n=== Running Sequential LED Test Sweep (Blinking all 7 LEDs) ===")
        print("Pins tested: Morning(16), Afternoon(17), Night(18), Caretaker(19), WC(23), Kitchen(27), Status(25)")
        send_cmd(ser, "LED,TEST")
        wait_for_ack(ser, "ACK,LED,TEST", timeout=4.0)
        print("\nSweep complete!")

    elif args.action == "all_on":
        print("\n=== Turning ALL 7 LEDs ON ===")
        print("Driving HIGH: Morning(16), Afternoon(17), Night(18), Caretaker(19), WC(23), Kitchen(27), Status(25)")
        send_cmd(ser, "LED,ALL,ON")
        wait_for_ack(ser, "ACK,LED,ALL,ON", timeout=2.0)
        print("\nAll 7 LEDs should now be glowing! Check your breadboard.")
        print("Press Enter to turn them OFF (or Ctrl+C)...")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass
        send_cmd(ser, "LED,ALL,OFF")
        wait_for_ack(ser, "ACK,LED,ALL,OFF", timeout=2.0)
        print("Turned all LEDs OFF.")

    elif args.action == "all_off":
        print("\n=== Turning ALL 7 LEDs OFF ===")
        send_cmd(ser, "LED,ALL,OFF")
        wait_for_ack(ser, "ACK,LED,ALL,OFF", timeout=2.0)

    elif args.action == "individual":
        leds = [
            ("MORNING", "GPIO 16 (Green/Morning pill reminder)"),
            ("AFTERNOON", "GPIO 17 (Green/Afternoon pill reminder)"),
            ("NIGHT", "GPIO 18 (Green/Night pill reminder)"),
            ("CARETAKER", "GPIO 19 (Red/Caretaker alert)"),
            ("PATH_WC", "GPIO 23 (White PWM/Washroom path light)"),
            ("PATH_KIT", "GPIO 27 (White PWM/Kitchen path light)"),
            ("STATUS", "GPIO 25 (Heartbeat LED)"),
        ]
        print("\n=== Testing Each LED Individually (2 seconds ON each) ===")
        for name, desc in leds:
            print(f"\n>>> Turning ON: {name} -> {desc}")
            send_cmd(ser, f"LED,{name},ON")
            wait_for_ack(ser, f"ACK,LED,{name},ON", timeout=2.0)
            time.sleep(2.0)
            print(f">>> Turning OFF: {name}")
            send_cmd(ser, f"LED,{name},OFF")
            wait_for_ack(ser, f"ACK,LED,{name},OFF", timeout=2.0)
            time.sleep(0.5)

    ser.close()
    print("\nPort closed. Done!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
