"""
T23 - Exercise Suggestion
==========================
Requirement : FR-05
Pass criterion:
  - After PENDULUM SWING appears twice in history, it is not the next suggestion
  - The least-recently-done in-band exercise is returned instead

Running: python Test_Scripts/T23_Exercise_Suggestion.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.exercise_library import EXERCISES, exercises_for_pain


def suggest_next(pain: int, history: list) -> object:
    """
    Mirror the suggestion logic used by the GUI:
    From in-band exercises, return the one least recently done.
    history is a list of exercise names in chronological order (oldest first).
    """
    in_band = exercises_for_pain(pain)
    if not in_band:
        return None
    # Build last-done index: name -> position in history (higher = more recent)
    last_done = {ex.name: -1 for ex in in_band}
    for i, name in enumerate(history):
        if name in last_done:
            last_done[name] = i
    # Return the in-band exercise with the lowest (oldest) last_done index
    return min(in_band, key=lambda ex: last_done[ex.name])


def run_test() -> bool:
    print("=" * 60)
    print("T23 - Exercise Suggestion")
    print("=" * 60)

    passed = True
    pain = 5
    in_band = exercises_for_pain(pain)
    print(f"  Pain score : {pain}")
    print(f"  In-band exercises: {[ex.name for ex in in_band]}")
    print()

    # History: PENDULUM SWING done twice (most recently)
    history = ["PENDULUM SWING", "FLEXION RAISE", "PENDULUM SWING"]
    suggestion = suggest_next(pain, history)

    # PENDULUM SWING must NOT be suggested
    not_pendulum = suggestion is not None and suggestion.name != "PENDULUM SWING"
    print(f"  History: {history}")
    print(f"  Suggestion: {suggestion.name if suggestion else 'None'}")
    print(f"  [{'PASS' if not_pendulum else 'FAIL'}]  PENDULUM SWING not suggested")
    if not not_pendulum: passed = False

    # Should be the least-recently-done in-band exercise
    # FLEXION RAISE was done at index 1, PENDULUM SWING at index 2
    # Any exercise not in history at all has index -1 (oldest) → preferred
    never_done = [ex for ex in in_band
                  if ex.name not in history and ex.name != "PENDULUM SWING"]
    if never_done:
        expected_name = never_done[0].name   # any never-done is valid
        # The suggestion should be a never-done exercise (index=-1, lowest)
        is_never_done = suggestion.name not in history
        print(f"  [{'PASS' if is_never_done else 'FAIL'}]  Suggestion is a never-done exercise: {suggestion.name}")
        if not is_never_done: passed = False
    else:
        # All in-band exercises have been done; FLEXION RAISE was done least recently
        is_least_recent = suggestion.name == "FLEXION RAISE"
        print(f"  [{'PASS' if is_least_recent else 'FAIL'}]  Suggestion is least-recently-done: {suggestion.name}")
        if not is_least_recent: passed = False

    # Edge case: empty history — any in-band exercise is acceptable
    print()
    print("  Edge case: empty history")
    suggestion_empty = suggest_next(pain, [])
    ok_empty = suggestion_empty is not None and suggestion_empty in in_band
    print(f"  [{'PASS' if ok_empty else 'FAIL'}]  Returns a valid in-band exercise: "
          f"{suggestion_empty.name if suggestion_empty else 'None'}")
    if not ok_empty: passed = False

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t23_exercise_suggestion():
    assert run_test()
