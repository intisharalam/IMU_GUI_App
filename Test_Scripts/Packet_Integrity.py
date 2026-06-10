"""
tests/t3_packet_integrity.py
============================
Test T3 — Quaternion Packet Integrity (FR-02)

Connects to all three IMU sensors simultaneously and collects 100 packets
from each. For every received packet the following checks are applied:

  1. Payload length is exactly 16 bytes.
  2. The four floats decode without exception (no NaN / Inf).
  3. The quaternion norm ||q|| = sqrt(w²+x²+y²+z²) lies in [0.99, 1.01].

Pass criteria (from test plan):
  - ||q|| in [0.99, 1.01] for >= 99 % of packets across all sensors.
  - Zero struct.unpack exceptions.

Usage
-----
    python tests/t3_packet_integrity.py

Run from the project root with all three sensors powered on and in range.
No GUI or application instance should be running at the same time (the
BLE connections would compete).

Output
------
Prints a per-sensor summary and an overall PASS / FAIL verdict to stdout.
A machine-readable CSV log is also written to data/t3_results_<timestamp>.csv
for inclusion in the testing appendix.
"""

import asyncio
import csv
import math
import struct
import time
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner

# ── Configuration ─────────────────────────────────────────────────────────────

UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"   # sensor → host (notify)

DEVICE_NAMES = {
    "wrist": "IMU_WRIST",
    "arm":   "IMU_ARM",
    "chest": "IMU_CHEST",
}

TARGET_PACKETS   = 100      # packets to collect per sensor
NORM_LOW         = 0.99     # lower bound for ||q||
NORM_HIGH        = 1.01     # upper bound for ||q||
PASS_RATE        = 0.99     # fraction of packets that must be in-range to pass
SCAN_TIMEOUT_S   = 20.0
COLLECT_TIMEOUT_S = 30.0    # max seconds to wait for TARGET_PACKETS after connect

OUTPUT_DIR = Path(__file__).parent.parent / "data"


# ── Per-sensor collection ─────────────────────────────────────────────────────

async def collect_packets(slot_name: str, device_name: str) -> dict:
    """
    Connects to one IMU sensor, collects TARGET_PACKETS quaternion packets,
    and returns a result dict with per-packet records and summary statistics.
    """
    print(f"[{slot_name}] Scanning for '{device_name}'...")

    result = {
        "slot":          slot_name,
        "device":        device_name,
        "address":       None,
        "packets":       [],          # list of dicts, one per packet
        "decode_errors": 0,
        "error":         None,        # set if connection / scan failed
    }

    # Scan
    try:
        device = await BleakScanner.find_device_by_name(
            device_name, timeout=SCAN_TIMEOUT_S
        )
    except Exception as exc:
        result["error"] = f"Scan exception: {exc}"
        return result

    if device is None:
        result["error"] = f"Device '{device_name}' not found within {SCAN_TIMEOUT_S:.0f} s"
        return result

    result["address"] = device.address
    print(f"[{slot_name}] Found at {device.address}. Connecting...")

    # Shared state between notification handler and the wait loop
    packets = []
    done_event = asyncio.Event()

    def handler(_, raw: bytearray):
        """Called by bleak on every BLE notification."""
        arrival_t = time.monotonic()
        record = {
            "index":       len(packets) + 1,
            "length":      len(raw),
            "w": None, "x": None, "y": None, "z": None,
            "norm":        None,
            "length_ok":   len(raw) == 16,
            "decode_ok":   False,
            "norm_in_range": False,
            "arrival_t":   arrival_t,
        }

        if len(raw) == 16:
            try:
                w, x, y, z = struct.unpack_from("<ffff", raw)
                # Reject NaN / Inf immediately — they would corrupt downstream
                if not all(math.isfinite(v) for v in (w, x, y, z)):
                    raise ValueError("Non-finite float in quaternion")
                norm = math.sqrt(w*w + x*x + y*y + z*z)
                record.update({
                    "w": w, "x": x, "y": y, "z": z,
                    "norm":        round(norm, 6),
                    "decode_ok":   True,
                    "norm_in_range": NORM_LOW <= norm <= NORM_HIGH,
                })
            except Exception as exc:
                result["decode_errors"] += 1
                record["decode_error_msg"] = str(exc)
        else:
            # Not a quaternion packet — could be a SYNC reply or unexpected data
            record["note"] = "wrong length — skipped"

        packets.append(record)

        if len(packets) >= TARGET_PACKETS and not done_event.is_set():
            done_event.set()

    # Connect and collect
    try:
        async with BleakClient(device, timeout=10.0) as client:
            try:
                await client.request_mtu(247)
            except Exception:
                pass

            await client.start_notify(UART_TX_UUID, handler)
            print(f"[{slot_name}] Connected. Collecting {TARGET_PACKETS} packets...")

            try:
                await asyncio.wait_for(done_event.wait(), timeout=COLLECT_TIMEOUT_S)
            except asyncio.TimeoutError:
                result["error"] = (
                    f"Timeout: only {len(packets)} / {TARGET_PACKETS} packets "
                    f"received within {COLLECT_TIMEOUT_S:.0f} s"
                )

            await client.stop_notify(UART_TX_UUID)

    except Exception as exc:
        result["error"] = f"Connection error: {exc}"

    result["packets"] = packets
    return result


# ── Summary and reporting ──────────────────────────────────────────────────────

def summarise(result: dict) -> dict:
    """
    Computes per-sensor pass/fail statistics from the raw packet list.
    Returns a summary dict ready for printing and CSV export.
    """
    packets = result["packets"]
    n_total = len(packets)

    if result["error"] and n_total == 0:
        return {
            "slot":            result["slot"],
            "device":          result["device"],
            "address":         result["address"],
            "n_total":         0,
            "n_valid_length":  0,
            "n_decode_ok":     0,
            "n_decode_errors": 0,
            "n_norm_in_range": 0,
            "norm_pass_rate":  0.0,
            "norm_min":        None,
            "norm_max":        None,
            "norm_mean":       None,
            "passed":          False,
            "fail_reason":     result["error"],
        }

    n_valid_length  = sum(1 for p in packets if p.get("length_ok"))
    n_decode_ok     = sum(1 for p in packets if p.get("decode_ok"))
    n_norm_in_range = sum(1 for p in packets if p.get("norm_in_range"))
    norms           = [p["norm"] for p in packets if p["norm"] is not None]

    norm_pass_rate  = n_norm_in_range / n_total if n_total else 0.0

    fail_reasons = []
    if result["decode_errors"] > 0:
        fail_reasons.append(f"{result['decode_errors']} decode exception(s)")
    if norm_pass_rate < PASS_RATE:
        fail_reasons.append(
            f"norm pass rate {norm_pass_rate*100:.1f}% < {PASS_RATE*100:.0f}%"
        )
    if result["error"]:
        fail_reasons.append(result["error"])

    return {
        "slot":            result["slot"],
        "device":          result["device"],
        "address":         result["address"],
        "n_total":         n_total,
        "n_valid_length":  n_valid_length,
        "n_decode_ok":     n_decode_ok,
        "n_decode_errors": result["decode_errors"],  # matches print_report key
        "n_norm_in_range": n_norm_in_range,
        "norm_pass_rate":  round(norm_pass_rate * 100, 2),
        "norm_min":        round(min(norms), 6)  if norms else None,
        "norm_max":        round(max(norms), 6)  if norms else None,
        "norm_mean":       round(sum(norms) / len(norms), 6) if norms else None,
        "passed":          len(fail_reasons) == 0,
        "fail_reason":     "; ".join(fail_reasons) if fail_reasons else "—",
    }


def print_report(summaries: list[dict]):
    """Prints a human-readable report to stdout."""
    print()
    print("=" * 72)
    print("  T3 — QUATERNION PACKET INTEGRITY REPORT")
    print(f"  Target: {TARGET_PACKETS} packets/sensor, "
          f"||q|| in [{NORM_LOW}, {NORM_HIGH}], "
          f">= {PASS_RATE*100:.0f}% in-range, 0 decode errors")
    print("=" * 72)

    overall_pass = True
    for s in summaries:
        status = "PASS" if s["passed"] else "FAIL"
        overall_pass = overall_pass and s["passed"]
        print()
        print(f"  Sensor : {s['device']} ({s['slot']})  —  {s['address'] or 'not found'}")
        print(f"  Packets collected    : {s['n_total']} / {TARGET_PACKETS}")
        print(f"  Valid length (16 B)  : {s['n_valid_length']}")
        print(f"  Decode OK            : {s['n_decode_ok']}  "
              f"  (errors: {s['n_decode_errors']})")
        print(f"  Norm in range        : {s['n_norm_in_range']}  "
              f"  (pass rate: {s['norm_pass_rate']:.1f}%)")
        if s["norm_mean"] is not None:
            print(f"  Norm  min/mean/max   : "
                  f"{s['norm_min']:.6f} / {s['norm_mean']:.6f} / {s['norm_max']:.6f}")
        print(f"  Result               : {status}", end="")
        if not s["passed"]:
            print(f"  [{s['fail_reason']}]", end="")
        print()

    print()
    print("=" * 72)
    verdict = "PASS" if overall_pass else "FAIL"
    print(f"  OVERALL VERDICT: {verdict}")
    print("=" * 72)
    print()


def write_csv(summaries: list[dict], packets_by_slot: dict):
    """
    Writes two CSV files:
      - t3_summary_<ts>.csv   — one row per sensor (for the report table)
      - t3_packets_<ts>.csv   — one row per packet (full dataset for appendix)
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Summary CSV
    summary_path = OUTPUT_DIR / f"t3_summary_{ts}.csv"
    summary_fields = [
        "slot", "device", "address", "n_total", "n_valid_length",
        "n_decode_ok", "n_decode_errors", "n_norm_in_range",
        "norm_pass_rate", "norm_min", "norm_mean", "norm_max",
        "passed", "fail_reason",
    ]
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(summaries)
    print(f"  Summary CSV  → {summary_path}")

    # Per-packet CSV
    packet_path = OUTPUT_DIR / f"t3_packets_{ts}.csv"
    packet_fields = ["slot", "index", "length", "w", "x", "y", "z",
                     "norm", "length_ok", "decode_ok", "norm_in_range"]
    with open(packet_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=packet_fields, extrasaction="ignore")
        w.writeheader()
        for slot_name, packets in packets_by_slot.items():
            for p in packets:
                w.writerow({"slot": slot_name, **p})
    print(f"  Packet CSV   → {packet_path}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print()
    print("T3 — Quaternion Packet Integrity Test")
    print(f"Collecting {TARGET_PACKETS} packets from each of: "
          + ", ".join(DEVICE_NAMES.values()))
    print("Make sure all three sensors are powered on and the main app is closed.")
    print()

    # Run all three collection tasks in parallel — same pattern as BLEManager
    tasks = [
        collect_packets(slot, name)
        for slot, name in DEVICE_NAMES.items()
    ]
    results = await asyncio.gather(*tasks)

    summaries        = [summarise(r) for r in results]
    packets_by_slot  = {r["slot"]: r["packets"] for r in results}

    print_report(summaries)
    write_csv(summaries, packets_by_slot)


if __name__ == "__main__":
    asyncio.run(main())