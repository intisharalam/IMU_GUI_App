"""
imu_test.py
───────────
BLE quaternion stream test — 3x XIAO nRF52840 (IMU_WRIST, IMU_ARM, IMU_CHEST)

Firmware sends 16-byte binary packets: w,x,y,z as 4x little-endian float32.
SYNC reply is text: "SYNC:<millis>" — handled separately.

Commands:
  h <wrist|arm|chest|all>   — haptic trigger  (0x01)
  s <wrist|arm|chest|all>   — sync request    (0x53)
  status                    — connection table
  quit                      — exit
"""

import asyncio
import struct
import threading
import time

from bleak import BleakClient, BleakScanner

# ── Config ────────────────────────────────────────────────────────────────────
UART_TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"   # board -> laptop (notify)
UART_RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"   # laptop -> board (write)

DEVICES = {
    "wrist": "IMU_WRIST",
    "arm":   "IMU_ARM",
    "chest": "IMU_CHEST",
}

HAPTIC      = bytes([0x01])
SYNC        = bytes([0x53])

SCAN_TO     = 15.0
RECONN_WAIT =  5.0
THRESHOLD   =  0.005   # min change in any component to print (~0.5 deg)

# ── Shared state ──────────────────────────────────────────────────────────────
_lock  = threading.Lock()
_state = {
    k: {
        "client":    None,
        "connected": False,
        "address":   None,
        "sync_buf":  "",       # text accumulator for SYNC reply only
        "count":     0,
        "sync_ms":   None,
        "last":      None,     # (w,x,y,z) of last printed packet
    }
    for k in DEVICES
}

_loop: asyncio.AbstractEventLoop = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def ts():
    return time.strftime("%H:%M:%S")

def get(key, field):
    with _lock:
        return _state[key][field]

def put(key, field, val):
    with _lock:
        _state[key][field] = val

# ── RX handler ────────────────────────────────────────────────────────────────

def make_handler(key: str):
    def handler(_, data: bytearray):

        # ── 16-byte binary quaternion packet ──────────────────────────────────
        if len(data) == 16:
            vals = struct.unpack_from('<ffff', data)   # (w, x, y, z)

            with _lock:
                _state[key]["count"] += 1
                count = _state[key]["count"]
                prev  = _state[key]["last"]
                changed = (
                    prev is None or
                    any(abs(vals[i] - prev[i]) >= THRESHOLD for i in range(4))
                )
                if changed:
                    _state[key]["last"] = vals

            if changed:
                w, x, y, z = vals
                print(f"[{ts()}] [{key.upper():6}] #{count:>6}  "
                      f"w={w:+.4f}  x={x:+.4f}  y={y:+.4f}  z={z:+.4f}")
            return

        # ── Text fallthrough — handle SYNC reply ──────────────────────────────
        # SYNC is sent only on demand so no delimiter stripping issue in practice.
        # We still buffer until we see the colon+digits pattern, just in case
        # the reply arrives fragmented.
        text = data.decode("utf-8", errors="replace")
        with _lock:
            _state[key]["sync_buf"] += text
            buf = _state[key]["sync_buf"]

        # Look for "SYNC:<digits>" anywhere in the buffer
        import re
        m = re.search(r'SYNC:(\d+)', buf)
        if m:
            board_ms = int(m.group(1))
            offset   = time.monotonic() * 1000.0 - board_ms
            put(key, "sync_ms", offset)
            put(key, "sync_buf", "")   # clear after successful parse
            print(f"\n[{ts()}] [{key.upper():6}] "
                  f"SYNC  board={board_ms} ms   offset={offset:+.1f} ms\n")
        elif len(buf) > 64:
            # Prevent unbounded growth if SYNC never completes
            put(key, "sync_buf", "")

    return handler

# ── BLE task ──────────────────────────────────────────────────────────────────

async def imu_task(key: str, name: str):
    while True:
        print(f"[{ts()}] [{key.upper():6}] Scanning for '{name}'...")
        try:
            device = await BleakScanner.find_device_by_name(name, timeout=SCAN_TO)
        except Exception as e:
            print(f"[{ts()}] [{key.upper():6}] Scan error: {e}")
            await asyncio.sleep(RECONN_WAIT)
            continue

        if device is None:
            print(f"[{ts()}] [{key.upper():6}] '{name}' not found. Retrying...")
            await asyncio.sleep(RECONN_WAIT)
            continue

        print(f"[{ts()}] [{key.upper():6}] Found {name} at {device.address}")
        put(key, "address", device.address)

        try:
            async with BleakClient(device, timeout=10.0) as client:
                try:
                    await client.request_mtu(247)
                except Exception:
                    pass

                put(key, "client",    client)
                put(key, "connected", True)
                print(f"[{ts()}] [{key.upper():6}] Connected   MTU={client.mtu_size} B")

                await client.start_notify(UART_TX, make_handler(key))

                while client.is_connected:
                    await asyncio.sleep(0.3)

        except Exception as e:
            print(f"[{ts()}] [{key.upper():6}] Connection error: {e}")
        finally:
            put(key, "client",    None)
            put(key, "connected", False)
            print(f"[{ts()}] [{key.upper():6}] Disconnected. "
                  f"Retrying in {RECONN_WAIT:.0f} s...")
            await asyncio.sleep(RECONN_WAIT)

# ── Send ──────────────────────────────────────────────────────────────────────

async def _send(key, payload, label):
    client    = get(key, "client")
    connected = get(key, "connected")
    if not client or not connected:
        print(f"  [{key.upper()}] not connected — skipped")
        return
    try:
        await client.write_gatt_char(UART_RX, payload, response=False)
        print(f"[{ts()}] [{key.upper():6}] sent {label}")
    except Exception as e:
        print(f"[{ts()}] [{key.upper():6}] Send error: {e}")

def send(key, payload, label):
    asyncio.run_coroutine_threadsafe(_send(key, payload, label), _loop)

def send_all(payload, label):
    for k in DEVICES:
        send(k, payload, label)

# ── Console ───────────────────────────────────────────────────────────────────

def console():
    print("\nReady.  Commands:")
    print("  h <wrist|arm|chest|all>   haptic trigger")
    print("  s <wrist|arm|chest|all>   sync request")
    print("  status                    connection table")
    print("  quit\n")

    while True:
        try:
            raw = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            raw = "quit"

        if not raw:
            continue

        parts = raw.split()
        cmd   = parts[0]

        if cmd == "quit":
            print("Shutting down...")
            _loop.call_soon_threadsafe(_loop.stop)
            break

        elif cmd == "status":
            print(f"\n  {'IMU':<8} {'Conn':<6} {'Packets':<10} {'Sync offset':<16} Address")
            print("  " + "-" * 60)
            with _lock:
                for k in DEVICES:
                    s    = _state[k]
                    conn = "YES" if s["connected"] else "NO"
                    sync = f"{s['sync_ms']:+.1f} ms" if s["sync_ms"] is not None else "---"
                    addr = s["address"] or "---"
                    print(f"  {k:<8} {conn:<6} {s['count']:<10} {sync:<16} {addr}")
            print()

        elif cmd in ("h", "s") and len(parts) == 2:
            payload = HAPTIC if cmd == "h" else SYNC
            label   = "HAPTIC (0x01)" if cmd == "h" else "SYNC (0x53)"
            target  = parts[1]
            if target == "all":
                send_all(payload, label)
            elif target in DEVICES:
                send(target, payload, label)
            else:
                print(f"  Unknown target '{target}'. Use: {' | '.join(DEVICES)} | all")
        else:
            print("  Unknown command. Try: h/s <wrist|arm|chest|all> | status | quit")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _loop
    _loop = asyncio.new_event_loop()

    for key, name in DEVICES.items():
        _loop.create_task(imu_task(key, name))

    t = threading.Thread(target=_loop.run_forever, daemon=True)
    t.start()

    print("=" * 55)
    print("  IMU BLE Test  .  3x XIAO nRF52840  .  quaternion")
    print("  Connecting to: IMU_WRIST  IMU_ARM  IMU_CHEST")
    print("=" * 55)

    console()
    t.join(timeout=3.0)
    print("Done.")

if __name__ == "__main__":
    main()