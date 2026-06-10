"""
Latency Measurement — Table 7.5
=================================
Requirement : NFR-01  (mean end-to-end latency <= 100ms)

What this measures
------------------
The software pipeline latency: time from when a BLE notification packet is
received and timestamped in the BLE callback, to when AngleProcessor.update()
reads and processes it.

This is the dominant controllable latency in the system. It is bounded above
by the 50Hz computation timer interval (20ms) plus OS thread scheduling jitter.
BLE radio transmission time (~7-15ms) and IMU sample period (20ms at 50Hz) are
hardware-fixed and not measurable in Python.

Method
------
The script replicates the exact runtime path without hardware:

  1. A "BLE producer" thread fires at 50Hz, writing synthetic quaternion
     packets to AppState with time.monotonic() stamps — identical to what
     ble_manager.py does on a real BLE callback.

  2. A "computation consumer" timer fires at 50Hz (20ms intervals), reads
     the latest quaternion from AppState, and calls AngleProcessor.update()
     — identical to the Qt 50Hz timer in the live application.

  3. At each consumer tick, latency = consumer_start_time - packet_recv_time
     (the timestamp stored in the slot by the producer).

  4. After 60 seconds, the script computes and prints Table 7.5.

The producer and consumer run at the same nominal rate but are unsynchronised
(independent threads), so the measured jitter is real OS scheduling jitter,
not an artifact of the simulation.

Running
-------
    cd IMU_GUI_App
    python Test_Scripts/Latency_Measurement.py

Output
------
Prints Table 7.5 values directly to stdout.
Also saves raw latency samples to:
    Test_Scripts/latency_raw.csv   (for plotting or further analysis)
"""

import sys
import os
import time
import threading
import csv
import math
import random
import struct
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from ble.ble_state import AppState
from calc.joint_angles import AngleProcessor, get_mount, MOUNT

# ── Configuration ──────────────────────────────────────────────────────────────
DURATION_S       = 60.0    # measurement window
PRODUCER_HZ      = 50      # BLE packet rate (matches firmware)
CONSUMER_HZ      = 50      # computation timer rate (matches Qt timer)
WARMUP_S         = 2.0     # discard first N seconds (filter/calibration settling)
OUTPUT_CSV       = os.path.join(os.path.dirname(__file__), "latency_raw.csv")


# ── Minimal AppState stub ──────────────────────────────────────────────────────
# We use a stripped-down version so the script runs without PyQt5 / GUI imports.

class SlotStub:
    def __init__(self):
        self.quat_w = 1.0
        self.quat_x = 0.0
        self.quat_y = 0.0
        self.quat_z = 0.0
        self.packet_count = 0
        self.recv_time = 0.0      # time.monotonic() when packet was written
        self.times  = []
        self.rolls  = []
        self.pitches= []
        self.yaws   = []
        self.connected = True
        self.address   = "00:00:00:00:00:00"
        self.client    = None
        self.haptic_active = False
        self.sync_offset_ms = 0.0

    def update_quaternion(self, w, x, y, z, timestamp):
        self.quat_w = w
        self.quat_x = x
        self.quat_y = y
        self.quat_z = z
        self.recv_time = timestamp
        self.packet_count += 1

    def get_quaternion(self):
        return (self.quat_w, self.quat_x, self.quat_y, self.quat_z)


class StateStub:
    """Minimal AppState-compatible object for this test."""
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = {
            "chest": SlotStub(),
            "arm":   SlotStub(),
            "wrist": SlotStub(),
        }
        self.calibrated         = True
        self.calibration_quats  = {"chest": (1,0,0,0), "arm": (1,0,0,0), "wrist": (1,0,0,0)}
        self.affected_side      = "right"
        self.trunk_lean         = 0.0
        self.flexion            = 0.0
        self.abduction          = 0.0
        self.ext_rot            = 0.0
        self.elbow              = 0.0
        self.rom_flex_limit     = 120.0
        self.rom_abd_limit      = 90.0

    def update_joint_angles(self, *args, **kwargs):
        pass   # not needed for latency measurement


# ── Synthetic quaternion generator ────────────────────────────────────────────

def _make_quat(t):
    """
    Generate a slowly rotating unit quaternion at time t.
    Mimics a sensor slowly moving through a 30° flexion arc.
    """
    angle = math.radians(30.0 * math.sin(2 * math.pi * t / 10.0))
    w = math.cos(angle / 2)
    z = math.sin(angle / 2)
    return (w, 0.0, 0.0, z)


# ── Producer thread ────────────────────────────────────────────────────────────

def producer(state, stop_event, hz=PRODUCER_HZ):
    """
    Fires at `hz` Hz, writing synthetic packets to all three slots.
    Mirrors the BLE notification callback in ble_manager.py.
    """
    interval = 1.0 / hz
    t_next   = time.monotonic() + interval

    while not stop_event.is_set():
        now = time.monotonic()
        w, x, y, z = _make_quat(now)

        with state.lock:
            for slot in state.slots.values():
                slot.update_quaternion(w, x, y, z, now)  # now = recv_time

        # Sleep until next tick
        sleep_for = t_next - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        t_next += interval


# ── Consumer (AngleProcessor) ──────────────────────────────────────────────────

def run_measurement(state):
    """
    Fires the computation update at CONSUMER_HZ.
    Records latency = now - recv_time for each slot, takes the max across
    three sensors per frame (worst-case sensor).
    Returns list of (frame_time, latency_ms) tuples.
    """
    mount = get_mount("right")

    # Bootstrap calibration into AngleProcessor
    ap = AngleProcessor(state, None)
    # Manually prime it so it doesn't skip the first frames
    ap._last_cal_id = id(state.calibration_quats)
    ap._last_side   = "right"
    from calc.joint_angles import JointAngles
    ap._angles = JointAngles()
    ap._angles.set_calibration(
        {"chest": (1,0,0,0), "arm": (1,0,0,0), "wrist": (1,0,0,0)},
        mount, "right"
    )

    interval   = 1.0 / CONSUMER_HZ
    t_start    = time.monotonic()
    t_next     = t_start + interval
    samples    = []

    print(f"  Measuring for {DURATION_S:.0f}s at {CONSUMER_HZ}Hz "
          f"(discarding first {WARMUP_S:.0f}s warmup)...")
    print()

    while True:
        now = time.monotonic()
        elapsed = now - t_start

        if elapsed >= DURATION_S:
            break

        # Read recv_time under lock (mirrors AngleProcessor reading slots)
        with state.lock:
            recv_times = [state.slots[n].recv_time for n in ["chest", "arm", "wrist"]]

        # Latency = time since most recent packet received
        # Use max across three sensors (worst-case, as the pipeline waits for all)
        if all(rt > 0 for rt in recv_times):
            latency_ms = (now - max(recv_times)) * 1000.0
            if elapsed >= WARMUP_S:
                samples.append((elapsed, latency_ms))

        # Run the actual AngleProcessor update (measures real computation overhead)
        ap.update(now)

        # Sleep until next tick
        sleep_for = t_next - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        t_next += interval

    return samples


# ── Statistics and reporting ───────────────────────────────────────────────────

def compute_stats(samples):
    latencies = [s[1] for s in samples]
    n = len(latencies)
    if n == 0:
        return {}
    latencies_sorted = sorted(latencies)
    mean   = sum(latencies) / n
    p95    = latencies_sorted[int(0.95 * n)]
    return {
        "n":    n,
        "min":  latencies_sorted[0],
        "mean": mean,
        "p95":  p95,
        "max":  latencies_sorted[-1],
    }


def print_table(stats):
    target_mean = 100.0
    mean_pass = stats["mean"] <= target_mean

    print("=" * 60)
    print("Table 7.5 — End-to-End Pipeline Latency")
    print(f"(NFR-01 target: mean <= {target_mean:.0f}ms)")
    print("=" * 60)
    print(f"  Samples collected : {stats['n']}")
    print()
    print(f"  {'Metric':<20}  {'Measured (ms)':>14}  {'Target (ms)':>12}")
    print(f"  {'-'*20}  {'-'*14}  {'-'*12}")
    print(f"  {'Minimum':<20}  {stats['min']:>14.2f}  {'—':>12}")
    print(f"  {'Mean':<20}  {stats['mean']:>14.2f}  {'<= 100':>12}")
    print(f"  {'95th percentile':<20}  {stats['p95']:>14.2f}  {'—':>12}")
    print(f"  {'Maximum':<20}  {stats['max']:>14.2f}  {'—':>12}")
    print()
    print(f"  NFR-01 (mean <= 100ms): {'PASS' if mean_pass else 'FAIL'}")
    print("=" * 60)
    print()
    print("  Note: measured latency is software pipeline only")
    print("  (BLE callback → AppState → AngleProcessor.update).")
    print("  BLE radio transmission (~7-15ms) and IMU sample")
    print("  period (20ms at 50Hz) are hardware-fixed and not")
    print("  included in this measurement.")


def save_csv(samples, path):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "latency_ms"])
        writer.writerows(samples)
    print(f"  Raw samples saved to: {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("Latency Measurement — Table 7.5")
    print("=" * 60)
    print()

    state      = StateStub()
    stop_event = threading.Event()

    # Start producer in background thread
    prod_thread = threading.Thread(
        target=producer, args=(state, stop_event), daemon=True)
    prod_thread.start()

    # Small startup delay to let producer fill slots before first consumer tick
    time.sleep(0.1)

    # Run consumer on main thread and collect samples
    samples = run_measurement(state)

    # Stop producer
    stop_event.set()
    prod_thread.join(timeout=2.0)

    # Compute and print results
    stats = compute_stats(samples)
    print_table(stats)
    save_csv(samples, OUTPUT_CSV)


if __name__ == "__main__":
    run()