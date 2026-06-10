"""
ble_reconnect_test.py
---------------------
Automatically measures BLE reconnection time by disconnecting the sensor
from the host side and timing how long until the connection is re-established.

No firmware changes required. Uses bleak's client.disconnect() to trigger
the same link-loss path as physical range dropout.

Method
------
For each trial:
  1. Connect to the sensor
  2. Record t0, call client.disconnect()
  3. Scan for the device and reconnect
  4. Record t1 when connected
  5. reconnect_time = t1 - t0

Repeats N trials (default 5) and prints a summary table.

Usage
-----
    python ble_reconnect_test.py
    python ble_reconnect_test.py --device IMU_ARM
    python ble_reconnect_test.py --trials 10
"""

import asyncio
import argparse
import statistics
import sys
import time

from bleak import BleakClient, BleakScanner

UART_TX_UUID   = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
SCAN_TIMEOUT   = 15.0
DEFAULT_DEVICE = "IMU_WRIST"
DEFAULT_TRIALS = 25
# Short settle delay after disconnect before scanning —
# gives the nRF52840 time to restart advertising (typically <100ms)
POST_DISCONNECT_SETTLE_S = 0.2


def print_separator(char="─", width=60):
    print(char * width)


async def connect(device_name: str) -> tuple:
    """Scan for device and return (BleakClient, address). Raises on timeout."""
    device = await BleakScanner.find_device_by_name(
        device_name, timeout=SCAN_TIMEOUT
    )
    if device is None:
        raise TimeoutError(f"'{device_name}' not found within {SCAN_TIMEOUT:.0f}s")
    client = BleakClient(device, timeout=10.0)
    await client.connect()
    return client, device.address


async def run_trials(device_name: str, n_trials: int) -> list:
    """
    Run n_trials reconnection measurements.
    Returns list of reconnection times in seconds.
    """
    times = []

    print(f"\n[SCAN] Initial scan for '{device_name}'...")
    client, address = await connect(device_name)
    print(f"[BLE]  Connected at {address}\n")

    for i in range(1, n_trials + 1):
        if not client.is_connected:
            # Reconnect if a previous trial left us disconnected
            print(f"  [T{i}] Reconnecting before trial...")
            client, _ = await connect(device_name)

        # ── Disconnect ────────────────────────────────────────────────────────
        t0 = time.perf_counter()
        await client.disconnect()
        print(f"  [T{i}] Disconnected. Waiting {POST_DISCONNECT_SETTLE_S*1000:.0f}ms "
              f"for sensor to restart advertising...")
        await asyncio.sleep(POST_DISCONNECT_SETTLE_S)

        # ── Scan and reconnect ────────────────────────────────────────────────
        print(f"  [T{i}] Scanning...")
        try:
            client, _ = await connect(device_name)
        except TimeoutError as e:
            print(f"  [T{i}] FAILED — {e}")
            times.append(None)
            continue

        t1 = time.perf_counter()
        elapsed = t1 - t0
        times.append(elapsed)
        print(f"  [T{i}] Reconnected in {elapsed:.2f}s")

        # Brief pause between trials so the connection stabilises
        await asyncio.sleep(1.0)

    # Clean disconnect at end
    if client.is_connected:
        await client.disconnect()

    return times


async def main(device_name: str, n_trials: int):
    print_separator("═", 64)
    print("  BLE RECONNECTION TIME TEST")
    print(f"  Device  : {device_name}")
    print(f"  Trials  : {n_trials}")
    print_separator("═", 64)

    try:
        times = await run_trials(device_name, n_trials)
    except TimeoutError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

    # ── Results table ─────────────────────────────────────────────────────────
    valid = [t for t in times if t is not None]
    failed = times.count(None)

    print()
    print_separator("═", 64)
    print("  RECONNECTION TIME RESULTS")
    print_separator("─", 64)

    # Per-trial row
    header = "  " + "  ".join(f"T{i+1} (s)" .rjust(8) for i in range(n_trials))
    print(header)
    print_separator("─", 64)

    row = "  " + "  ".join(
        f"{t:>8.2f}" if t is not None else f"{'FAIL':>8}"
        for t in times
    )
    print(row)
    print_separator("─", 64)

    if valid:
        mean = statistics.mean(valid)
        mn   = min(valid)
        mx   = max(valid)
        std  = statistics.stdev(valid) if len(valid) > 1 else 0.0
        print(f"  Mean : {mean:.2f}s   Min : {mn:.2f}s   "
              f"Max : {mx:.2f}s   Stdev : {std:.2f}s   "
              f"Failed : {failed}/{n_trials}")
    else:
        print("  No successful trials.")

    print_separator("═", 64)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Automated BLE reconnection time test"
    )
    parser.add_argument("--device",  default=DEFAULT_DEVICE,
                        help=f"BLE device name (default: {DEFAULT_DEVICE})")
    parser.add_argument("--trials",  type=int, default=DEFAULT_TRIALS,
                        help=f"Number of trials (default: {DEFAULT_TRIALS})")
    args = parser.parse_args()

    try:
        asyncio.run(main(args.device, args.trials))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED]")