"""
T18 - Angle Pipeline (Synthetic)
==================================
Requirement : FR-03
Pass criterion:
  - Shoulder flexion error  <= 0.5 degrees at 30, 60, 90, 120 degrees
  - Shoulder abduction error <= 0.5 degrees at 30, 60, 90, 120 degrees
  - Elbow flexion error     <= 0.5 degrees at 30, 60, 90, 120 degrees

What this tests
---------------
The joint angle computation pipeline in calc/joint_angles.py, specifically:

  Step 1  SLERP low-pass filter applied to raw quaternion
  Step 2  Mount correction  (sensor axes -> shared anatomical frame)
  Step 3  Calibration removal  (subtract I-pose reference)
  Step 4  Relative joint rotations  (chest^-1 * arm, arm^-1 * wrist)
  Step 5  Direction-vector extraction  (rotate DOWN by joint rotation)
  Step 6  Geometric angle computation  (arccos dot-products, atan2 plane)
  Step 7  Outlier gate  (hold previous value on implausible jump > 60 deg)

Method
------
Synthetic Rotation objects are constructed so that, after passing through the
full pipeline, they produce exactly known ground-truth angles:

  Pure flexion at θ:
      shoulder_rot = Rotation.from_euler("Z", θ)
      → upper arm points forward+down in anatomical frame
      → plane_of_elevation = 90° (sagittal) → counts as flexion

  Pure abduction at θ:
      shoulder_rot = Rotation.from_euler("X", -θ)
      → upper arm points sideways+down in anatomical frame
      → plane_of_elevation = 0° (frontal) → counts as abduction

  Pure elbow flexion at θ (shoulder neutral):
      shoulder_rot = identity
      elbow_rot    = Rotation.from_euler("Z", θ)
      → forearm bends away from upper arm by θ degrees

  Combined flexion + elbow:
      Tests both outputs simultaneously.

Calibration references are set to identity (I-pose = identity orientation),
so mount correction is the only transform between raw sensor quaternion and
the desired corrected rotation.

Ramp protocol
-------------
The outlier gate blocks any single-frame jump > 60 degrees from the previous
value. To reach angles >= 60° without triggering it, inputs are ramped up in
20-degree steps. 50 frames are fed at each step to allow the SLERP low-pass
filter (alpha=0.15) to converge.

At 50 frames per step and alpha=0.15:
    residual = (1 - 0.15)^50 = 0.85^50 ≈ 0.00030
    max SLERP error at 120° ≈ 120 * 0.0003 = 0.036°

This is well within the 0.5° pass criterion.

Running standalone
------------------
    cd <project root>
    python tests/T18_Angle_Pipeline.py

Running under pytest
--------------------
    cd <project root>
    pytest tests/T18_Angle_Pipeline.py -v
"""

import sys
import math
import numpy as np
from scipy.spatial.transform import Rotation

# Ensure project root is on path when run standalone
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.joint_angles import JointAngles, MOUNT

# ── Constants ──────────────────────────────────────────────────────────────────
PASS_THRESHOLD_DEG   = 0.5    # pass criterion from test spec
RAMP_STEP_DEG        = 20     # degrees per ramp step (must be < 60 to avoid gate)
FRAMES_PER_STEP      = 50     # frames held at each step for SLERP convergence
TEST_ANGLES_DEG      = [30, 60, 90, 120]

# ── Helpers ────────────────────────────────────────────────────────────────────

def rot_to_wxyz(r: Rotation) -> tuple:
    """Convert scipy Rotation to (w, x, y, z) quaternion tuple."""
    x, y, z, w = r.as_quat()
    return (float(w), float(x), float(y), float(z))


def make_sensor_quat(desired_corrected: Rotation, sensor: str,
                     mount: dict) -> tuple:
    """
    Compute the raw sensor quaternion that, after mount correction and identity
    calibration removal, yields desired_corrected.

    Pipeline (forward):
        live_anat[n]  = _to_rot(q_raw) * mount[n].inv()
        corrected[n]  = ref_anat[n].inv() * live_anat[n]

    With ref_anat[n] = mount[n].inv()  (identity calibration):
        corrected[n]  = mount[n] * _to_rot(q_raw) * mount[n].inv()

    Solving for q_raw:
        _to_rot(q_raw) = mount[n].inv() * desired_corrected * mount[n]
    """
    m = mount[sensor]
    raw_rot = m.inv() * desired_corrected * m
    return rot_to_wxyz(raw_rot)


def identity_cal() -> tuple:
    """(w, x, y, z) identity quaternion."""
    return (1.0, 0.0, 0.0, 0.0)


def run_to_angle(shoulder_rot: Rotation,
                 elbow_rot: Rotation,
                 mount: dict,
                 ramp_step: int = RAMP_STEP_DEG,
                 frames_per_step: int = FRAMES_PER_STEP) -> dict:
    """
    Drive a fresh JointAngles instance to the given shoulder/elbow rotations
    via a ramp, returning the final computed angles.

    The ramp avoids triggering the 60-degree outlier gate by incrementing in
    steps of ramp_step degrees, converging the SLERP filter at each step.
    """
    cal_quats = {n: identity_cal() for n in ["chest", "arm", "wrist"]}
    ja = JointAngles()
    ja.set_calibration(cal_quats, mount, "right")

    # Determine the "magnitude" of motion for ramping.
    # Use the shoulder elevation angle as the ramp driver.
    from calc.joint_angles import DOWN
    shoulder_elevation = math.degrees(
        math.acos(float(np.clip(np.dot(shoulder_rot.apply(DOWN), DOWN), -1, 1)))
    )
    elbow_angle = math.degrees(
        math.acos(float(np.clip(
            np.dot(shoulder_rot.apply(DOWN),
                   (shoulder_rot * elbow_rot).apply(DOWN)), -1, 1)))
    )
    max_angle = max(shoulder_elevation, elbow_angle)

    if max_angle < 1e-3:
        max_angle = 1.0   # trivial case: just run a few frames

    # Build waypoints: fractions of the total rotation
    waypoints = []
    step = ramp_step
    while step < max_angle:
        waypoints.append(step / max_angle)
        step += ramp_step
    waypoints.append(1.0)   # always end at full target

    result = None
    for frac in waypoints:
        # Interpolate shoulder and elbow rotations to this fraction
        # Use Slerp for correct interpolation on SO(3)
        slerp_shoulder = Rotation.concatenate(
            [Rotation.identity(), shoulder_rot])
        slerp_elbow = Rotation.concatenate(
            [Rotation.identity(), elbow_rot])
        from scipy.spatial.transform import Slerp
        s_rot = Slerp([0, 1], slerp_shoulder)(frac)
        e_rot = Slerp([0, 1], slerp_elbow)(frac)

        q_chest = make_sensor_quat(Rotation.identity(), "chest", mount)
        q_arm   = make_sensor_quat(s_rot, "arm", mount)
        q_wrist = make_sensor_quat(s_rot * e_rot, "wrist", mount)
        live = {"chest": q_chest, "arm": q_arm, "wrist": q_wrist}

        for _ in range(frames_per_step):
            result = ja.compute(live)

    return result


# ── Sub-tests ──────────────────────────────────────────────────────────────────

def check(label: str, measured: float, expected: float,
          threshold: float = PASS_THRESHOLD_DEG) -> bool:
    err = abs(measured - expected)
    passed = err <= threshold
    status = "PASS" if passed else "FAIL"
    print(f"    [{status}]  {label}: expected={expected:.1f}°  "
          f"measured={measured:.4f}°  error={err:.4f}°  "
          f"(threshold {threshold}°)")
    return passed


def test_shoulder_flexion(mount: dict) -> bool:
    """T18a — Pure shoulder flexion at 30, 60, 90, 120 degrees."""
    print("  T18a  Pure shoulder flexion")
    results = []
    for θ in TEST_ANGLES_DEG:
        shoulder_rot = Rotation.from_euler("Z", θ, degrees=True)
        out = run_to_angle(shoulder_rot, Rotation.identity(), mount)
        results.append(check(f"flexion {θ:3d}°", out["shoulder_flexion"], θ))
        # Abduction and elbow should be near zero
        results.append(check(f"  abd~0 @ {θ:3d}°", out["shoulder_abduction"], 0.0, 2.0))
    return all(results)


def test_shoulder_abduction(mount: dict) -> bool:
    """T18b — Pure shoulder abduction at 30, 60, 90, 120 degrees."""
    print("  T18b  Pure shoulder abduction")
    results = []
    for θ in TEST_ANGLES_DEG:
        shoulder_rot = Rotation.from_euler("X", -θ, degrees=True)
        out = run_to_angle(shoulder_rot, Rotation.identity(), mount)
        results.append(check(f"abduction {θ:3d}°", out["shoulder_abduction"], θ))
        results.append(check(f"  flex~0 @ {θ:3d}°", out["shoulder_flexion"], 0.0, 2.0))
    return all(results)


def test_elbow_flexion(mount: dict) -> bool:
    """T18c — Pure elbow flexion (shoulder neutral) at 30, 60, 90, 120 degrees."""
    print("  T18c  Pure elbow flexion (shoulder neutral)")
    results = []
    for θ in TEST_ANGLES_DEG:
        elbow_rot = Rotation.from_euler("Z", θ, degrees=True)
        out = run_to_angle(Rotation.identity(), elbow_rot, mount)
        results.append(check(f"elbow {θ:3d}°", out["elbow_flexion"], θ))
        results.append(check(f"  flex~0 @ {θ:3d}°", out["shoulder_flexion"], 0.0, 2.0))
        results.append(check(f"  abd~0  @ {θ:3d}°", out["shoulder_abduction"], 0.0, 2.0))
    return all(results)


def test_combined_flexion_and_elbow(mount: dict) -> bool:
    """T18d — Simultaneous shoulder flexion + elbow flexion."""
    print("  T18d  Combined shoulder flexion + elbow flexion")
    results = []
    cases = [(60, 90), (90, 60), (120, 90)]
    for flex_deg, elbow_deg in cases:
        shoulder_rot = Rotation.from_euler("Z", flex_deg, degrees=True)
        elbow_rot    = Rotation.from_euler("Z", elbow_deg, degrees=True)
        out = run_to_angle(shoulder_rot, elbow_rot, mount)
        results.append(check(
            f"flex={flex_deg}° + elbow={elbow_deg}° -> flex",
            out["shoulder_flexion"], flex_deg))
        results.append(check(
            f"flex={flex_deg}° + elbow={elbow_deg}° -> elbow",
            out["elbow_flexion"], elbow_deg))
    return all(results)


def test_zero_angle(mount: dict) -> bool:
    """T18e — I-pose (all identity). Every angle should be ~0."""
    print("  T18e  I-pose (all identity rotations)")
    out = run_to_angle(Rotation.identity(), Rotation.identity(), mount)
    results = [
        check("flexion  @ I-pose", out["shoulder_flexion"],  0.0, 1.0),
        check("abduction@ I-pose", out["shoulder_abduction"], 0.0, 1.0),
        check("elbow    @ I-pose", out["elbow_flexion"],      0.0, 1.0),
    ]
    return all(results)


# ── Standalone runner ──────────────────────────────────────────────────────────

def run_all() -> bool:
    print("=" * 65)
    print("T18 - Angle Pipeline (Synthetic)")
    print("=" * 65)
    print(f"  Pass threshold  : {PASS_THRESHOLD_DEG} degrees")
    print(f"  Test angles     : {TEST_ANGLES_DEG}")
    print(f"  Ramp step       : {RAMP_STEP_DEG} degrees")
    print(f"  Frames per step : {FRAMES_PER_STEP}")
    print()

    mount = MOUNT
    subtests = [
        test_shoulder_flexion,
        test_shoulder_abduction,
        test_elbow_flexion,
        test_combined_flexion_and_elbow,
        test_zero_angle,
    ]

    results = []
    for fn in subtests:
        passed = fn(mount)
        results.append(passed)
        print()

    total  = len(results)
    n_pass = sum(results)
    n_fail = total - n_pass
    overall = n_fail == 0

    print("-" * 65)
    print(f"  Sub-tests passed : {n_pass} / {total}")
    if n_fail:
        print(f"  Sub-tests failed : {n_fail}")
    print()
    print(f"  RESULT: {'PASS' if overall else 'FAIL'}")
    print("=" * 65)
    return overall


# ── pytest entry points ────────────────────────────────────────────────────────

def test_t18a_shoulder_flexion():
    assert test_shoulder_flexion(MOUNT)

def test_t18b_shoulder_abduction():
    assert test_shoulder_abduction(MOUNT)

def test_t18c_elbow_flexion():
    assert test_elbow_flexion(MOUNT)

def test_t18d_combined():
    assert test_combined_flexion_and_elbow(MOUNT)

def test_t18e_zero():
    assert test_zero_angle(MOUNT)


if __name__ == "__main__":
    passed = run_all()
    sys.exit(0 if passed else 1)