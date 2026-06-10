"""
T8 / T9 — BNO085 Static Orientation Accuracy
===============================================
T8  Static orientation accuracy  (NFR-02)
    Tilt sensor to 30, 45, 60, 90 deg via protractor.
    3 readings per angle with fresh mount between each.
    Pass criterion: mean |error| <= 5 deg at each angle.

T9  Static hold stability  (NFR-02)
    Hold sensor at a fixed angle for 60s.
    Pass criterion: SD <= 1 deg over 60s.

Method
------
The BNO085 is in GAME_ROTATION_VECTOR mode (6-axis, no magnetometer).
Tilt is a rotation about the sensor's X or Z axis — the angle appears
in the Euler PITCH channel when the sensor is tilted nose-up/down.

For this test, tilt the sensor so that PITCH changes (rotate around
the long axis of the board). The script auto-identifies which Euler
channel is changing and uses that as the tilt measurement.

Setup
-----
  1. Power on ONE sensor (any of the three).
  2. Close the main ShoulderSense app.
  3. Have your protractor / angle reference ready.
  4. Run this script and follow the prompts.

Run
---
    cd IMU_GUI_App
    python Test_Scripts/T8_T9_Orientation_Accuracy.py

Output
------
  T8_T9_results_<timestamp>.csv   — all readings for Table 7.1
  T8_T9_report_<timestamp>.txt    — formatted report with pass/fail
"""

import asyncio
import csv
import math
import struct
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

# ── Configuration ──────────────────────────────────────────────────────────────
UART_TX_UUID    = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
DEVICE_NAMES    = ["IMU_WRIST", "IMU_ARM", "IMU_CHEST"]

SCAN_TIMEOUT_S  = 30.0
SETTLE_FRAMES   = 50       # discard first N frames after mount (filter settling)
READING_FRAMES  = 150      # frames to average per reading (~3s at 50Hz)
HOLD_FRAMES     = 3000     # frames for T9 hold stability (~60s at 50Hz)
SETTLE_SD_THRESHOLD = 0.05 # deg — SD below this = sensor has settled

T8_ANGLES       = [30, 45, 60, 90]   # reference angles in degrees
T8_READINGS     = 3                   # readings per angle
T8_PASS_DEG     = 5.0                 # pass criterion: mean |error| <= 5 deg
T9_PASS_SD      = 1.0                 # pass criterion: SD <= 1 deg over 60s

OUT_DIR = Path(__file__).parent


# ── Maths ──────────────────────────────────────────────────────────────────────

def quat_to_euler(w, x, y, z):
    """Unit quaternion -> (roll, pitch, yaw) degrees, ZYX Tait-Bryan."""
    sinr = 2.0 * (w * x + y * z)
    cosr = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr, cosr))

    sinp = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    pitch = math.degrees(math.asin(sinp))

    siny = 2.0 * (w * z + x * y)
    cosy = 1.0 - 2.0 * (y * y + z * z)
    yaw  = math.degrees(math.atan2(siny, cosy))

    return roll, pitch, yaw


def mean_sd(values):
    n    = len(values)
    if n == 0:
        return 0.0, 0.0
    m    = sum(values) / n
    sd   = math.sqrt(sum((v - m)**2 for v in values) / n) if n > 1 else 0.0
    return m, sd


def norm(w, x, y, z):
    return math.sqrt(w*w + x*x + y*y + z*z)


# ── BLE packet queue ───────────────────────────────────────────────────────────

class PacketQueue:
    """Thread-safe queue of decoded quaternion packets."""
    def __init__(self):
        self._lock    = threading.Lock()
        self._packets = []   # list of (w, x, y, z)
        self.connected = False
        self.device_name = None

    def push(self, w, x, y, z):
        with self._lock:
            self._packets.append((w, x, y, z))

    def pop_all(self):
        with self._lock:
            out = list(self._packets)
            self._packets.clear()
        return out

    def count(self):
        with self._lock:
            return len(self._packets)

    def flush(self):
        with self._lock:
            self._packets.clear()


# ── BLE connection (background) ────────────────────────────────────────────────

async def _ble_run(queue: PacketQueue, stop_event: threading.Event):
    """Scan for any known sensor, connect, stream into queue."""
    print("  Scanning for any sensor...")
    try:
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT_S)
    except Exception as e:
        print(f"  Scan error: {e}")
        return

    target = None
    for d in devices:
        if d.name in DEVICE_NAMES:
            target = d
            break

    if target is None:
        print(f"  No sensor found. Tried: {DEVICE_NAMES}")
        return

    queue.device_name = target.name
    print(f"  Found: {target.name} at {target.address}")
    print(f"  Connecting...")

    try:
        async with BleakClient(target, timeout=10.0) as client:
            try:
                await client.request_mtu(247)
            except Exception:
                pass

            queue.connected = True
            print(f"  Connected to {target.name}.\n")

            def handler(_, raw: bytearray):
                if len(raw) == 16:
                    w, x, y, z = struct.unpack_from("<ffff", raw)
                    queue.push(w, x, y, z)

            await client.start_notify(UART_TX_UUID, handler)
            while client.is_connected and not stop_event.is_set():
                await asyncio.sleep(0.1)

    except Exception as e:
        print(f"  Connection error: {e}")
    finally:
        queue.connected = False


def start_ble(queue: PacketQueue, stop_event: threading.Event):
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_ble_run(queue, stop_event))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ── Sensor reading helpers ─────────────────────────────────────────────────────

def wait_for_connection(queue: PacketQueue, timeout=40.0):
    """Block until BLE is connected."""
    t0 = time.time()
    while not queue.connected:
        if time.time() - t0 > timeout:
            print("  ERROR: timed out waiting for connection.")
            sys.exit(1)
        print(f"  Waiting for connection...    ", end="\r")
        time.sleep(0.5)
    print()


def collect_frames(queue: PacketQueue, n_frames: int,
                   show_live=True, label=""):
    """
    Collect exactly n_frames quaternion packets.
    Returns list of (w, x, y, z, roll, pitch, yaw, qnorm).
    Prints a live progress bar and current readings.
    """
    queue.flush()
    collected = []

    while len(collected) < n_frames:
        pkts = queue.pop_all()
        for w, x, y, z in pkts:
            roll, pitch, yaw = quat_to_euler(w, x, y, z)
            qn = norm(w, x, y, z)
            collected.append((w, x, y, z, roll, pitch, yaw, qn))

        if show_live and collected:
            w, x, y, z, roll, pitch, yaw, qn = collected[-1]
            pct = len(collected) / n_frames * 100
            bar = int(pct / 5) * "█" + int((100 - pct) / 5) * "░"
            print(f"  {label} [{bar}] {len(collected):4d}/{n_frames}  "
                  f"roll={roll:7.3f}  pitch={pitch:7.3f}  "
                  f"yaw={yaw:7.3f}  norm={qn:.6f}",
                  end="\r")
        time.sleep(0.01)

    print()
    return collected


def wait_for_settle(queue: PacketQueue, threshold=SETTLE_SD_THRESHOLD):
    """
    Wait until the sensor has settled (pitch SD < threshold over last 20 frames).
    Shows live readings while waiting.
    """
    print("  Waiting for sensor to settle (hold still)...")
    window = []
    while True:
        pkts = queue.pop_all()
        for w, x, y, z in pkts:
            _, pitch, _ = quat_to_euler(w, x, y, z)
            window.append(pitch)
            if len(window) > 20:
                window.pop(0)

        if len(window) >= 20:
            _, sd = mean_sd(window)
            _, pitch, _ = quat_to_euler(*queue.pop_all()[-1][:4]) \
                if queue.count() else (0, window[-1], 0)
            print(f"  Settling... pitch={window[-1]:7.3f} deg  "
                  f"SD(20)={sd:.4f} deg  "
                  f"(threshold {threshold} deg)    ",
                  end="\r")
            if sd < threshold:
                print()
                print("  Sensor settled.")
                return

        time.sleep(0.02)


def identify_tilt_axis(frames):
    """
    Given a set of frames, identify which Euler channel has the largest
    absolute mean value — that's the tilt axis being measured.
    Returns ('roll'|'pitch'|'yaw', mean_value).
    """
    rolls   = [f[4] for f in frames]
    pitches = [f[5] for f in frames]
    yaws    = [f[6] for f in frames]

    rm, _ = mean_sd(rolls)
    pm, _ = mean_sd(pitches)
    ym, _ = mean_sd(yaws)

    candidates = [("roll", rm), ("pitch", pm), ("yaw", ym)]
    axis, val  = max(candidates, key=lambda x: abs(x[1]))
    return axis, val


# ── T8: Static orientation accuracy ───────────────────────────────────────────

def run_t8(queue: PacketQueue, csv_rows: list):
    """
    Run T8: 3 readings per reference angle, fresh mount between each.
    Returns dict of {angle: [reading1, reading2, reading3]}.
    """
    print()
    print("=" * 65)
    print("  T8 — Static Orientation Accuracy")
    print("=" * 65)
    print(f"  Reference angles : {T8_ANGLES}")
    print(f"  Readings per angle: {T8_READINGS} (fresh mount between each)")
    print(f"  Pass criterion   : mean |error| <= {T8_PASS_DEG} deg")
    print()
    print("  TIP: tilt the sensor so PITCH changes (rotate nose up/down).")
    print("       Use roll side of the board on your protractor surface.")
    print()

    results = {}   # {angle: [measured1, measured2, measured3]}
    tilt_axis_used = None

    for ref_angle in T8_ANGLES:
        readings = []
        print(f"\n  ── Reference angle: {ref_angle} deg ──────────────────────")

        for reading_num in range(1, T8_READINGS + 1):
            print(f"\n  Reading {reading_num} of {T8_READINGS}")
            if reading_num > 1:
                print("  Pick the sensor up, put it back down at the same angle.")

            input(f"  Set sensor to {ref_angle} deg and press Enter...")

            # Discard settling frames
            print(f"  Discarding {SETTLE_FRAMES} settling frames...")
            collect_frames(queue, SETTLE_FRAMES, show_live=False)

            # Wait for signal to stabilise
            wait_for_settle(queue)

            # Take reading
            print(f"  Taking {READING_FRAMES} frames ({READING_FRAMES/50:.0f}s)...")
            frames = collect_frames(queue, READING_FRAMES,
                                    show_live=True,
                                    label=f"  R{reading_num}@{ref_angle}deg")

            # Identify tilt axis from first angle measurement
            axis, measured = identify_tilt_axis(frames)
            if tilt_axis_used is None:
                tilt_axis_used = axis
                print(f"  Tilt axis identified: {axis.upper()}")
            else:
                axis = tilt_axis_used   # stay consistent

            # Get the right channel
            if axis == "roll":
                vals = [f[4] for f in frames]
            elif axis == "pitch":
                vals = [f[5] for f in frames]
            else:
                vals = [f[6] for f in frames]

            m, sd     = mean_sd(vals)
            error     = abs(abs(m) - ref_angle)
            norms_    = [f[7] for f in frames]
            nm, nsd   = mean_sd(norms_)

            readings.append(round(abs(m), 4))

            print(f"  Result: {axis}={m:.4f} deg  |error|={error:.4f} deg  "
                  f"SD={sd:.4f} deg  norm={nm:.6f}")

            # Save to CSV rows
            csv_rows.append({
                "test":       "T8",
                "ref_deg":    ref_angle,
                "reading":    reading_num,
                "axis":       axis,
                "measured":   round(abs(m), 4),
                "error":      round(error, 4),
                "sd":         round(sd, 4),
                "norm_mean":  round(nm, 6),
                "norm_sd":    round(nsd, 6),
            })

        results[ref_angle] = readings

        # Per-angle summary
        mean_err = sum(abs(abs(r) - ref_angle) for r in readings) / len(readings)
        status   = "PASS" if mean_err <= T8_PASS_DEG else "FAIL"
        print(f"\n  Angle {ref_angle} deg: readings={readings}  "
              f"mean |error|={mean_err:.4f} deg  [{status}]")

    return results, tilt_axis_used


# ── T9: Static hold stability ──────────────────────────────────────────────────

def run_t9(queue: PacketQueue, tilt_axis: str, csv_rows: list):
    """
    Run T9: hold at any fixed angle for 60s, compute SD.
    """
    print()
    print("=" * 65)
    print("  T9 — Static Hold Stability (60s)")
    print("=" * 65)
    print(f"  Hold sensor at any convenient fixed angle for 60s.")
    print(f"  Pass criterion: SD <= {T9_PASS_SD} deg")
    print()

    input("  Set sensor at a fixed angle, press Enter to start 60s hold...")

    print(f"  Recording {HOLD_FRAMES} frames (~60s). Hold completely still.")
    frames = collect_frames(queue, HOLD_FRAMES, show_live=True, label="T9")

    if tilt_axis == "roll":
        vals = [f[4] for f in frames]
    elif tilt_axis == "pitch":
        vals = [f[5] for f in frames]
    else:
        vals = [f[6] for f in frames]

    m, sd   = mean_sd(vals)
    passed  = sd <= T9_PASS_SD
    status  = "PASS" if passed else "FAIL"

    print(f"\n  T9 Result: mean={m:.4f} deg  SD={sd:.4f} deg  [{status}]")
    print(f"  (target: SD <= {T9_PASS_SD} deg)")

    csv_rows.append({
        "test":       "T9",
        "ref_deg":    "hold",
        "reading":    1,
        "axis":       tilt_axis,
        "measured":   round(m, 4),
        "error":      "",
        "sd":         round(sd, 4),
        "norm_mean":  "",
        "norm_sd":    "",
    })

    return m, sd, passed


# ── Report ─────────────────────────────────────────────────────────────────────

def write_report(path, t8_results, tilt_axis, t9_mean, t9_sd,
                 t9_passed, device_name, csv_rows):
    L = []
    L.append("=" * 65)
    L.append("T8 / T9 — BNO085 STATIC ORIENTATION ACCURACY REPORT")
    L.append(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"Sensor    : {device_name}")
    L.append(f"Tilt axis : {tilt_axis.upper()}")
    L.append("=" * 65)

    # T8 Table
    L.append("")
    L.append("T8 — Static Orientation Accuracy")
    L.append(f"Pass criterion: mean |error| <= {T8_PASS_DEG} deg")
    L.append("")
    L.append(f"  {'Ref (deg)':>10}  {'M1 (deg)':>10}  {'M2 (deg)':>10}  "
             f"{'M3 (deg)':>10}  {'Mean err':>10}  {'Result':>8}")
    L.append("  " + "-" * 65)

    t8_overall_errors = []
    for ref in T8_ANGLES:
        readings = t8_results.get(ref, [])
        while len(readings) < 3:
            readings.append(None)
        errors = [abs(abs(r) - ref) for r in readings if r is not None]
        mean_err = sum(errors) / len(errors) if errors else None
        if mean_err is not None:
            t8_overall_errors.append(mean_err)
        status = "PASS" if (mean_err is not None and mean_err <= T8_PASS_DEG) \
                 else "FAIL"
        r_strs = [f"{r:.4f}" if r is not None else "---" for r in readings]
        L.append(f"  {ref:>10}  {r_strs[0]:>10}  {r_strs[1]:>10}  "
                 f"{r_strs[2]:>10}  "
                 f"{mean_err:>10.4f}  {status:>8}" if mean_err is not None
                 else f"  {ref:>10}  {'---':>10}  {'---':>10}  {'---':>10}  "
                 f"{'---':>10}  {'---':>8}")

    if t8_overall_errors:
        overall_mean = sum(t8_overall_errors) / len(t8_overall_errors)
        overall_sd   = math.sqrt(
            sum((e - overall_mean)**2 for e in t8_overall_errors)
            / len(t8_overall_errors))
        overall_pass = all(e <= T8_PASS_DEG for e in t8_overall_errors)
        L.append("  " + "-" * 65)
        L.append(f"  {'Overall':>10}  {'':>10}  {'':>10}  {'':>10}  "
                 f"{overall_mean:>10.4f}  "
                 f"{'PASS' if overall_pass else 'FAIL':>8}")
        L.append(f"  Overall SD: {overall_sd:.4f} deg")

    # T9 Result
    L.append("")
    L.append("T9 — Static Hold Stability (60s)")
    L.append(f"Pass criterion: SD <= {T9_PASS_SD} deg")
    L.append("")
    L.append(f"  Mean  : {t9_mean:.4f} deg")
    L.append(f"  SD    : {t9_sd:.4f} deg")
    L.append(f"  Result: {'PASS' if t9_passed else 'FAIL'}")

    # LaTeX-ready values for Table 7.1
    L.append("")
    L.append("=" * 65)
    L.append("LaTeX table values for Table 7.1:")
    L.append("-" * 40)
    for ref in T8_ANGLES:
        readings = t8_results.get(ref, [None, None, None])
        while len(readings) < 3:
            readings.append(None)
        errors = [abs(abs(r) - ref) for r in readings if r is not None]
        me = sum(errors) / len(errors) if errors else 0
        r_strs = [f"{r:.2f}" if r is not None else "---" for r in readings]
        L.append(f"  {ref} & {r_strs[0]} & {r_strs[1]} & {r_strs[2]} & "
                 f"{me:.2f} & \\\\")
    L.append(f"  Hold stability (T9): SD = {t9_sd:.4f} deg")
    L.append("=" * 65)

    text = "\n".join(L)
    with open(path, "w") as f:
        f.write(text)
    print()
    print(text)


def write_csv(path, rows):
    fields = ["test", "ref_deg", "reading", "axis",
              "measured", "error", "sd", "norm_mean", "norm_sd"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path    = OUT_DIR / f"T8_T9_results_{ts}.csv"
    report_path = OUT_DIR / f"T8_T9_report_{ts}.txt"

    print("=" * 65)
    print("  T8 / T9 — BNO085 Static Orientation Accuracy Test")
    print("=" * 65)
    print()
    print("  This test characterises the BNO085 in isolation,")
    print("  before any mount correction or calibration.")
    print()
    print("  You will need:")
    print("    - A protractor or precision angle reference")
    print("    - One powered-on IMU sensor (any of the three)")
    print("    - A rigid flat surface to mount the sensor on")
    print()
    print("  Test sequence:")
    print("    T8: 30 / 45 / 60 / 90 deg  x  3 readings each")
    print("    T9: 60s hold at one angle")
    print()
    input("  Press Enter to begin scanning...")
    print()

    queue      = PacketQueue()
    stop_event = threading.Event()
    start_ble(queue, stop_event)

    wait_for_connection(queue)

    csv_rows = []

    # Run T8
    t8_results, tilt_axis = run_t8(queue, csv_rows)

    # Run T9
    t9_mean, t9_sd, t9_passed = run_t9(queue, tilt_axis, csv_rows)

    stop_event.set()

    # Write outputs
    write_csv(csv_path, csv_rows)
    write_report(report_path, t8_results, tilt_axis,
                 t9_mean, t9_sd, t9_passed,
                 queue.device_name or "unknown", csv_rows)

    print(f"\nFiles written:")
    print(f"  CSV    : {csv_path}")
    print(f"  Report : {report_path}")


if __name__ == "__main__":
    main()
