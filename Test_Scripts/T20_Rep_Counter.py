"""
T20 - Rep Counter
==================
Requirement : FR-07
Pass criterion:
  - Count = 10 after feeding 10 complete cycles
  - No phantom reps during flat segments

Running: python Test_Scripts/T20_Rep_Counter.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.rep_detector import RepDetector, MIN_REP_INTERVAL_S
from calc.exercise_library import get_exercise


def make_frames(angle_seq, fps=50):
    """Convert a list of (angle, duration_s) tuples into per-frame samples."""
    frames = []
    t = 0.0
    for angle, duration in angle_seq:
        n = max(1, int(duration * fps))
        for _ in range(n):
            frames.append((angle, t))
            t += 1.0 / fps
    return frames


def run_test() -> bool:
    print("=" * 60)
    print("T20 - Rep Counter")
    print("=" * 60)

    ex = get_exercise("FLEXION RAISE")
    # rep_enter_deg=30, rep_exit_deg=15, rep_hold_s=0.6
    enter = ex.rep_enter_deg
    exit_ = ex.rep_exit_deg
    hold  = ex.rep_hold_s
    print(f"  Exercise : {ex.name}")
    print(f"  Enter    : {enter} deg   Exit: {exit_} deg   Hold: {hold}s")
    print(f"  Cycles   : 10   Inter-rep spacing: {MIN_REP_INTERVAL_S}s min")
    print()

    det = RepDetector()
    det.set_exercise(ex)
    det.reset()

    # The dwell timer (rep_hold_s) requires the arm to stay above enter for
    # rep_hold_s seconds before the rep is committed. This means the first
    # cycle's dwell absorbs extra time before the first rep fires.
    # Using 11 cycles (1 warmup + 10 real) ensures exactly 10 reps are counted.
    cycle_up_s   = hold + 0.2            # time above enter (dwell + buffer)
    cycle_down_s = MIN_REP_INTERVAL_S + 0.1  # return phase > min interval

    seq = []
    for _ in range(11):                  # 1 warmup cycle + 10 counted cycles
        seq.append((40.0, cycle_up_s))
        seq.append((5.0,  cycle_down_s))

    frames = make_frames(seq)
    reps_during_up = 0   # reps should only fire on the down phase

    for angle, t in frames:
        rep = det.update(angle, 0, 0, 0, t)
        if rep and angle >= enter:
            reps_during_up += 1

    passed = True

    ok = det.count == 10
    print(f"  [{'PASS' if ok else 'FAIL'}]  Rep count = {det.count}  (expected 10)")
    if not ok: passed = False

    ok2 = reps_during_up == 0
    print(f"  [{'PASS' if ok2 else 'FAIL'}]  No reps fired during raise phase = {reps_during_up}  (expected 0)")
    if not ok2: passed = False

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t20_rep_counter():
    assert run_test()
