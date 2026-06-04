"""
calc/rep_detector.py  —  v4
----------------------------
Counts repetitions (or tracks a timed hold) from a streaming joint-angle
signal.  Driven entirely by an ExerciseDef so all thresholds and the
tracked angle are per-exercise — no hard-coded name lookups.

Rep mode  (ex.rep_angle is not None)
─────────────────────────────────────
A rep is counted when:
  1. The tracked angle rises above ex.rep_enter_deg  (arm raised)
  2. Then falls back below ex.rep_exit_deg           (arm returns to neutral)
Full-cycle counting avoids false positives from noise.
Minimum time between reps: MIN_REP_INTERVAL_S.

Hold mode  (ex.rep_angle is None)
──────────────────────────────────
No reps are counted.  update() always returns False.
The HUD uses hold_elapsed / ex.hold_duration_s to show a progress bar.

Outlier gate (both modes)
──────────────────────────
Any sample that jumps more than JUMP_THRESH_DEG from the previous is
discarded — same logic as joint_angles.py.

Usage
─────
    from calc.exercise_library import get_exercise
    det = RepDetector()
    det.set_exercise(get_exercise("FLEXION RAISE"))

    # each 50 Hz frame:
    rep_done = det.update(flex, abd, ext_rot, elbow, timestamp)
    if rep_done:
        trigger_haptic()

    # for hold exercises:
    progress = det.hold_progress   # 0.0 → 1.0
"""

import time

MIN_REP_INTERVAL_S = 0.8    # seconds — minimum time between consecutive reps
JUMP_THRESH_DEG    = 45.0   # degrees — discard outlier samples


class RepDetector:

    def __init__(self):
        self._ex         = None   # current ExerciseDef (or None)
        self._angle_key  = "flexion"
        self._enter      = 30.0
        self._exit       = 15.0
        self._above      = False
        self._reps       = 0
        self._last_rep_t = 0.0
        self._prev       = None
        self._hold_start = None   # monotonic time when hold began, or None

    # ── Configuration ─────────────────────────────────────────────────────────

    def set_exercise(self, ex) -> None:
        """
        Configure the detector for the given ExerciseDef.
        Accepts None gracefully (detector becomes a no-op until set again).
        """
        self._ex        = ex
        self._above     = False
        self._prev      = None
        self._hold_start = None

        if ex is None:
            self._angle_key = "flexion"
            self._enter     = 30.0
            self._exit      = 15.0
            print("[REP] No exercise set — detector idle.")
            return

        self._angle_key = ex.rep_angle or "flexion"  # hold exercises: key unused
        self._enter     = ex.rep_enter_deg
        self._exit      = ex.rep_exit_deg
        print(
            f"[REP] Exercise: '{ex.name}'  "
            f"mode={'HOLD' if ex.is_hold_exercise else 'REPS'}  "
            f"angle='{self._angle_key}'  "
            f"enter={self._enter}°  exit={self._exit}°"
        )

    def reset(self) -> None:
        """Reset counters and state (call at session start)."""
        self._reps       = 0
        self._above      = False
        self._prev       = None
        self._last_rep_t = 0.0
        self._hold_start = None

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, flexion: float, abduction: float,
               ext_rot: float, elbow: float, timestamp: float) -> bool:
        """
        Feed one frame of angles.
        Returns True only when a rep is completed (rep mode only).
        Always returns False in hold mode — use hold_progress instead.
        """
        if self._ex is None:
            return False

        # Hold-exercise mode
        if self._ex.is_hold_exercise:
            return False

        # Select the tracked angle
        angle_map = {
            "flexion":   flexion,
            "abduction": abduction,
            "ext_rot":   ext_rot,
            "elbow":     elbow,
        }
        val = abs(angle_map.get(self._angle_key, flexion))

        # Outlier gate
        if self._prev is not None and abs(val - self._prev) > JUMP_THRESH_DEG:
            return False
        self._prev = val

        # Hysteresis state machine
        rep_done = False

        if not self._above and val >= self._enter:
            self._above = True

        elif self._above and val < self._exit:
            self._above = False
            dt = timestamp - self._last_rep_t
            if dt >= MIN_REP_INTERVAL_S:
                self._reps      += 1
                self._last_rep_t = timestamp
                rep_done         = True
                print(
                    f"[REP] Rep #{self._reps} completed  "
                    f"angle={self._angle_key}  val={val:.1f}°  "
                    f"t={timestamp:.1f}s"
                )

        return rep_done

    # ── Hold-mode helpers ─────────────────────────────────────────────────────

    def start_hold(self) -> None:
        """Call when the user begins holding the stretch position."""
        self._hold_start = time.monotonic()

    def cancel_hold(self) -> None:
        """Call if the arm drops before the hold is complete."""
        self._hold_start = None

    @property
    def hold_elapsed(self) -> float:
        """Seconds since the hold started, or 0.0 if not holding."""
        if self._hold_start is None:
            return 0.0
        return time.monotonic() - self._hold_start

    @property
    def hold_progress(self) -> float:
        """0.0 → 1.0 fraction of the target hold duration completed."""
        if self._ex is None or not self._ex.is_hold_exercise:
            return 0.0
        target = self._ex.hold_duration_s
        if target <= 0:
            return 0.0
        return min(1.0, self.hold_elapsed / target)

    @property
    def hold_complete(self) -> bool:
        """True once the hold duration has been reached."""
        return self.hold_progress >= 1.0

    # ── Read-only properties ──────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return self._reps

    @property
    def is_hold_mode(self) -> bool:
        return self._ex is not None and self._ex.is_hold_exercise
