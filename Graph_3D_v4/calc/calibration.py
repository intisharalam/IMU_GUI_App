"""
calibration.py  —  v3
---------------------
3-second averaged calibration using the quaternion eigenvalue method.
Supports unlimited recalibration. Non-blocking (background thread).
"""

import time, threading, numpy as np
from ble.ble_state import AppState, SLOT_NAMES

CAL_WINDOW_S = 3.0

def _average_quaternions(quats_wxyz: list) -> tuple:
    if not quats_wxyz:
        return (1.0, 0.0, 0.0, 0.0)
    arr = np.array(quats_wxyz, dtype=float)
    ref = arr[0]
    for i in range(1, len(arr)):
        if np.dot(arr[i], ref) < 0.0:
            arr[i] = -arr[i]
    M = arr.T @ arr
    eigenvalues, eigenvectors = np.linalg.eigh(M)
    avg = eigenvectors[:, np.argmax(eigenvalues)]
    avg /= np.linalg.norm(avg)
    if avg[0] < 0:
        avg = -avg
    return tuple(float(v) for v in avg)


class Calibration:
    def __init__(self, state: AppState):
        self._state     = state
        self._capturing       = False
        self._thread          = None
        self._t_end           = None   # set during capture, read by GUI

    def capture(self) -> bool:
        with self._state.lock:
            if not self._state.all_connected():
                print("[CAL] Not all sensors connected.")
                return False
        if self._capturing:
            print("[CAL] Already capturing.")
            return False
        self._capturing = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="CalThread")
        self._thread.start()
        return True

    def is_capturing(self) -> bool:
        return self._capturing

    def time_remaining_s(self) -> float:
        """Seconds left in the current capture window. 0.0 if not capturing."""
        if not self._capturing or self._t_end is None:
            return 0.0
        return max(0.0, self._t_end - time.monotonic())

    def is_calibrated(self) -> bool:
        with self._state.lock:
            return self._state.calibrated

    def get_references(self) -> dict:
        with self._state.lock:
            return dict(self._state.calibration_quats)

    def _run(self):
        print(f"[CAL] Capturing {CAL_WINDOW_S:.0f}s average — hold I-pose.")
        buffers = {n: [] for n in SLOT_NAMES}
        self._t_end = time.monotonic() + CAL_WINDOW_S
        t_end = self._t_end
        while time.monotonic() < t_end:
            with self._state.lock:
                for n in SLOT_NAMES:
                    buffers[n].append(self._state.slots[n].get_quaternion())
            time.sleep(0.02)
        refs = {n: _average_quaternions(buffers[n]) for n in SLOT_NAMES}
        with self._state.lock:
            self._state.calibration_quats = refs   # new dict → triggers recal detection
            self._state.calibrated = True
        self._capturing = False
        self._t_end = None
        print("[CAL] Done.")
        for n, q in refs.items():
            print(f"  {n}: w={q[0]:.4f} x={q[1]:.4f} y={q[2]:.4f} z={q[3]:.4f}")