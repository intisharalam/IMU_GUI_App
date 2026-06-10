"""
T10 — Yaw Drift Test (1 hour, all three sensors)
==================================================
Sensors sit completely still for 1 hour.
One reading per minute per sensor (mean of all packets in that minute).
Drift = change in each Euler angle from minute-1 baseline.
At the end: prints a drift table and saves CSV + report.

Run
---
    cd IMU_GUI_App
    python Test_Scripts/T10_Yaw_Drift.py [--duration 60]
"""

import asyncio
import argparse
import csv
import math
import struct
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

# ── Config ─────────────────────────────────────────────────────────────────────
UART_TX_UUID     = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
DEVICE_NAMES     = ["IMU_WRIST", "IMU_ARM", "IMU_CHEST"]
SCAN_TIMEOUT_S   = 15.0
RECONNECT_WAIT_S = 5.0
LOG_INTERVAL_S   = 60
OUT_DIR          = Path(__file__).parent


# ── Maths ──────────────────────────────────────────────────────────────────────

def quat_to_euler(w, x, y, z):
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr, cosr))

    sinp = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.degrees(math.asin(sinp))

    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    yaw  = math.degrees(math.atan2(siny, cosy))

    return roll, pitch, yaw


def angle_diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def mean(values):
    return sum(values) / len(values) if values else None


# ── Per-sensor buffer ──────────────────────────────────────────────────────────

class Sensor:
    def __init__(self, name):
        self.name      = name
        self.lock      = threading.Lock()
        self.connected = False
        self.buf       = []   # (roll, pitch, yaw) tuples for current minute

    def ingest(self, w, x, y, z):
        r, p, y = quat_to_euler(w, x, y, z)
        with self.lock:
            self.buf.append((r, p, y))

    def snapshot(self):
        """Return mean (roll, pitch, yaw) for this minute and clear buffer."""
        with self.lock:
            b = list(self.buf)
            self.buf.clear()
        if not b:
            return None, None, None
        return (mean([x[0] for x in b]),
                mean([x[1] for x in b]),
                mean([x[2] for x in b]))


# ── BLE ────────────────────────────────────────────────────────────────────────

async def connect_loop(name, sensor, stop_event):
    while not stop_event.is_set():
        try:
            device = await BleakScanner.find_device_by_name(
                name, timeout=SCAN_TIMEOUT_S)
        except Exception:
            await asyncio.sleep(RECONNECT_WAIT_S)
            continue

        if device is None:
            await asyncio.sleep(RECONNECT_WAIT_S)
            continue

        try:
            async with BleakClient(device, timeout=10.0) as client:
                try:
                    await client.request_mtu(247)
                except Exception:
                    pass

                sensor.connected = True

                def handler(_, raw):
                    if len(raw) == 16:
                        w, x, y, z = struct.unpack_from("<ffff", raw)
                        sensor.ingest(w, x, y, z)

                await client.start_notify(UART_TX_UUID, handler)
                while client.is_connected and not stop_event.is_set():
                    await asyncio.sleep(0.3)
        except Exception:
            pass
        finally:
            sensor.connected = False
            await asyncio.sleep(RECONNECT_WAIT_S)


def start_ble(sensors, stop_event):
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for name, sensor in sensors.items():
            loop.create_task(connect_loop(name, sensor, stop_event))
        loop.run_forever()
    threading.Thread(target=run, daemon=True).start()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=60)
    args = parser.parse_args()

    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path    = OUT_DIR / f"T10_drift_{ts}.csv"
    report_path = OUT_DIR / f"T10_drift_{ts}_report.txt"

    sensors    = {name: Sensor(name) for name in DEVICE_NAMES}
    stop_event = threading.Event()
    start_ble(sensors, stop_event)

    # Wait for all to connect
    print("Waiting for all three sensors...")
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if all(s.connected for s in sensors.values()):
            break
        time.sleep(1.0)
    else:
        print("ERROR: could not connect to all sensors.")
        sys.exit(1)

    print(f"All connected. Running {args.duration}-minute drift test.")
    print("Keep sensors completely still. No output until finished.\n")

    # {sensor_name: [(minute, roll, pitch, yaw), ...]}
    log = {name: [] for name in DEVICE_NAMES}
    # baseline = first minute reading
    baseline = {name: None for name in DEVICE_NAMES}

    t_start   = time.monotonic()
    t_next    = t_start + LOG_INTERVAL_S
    minute    = 0
    target    = t_start + args.duration * 60.0

    while time.monotonic() < target:
        # Sleep until next minute mark — no printing, no blocking
        now  = time.monotonic()
        wait = t_next - now
        if wait > 0:
            time.sleep(wait)

        minute += 1
        for name, sensor in sensors.items():
            r, p, y = sensor.snapshot()
            if r is None:
                continue
            if baseline[name] is None:
                baseline[name] = (r, p, y)
            log[name].append((minute, r, p, y))

        elapsed = int(time.monotonic() - t_start)
        remain  = int(target - time.monotonic())
        print(f"  Minute {minute:3d}  {elapsed//60:02d}:{elapsed%60:02d} elapsed  "
              f"{remain//60:02d}:{remain%60:02d} remaining  "
              + "  ".join(
                  f"{n.split('_')[1]}: "
                  f"{'OK' if sensors[n].connected else 'LOST'}"
                  for n in DEVICE_NAMES))

        t_next += LOG_INTERVAL_S

    stop_event.set()

    # ── Build drift table ──────────────────────────────────────────────────────
    # rows: minute, sensor, roll, pitch, yaw, drift_roll, drift_pitch, drift_yaw
    rows = []
    for name in DEVICE_NAMES:
        b = baseline[name]
        for (min_n, r, p, y) in log[name]:
            if b is None:
                dr, dp, dy = None, None, None
            else:
                dr = angle_diff(r, b[0])
                dp = angle_diff(p, b[1])
                dy = angle_diff(y, b[2])
            rows.append({
                "minute":      min_n,
                "sensor":      name,
                "roll":        round(r,  4),
                "pitch":       round(p,  4),
                "yaw":         round(y,  4),
                "drift_roll":  round(dr, 4) if dr is not None else "",
                "drift_pitch": round(dp, 4) if dp is not None else "",
                "drift_yaw":   round(dy, 4) if dy is not None else "",
            })

    # ── CSV ────────────────────────────────────────────────────────────────────
    fields = ["minute", "sensor", "roll", "pitch", "yaw",
              "drift_roll", "drift_pitch", "drift_yaw"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ── Report ─────────────────────────────────────────────────────────────────
    L = []
    L.append("=" * 70)
    L.append("T10 — Yaw (and full Euler) Drift Report")
    L.append(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"Duration  : {args.duration} min")
    L.append("No hard pass criterion for T10 — characterises magnetometer-off limitation.")
    L.append("=" * 70)

    for name in DEVICE_NAMES:
        sensor_rows = [r for r in rows if r["sensor"] == name]
        if not sensor_rows:
            L.append(f"\n{name}: no data")
            continue

        L.append(f"\n{name}")
        L.append("-" * 50)

        # Final drifts
        last = sensor_rows[-1]
        L.append(f"  Final drift at {last['minute']} min:")
        L.append(f"    Roll  : {last['drift_roll']:>8} deg")
        L.append(f"    Pitch : {last['drift_pitch']:>8} deg")
        L.append(f"    Yaw   : {last['drift_yaw']:>8} deg  <- T10 key metric")

        # Max drift across all minutes
        dr_vals = [r["drift_roll"]  for r in sensor_rows if r["drift_roll"]  != ""]
        dp_vals = [r["drift_pitch"] for r in sensor_rows if r["drift_pitch"] != ""]
        dy_vals = [r["drift_yaw"]   for r in sensor_rows if r["drift_yaw"]   != ""]

        if dr_vals:
            L.append(f"  Max |drift| over session:")
            L.append(f"    Roll  : {max(abs(v) for v in dr_vals):.4f} deg")
            L.append(f"    Pitch : {max(abs(v) for v in dp_vals):.4f} deg")
            L.append(f"    Yaw   : {max(abs(v) for v in dy_vals):.4f} deg")

        # Drift at 30 min
        row_30 = next((r for r in sensor_rows if r["minute"] == 30), None)
        if row_30:
            L.append(f"  Drift at 30 min (T10 reference point):")
            L.append(f"    Roll  : {row_30['drift_roll']:>8} deg")
            L.append(f"    Pitch : {row_30['drift_pitch']:>8} deg")
            L.append(f"    Yaw   : {row_30['drift_yaw']:>8} deg")

        # Drift table (every 10 min)
        L.append(f"\n  {'Min':>4}  {'Roll':>8}  {'Pitch':>8}  {'Yaw':>8}  "
                 f"{'dRoll':>8}  {'dPitch':>8}  {'dYaw':>8}")
        L.append("  " + "-" * 60)
        for r in sensor_rows:
            if r["minute"] % 10 == 0 or r["minute"] == 1:
                L.append(f"  {r['minute']:>4}  "
                         f"{r['roll']:>8.3f}  {r['pitch']:>8.3f}  {r['yaw']:>8.3f}  "
                         f"{str(r['drift_roll']):>8}  "
                         f"{str(r['drift_pitch']):>8}  "
                         f"{str(r['drift_yaw']):>8}")

    L.append("")
    L.append("=" * 70)

    text = "\n".join(L)
    with open(report_path, "w") as f:
        f.write(text)

    print()
    print(text)
    print(f"\nCSV    : {csv_path}")
    print(f"Report : {report_path}")


if __name__ == "__main__":
    main()