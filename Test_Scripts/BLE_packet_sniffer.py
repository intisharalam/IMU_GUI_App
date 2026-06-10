"""
imu_monitor.py
--------------
Connects to IMU_WRIST and prints quaternion data once per second
(every 50th packet at 50 Hz).

Usage
-----
    python imu_monitor.py
    python imu_monitor.py --device IMU_ARM
"""

import asyncio
import argparse
import struct
import sys

from bleak import BleakClient, BleakScanner

UART_TX_UUID   = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
SCAN_TIMEOUT   = 15.0
DEFAULT_DEVICE = "IMU_WRIST"
PRINT_EVERY    = 50


def handler(count, _sender, raw: bytearray):
    if len(raw) != 20:
        return
    w, x, y, z, ms = struct.unpack_from("<ffffI", raw)
    count[0] += 1
    if count[0] % PRINT_EVERY == 0:
        print(f"[{count[0]:>6} | {ms/1000:>8.1f}s]  "
              f"w={w:>7.4f}  x={x:>7.4f}  y={y:>7.4f}  z={z:>7.4f}")


async def main(device_name: str):
    print(f"[SCAN] Looking for '{device_name}'...")
    device = await BleakScanner.find_device_by_name(device_name, timeout=SCAN_TIMEOUT)
    if device is None:
        print(f"[ERROR] '{device_name}' not found.")
        sys.exit(1)

    print(f"[BLE]  Found at {device.address}. Connecting...")

    count = [0]   # list so the closure can mutate it

    async with BleakClient(device) as client:
        conn = client  # bleak handles connection parameter negotiation via the OS
        print(f"[BLE]  Connected. Printing every {PRINT_EVERY} packets (≈1s).\n")
        print(f"  {'Packet':>6}   {'Uptime':>8}   {'w':>7}  {'x':>7}  {'y':>7}  {'z':>7}")
        print("  " + "─" * 58)

        await client.start_notify(
            UART_TX_UUID,
            lambda s, r: handler(count, s, r)
        )

        while client.is_connected:
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IMU quaternion monitor")
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    args = parser.parse_args()

    try:
        asyncio.run(main(args.device))
    except KeyboardInterrupt:
        print("\n[DONE]")