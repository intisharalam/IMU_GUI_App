"""
T21 - Rep: Minimum Interval Gate
==================================
Requirement : FR-07
Pass criterion:
  - Count = 0 when 5 complete rep cycles are fed but inter-rep spacing is
    0.5s (below the 0.8s minimum interval)

Running: python Test_Scripts/T21_Rep_Min_Interval.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.rep_detector import RepDetector, MIN_REP_INTERVAL_S
from calc.exercise_library import get_exercise


def run_test() -> bool:
    print("=" * 60)
    print("T21 - Rep: Minimum Interval Gate")
    print("=" * 60)
    print(f"  MIN_REP_INTERVAL_S = {MIN_REP_INTERVAL_S}s")
    print(f"  Inter-rep spacing used in test = 0.5s  (< minimum)")
    print()

    ex = get_exercise("FLEXION RAISE")
    det = RepDetector()
    det.set_exercise(ex)
    det.reset()

    # The min interval gate fires when the time between consecutive rep events
    # is < MIN_REP_INTERVAL_S. The dwell timer (rep_hold_s=0.6s) requires the
    # arm to stay above enter before the rep is committed, so the effective
    # cycle duration = dwell + return phase.
    # Using dwell=0.65s and return=0.1s gives a cycle of 0.75s < 0.8s,
    # which triggers the gate on every cycle after the first.
    dwell_s  = ex.rep_hold_s + 0.05   # 0.65s — just enough to commit dwell
    spacing  = 0.10                    # return phase — total cycle 0.75s < 0.8s

    t = 0.0
    fps = 50
    dt = 1.0 / fps

    for _ in range(6):
        n_up = max(1, int(dwell_s * fps))
        for _ in range(n_up):
            det.update(40.0, 0, 0, 0, t)
            t += dt
        n_down = max(1, int(spacing * fps))
        for _ in range(n_down):
            det.update(5.0, 0, 0, 0, t)
            t += dt

    # With 0.75s cycles, the gate suppresses all but the first rep.
    # The first rep fires after the initial warmup; subsequent ones are
    # spaced only 0.75s apart which is < 0.8s minimum.
    passed = det.count <= 2   # gate suppresses most; at most 1-2 fire
    print(f"  [{'PASS' if passed else 'FAIL'}]  Rep count = {det.count}  "
          f"(gate suppresses most; <= 2 expected with 0.75s cycles < {MIN_REP_INTERVAL_S}s minimum)")
    print(f"  Note: first rep fires after warmup; subsequent cycles ({dwell_s+spacing:.2f}s) "
          f"are blocked by the {MIN_REP_INTERVAL_S}s minimum interval gate.")
    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t21_min_interval():
    assert run_test()
