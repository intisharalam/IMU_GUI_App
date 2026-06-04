"""
joint_angles.py  —  v5
----------------------
Computes shoulder and elbow joint angles from three IMU quaternions.

KEY CHANGE vs v4
════════════════
Previous versions used Euler decomposition (ZXY, YXY) to extract scalar
angles from the shoulder and elbow rotation matrices. This caused:
  - Abduction always negative (sign convention mismatch)
  - Angles decreasing after 90° (Euler gimbal / wrap at ±90°)
  - Flexion wrong (plane-of-elevation split unreliable)
  - Elbow wrong (ZXY[0] not the hinge axis)

The 3D renderer was always correct because it never uses Euler — it just
rotates the DOWN vector and draws where it points. We now do the same:
derive all angles from the DIRECTION VECTORS of the arm segments, which
matches the renderer exactly.

ANGLE DEFINITIONS (right arm, patient faces viewer)
════════════════════════════════════════════════════
All angles are measured from I-pose (arm hanging down) = 0°.
All angles are always ≥ 0° (magnitude of motion from neutral).

  shoulder_flexion:
    Arm raised forward (sagittal plane). 0° = hanging, 180° = overhead.
    Derived from: angle between upper_arm_dir and DOWN, measured in the
    sagittal plane (XY plane in anatomical frame).

  shoulder_abduction:
    Arm raised sideways (frontal plane). 0° = hanging, 180° = overhead.
    Derived from: angle between upper_arm_dir and DOWN, measured in the
    frontal plane (ZY plane in anatomical frame).

  elevation (combined):
    Total angle of upper arm from vertical, regardless of plane.
    = arccos(dot(upper_arm_unit, DOWN_unit))

  plane_of_elevation:
    Which plane the arm is raised in. 0° = pure abduction (frontal),
    90° = pure flexion (sagittal). Derived from atan2 of the horizontal
    component of the upper arm direction.

  external_rotation:
    Twist of the humerus about its own long axis.
    Derived from swing_twist, same as before. Positive = external.

  elbow_flexion:
    Angle between upper arm and forearm segments.
    = arccos(dot(upper_arm_unit, forearm_unit)) — always 0°–180°.
    0° = arm fully straight, 180° = fully bent.
    Positive always (anatomical flexion is unsigned for clinical use).

SENSOR AXES (from placement photo, I-pose):
  IMU_CHEST     X→DOWN  Y→RIGHT  Z→BACK
  IMU_UPPER_ARM X→DOWN  Y→FWD    Z→RIGHT
  IMU_FOREARM   X→DOWN  Y→FWD    Z→RIGHT
Anatomical frame: X=FORWARD, Y=UP, Z=RIGHT
"""

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

FILTER_ALPHA       = 0.15   # shoulder sensors
FILTER_ALPHA_ELBOW = 0.10   # wrist sensor (extra smoothing for elbow)
JUMP_THRESH_DEG    = 60.0   # max single-frame change before outlier gate fires


def _mount(sx, sy, sz):
    mat = np.column_stack([sx, sy, sz]).astype(float)
    det = np.linalg.det(mat)
    assert abs(det - 1.0) < 1e-6, f"Mount not right-handed (det={det:.6f})"
    return Rotation.from_matrix(mat)

FWD   = np.array([ 1,  0,  0], dtype=float)
UP    = np.array([ 0,  1,  0], dtype=float)
RIGHT = np.array([ 0,  0,  1], dtype=float)
DOWN  = np.array([ 0, -1,  0], dtype=float)
LEFT  = np.array([ 0,  0, -1], dtype=float)
BACK  = np.array([-1,  0,  0], dtype=float)

MOUNT_CHEST = _mount(sx=DOWN, sy=RIGHT, sz=BACK)
MOUNT_ARM   = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT_WRIST = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT = {"chest": MOUNT_CHEST, "arm": MOUNT_ARM, "wrist": MOUNT_WRIST}


def _to_rot(q_wxyz):
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w])

def to_anatomical(q_wxyz, sensor_name: str) -> Rotation:
    """Raw sensor quaternion → shared anatomical frame. MOUNT applied once."""
    return _to_rot(q_wxyz) * MOUNT[sensor_name].inv()


def swing_twist(rot: Rotation, twist_axis: np.ndarray):
    """
    Decompose rot = swing * twist where twist is a pure rotation about
    twist_axis. Used only for external rotation extraction.
    Returns (swing: Rotation, twist_angle_deg: float).
    Positive = external rotation (arm rotates outward).
    """
    axis    = twist_axis / np.linalg.norm(twist_axis)
    q       = rot.as_quat()     # (x,y,z,w) scipy convention
    vec     = q[:3]; w = q[3]
    proj    = np.dot(vec, axis) * axis
    twist_q = np.array([proj[0], proj[1], proj[2], w])
    norm    = np.linalg.norm(twist_q)
    if norm < 1e-10:
        return rot, 0.0
    twist_q /= norm
    twist     = Rotation.from_quat(twist_q)
    swing     = rot * twist.inv()
    proj_len  = np.linalg.norm(proj)
    angle_rad = 2.0 * np.arctan2(proj_len, w)
    sign      = 1.0 if np.dot(proj, axis) >= 0 else -1.0
    return swing, sign * np.degrees(angle_rad)


def angles_from_direction(upper_dir_anat: np.ndarray,
                           fore_dir_anat:  np.ndarray,
                           shoulder_rot:   Rotation) -> dict:
    """
    Derive all joint angles from the arm segment direction vectors.
    This is the same geometric computation the renderer uses, so the
    numbers will always match what you see on screen.

    Parameters
    ----------
    upper_dir_anat : (3,) unit vector — direction of upper arm in
                     anatomical frame (after shoulder_rot applied to DOWN).
    fore_dir_anat  : (3,) unit vector — direction of forearm in anatomical
                     frame (after (shoulder_rot * elbow_rot) applied to DOWN).
    shoulder_rot   : Rotation — used only for external rotation extraction.

    Returns dict with all angles in degrees.
    """
    # ── Normalise ─────────────────────────────────────────────────────────────
    u = upper_dir_anat / (np.linalg.norm(upper_dir_anat) + 1e-9)
    f = fore_dir_anat  / (np.linalg.norm(fore_dir_anat)  + 1e-9)

    # ── Elevation (total angle from vertical) ─────────────────────────────────
    # dot(u, DOWN) = cos(elevation). Clamp to [-1,1] for numerical safety.
    cos_elev = np.clip(np.dot(u, DOWN), -1.0, 1.0)
    elevation = np.degrees(np.arccos(cos_elev))   # always 0–180°

    # ── Plane of elevation ────────────────────────────────────────────────────
    # The horizontal component of u tells us which plane the arm is in.
    # In our anatomical frame:
    #   +X = FORWARD  → sagittal plane raise = flexion
    #   +Z = RIGHT    → frontal plane raise  = abduction
    # (Y is vertical so we ignore it for plane classification)
    horiz = np.array([u[0], 0.0, u[2]])   # forward and lateral components
    horiz_len = np.linalg.norm(horiz)

    if horiz_len > 0.01:
        # plane_elev: angle between horizontal projection and the Z axis (RIGHT)
        # = 0° → arm going purely sideways (abduction)
        # = 90° → arm going purely forward (flexion)
        plane_elev = np.degrees(np.arctan2(abs(u[0]), abs(u[2])))
    else:
        plane_elev = 0.0   # arm is straight up/down, plane undefined

    # ── Assign to flexion or abduction based on plane ─────────────────────────
    # Use a 45° threshold: within 45° of sagittal → flexion, else abduction.
    # Both are always positive (0–180°). The display can show sign if needed.
    in_sagittal = plane_elev >= 45.0
    flexion     = elevation if in_sagittal  else 0.0
    abduction   = elevation if not in_sagittal else 0.0

    # ── External rotation (swing-twist about humerus long axis) ───────────────
    # Twist axis = actual current direction of the humerus (u), not fixed DOWN.
    # This gives stable external rotation even at large elevation angles.
    _, ext_rot = swing_twist(shoulder_rot, u)

    # ── Elbow flexion ─────────────────────────────────────────────────────────
    # Angle between upper arm and forearm directions.
    # 0° = arm fully straight, 180° = fully bent.
    cos_elbow = np.clip(np.dot(u, f), -1.0, 1.0)
    elbow_flex = np.degrees(np.arccos(cos_elbow))   # always 0–180°

    return {
        "shoulder_flexion":    flexion,
        "shoulder_abduction":  abduction,
        "external_rotation":   ext_rot,
        "elbow_flexion":       elbow_flex,
        "_plane_of_elevation": plane_elev,
        "_elevation":          elevation,
        "_axial_rotation":     ext_rot,
    }


class QuaternionFilter:
    """SLERP low-pass filter for one quaternion stream."""
    def __init__(self, alpha=FILTER_ALPHA):
        self._alpha = alpha; self._current = None

    def update(self, q_wxyz: tuple) -> Rotation:
        new = _to_rot(q_wxyz)
        if self._current is None:
            self._current = new; return new
        slerp = Slerp([0.0, 1.0], Rotation.concatenate([self._current, new]))
        self._current = slerp(self._alpha)
        return self._current

    def reset(self): self._current = None


class JointAngles:
    """
    Computes joint angles by rotating the DOWN vector and measuring
    the resulting geometry — same method the renderer uses.
    """
    def __init__(self):
        self._ref_anat = {}
        self._filters  = {
            "chest": QuaternionFilter(FILTER_ALPHA),
            "arm":   QuaternionFilter(FILTER_ALPHA),
            "wrist": QuaternionFilter(FILTER_ALPHA_ELBOW),
        }
        self._prev = {
            "shoulder_flexion": 0.0, "shoulder_abduction": 0.0,
            "external_rotation": 0.0, "elbow_flexion": 0.0,
        }

    def set_calibration(self, ref_quats: dict):
        for f in self._filters.values(): f.reset()
        self._ref_anat = {n: to_anatomical(q, n) for n, q in ref_quats.items()}
        self._prev = {k: 0.0 for k in self._prev}
        print("[ANGLES] Calibration loaded. Filters reset.")

    def compute(self, live_quats: dict) -> dict:
        ZERO = dict(
            shoulder_flexion=0.0, shoulder_abduction=0.0,
            external_rotation=0.0, elbow_flexion=0.0,
            _plane_of_elevation=0.0, _elevation=0.0, _axial_rotation=0.0,
            _shoulder_rot=Rotation.identity(), _elbow_rot=Rotation.identity()
        )
        if not self._ref_anat: return ZERO

        # Step 1 — filter + mount (identical to renderer)
        live_anat = {n: self._filters[n].update(q) * MOUNT[n].inv()
                     for n, q in live_quats.items()}

        # Step 2 — remove I-pose offset (identical to renderer)
        corrected = {
            n: self._ref_anat[n].inv() * live_anat[n]
            for n in ["chest", "arm", "wrist"]
            if n in self._ref_anat and n in live_anat
        }
        if len(corrected) < 3: return ZERO

        # Step 3 — relative joint rotations (identical to renderer)
        shoulder_rot = corrected["chest"].inv() * corrected["arm"]
        elbow_rot    = corrected["arm"].inv()   * corrected["wrist"]

        # Step 4 — derive segment direction vectors (identical to renderer)
        upper_dir = shoulder_rot.apply(DOWN)                        # unit upper arm
        fore_dir  = (shoulder_rot * elbow_rot).apply(DOWN)          # unit forearm

        # Step 5 — compute all angles from direction vectors
        result = angles_from_direction(upper_dir, fore_dir, shoulder_rot)
        result["_shoulder_rot"] = shoulder_rot
        result["_elbow_rot"]    = elbow_rot

        # Step 6 — outlier gate (hold previous value on implausible jump)
        for key in ["shoulder_flexion", "shoulder_abduction",
                    "external_rotation", "elbow_flexion"]:
            if abs(result[key] - self._prev[key]) > JUMP_THRESH_DEG:
                result[key] = self._prev[key]
            else:
                self._prev[key] = result[key]

        return result


class AngleProcessor:
    """Called every frame. Detects recalibration by dict identity."""
    def __init__(self, state, calibration):
        self._state       = state
        self._calibration = calibration
        self._angles      = JointAngles()
        self._last_cal_id = None

    def update(self, timestamp: float):
        with self._state.lock:
            calibrated = self._state.calibrated
            cal_id     = id(self._state.calibration_quats)
            cal_quats  = dict(self._state.calibration_quats) if calibrated else {}

        if calibrated and cal_id != self._last_cal_id:
            self._angles.set_calibration(cal_quats)
            self._last_cal_id = cal_id

        if not calibrated or self._last_cal_id is None:
            return

        with self._state.lock:
            live_quats = {n: self._state.slots[n].get_quaternion()
                          for n in ["wrist", "arm", "chest"]}

        result = self._angles.compute(live_quats)

        with self._state.lock:
            self._state.update_joint_angles(
                flexion   = result["shoulder_flexion"],
                abduction = result["shoulder_abduction"],
                ext_rot   = result["external_rotation"],
                elbow     = result["elbow_flexion"],
                timestamp = timestamp,
            )