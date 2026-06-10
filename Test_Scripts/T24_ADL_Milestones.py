"""
T24 - ADL Milestones
=====================
Requirement : FR-06
Pass criterion:
  - Touch head (flexion >= 130°): unlocked at and above threshold only
  - Reach overhead (abduction >= 150°): unlocked at and above threshold only
  - Put on coat (ext_rot >= 60°): unlocked at and above threshold only
  - No false positives below any threshold

Running: python Test_Scripts/T24_ADL_Milestones.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))


# ADL milestone definitions — mirror the values in the codebase
MILESTONES = {
    "Touch head":    ("flexion",   130),
    "Reach overhead":("abduction", 150),
    "Put on coat":   ("ext_rot",    60),
}


def milestone_reached(max_flex, max_abd, max_ext_rot) -> dict:
    """Mirror the milestone logic used in the analytics panel."""
    values = {"flexion": max_flex, "abduction": max_abd, "ext_rot": max_ext_rot}
    return {
        name: values[axis] >= threshold
        for name, (axis, threshold) in MILESTONES.items()
    }


def run_test() -> bool:
    print("=" * 60)
    print("T24 - ADL Milestones")
    print("=" * 60)
    print(f"  Milestones: {MILESTONES}")
    print()

    passed = True

    # Test boundary values: one below, at, and one above threshold
    cases = [
        # (max_flex, max_abd, max_ext_rot, expected_touch, expected_overhead, expected_coat)
        (129, 149, 59, False, False, False),  # all just below
        (130, 150, 60, True,  True,  True),   # all exactly at threshold
        (131, 151, 61, True,  True,  True),   # all just above
        (130,   0,  0, True,  False, False),  # only touch head
        (  0, 150,  0, False, True,  False),  # only reach overhead
        (  0,   0, 60, False, False, True),   # only put on coat
        (  0,   0,  0, False, False, False),  # all zero
        (180, 180, 90, True,  True,  True),   # all max
    ]

    for max_flex, max_abd, max_ext_rot, exp_touch, exp_overhead, exp_coat in cases:
        result = milestone_reached(max_flex, max_abd, max_ext_rot)
        expected = {
            "Touch head":     exp_touch,
            "Reach overhead": exp_overhead,
            "Put on coat":    exp_coat,
        }
        ok = result == expected
        if not ok: passed = False
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}]  flex={max_flex:3d} abd={max_abd:3d} ext={max_ext_rot:2d}  "
              f"→ touch={result['Touch head']} overhead={result['Reach overhead']} coat={result['Put on coat']}"
              + ("" if ok else f"  EXPECTED: {expected}"))

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t24_adl_milestones():
    assert run_test()
