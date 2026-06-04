"""
calibration.py
--------------
Handles neutral-pose (I-pose) calibration.

Changes from previous version:
  - Averages quaternion samples over 3 seconds instead of a single snapshot.
    This removes the effect of the sensor being in mid-vibration or mid-noise
    spike at the exact moment the button is pressed.
  - Recalibration is fully supported — capture() can be called any number of
    times. Each call replaces the previous references and resets the angle
    filters in JointAngles.
  - A new `is_capturing` flag lets the GUI show a "Capturing..." state during
    the 3-second averaging window.

Quaternion averaging method:
  We use the eigenvalue method (Markley et al. 2007): stack the quaternions
  as rows of a matrix M, compute M^T M, take the eigenvector corresponding
  to the largest eigenvalue. This is the provably correct way to average
  unit quaternions — simple component mean would drift off the unit sphere.

  Reference: Markley et al., "Averaging Quaternions", Journal of Guidance,
  Control, and Dynamics, 30(4), 2007.
"""

import time
import threading
import numpy as np

from ble.ble_state import AppState, SLOT_NAMES


# Duration of the averaging window in seconds
CAL_WINDOW_S = 3.0


def _average_quaternions(quats_wxyz: list) -> tuple:
    """
    Compute the average of a list of (w,x,y,z) unit quaternions using the
    eigenvalue method (Markley 2007).

    Handles quaternion double-cover: q and -q represent the same rotation,
    so we flip any quaternion that has a negative dot product with the first
    sample before accumulating, ensuring all samples are on the same hemisphere.

    Returns a single averaged (w,x,y,z) tuple.
    """
    if not quats_wxyz:
        return (1.0, 0.0, 0.0, 0.0)

    arr = np.array(quats_wxyz, dtype=float)   # shape (N, 4), order (w,x,y,z)

    # Flip quaternions that are on the opposite hemisphere to quats[0]
    ref = arr[0]
    for i in range(1, len(arr)):
        if np.dot(arr[i], ref) < 0.0:
            arr[i] = -arr[i]

    # Build the 4×4 accumulation matrix M^T M
    M = arr.T @ arr   # (4,4)

    # Largest eigenvector of M^T M is the average quaternion
    eigenvalues, eigenvectors = np.linalg.eigh(M)
    avg = eigenvectors[:, np.argmax(eigenvalues)]   # column vector (w,x,y,z)

    # Normalise (should already be unit, but guard against float drift)
    avg /= np.linalg.norm(avg)

    # Ensure positive w for canonical form
    if avg[0] < 0:
        avg = -avg

    return (float(avg[0]), float(avg[1]), float(avg[2]), float(avg[3]))


class Calibration:
    """
    Captures a 3-second average of all three sensor quaternions as the I-pose
    neutral reference. Can be called multiple times to recalibrate.

    Usage:
        cal = Calibration(state)
        cal.capture()           # non-blocking; runs in background thread
        cal.is_capturing()      # True during the 3-second window
        cal.is_calibrated()     # True once at least one capture completed
        cal.get_references()    # {"wrist": (w,x,y,z), ...}
    """

    def __init__(self, state: AppState):
        self._state      = state
        self._capturing  = False
        self._thread     = None

    def capture(self) -> bool:
        """
        Start a 3-second calibration capture in the background.
        Returns False immediately if sensors aren't all connected, or if a
        capture is already in progress.
        Returns True if the capture was successfully started.
        """
        with self._state.lock:
            if not self._state.all_connected():
                print("[CAL] Calibration failed — not all sensors connected.")
                return False

        if self._capturing:
            print("[CAL] Capture already in progress — ignoring.")
            return False

        self._capturing = True
        self._thread = threading.Thread(
            target=self._run_capture,
            daemon=True,
            name="CalibrationCapture"
        )
        self._thread.start()
        return True

    def is_capturing(self) -> bool:
        """True while a 3-second capture window is active."""
        return self._capturing

    def is_calibrated(self) -> bool:
        """True once at least one successful capture has completed."""
        with self._state.lock:
            return self._state.calibrated

    def get_references(self) -> dict:
        """
        Returns a copy of the stored reference quaternions.
        {"wrist": (w,x,y,z), "arm": (w,x,y,z), "chest": (w,x,y,z)}
        Returns empty dict if not yet calibrated.
        """
        with self._state.lock:
            return dict(self._state.calibration_quats)

    # ── Private ───────────────────────────────────────────────────────────────

    def _run_capture(self):
        """
        Background thread: collect CAL_WINDOW_S seconds of quaternion samples
        from all three sensors, then compute and store the averaged quaternion.
        """
        print(f"[CAL] Capturing for {CAL_WINDOW_S:.0f} seconds... hold I-pose.")

        buffers  = {name: [] for name in SLOT_NAMES}
        t_start  = time.monotonic()

        while time.monotonic() - t_start < CAL_WINDOW_S:
            with self._state.lock:
                for name in SLOT_NAMES:
                    q = self._state.slots[name].get_quaternion()
                    buffers[name].append(q)
            time.sleep(0.02)   # ~50 Hz polling — matches IMU output rate

        # Compute average quaternion per sensor
        refs = {
            name: _average_quaternions(buffers[name])
            for name in SLOT_NAMES
        }

        # Store in shared state — always creates a NEW dict so AngleProcessor
        # can detect recalibration by checking id() of calibration_quats
        with self._state.lock:
            self._state.calibration_quats = refs
            self._state.calibrated        = True

        self._capturing = False

        print("[CAL] Calibration complete (3-second average):")
        for name, q in refs.items():
            n_samples = len(buffers[name])
            print(f"  {name} ({n_samples} samples): "
                  f"w={q[0]:.4f} x={q[1]:.4f} y={q[2]:.4f} z={q[3]:.4f}")