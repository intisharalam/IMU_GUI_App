"""
calibration.py
--------------
Handles the neutral-pose (I-pose) calibration step.

When the user clicks Calibrate:
  1. We snapshot the current quaternion from each sensor.
  2. These become the "reference" quaternions for the zero angle position.
  3. All future joint angles are computed RELATIVE to these references.

Why calibration is needed:
  - Each sensor is strapped on at a slightly different rotation.
  - Without calibration, even a perfectly still arm would show non-zero angles.
  - After calibration, the I-pose reads 0° for all joints.

---------------------------------------------------------
AppState structure (for reference):
-----------------------------------------------------------
state.lock                  -> threading.Lock - acquire before any read/write
state.calibrated            -> bool - True once capture() has succeeded
state.calibration_quats     -> dict - neutral-pose quaternion per sensor:
                                {
                                    "wrist": (w, x, y, z),
                                    "arm":   (w, x, y, z),
                                    "chest": (w, x, y, z),
                                }
state.slots["wrist"]        -> IMUSlot - live data for one sensor
state.slots["arm"]          -> IMUSlot
state.slots["chest"]        -> IMUSlot

IMUSlot fields used here:
    .connected                -> bool   - is the sensor currently connected?
    .get_quaternion()         -> tuple  - latest (w, x, y, z) from the sensor
----------------------------------------------------------
"""

from ble.ble_state import AppState, SLOT_NAMES


class Calibration:
    """
    Captures and stores the neutral-pose reference quaternions.

    Usage:
        cal = Calibration(state)
        success = cal.capture()   # call when user clicks Calibrate
        if success:
            ref = cal.get_references()  # use in JointAngles
    """

    def __init__(self, state: AppState):
        self._state = state

    def capture(self) -> bool:
        """
        Snapshots the current quaternion of all three sensors.
        Returns True if all three were connected and calibration succeeded.
        Returns False if any sensor was missing.
        """
        with self._state.lock:
            # Only calibrate if all sensors are live
            if not self._state.all_connected():
                print("[CAL] Calibration failed — not all sensors connected.")
                return False

            # Grab the current quaternion from each sensor
            refs = {}
            for name in SLOT_NAMES:
                refs[name] = self._state.slots[name].get_quaternion()

            # Store in shared state
            self._state.calibration_quats = refs
            self._state.calibrated = True

        print("[CAL] Calibration captured:")
        for name, q in refs.items():
            print(f"  {name}: w={q[0]:.4f} x={q[1]:.4f} y={q[2]:.4f} z={q[3]:.4f}")

        return True

    def get_references(self) -> dict:
        """
        Returns the stored reference quaternions as a dict.
        Example: {"wrist": (w, x, y, z), "arm": ..., "chest": ...}
        Returns an empty dict if calibration hasn't been done yet.
        """

        # We are sending a copy to prevent read and write at the same time by BLE & GUI
        with self._state.lock:
            return dict(self._state.calibration_quats)

    def is_calibrated(self) -> bool:
        """Returns True if calibration has been completed."""
        with self._state.lock:
            return self._state.calibrated
