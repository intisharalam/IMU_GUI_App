"""
T22 - Pain Filter
==================
Requirements : FR-04, FR-05
Pass criterion:
  - Pain=0 : all exercises returned
  - Pain=8 : only in-band exercises returned (min_pain<=8<=max_pain)
  - No out-of-band exercises at any pain score

Running: python Test_Scripts/T22_Pain_Filter.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.exercise_library import EXERCISES, exercises_for_pain


def run_test() -> bool:
    print("=" * 60)
    print("T22 - Pain Filter")
    print("=" * 60)
    print(f"  Total exercises in library: {len(EXERCISES)}")
    print()

    passed = True

    # Pain=0: all exercises where min_pain<=0<=max_pain
    result_0 = exercises_for_pain(0)
    all_valid_0 = [ex for ex in EXERCISES if ex.min_pain <= 0 <= ex.max_pain]
    ok = set(ex.name for ex in result_0) == set(ex.name for ex in all_valid_0)
    out_of_band_0 = [ex for ex in result_0 if not (ex.min_pain <= 0 <= ex.max_pain)]
    print(f"  [{'PASS' if ok else 'FAIL'}]  Pain=0: returned {len(result_0)} exercises  "
          f"(expected {len(all_valid_0)})")
    if out_of_band_0:
        print(f"    Out-of-band: {[ex.name for ex in out_of_band_0]}")
        passed = False
    if not ok: passed = False

    # Pain=8: only in-band
    result_8 = exercises_for_pain(8)
    all_valid_8 = [ex for ex in EXERCISES if ex.min_pain <= 8 <= ex.max_pain]
    out_of_band_8 = [ex for ex in result_8 if not (ex.min_pain <= 8 <= ex.max_pain)]
    missing_8 = [ex for ex in all_valid_8 if ex not in result_8]
    ok8a = len(out_of_band_8) == 0
    ok8b = len(missing_8) == 0
    print(f"  [{'PASS' if ok8a and ok8b else 'FAIL'}]  Pain=8: returned {len(result_8)} exercises  "
          f"(expected {len(all_valid_8)})")
    if out_of_band_8:
        print(f"    Out-of-band included: {[ex.name for ex in out_of_band_8]}")
        passed = False
    if missing_8:
        print(f"    In-band missing: {[ex.name for ex in missing_8]}")
        passed = False

    # No out-of-band at any score 0-10
    print(f"  Checking all pain scores 0-10 for out-of-band results...")
    any_out = False
    for pain in range(11):
        result = exercises_for_pain(pain)
        bad = [ex for ex in result if not (ex.min_pain <= pain <= ex.max_pain)]
        if bad:
            print(f"    [FAIL]  Pain={pain}: out-of-band: {[ex.name for ex in bad]}")
            any_out = True
            passed = False
    if not any_out:
        print(f"  [PASS]  No out-of-band exercises at any pain score 0-10")

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t22_pain_filter():
    assert run_test()
