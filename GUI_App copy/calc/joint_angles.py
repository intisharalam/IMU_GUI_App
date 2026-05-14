"""
joint_angles.py
---------------
Computes clinically meaningful shoulder and elbow joint angles from the
three IMU quaternions.

The maths explained:
  - Each IMU gives us a quaternion = its absolute orientation in the world.
  - We first "undo" the calibration offset (subtract the neutral pose).
  - Then we compute the RELATIVE rotation between adjacent body segments:
      chest  → arm   = shoulder joint  (flexion, abduction, ext. rotation)
      arm    → wrist = elbow joint     (elbow flexion)
  - We decompose that relative rotation into anatomically meaningful angles.

Sensor axis convention (all three sensors, in I-pose):
  - x: pointing DOWN (gravity direction)
  - y: pointing FORWARD (wrist/arm) or LEFT (chest)
  - z: pointing RIGHT (wrist/arm) or OUTWARD from chest

Reference:
  Cutti et al. (2023), Gait & Posture — validated 3-sensor shoulder method.
"""

import math
import numpy as np
from scipy.spatial.transform import Rotation


class JointAngles:
    """
    Computes shoulder and elbow joint angles from calibrated quaternions.

    Usage:
        ja = JointAngles()
        ja.set_calibration(ref_quats)          # once, after calibration
        result = ja.compute(live_quats)         # every frame
        print(result["shoulder_flexion"])       # degrees
    """

    def __init__(self):
        # Calibration reference rotations (Rotation objects, set after calibration)
        self._ref = {}   # {"wrist": Rotation, "arm": Rotation, "chest": Rotation}

    def set_calibration(self, ref_quats: dict):
        """
        Stores the neutral-pose reference quaternions.

        ref_quats: dict of {"wrist": (w,x,y,z), "arm": ..., "chest": ...}
        """
        self._ref = {}
        for name, q in ref_quats.items():
            # scipy uses (x, y, z, w) order — note the reordering
            self._ref[name] = Rotation.from_quat([q[1], q[2], q[3], q[0]])

        print("[ANGLES] Calibration references stored.")

    def compute(self, live_quats: dict) -> dict:
        """
        Given the current quaternions from all three sensors,
        returns a dict with four joint angles in degrees.

        live_quats: {"wrist": (w,x,y,z), "arm": ..., "chest": ...}

        Returns:
            {
                "shoulder_flexion":   float,  # forward/backward arm swing
                "shoulder_abduction": float,  # arm raising sideways
                "external_rotation":  float,  # arm rotating outward
                "elbow_flexion":      float,  # elbow bend angle
            }
        """
        if not self._ref:
            # No calibration yet — return zeros
            return {
                "shoulder_flexion":   0.0,
                "shoulder_abduction": 0.0,
                "external_rotation":  0.0,
                "elbow_flexion":      0.0,
            }

        # Convert live quaternions to Rotation objects (scipy x,y,z,w order)
        live = {}
        for name, q in live_quats.items():
            live[name] = Rotation.from_quat([q[1], q[2], q[3], q[0]])

        # --- Remove calibration offset ---
        # "What rotation happened since the I-pose?"
        # corrected = inv(reference) * current
        corrected = {}
        for name in ["wrist", "arm", "chest"]:
            if name in self._ref and name in live:
                corrected[name] = self._ref[name].inv() * live[name]

        if len(corrected) < 3:
            return {k: 0.0 for k in [
                "shoulder_flexion", "shoulder_abduction",
                "external_rotation", "elbow_flexion"
            ]}

        # --- Shoulder joint: arm relative to chest ---
        # This gives us the humerus orientation w.r.t. the thorax
        shoulder_rot = corrected["chest"].inv() * corrected["arm"]

        # --- Elbow joint: wrist relative to arm ---
        elbow_rot = corrected["arm"].inv() * corrected["wrist"]

        # --- Extract shoulder angles ---
        flexion, abduction, ext_rot = self._shoulder_angles(shoulder_rot)

        # --- Extract elbow flexion ---
        elbow = self._elbow_angle(elbow_rot)

        return {
            "shoulder_flexion":   flexion,
            "shoulder_abduction": abduction,
            "external_rotation":  ext_rot,
            "elbow_flexion":      elbow,
        }

    # ── Angle extraction helpers ─────────────────────────────────────────────

    def _shoulder_angles(self, rot: Rotation):
        """
        Decomposes the shoulder relative rotation into three clinical angles.

        Uses a YXZ Euler decomposition which maps onto:
          Y-axis rotation → abduction/adduction
          X-axis rotation → flexion/extension
          Z-axis rotation → internal/external rotation

        Returns (flexion_deg, abduction_deg, ext_rotation_deg)
        """
        # YXZ decomposition: intrinsic rotations in Y, X, Z order
        # Result is in degrees
        angles = rot.as_euler("YXZ", degrees=True)

        abduction = angles[0]   # Y: arm swinging out to the side
        flexion   = angles[1]   # X: arm swinging forward
        ext_rot   = angles[2]   # Z: arm rotating outward

        return flexion, abduction, ext_rot

    def _elbow_angle(self, rot: Rotation):
        """
        Extracts the elbow flexion angle.

        The elbow is mostly a 1-DOF hinge joint (flexion only).
        We compute the total rotation angle from the relative rotation,
        which gives a reliable estimate of how bent the elbow is.

        Returns elbow_flexion_deg (0 = straight arm, positive = bent)
        """
        # Convert to rotation vector; its magnitude is the total rotation angle
        rotvec = rot.as_rotvec()
        angle_rad = np.linalg.norm(rotvec)
        angle_deg = math.degrees(angle_rad)
        return angle_deg


class AngleProcessor:
    """
    Called every GUI frame to update joint angles in AppState.

    This is the bridge between the calculation layer and the shared state.
    It reads raw quaternions from state, computes angles, and writes them back.
    """

    def __init__(self, state, calibration):
        """
        state:       AppState instance
        calibration: Calibration instance
        """
        self._state = state
        self._calibration = calibration
        self._joint_angles = JointAngles()
        self._cal_loaded = False   # tracks whether we've loaded cal into JointAngles

    def update(self, timestamp: float):
        """
        Call this every GUI frame.
        If calibrated, computes joint angles and writes them to state.
        Does nothing if calibration hasn't happened yet.
        """
        # Load calibration into JointAngles the first time it becomes available
        if not self._cal_loaded and self._calibration.is_calibrated():
            refs = self._calibration.get_references()
            self._joint_angles.set_calibration(refs)
            self._cal_loaded = True

        if not self._cal_loaded:
            return   # nothing to do yet

        # Read the latest quaternion from each sensor
        with self._state.lock:
            live_quats = {
                name: self._state.slots[name].get_quaternion()
                for name in ["wrist", "arm", "chest"]
            }

        # Compute angles (outside the lock — maths can take a tiny moment)
        result = self._joint_angles.compute(live_quats)

        # Write results back to shared state
        with self._state.lock:
            self._state.update_joint_angles(
                flexion   = result["shoulder_flexion"],
                abduction = result["shoulder_abduction"],
                ext_rot   = result["external_rotation"],
                elbow     = result["elbow_flexion"],
                timestamp = timestamp,
            )
