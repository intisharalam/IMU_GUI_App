"""
ble_manager.py
--------------
Handles everything BLE-related:
  - Scanning for the three IMU devices by name
  - Connecting and auto-reconnecting if they drop
  - Receiving 16-byte quaternion packets and writing them to AppState
  - Sending haptic (0x01) and sync (0x53) commands back to a sensor

This runs entirely in its own asyncio event loop on a background thread.
The GUI thread never calls anything here directly — it just reads AppState.
"""

import asyncio
import struct
import time
import threading

from bleak import BleakClient, BleakScanner

from ble.ble_state import AppState, SLOT_NAMES


# ── BLE UUIDs for Nordic UART Service (NUS) ──────────────────────────────────
# TX = board sends data to laptop (we subscribe/notify)
# RX = laptop sends data to board (we write)
UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"

# Commands we can send to a sensor
CMD_HAPTIC = bytes([0x01])   # trigger haptic motor
CMD_SYNC   = bytes([0x53])   # request sync timestamp ('S')

# How long to wait before retrying after a failed connection
RECONNECT_WAIT_S = 5.0
SCAN_TIMEOUT_S   = 15.0

# The BLE device names the firmware advertises
DEVICE_NAMES = {
    "wrist": "IMU_WRIST",
    "arm":   "IMU_ARM",
    "chest": "IMU_CHEST",
}


class BLEManager:
    """
    Manages BLE connections for all three IMU sensors.

    Usage:
        manager = BLEManager(app_state)
        manager.start()          # call once at app startup
        manager.send_haptic("wrist")
        manager.send_haptic_all()
    """

    def __init__(self, state: AppState):
        self._state = state       # shared state object
        self._loop = None         # asyncio event loop (set when started)
        self._running = False

    def start(self):
        """
        Starts the BLE loop in a background daemon thread.
        Returns immediately — BLE runs in the background.
        """
        self._running = True
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()

    def stop(self):
        """Signals the BLE loop to shut down cleanly."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def send_haptic(self, slot_name: str):
        """Send a haptic trigger to one sensor."""
        self._schedule(self._send_cmd(slot_name, CMD_HAPTIC))

    def send_sync(self, slot_name: str):
        """Send a sync request to one sensor."""
        self._schedule(self._send_cmd(slot_name, CMD_SYNC))

    def send_haptic_all(self):
        """Send haptic trigger to all connected sensors."""
        for name in SLOT_NAMES:
            self.send_haptic(name)

    def send_sync_all(self):
        """Send sync request to all connected sensors."""
        for name in SLOT_NAMES:
            self.send_sync(name)

    # ── Internal methods ─────────────────────────────────────────────────────

    def _run_loop(self):
        """Creates the asyncio loop and launches one task per sensor."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # Launch a connection task for each sensor simultaneously
        for slot_name in SLOT_NAMES:
            device_name = DEVICE_NAMES[slot_name]
            self._loop.create_task(self._connect_task(slot_name, device_name))

        self._loop.run_forever()

    def _schedule(self, coro):
        """Safely schedules a coroutine from any thread onto the BLE loop."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _connect_task(self, slot_name: str, device_name: str):
        """
        Keeps trying to find and connect to one IMU sensor forever.
        If the connection drops, it waits and tries again.
        """
        while self._running:
            print(f"[BLE] Scanning for '{device_name}'...")

            # --- Scan ---
            try:
                device = await BleakScanner.find_device_by_name(
                    device_name, timeout=SCAN_TIMEOUT_S
                )
            except Exception as e:
                print(f"[BLE] Scan error for {device_name}: {e}")
                await asyncio.sleep(RECONNECT_WAIT_S)
                continue

            if device is None:
                print(f"[BLE] '{device_name}' not found. Retrying...")
                await asyncio.sleep(RECONNECT_WAIT_S)
                continue

            print(f"[BLE] Found {device_name} at {device.address}")

            # Store the address in shared state
            with self._state.lock:
                self._state.slots[slot_name].address = device.address

            # --- Connect ---
            try:
                async with BleakClient(device, timeout=10.0) as client:
                    # Try to negotiate a larger MTU for fewer fragmented packets
                    try:
                        await client.request_mtu(247)
                    except Exception:
                        pass  # not all platforms support this, that's fine

                    with self._state.lock:
                        self._state.slots[slot_name].client    = client
                        self._state.slots[slot_name].connected = True

                    print(f"[BLE] Connected to {device_name}")

                    # Subscribe to incoming data (quaternion packets)
                    handler = self._make_rx_handler(slot_name)
                    await client.start_notify(UART_TX_UUID, handler)

                    # Stay connected until the link drops
                    while client.is_connected and self._running:
                        await asyncio.sleep(0.3)

            except Exception as e:
                print(f"[BLE] Connection error for {device_name}: {e}")

            finally:
                with self._state.lock:
                    self._state.slots[slot_name].client    = None
                    self._state.slots[slot_name].connected = False

                print(f"[BLE] Disconnected from {device_name}. "
                      f"Retrying in {RECONNECT_WAIT_S:.0f}s...")
                await asyncio.sleep(RECONNECT_WAIT_S)

    def _make_rx_handler(self, slot_name: str):
        """
        Returns a notification handler function for one sensor slot.
        The handler is called by bleak every time a BLE packet arrives.
        """
        # Text buffer for SYNC replies (which arrive as partial text)
        sync_buf = {"data": ""}

        def handler(_, raw: bytearray):
            # --- 16-byte binary quaternion packet ---
            if len(raw) == 16:
                w, x, y, z = struct.unpack_from("<ffff", raw)
                t = time.monotonic()
                with self._state.lock:
                    self._state.slots[slot_name].update_quaternion(w, x, y, z, t)
                return

            # --- Text fallback: SYNC reply ---
            import re
            text = raw.decode("utf-8", errors="replace")
            sync_buf["data"] += text
            m = re.search(r"SYNC:(\d+)", sync_buf["data"])
            if m:
                board_ms = int(m.group(1))
                offset = time.monotonic() * 1000.0 - board_ms
                with self._state.lock:
                    self._state.slots[slot_name].sync_offset_ms = offset
                sync_buf["data"] = ""
                print(f"[BLE] {slot_name} SYNC offset = {offset:+.1f} ms")
            elif len(sync_buf["data"]) > 64:
                sync_buf["data"] = ""  # prevent unbounded growth

        return handler

    async def _send_cmd(self, slot_name: str, payload: bytes):
        """Writes a command byte to one sensor's RX characteristic."""
        with self._state.lock:
            client    = self._state.slots[slot_name].client
            connected = self._state.slots[slot_name].connected

        if not client or not connected:
            print(f"[BLE] {slot_name} not connected — command skipped.")
            return

        try:
            await client.write_gatt_char(UART_RX_UUID, payload, response=False)
            print(f"[BLE] Sent command {payload.hex()} to {slot_name}")

            # Flash the haptic indicator in the GUI for 0.5 s
            if payload == CMD_HAPTIC:
                with self._state.lock:
                    self._state.slots[slot_name].haptic_active = True
                await asyncio.sleep(0.5)
                with self._state.lock:
                    self._state.slots[slot_name].haptic_active = False

        except Exception as e:
            print(f"[BLE] Send error to {slot_name}: {e}")
