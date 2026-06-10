"""
ble_latency_test.py
-------------------
Measures BLE round-trip latency to an IMU sensor node using the
existing SYNC mechanism:
  1. Host sends 0x53 ('S') to the sensor's RX characteristic
  2. Sensor replies "SYNC:<millis>\\r\\n" on its TX characteristic
  3. We measure wall-clock time around that exchange
  4. Half the round-trip = one-way latency estimate

Trial structure
---------------
You are prompted to enter a distance label (e.g. "1m", "2m", "5m"),
then a number of pings.  Results are printed per-trial and summarised
in a comparison table at the end.

Usage
-----
    python ble_latency_test.py
    python ble_latency_test.py --device IMU_ARM   # target a different node
    python ble_latency_test.py --pings 50         # default pings per trial

Requirements
------------
    pip install bleak
"""

import asyncio
import argparse
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner

# ── BLE UUIDs (Nordic UART Service) ──────────────────────────────────────────
UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"   # sensor → host (notify)
UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"   # host → sensor (write)

CMD_SYNC     = bytes([0x53])   # 'S'
SCAN_TIMEOUT = 15.0            # seconds to wait while scanning
PING_TIMEOUT = 3.0             # seconds before a single ping is declared lost
DEFAULT_DEVICE = "IMU_CHEST"
DEFAULT_PINGS  = 100


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class TrialResult:
    label:      str
    rtts:       list = field(default_factory=list)   # round-trip times, ms
    lost:       int  = 0

    @property
    def one_way(self):
        """Half round-trip estimates, ms."""
        return [r / 2.0 for r in self.rtts]

    @property
    def n(self):
        return len(self.rtts)

    def summary(self):
        if not self.rtts:
            return f"  No samples recorded."
        ow = self.one_way
        return (
            f"  Samples : {self.n}   Lost: {self.lost}\n"
            f"  One-way : mean={statistics.mean(ow):.1f}ms  "
            f"min={min(ow):.1f}ms  max={max(ow):.1f}ms  "
            f"stdev={statistics.stdev(ow) if self.n > 1 else 0.0:.1f}ms\n"
            f"  RTT     : mean={statistics.mean(self.rtts):.1f}ms  "
            f"min={min(self.rtts):.1f}ms  max={max(self.rtts):.1f}ms"
        )


# ── Core ping logic ───────────────────────────────────────────────────────────

class SyncPinger:
    """
    Sends CMD_SYNC and waits for the SYNC:<millis> reply.
    Uses an asyncio.Event so the notification callback can unblock the sender.
    """

    def __init__(self, client: BleakClient):
        self._client   = client
        self._event    = asyncio.Event()
        self._reply_ms = None       # board millis from the reply
        self._buf      = ""         # accumulate partial text replies

    def handle_notification(self, _sender, raw: bytearray):
        """Registered as the BLE notification callback for the TX characteristic."""
        # Pass through binary quaternion packets silently (they keep arriving)
        if len(raw) == 16:
            return

        self._buf += raw.decode("utf-8", errors="replace")
        m = re.search(r"SYNC:(\d+)", self._buf)
        if m:
            self._reply_ms = int(m.group(1))
            self._buf      = ""
            self._event.set()

    async def ping(self) -> float | None:
        """
        Send one sync request and return the round-trip time in ms,
        or None if the reply didn't arrive within PING_TIMEOUT.
        """
        self._event.clear()
        self._reply_ms = None

        t0 = time.perf_counter()
        await self._client.write_gatt_char(UART_RX_UUID, CMD_SYNC, response=False)

        try:
            await asyncio.wait_for(self._event.wait(), timeout=PING_TIMEOUT)
        except asyncio.TimeoutError:
            return None

        rtt_ms = (time.perf_counter() - t0) * 1000.0
        return rtt_ms


# ── Console interaction (runs in executor to avoid blocking asyncio) ──────────

def ask(prompt: str) -> str:
    """Read a line from stdin without blocking the event loop."""
    return input(prompt).strip()


def print_separator(char="─", width=60):
    print(char * width)


def print_comparison_table(trials: list[TrialResult]):
    """Print a summary table comparing all completed trials."""
    print_separator("═")
    print("  COMPARISON TABLE — one-way latency (ms)")
    print_separator("─")
    header = f"  {'Distance':<12}  {'N':>4}  {'Lost':>4}  {'Mean':>7}  {'Min':>7}  {'Max':>7}  {'Stdev':>7}"
    print(header)
    print_separator("─")
    for t in trials:
        if not t.rtts:
            print(f"  {t.label:<12}  {'—':>4}  {'—':>4}  {'—':>7}  {'—':>7}  {'—':>7}  {'—':>7}")
            continue
        ow = t.one_way
        std = statistics.stdev(ow) if t.n > 1 else 0.0
        print(
            f"  {t.label:<12}  {t.n:>4}  {t.lost:>4}  "
            f"{statistics.mean(ow):>7.1f}  {min(ow):>7.1f}  "
            f"{max(ow):>7.1f}  {std:>7.1f}"
        )
    print_separator("═")


# ── Main async flow ───────────────────────────────────────────────────────────

async def run_trial(pinger: SyncPinger, label: str, n_pings: int) -> TrialResult:
    result = TrialResult(label=label)
    print(f"\n  Sending {n_pings} pings at distance: {label}")
    print_separator()

    for i in range(1, n_pings + 1):
        rtt = await pinger.ping()
        if rtt is None:
            result.lost += 1
            print(f"  Ping {i:>3}/{n_pings}  LOST (timeout {PING_TIMEOUT:.0f}s)")
        else:
            result.rtts.append(rtt)
            ow = rtt / 2.0
            bar = "█" * min(int(ow / 2), 40)   # 2ms per block, max 40 blocks
            print(f"  Ping {i:>3}/{n_pings}  RTT={rtt:>7.2f}ms  1-way≈{ow:>7.2f}ms  {bar}")
        await asyncio.sleep(0.05)   # 50ms gap between pings — avoids flooding

    print()
    print(f"  ── Trial '{label}' results ──")
    print(result.summary())
    return result


async def main(device_name: str, default_pings: int):
    loop = asyncio.get_event_loop()

    print_separator("═")
    print("  BLE LATENCY TESTER  —  SYNC round-trip method")
    print(f"  Target device : {device_name}")
    print(f"  Default pings : {default_pings}")
    print_separator("═")

    # ── Scan ─────────────────────────────────────────────────────────────────
    print(f"\n[SCAN] Looking for '{device_name}' (timeout {SCAN_TIMEOUT:.0f}s)...")
    device = await BleakScanner.find_device_by_name(device_name, timeout=SCAN_TIMEOUT)
    if device is None:
        print(f"[ERROR] '{device_name}' not found. Make sure the sensor is on and advertising.")
        sys.exit(1)

    print(f"[SCAN] Found at {device.address}")

    all_trials: list[TrialResult] = []

    # ── Connect ───────────────────────────────────────────────────────────────
    async with BleakClient(device, timeout=10.0) as client:
        print(f"[BLE]  Connected to {device_name}\n")

        pinger = SyncPinger(client)
        await client.start_notify(UART_TX_UUID, pinger.handle_notification)

        # Short warm-up ping — first BLE packet sometimes has elevated latency
        print("[INIT] Warm-up ping...")
        warmup = await pinger.ping()
        if warmup is not None:
            print(f"[INIT] Warm-up RTT = {warmup:.2f}ms  (discarded)\n")
        else:
            print("[INIT] Warm-up ping lost — continuing anyway\n")

        # ── Trial loop ────────────────────────────────────────────────────────
        while True:
            print_separator()
            print("  Enter trial details  (or 'done' to finish, 'quit' to exit)")
            print_separator()

            label = await loop.run_in_executor(
                None, ask, "  Distance label (e.g. 1m / 2m / 5m): "
            )
            if label.lower() in ("quit", "q", "exit"):
                break
            if label.lower() in ("done", "d", ""):
                break

            pings_str = await loop.run_in_executor(
                None, ask, f"  Pings for this trial [{default_pings}]: "
            )
            if pings_str == "":
                n_pings = default_pings
            elif pings_str.isdigit() and int(pings_str) > 0:
                n_pings = int(pings_str)
            else:
                print("  Invalid number — using default.")
                n_pings = default_pings

            confirm = await loop.run_in_executor(
                None, ask,
                f"\n  Ready to run {n_pings} pings at '{label}'.\n"
                f"  Position sensor now, then press Enter to start..."
            )
            if confirm.lower() in ("quit", "q"):
                break

            trial = await run_trial(pinger, label, n_pings)
            all_trials.append(trial)

            again = await loop.run_in_executor(
                None, ask, "\n  Run another trial? [Y/n]: "
            )
            if again.lower() in ("n", "no"):
                break

        await client.stop_notify(UART_TX_UUID)

    # ── Final summary ─────────────────────────────────────────────────────────
    if all_trials:
        print()
        print_comparison_table(all_trials)
    else:
        print("\n  No trials completed.")

    print("\n[DONE] Disconnected.\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BLE latency tester for IMU sensor nodes")
    parser.add_argument(
        "--device", default=DEFAULT_DEVICE,
        help=f"BLE device name to connect to (default: {DEFAULT_DEVICE})"
    )
    parser.add_argument(
        "--pings", type=int, default=DEFAULT_PINGS,
        help=f"Default number of pings per trial (default: {DEFAULT_PINGS})"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.device, args.pings))
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Exiting.")