"""
rep_detector.py  —  v3
----------------------
Counts repetitions from a streaming joint angle signal.

Algorithm: smoothed peak detection.
- Maintains a short ring buffer of the primary angle.
- A rep is counted when:
    1. The angle rises above REP_ENTER_THRESH  (arm is raised)
    2. Then falls back below REP_EXIT_THRESH   (arm returns to neutral)
  This full-cycle counting avoids false positives from noise.
- Minimum time between reps: MIN_REP_INTERVAL_S (prevents double-counting).

Euler angle jump protection:
  Any sample that differs from the previous by more than JUMP_THRESH_DEG
  is discarded (same logic as joint_angles.py outlier gate).

Usage:
    det = RepDetector(enter_thresh=30.0, exit_thresh=15.0)
    det.set_exercise("shoulder_flexion")   # which angle to track
    rep_completed = det.update(flex, abd, ext_rot, elbow, timestamp)
    if rep_completed:
        trigger_haptic()
"""

import collections, time

REP_ENTER_THRESH   = 30.0   # degrees — arm must exceed this to start a rep
REP_EXIT_THRESH    = 15.0   # degrees — arm must drop below this to complete a rep
MIN_REP_INTERVAL_S = 0.8    # seconds — ignore reps faster than this
JUMP_THRESH_DEG    = 45.0   # degrees — discard outlier samples


# Map exercise name → which angle drives rep detection
EXERCISE_ANGLE = {
    "Shoulder Flexion Raise": "flexion",
    "Shoulder Abduction":     "abduction",
    "External Rotation":      "ext_rot",
    "Pendulum Swing":         "flexion",
    "Elbow Curl":             "elbow",
}


class RepDetector:
    def __init__(self, enter_thresh=REP_ENTER_THRESH, exit_thresh=REP_EXIT_THRESH):
        self._enter  = enter_thresh
        self._exit   = exit_thresh
        self._above  = False    # True when angle is currently above enter_thresh
        self._reps   = 0
        self._last_t = 0.0
        self._prev   = None
        self._angle_key = "flexion"

    def set_exercise(self, exercise_name: str):
        self._angle_key = EXERCISE_ANGLE.get(exercise_name, "flexion")
        self._above = False
        self._prev  = None
        print(f"[REP] Tracking '{self._angle_key}' for '{exercise_name}'")

    def reset(self):
        self._reps  = 0
        self._above = False
        self._prev  = None
        self._last_t = 0.0

    def update(self, flexion: float, abduction: float,
               ext_rot: float, elbow: float, timestamp: float) -> bool:
        """
        Feed one frame of angles. Returns True if a rep was just completed.
        """
        angle_map = {
            "flexion":   flexion,
            "abduction": abduction,
            "ext_rot":   ext_rot,
            "elbow":     elbow,
        }
        val = abs(angle_map.get(self._angle_key, flexion))

        # Discard outlier
        if self._prev is not None and abs(val - self._prev) > JUMP_THRESH_DEG:
            return False
        self._prev = val

        rep_done = False

        if not self._above and val >= self._enter:
            self._above = True

        elif self._above and val < self._exit:
            self._above = False
            dt = timestamp - self._last_t
            if dt >= MIN_REP_INTERVAL_S:
                self._reps  += 1
                self._last_t = timestamp
                rep_done = True
                print(f"[REP] Rep #{self._reps} at t={timestamp:.1f}s  ({self._angle_key}={val:.1f}°)")

        return rep_done

    @property
    def count(self):
        return self._reps
