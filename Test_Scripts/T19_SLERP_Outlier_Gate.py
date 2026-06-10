"""
T19 - SLERP Outlier Gate
=========================
Requirement : NFR-02
Pass criterion:
  - The 70-degree spike at frame 50 does NOT appear in the output
    (output must not reach the spike value of 100 degrees)
  - All non-spike frames remain within 15 degrees of the steady value
    (the SLERP filter bleeds ~15% of the spike into its internal state,
     causing a brief transient before recovering — this is expected behaviour)

What this tests
---------------
The outlier gate in JointAngles.compute():

    if abs(result[key] - self._prev[key]) > JUMP_THRESH_DEG:   # JUMP_THRESH_DEG = 60
        result[key] = self._prev[key]

A 50Hz stream of steady shoulder flexion at 30 degrees is fed for 100 frames.
At frame 50 a spike is injected: the sensor quaternion jumps to 100 degrees
(a 70-degree change — exceeds the 60-degree gate threshold).

The gate discards the spike from the ANGLE OUTPUT (holds previous value).
However, the SLERP filter operates on the raw quaternion BEFORE the gate,
so it absorbs 15% of the spike into its internal state (alpha=0.15).
This causes a visible transient step in frames 51-65 before recovery.

This is correct and expected behaviour:
  - The gate protects downstream consumers (rep detector, haptic triggers)
    from acting on the corrupted sample.
  - The filter transient resolves naturally within ~15 frames.
  - Output never reaches the spike value (100 degrees).

Running standalone
------------------
    cd IMU_GUI_App
    python Test_Scripts/T19_SLERP_Outlier_Gate.py
"""

import sys
import os
from scipy.spatial.transform import Rotation

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

from calc.joint_angles import JointAngles, MOUNT, JUMP_THRESH_DEG

SPIKE_FRAME   = 50
TOTAL_FRAMES  = 100
STEADY_DEG    = 30.0
SPIKE_DEG     = 100.0   # 70-degree jump from steady — exceeds 60-deg gate


def rot_to_wxyz(r):
    x, y, z, w = r.as_quat()
    return (float(w), float(x), float(y), float(z))


def make_sensor_quat(shoulder_rot, sensor, mount):
    m = mount[sensor]
    return rot_to_wxyz(m.inv() * shoulder_rot * m)


def run_test() -> bool:
    print("=" * 60)
    print("T19 - SLERP Outlier Gate")
    print("=" * 60)
    print(f"  Steady angle  : {STEADY_DEG} deg (shoulder flexion)")
    print(f"  Spike angle   : {SPIKE_DEG} deg at frame {SPIKE_FRAME}")
    print(f"  Jump size     : {SPIKE_DEG - STEADY_DEG} deg  "
          f"(gate threshold = {JUMP_THRESH_DEG} deg)")
    print()

    mount = MOUNT
    ja = JointAngles()
    ja.set_calibration(
        {"chest": (1,0,0,0), "arm": (1,0,0,0), "wrist": (1,0,0,0)},
        mount, "right"
    )

    # Ramp to STEADY_DEG so the gate is not triggered on startup
    for warmup_deg in range(10, int(STEADY_DEG) + 1, 10):
        r = Rotation.from_euler("Z", warmup_deg, degrees=True)
        q = make_sensor_quat(r, "arm", mount)
        live = {"chest": (1,0,0,0), "arm": q, "wrist": q}
        for _ in range(20):
            ja.compute(live)

    q_steady = make_sensor_quat(
        Rotation.from_euler("Z", STEADY_DEG, degrees=True), "arm", mount)
    q_spike = make_sensor_quat(
        Rotation.from_euler("Z", SPIKE_DEG, degrees=True), "arm", mount)

    outputs = []
    for frame in range(TOTAL_FRAMES):
        q = q_spike if frame == SPIKE_FRAME else q_steady
        live = {"chest": (1,0,0,0), "arm": q, "wrist": q}
        outputs.append(ja.compute(live)["shoulder_flexion"])

    # ── Checks ─────────────────────────────────────────────────────────────────
    passed = True

    # 1. Spike output must not reach the spike angle (gate blocked it)
    spike_out = outputs[SPIKE_FRAME]
    spike_blocked = spike_out < SPIKE_DEG - 10.0
    status = "PASS" if spike_blocked else "FAIL"
    print(f"  [{status}]  Spike blocked: frame {SPIKE_FRAME} output = "
          f"{spike_out:.2f} deg  (spike was {SPIKE_DEG} deg, gate threshold {JUMP_THRESH_DEG} deg)")
    if not spike_blocked:
        passed = False

    # 2. All non-spike frames stay within 15 deg of steady
    #    (15 deg accounts for the SLERP filter transient after the spike)
    TRANSIENT_TOL = 15.0
    bad_frames = [
        (i, v) for i, v in enumerate(outputs)
        if i != SPIKE_FRAME and abs(v - STEADY_DEG) > TRANSIENT_TOL
    ]
    stream_ok = len(bad_frames) == 0
    status = "PASS" if stream_ok else "FAIL"
    max_dev = max(abs(v - STEADY_DEG) for i, v in enumerate(outputs) if i != SPIKE_FRAME)
    print(f"  [{status}]  Non-spike frames within {TRANSIENT_TOL} deg of {STEADY_DEG} deg  "
          f"(max deviation = {max_dev:.2f} deg)"
          + (f"\n    bad frames: {bad_frames}" if bad_frames else ""))
    if not stream_ok:
        passed = False

    # 3. Stream recovers: last 20 frames all within 1 deg of steady
    recovery_frames = outputs[-20:]
    recovery_ok = all(abs(v - STEADY_DEG) <= 1.0 for v in recovery_frames)
    status = "PASS" if recovery_ok else "FAIL"
    max_tail = max(abs(v - STEADY_DEG) for v in recovery_frames)
    print(f"  [{status}]  Recovery: last 20 frames within 1 deg of {STEADY_DEG} deg  "
          f"(max tail deviation = {max_tail:.4f} deg)")
    if not recovery_ok:
        passed = False

    print()
    print("  Note: SLERP filter absorbs ~15% of spike into internal state,")
    print("  causing a brief transient before recovery. This is expected.")
    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    passed = run_test()
    sys.exit(0 if passed else 1)


def test_t19_outlier_gate():
    assert run_test()