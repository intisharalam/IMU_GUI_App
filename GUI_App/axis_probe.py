"""
axis_probe.py
-------------
Axis mapping diagnostic tool.

Tells you exactly what to do physically, then prints what each sensor
axis is doing in real time. You report back what you observe and we
use that to fix the mounting rotations.

Run with:
    python axis_probe.py

All three sensors must be connected (BLE). No calibration needed.
"""

import asyncio
import time
import struct
import threading

from bleak import BleakClient, BleakScanner
from scipy.spatial.transform import Rotation
import numpy as np

UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
DEVICE_NAMES = {"wrist": "IMU_WRIST", "arm": "IMU_ARM", "chest": "IMU_CHEST"}

latest    = {"wrist": (1.,0.,0.,0.), "arm": (1.,0.,0.,0.), "chest": (1.,0.,0.,0.)}
connected = {"wrist": False, "arm": False, "chest": False}
lock = threading.Lock()


# ── BLE ───────────────────────────────────────────────────────────────────────

def start_ble():
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    for name, device_name in DEVICE_NAMES.items():
        asyncio.run_coroutine_threadsafe(_connect(name, device_name), loop)

async def _connect(slot, device_name):
    while True:
        try:
            device = await BleakScanner.find_device_by_name(device_name, timeout=15)
            if device is None:
                await asyncio.sleep(3)
                continue
            async with BleakClient(device, timeout=10) as client:
                with lock:
                    connected[slot] = True
                print(f"  [BLE] {slot} connected")
                await client.start_notify(UART_TX_UUID, _make_handler(slot))
                while client.is_connected:
                    await asyncio.sleep(0.2)
        except Exception:
            pass
        with lock:
            connected[slot] = False
        await asyncio.sleep(3)

def _make_handler(slot):
    def handler(_, raw):
        if len(raw) == 16:
            w, x, y, z = struct.unpack_from("<ffff", raw)
            with lock:
                latest[slot] = (w, x, y, z)
    return handler


# ── Helpers ───────────────────────────────────────────────────────────────────

def wait_for_sensors():
    print("\nWaiting for all 3 sensors to connect", end="", flush=True)
    while True:
        with lock:
            if all(connected.values()):
                break
        print(".", end="", flush=True)
        time.sleep(0.5)
    print(" done.\n")

def read_euler(slot):
    """Returns current Euler angles (deg) as a numpy array [x, y, z]."""
    with lock:
        q = latest[slot]
    r = Rotation.from_quat([q[1], q[2], q[3], q[0]])
    return r.as_euler("xyz", degrees=True)

def measure_axis(slot, duration=3.0):
    """
    Snapshots the baseline the instant this function is called,
    then records deltas from that baseline for `duration` seconds.
    Returns (axis_label, sign, delta_ranges).

    Using deltas means any position the sensor was in when the user
    pressed Enter is treated as zero — only the movement matters.
    """
    # Snapshot baseline immediately
    baseline = read_euler(slot)

    deltas = []
    t_end = time.monotonic() + duration
    while time.monotonic() < t_end:
        delta = read_euler(slot) - baseline
        deltas.append(delta)
        time.sleep(0.05)

    arr    = np.array(deltas)                      # shape (N, 3)
    ranges = arr.max(axis=0) - arr.min(axis=0)     # peak-to-peak per axis
    idx    = int(np.argmax(ranges))                # axis that moved most
    # Sign based on where the delta ended up vs baseline
    sign   = "+" if arr[-1, idx] > 0 else "-"
    return ["X", "Y", "Z"][idx], sign, ranges

def separator():
    print("\n" + "─" * 60)

def do_step(slot, instruction):
    """
    Runs one measurement step with a retry loop.
    Baselines exactly at the moment the user presses Enter.
    Returns (axis, sign, ranges) once the user is happy.
    """
    while True:
        input(
            f"\n  >>> {instruction}\n"
            f"      Hold your START position still, then press ENTER.\n"
            f"      Begin moving AFTER you press Enter."
        )

        # Baseline is snapshotted inside measure_axis, right at call time.
        # Small sleep so the Enter keypress vibration settles.
        time.sleep(0.15)

        print("  Recording", end="", flush=True)
        # Print a dot every 0.5 s so user sees progress
        result_holder = [None]

        def _measure():
            result_holder[0] = measure_axis(slot, duration=3.0)

        t = threading.Thread(target=_measure)
        t.start()
        while t.is_alive():
            time.sleep(0.5)
            print(".", end="", flush=True)
        t.join()

        ax, sign, ranges = result_holder[0]
        print(
            f"\n  Result: strongest change → sensor {ax} axis  ({sign}change)\n"
            f"          [X:{ranges[0]:.1f}°  Y:{ranges[1]:.1f}°  Z:{ranges[2]:.1f}°]"
        )

        answer = input("\n  Happy with that? [y = accept, r = redo]:  ").strip().lower()
        if answer == "r":
            print("  Retrying step...")
            continue
        return ax, sign, ranges


# ── Main probe ────────────────────────────────────────────────────────────────

def run_probe():
    print("\n" + "=" * 60)
    print("  AXIS MAPPING DIAGNOSTIC")
    print("=" * 60)
    print("""
  We will test each sensor one at a time — 3 movements each.

  For each step:
    1. Read the instruction.
    2. Get into the START position and hold STILL.
    3. Press ENTER — the baseline is captured at that exact moment.
    4. Begin the movement immediately after pressing Enter.
    5. Hold the end position until the dots stop.
    6. Type 'r' to redo, 'y' to accept and move on.

  Keep all OTHER sensors as still as possible during each test.
    """)

    results = {}

    for slot in ["chest", "arm", "wrist"]:
        separator()
        print(f"  SENSOR: {slot.upper()}")
        separator()
        results[slot] = {}

        results[slot]["tilt_forward"] = do_step(
            slot,
            f"[{slot.upper()}]  Stand upright (I-pose). "
            f"Tilt the {slot} sensor FORWARD "
            f"(tip the top of the sensor toward your front) ~45°. "
            f"Hold that end position until the dots stop."
        )

        results[slot]["tilt_right"] = do_step(
            slot,
            f"[{slot.upper()}]  Return to upright. "
            f"Tilt the {slot} sensor to the RIGHT "
            f"(right edge tips downward) ~45°. "
            f"Hold that end position until the dots stop."
        )

        results[slot]["spin_cw"] = do_step(
            slot,
            f"[{slot.upper()}]  Return to upright. "
            f"SPIN the {slot} sensor CLOCKWISE "
            f"(rotate like a steering wheel, keeping it flat) ~45°. "
            f"Hold that end position until the dots stop."
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    separator()
    print("\n  RESULTS SUMMARY")
    print("  (copy and paste this back to Claude)\n")
    separator()
    print(f"  {'SENSOR':<8}  {'FORWARD TILT':<20}  {'RIGHT TILT':<20}  {'CW SPIN'}")
    for slot in ["chest", "arm", "wrist"]:
        r = results[slot]
        fwd   = f"{r['tilt_forward'][0]} ({r['tilt_forward'][1]})"
        right = f"{r['tilt_right'][0]} ({r['tilt_right'][1]})"
        spin  = f"{r['spin_cw'][0]} ({r['spin_cw'][1]})"
        print(f"  {slot.upper():<8}  {fwd:<20}  {right:<20}  {spin}")
    separator()
    print()


if __name__ == "__main__":
    print("\nStarting BLE scan...")
    start_ble()
    wait_for_sensors()
    run_probe()