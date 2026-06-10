"""
ble_latency_test_v2.py
----------------------
Measures true one-way BLE latency using the embedded millis() timestamp
in each 20-byte quaternion packet.

Packet format (little-endian):
    [ w float 4B ][ x float 4B ][ y float 4B ][ z float 4B ][ millis uint32 4B ]

Method
------
1. On connect, send SYNC (0x53) once to establish the clock offset between
   sensor millis() and host time.monotonic().  The offset is re-established
   at the start of every trial in case of drift or reconnection.

2. Every quaternion packet then gives a latency sample:
       latency = host_recv_time_ms - (sensor_millis + clock_offset_ms)

3. At 50 Hz this yields 3000 samples/minute — far more than the ping approach.

Trial structure
---------------
You are prompted to enter a distance label, an optional duration (default 30s),
and the script collects samples for that window.  Per-trial stats are printed
live and a comparison table is shown at the end.

Usage
-----
    python ble_latency_test_v2.py
    python ble_latency_test_v2.py --device IMU_ARM
    python ble_latency_test_v2.py --duration 60

Requirements
------------
    pip install bleak
"""

import asyncio
import argparse
import re
import statistics
import struct
import sys
import time
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner

# ── BLE UUIDs (Nordic UART Service) ──────────────────────────────────────────
UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

CMD_SYNC        = bytes([0x53])
SCAN_TIMEOUT    = 15.0    # seconds
SYNC_TIMEOUT    = 3.0     # seconds to wait for SYNC reply
DEFAULT_DEVICE  = "IMU_WRIST"
DEFAULT_DURATION = 30     # seconds per trial
PROGRESS_EVERY  = 50      # print a live update every N samples


# ── Trial result ──────────────────────────────────────────────────────────────

@dataclass
class TrialResult:
    label:          str
    duration_s:     float
    latencies_ms:   list = field(default_factory=list)
    clock_offset_ms: float = 0.0

    @property
    def n(self):
        return len(self.latencies_ms)

    def summary(self):
        if not self.latencies_ms:
            return "  No samples recorded."
        s = self.latencies_ms
        pct95 = sorted(s)[int(0.95 * len(s))]
        std   = statistics.stdev(s) if self.n > 1 else 0.0
        return (
            f"  Samples  : {self.n}  ({self.n / self.duration_s:.1f} Hz effective)\n"
            f"  Offset   : {self.clock_offset_ms:+.2f} ms (sensor clock vs host)\n"
            f"  Latency  : mean={statistics.mean(s):.2f}ms  "
            f"min={min(s):.2f}ms  max={max(s):.2f}ms  "
            f"p95={pct95:.2f}ms  stdev={std:.2f}ms"
        )


# ── Clock sync ────────────────────────────────────────────────────────────────

class ClockSync:
    """
    Sends SYNC once and computes the offset between sensor millis() and
    host time.monotonic() in milliseconds.

    offset_ms = host_time_ms_at_send - sensor_millis_in_reply
                + (rtt_ms / 2)

    After this, for any packet:
        true_send_time_ms = sensor_millis + offset_ms
        latency_ms        = host_recv_time_ms - true_send_time_ms
    """

    def __init__(self, client: BleakClient):
        self._client     = client
        self._event      = asyncio.Event()
        self._sensor_ms  = None
        self._host_t0    = None
        self._host_t1    = None
        self._buf        = ""
        self.offset_ms   = None

    def handle_notification(self, _sender, raw: bytearray):
        if len(raw) == 20 or len(raw) == 16:
            return   # quaternion packet — ignore during sync
        self._buf += raw.decode("utf-8", errors="replace")
        m = re.search(r"SYNC:(\d+)", self._buf)
        if m:
            self._host_t1   = time.monotonic() * 1000.0
            self._sensor_ms = int(m.group(1))
            self._buf       = ""
            self._event.set()

    async def run(self) -> float:
        """Returns offset_ms, raises TimeoutError if no reply."""
        self._event.clear()
        self._sensor_ms = None

        await self._client.start_notify(UART_TX_UUID, self.handle_notification)

        self._host_t0 = time.monotonic() * 1000.0
        await self._client.write_gatt_char(UART_RX_UUID, CMD_SYNC, response=False)

        try:
            await asyncio.wait_for(self._event.wait(), timeout=SYNC_TIMEOUT)
        except asyncio.TimeoutError:
            await self._client.stop_notify(UART_TX_UUID)
            raise TimeoutError("SYNC reply not received — is the firmware flashed correctly?")

        await self._client.stop_notify(UART_TX_UUID)

        rtt_ms       = self._host_t1 - self._host_t0
        self.offset_ms = self._host_t0 - self._sensor_ms + (rtt_ms / 2.0)

        print(f"[SYNC] RTT={rtt_ms:.2f}ms  sensor_ms={self._sensor_ms}  "
              f"offset={self.offset_ms:+.2f}ms")
        return self.offset_ms


# ── Latency collector ─────────────────────────────────────────────────────────

class LatencyCollector:
    """
    Subscribes to quaternion notifications and records one-way latency
    per packet using the pre-computed clock offset.
    """

    def __init__(self, client: BleakClient, offset_ms: float):
        self._client    = client
        self._offset_ms = offset_ms
        self._samples   = []
        self._count     = 0
        self._active    = False

    def handle_notification(self, _sender, raw: bytearray):
        if not self._active:
            return
        if len(raw) != 20:
            return   # not a timestamped quaternion packet

        host_recv_ms = time.monotonic() * 1000.0

        # Unpack: four floats + one uint32
        w, x, y, z, sensor_ms = struct.unpack_from("<ffffI", raw)

        true_send_ms  = sensor_ms + self._offset_ms
        latency_ms    = host_recv_ms - true_send_ms
        self._samples.append(latency_ms)
        self._count   += 1

        if self._count % PROGRESS_EVERY == 0:
            recent = self._samples[-PROGRESS_EVERY:]
            print(f"  [{self._count:>5} samples]  "
                  f"last={latency_ms:.2f}ms  "
                  f"recent_mean={statistics.mean(recent):.2f}ms  "
                  f"recent_max={max(recent):.2f}ms")

    async def collect(self, duration_s: float) -> list:
        self._samples = []
        self._count   = 0
        self._active  = True

        await self._client.start_notify(UART_TX_UUID, self.handle_notification)

        t_end = time.monotonic() + duration_s
        while time.monotonic() < t_end:
            remaining = t_end - time.monotonic()
            # Progress heartbeat every 5 seconds
            await asyncio.sleep(min(5.0, remaining))
            if time.monotonic() < t_end:
                elapsed = duration_s - (t_end - time.monotonic())
                print(f"  ... {elapsed:.0f}s / {duration_s:.0f}s  "
                      f"({self._count} samples so far)")

        self._active = False
        await self._client.stop_notify(UART_TX_UUID)
        return list(self._samples)


# ── Helpers ───────────────────────────────────────────────────────────────────

def ask(prompt: str) -> str:
    return input(prompt).strip()

def print_separator(char="─", width=64):
    print(char * width)

def print_histogram(latencies: list, bins: int = 10):
    """Simple ASCII histogram of latency distribution."""
    if not latencies:
        return
    lo, hi = min(latencies), max(latencies)
    if lo == hi:
        return
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in latencies:
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    peak = max(counts)
    bar_scale = 30 / peak if peak > 0 else 1
    print("  Latency distribution:")
    for i, c in enumerate(counts):
        lo_edge = lo + i * width
        hi_edge = lo_edge + width
        bar = "█" * int(c * bar_scale)
        print(f"  {lo_edge:>6.1f}–{hi_edge:<6.1f}ms  {bar} {c}")

def print_comparison_table(trials: list):
    print_separator("═", 80)
    print("  COMPARISON TABLE — one-way latency (ms)")
    print_separator("─", 80)
    hdr = (f"  {'Distance':<12}  {'N':>5}  {'Mean':>7}  {'Min':>7}  "
           f"{'Max':>7}  {'p95':>7}  {'Stdev':>7}  {'Hz':>6}")
    print(hdr)
    print_separator("─", 80)
    for t in trials:
        if not t.latencies_ms:
            print(f"  {t.label:<12}  {'—':>5}")
            continue
        s    = t.latencies_ms
        p95  = sorted(s)[int(0.95 * len(s))]
        std  = statistics.stdev(s) if t.n > 1 else 0.0
        hz   = t.n / t.duration_s
        print(
            f"  {t.label:<12}  {t.n:>5}  "
            f"{statistics.mean(s):>7.2f}  {min(s):>7.2f}  "
            f"{max(s):>7.2f}  {p95:>7.2f}  {std:>7.2f}  {hz:>6.1f}"
        )
    print_separator("═", 80)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(device_name: str, default_duration: int):
    loop = asyncio.get_event_loop()

    print_separator("═", 64)
    print("  BLE LATENCY TESTER v2 — embedded timestamp method")
    print(f"  Target : {device_name}")
    print(f"  Default trial duration : {default_duration}s")
    print_separator("═", 64)

    # ── Scan ──────────────────────────────────────────────────────────────────
    print(f"\n[SCAN] Looking for '{device_name}' ({SCAN_TIMEOUT:.0f}s timeout)...")
    device = await BleakScanner.find_device_by_name(device_name, timeout=SCAN_TIMEOUT)
    if device is None:
        print(f"[ERROR] '{device_name}' not found.")
        sys.exit(1)
    print(f"[SCAN] Found at {device.address}")

    all_trials = []

    async with BleakClient(device, timeout=10.0) as client:
        print(f"[BLE]  Connected\n")

        # ── Initial clock sync ────────────────────────────────────────────────
        print("[SYNC] Establishing clock offset...")
        syncer = ClockSync(client)
        try:
            offset_ms = await syncer.run()
        except TimeoutError as e:
            print(f"[ERROR] {e}")
            sys.exit(1)

        collector = LatencyCollector(client, offset_ms)

        # ── Trial loop ────────────────────────────────────────────────────────
        while True:
            print_separator()
            print("  Enter trial details  (or 'done' / 'quit')")
            print_separator()

            label = await loop.run_in_executor(
                None, ask, "  Distance label (e.g. 1m / 2m / 5m): "
            )
            if label.lower() in ("done", "d", "", "quit", "q"):
                break

            dur_str = await loop.run_in_executor(
                None, ask, f"  Duration in seconds [{default_duration}]: "
            )
            duration = default_duration
            if dur_str.isdigit() and int(dur_str) > 0:
                duration = int(dur_str)

            # Re-sync clock before each trial to minimise drift error
            resync = await loop.run_in_executor(
                None, ask, "  Re-sync clock before trial? [Y/n]: "
            )
            if resync.lower() not in ("n", "no"):
                print("[SYNC] Re-syncing...")
                try:
                    offset_ms = await syncer.run()
                    collector = LatencyCollector(client, offset_ms)
                except TimeoutError as e:
                    print(f"[WARN] Re-sync failed ({e}). Using previous offset.")

            await loop.run_in_executor(
                None, ask,
                f"\n  Ready: {duration}s trial at '{label}'.\n"
                f"  Position sensor, then press Enter..."
            )

            print(f"\n  Collecting for {duration}s...\n")
            samples = await collector.collect(duration)

            trial = TrialResult(
                label          = label,
                duration_s     = duration,
                latencies_ms   = samples,
                clock_offset_ms = offset_ms,
            )
            all_trials.append(trial)

            print(f"\n  ── Trial '{label}' results ──")
            print(trial.summary())
            print()
            print_histogram(samples)

            again = await loop.run_in_executor(
                None, ask, "\n  Run another trial? [Y/n]: "
            )
            if again.lower() in ("n", "no"):
                break

    # ── Final summary ─────────────────────────────────────────────────────────
    if all_trials:
        print()
        print_comparison_table(all_trials)
    else:
        print("\n  No trials completed.")

    print("\n[DONE]\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BLE latency tester — embedded timestamp method"
    )
    parser.add_argument(
        "--device", default=DEFAULT_DEVICE,
        help=f"BLE device name (default: {DEFAULT_DEVICE})"
    )
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_DURATION,
        help=f"Default trial duration in seconds (default: {DEFAULT_DURATION})"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.device, args.duration))
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED]")