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

MIN_REP_INTERVAL_S  = 0.8    # seconds — minimum time between consecutive reps
JUMP_THRESH_DEG     = 45.0   # degrees — discard outlier samples
HAPTIC_LOCKOUT_S    = 1.2    # seconds — freeze rep detection after a haptic fires
                              # (vibration physically shakes IMU causing false reps)
TRUNK_LEAN_LIMIT_DEG = 20.0  # degrees — block rep if trunk is leaning
                              # 10° was too tight; normal movement exceeds it
DWELL_TOLERANCE_DEG  = 3.0   # degrees below enter threshold before dwell resets
                              # prevents tiny wobbles at threshold killing the timer


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
        self._haptic_t   = 0.0   # time of last haptic; rep detection frozen for HAPTIC_LOCKOUT_S
        self._above_since = None  # monotonic time when angle first crossed rep_enter_deg
        self._rep_hold_s  = 0.3   # seconds angle must stay above enter before _above flips True

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
        self._hold_duration_override = None   # cleared on each new exercise

        if ex is None:
            self._angle_key = "flexion"
            self._enter     = 30.0
            self._exit      = 15.0
            print("[REP] No exercise set — detector idle.")
            return

        self._angle_key  = ex.rep_angle or "flexion"  # hold exercises: key unused
        self._enter      = ex.rep_enter_deg
        self._exit       = ex.rep_exit_deg
        self._rep_hold_s = getattr(ex, "rep_hold_s", 0.3)
        print(
            f"[REP] Exercise: '{ex.name}'  "
            f"mode={'HOLD' if ex.is_hold_exercise else 'REPS'}  "
            f"angle='{self._angle_key}'  "
            f"enter={self._enter}°  exit={self._exit}°"
        )

    def set_hold_duration(self, seconds: float) -> None:
        """Override the hold target duration (user-adjusted value from exercise panel)."""
        self._hold_duration_override = max(1.0, float(seconds))
        print(f"[REP] Hold duration override → {self._hold_duration_override:.0f}s")

    def reset(self) -> None:
        """Reset counters and state (call at session start)."""
        self._reps        = 0
        self._above       = False
        self._prev        = None
        self._last_rep_t  = 0.0
        self._hold_start  = None
        self._hold_wait_for_drop = False
        self._haptic_t    = 0.0
        self._above_since = None

    def notify_haptic(self, timestamp: float) -> None:
        """Call whenever a haptic fires so rep detection is frozen briefly."""
        self._haptic_t = timestamp

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, flexion: float, abduction: float,
               ext_rot: float, elbow: float, timestamp: float,
               trunk_lean: float = 0.0) -> bool:
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

        # Haptic lockout — vibration physically disturbs the IMU.
        # Also reset _above so a vibration mid-rep doesn't produce a phantom
        # count when the arm returns to neutral after the lockout clears.
        if timestamp - self._haptic_t < HAPTIC_LOCKOUT_S:
            self._above       = False
            self._above_since = None
            self._prev        = None
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

        # Trunk lean gate — postural sway mimics shoulder movement
        if trunk_lean > TRUNK_LEAN_LIMIT_DEG:
            self._above       = False
            self._above_since = None
            return False

        # ── State machine ─────────────────────────────────────────────────────
        #
        #  Phase 1 — WAITING (arm below enter threshold)
        #      val >= enter  →  start dwell timer, move to DWELLING
        #
        #  Phase 2 — DWELLING (arm above enter, waiting for hold)
        #      val < (enter - tolerance)  →  wobble reset, back to WAITING
        #      dwell elapsed >= rep_hold_s  →  commit, move to ABOVE
        #
        #  Phase 3 — ABOVE (arm confirmed raised)
        #      val < exit  →  rep complete, back to WAITING
        #
        rep_done = False

        if not self._above:
            if val >= self._enter:
                # Arm is above threshold — start or continue dwell timer
                if self._above_since is None:
                    self._above_since = timestamp
                elif timestamp - self._above_since >= self._rep_hold_s:
                    # Held long enough — commit
                    self._above       = True
                    self._above_since = None
            elif val < self._enter - DWELL_TOLERANCE_DEG:
                # Dropped clearly below threshold — reset dwell
                # (tolerance prevents wobble at boundary from killing timer)
                self._above_since = None
            # else: in the tolerance band — hold dwell timer, do nothing

        else:
            # Arm is confirmed raised — wait for it to return to neutral
            if val < self._exit:
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
        if getattr(self, "_hold_wait_for_drop", False):
            return   # don't restart until arm has dropped
        self._hold_start = time.monotonic()

    def cancel_hold(self, completed: bool = False) -> None:
        """Cancel the current hold timer.
        Pass completed=True when the hold finished successfully — this sets a
        'wait for drop' flag so the next hold only starts after the arm lowers."""
        self._hold_start = None
        self._hold_wait_for_drop = completed

    def notify_arm_dropped(self) -> None:
        """Call when the arm goes below the activity threshold to clear the drop guard."""
        self._hold_wait_for_drop = False

    @property
    def hold_elapsed(self) -> float:
        """Seconds since the hold started, or 0.0 if not holding."""
        if self._hold_start is None:
            return 0.0
        return time.monotonic() - self._hold_start

    @property
    def hold_target_s(self) -> float:
        """Effective hold target — user override if set, else ExerciseDef default."""
        override = getattr(self, "_hold_duration_override", None)
        if override is not None:
            return override
        if self._ex is not None:
            return self._ex.hold_duration_s
        return 1.0

    @property
    def hold_progress(self) -> float:
        """0.0 → 1.0 fraction of the target hold duration completed."""
        if self._ex is None or not self._ex.is_hold_exercise:
            return 0.0
        target = self.hold_target_s
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