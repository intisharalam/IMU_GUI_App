"""
joint_angles.py
---------------
Computes shoulder and elbow joint angles from three IMU quaternions.
ISB convention: Wu et al., Journal of Biomechanics 38(5), 2005.

──────────────────────────────────────────────────────────────────────────────
KEY DESIGN DECISIONS
──────────────────────────────────────────────────────────────────────────────

1. SWING-TWIST DECOMPOSITION for shoulder rotation
   ─────────────────────────────────────────────────
   YXY Euler decomposition has a gimbal singularity when elevation ≈ 0°
   (arm near I-pose). At low elevation the first and third Y rotations become
   indistinguishable, causing huge random swings in plane-of-elevation and
   axial rotation — exactly the noise seen in the GUI.

   We instead use swing-twist decomposition:
     - Twist axis = humerus long axis = anatomical DOWN (0,-1,0) in I-pose
     - Swing = rotation that brings the arm to its current elevation/plane
     - Twist = rotation about the long axis = pure axial/external rotation

   This is numerically stable at ALL elevations including 0°.

2. FLEXION vs ABDUCTION
   ──────────────────────
   After MOUNT correction with our sensors, plane-of-elevation = 0° means
   the arm is moving in the FRONTAL plane → ABDUCTION, not flexion.
   Plane-of-elevation = 90° means SAGITTAL plane → FLEXION.
   The previous code had this backwards.

3. ELBOW FLEXION
   ──────────────
   Elbow hinge = anatomical Z-axis of the humerus frame (medial-lateral).
   After MOUNT and calibration correction, this corresponds to the Z-axis
   of the relative elbow_rot rotation. We use ZXY decomposition, [0] = Z.
   (XZY was wrong — that takes X as primary axis.)

4. FILTER
   ───────
   SLERP low-pass, α=0.15 at 50 Hz. Applied per-sensor in raw quaternion
   space. Sufficient for slow PT movements; leaves fast correction feedback
   responsive enough.

──────────────────────────────────────────────────────────────────────────────
SENSOR AXIS LAYOUT  (from placement photo, I-pose)
──────────────────────────────────────────────────────────────────────────────
  IMU_CHEST     sensor-X→DOWN  sensor-Y→RIGHT  sensor-Z→BACK
  IMU_UPPER_ARM sensor-X→DOWN  sensor-Y→FWD    sensor-Z→RIGHT
  IMU_FOREARM   sensor-X→DOWN  sensor-Y→FWD    sensor-Z→RIGHT
  Z verified by right-hand rule: cross(DOWN,RIGHT)=BACK, cross(DOWN,FWD)=RIGHT

Anatomical frame: X=FORWARD, Y=UP, Z=RIGHT
"""

import numpy as np
from scipy.spatial.transform import Rotation, Slerp


# ── Mounting rotation builder ─────────────────────────────────────────────────

def _mount(sx, sy, sz):
    mat = np.column_stack([sx, sy, sz]).astype(float)
    det = np.linalg.det(mat)
    assert abs(det - 1.0) < 1e-6, \
        f"Mounting matrix not right-handed (det={det:.6f})."
    return Rotation.from_matrix(mat)


# ── Anatomical frame basis vectors ────────────────────────────────────────────
FWD   = np.array([ 1,  0,  0])
UP    = np.array([ 0,  1,  0])
RIGHT = np.array([ 0,  0,  1])
DOWN  = np.array([ 0, -1,  0])
LEFT  = np.array([ 0,  0, -1])
BACK  = np.array([-1,  0,  0])


# ── Per-sensor mounting rotations ─────────────────────────────────────────────
MOUNT_CHEST = _mount(sx=DOWN, sy=RIGHT, sz=BACK)
MOUNT_ARM   = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT_WRIST = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT = {"chest": MOUNT_CHEST, "arm": MOUNT_ARM, "wrist": MOUNT_WRIST}

# Filter alpha: 0.15 ≈ 7.5 Hz cutoff at 50 Hz — good for slow PT movements.
# Increase toward 1.0 for less smoothing; decrease toward 0.0 for more.
FILTER_ALPHA = 0.15


def _to_rot(q_wxyz):
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w])


def to_anatomical(q_wxyz, sensor_name: str) -> Rotation:
    """Convert raw sensor quaternion into shared anatomical frame (one-time MOUNT)."""
    return _to_rot(q_wxyz) * MOUNT[sensor_name].inv()


# ── Swing-Twist decomposition ─────────────────────────────────────────────────

def swing_twist(rot: Rotation, twist_axis: np.ndarray):
    """
    Decompose `rot` into swing * twist, where twist is a pure rotation
    about `twist_axis` and swing is orthogonal to it.

    Returns (swing: Rotation, twist_angle_deg: float)

    Numerically stable at all angles including 0° — unlike Euler sequences
    which have gimbal singularities.

    Algorithm:
      Given quaternion q = (w, x, y, z) and twist axis unit vector d:
        twist_component = projection of (x,y,z) onto d
        twist_quat      = normalise(w, twist_component)
        swing           = rot * twist.inv()
    """
    # Ensure unit axis
    axis = twist_axis / np.linalg.norm(twist_axis)

    q = rot.as_quat()          # (x, y, z, w) — scipy convention
    vec = q[:3]                # (x, y, z) imaginary part
    w   = q[3]

    # Project imaginary part onto twist axis
    proj = np.dot(vec, axis) * axis

    # Reconstruct twist quaternion (before normalisation)
    twist_q = np.array([proj[0], proj[1], proj[2], w])
    norm = np.linalg.norm(twist_q)
    if norm < 1e-10:
        # Rotation is pure swing (no axial component)
        return rot, 0.0
    twist_q /= norm

    twist = Rotation.from_quat(twist_q)
    swing = rot * twist.inv()

    # Extract signed angle from twist quaternion
    # angle = 2 * atan2(|proj|, w) with sign from dot(proj, axis)
    proj_len = np.linalg.norm(proj)
    angle_rad = 2.0 * np.arctan2(proj_len, w)

    # Determine sign: positive = external rotation (for our convention)
    sign = 1.0 if np.dot(proj, axis) >= 0 else -1.0
    twist_angle_deg = sign * np.degrees(angle_rad)

    return swing, twist_angle_deg


# ── QuaternionFilter ──────────────────────────────────────────────────────────

class QuaternionFilter:
    """
    SLERP-based low-pass filter for a single quaternion stream.
    alpha=1.0 → no filtering; alpha→0.0 → maximum smoothing.
    """

    def __init__(self, alpha: float = FILTER_ALPHA):
        self._alpha   = alpha
        self._current = None

    def update(self, q_wxyz: tuple) -> Rotation:
        new_rot = _to_rot(q_wxyz)
        if self._current is None:
            self._current = new_rot
            return self._current
        key_rots      = Rotation.concatenate([self._current, new_rot])
        slerp         = Slerp([0.0, 1.0], key_rots)
        self._current = slerp(self._alpha)
        return self._current

    def reset(self):
        self._current = None


# ── JointAngles ───────────────────────────────────────────────────────────────

class JointAngles:
    """
    Computes ISB-compliant shoulder and elbow angles.

    Shoulder:
      - Elevation plane and elevation angle via swing decomposition
        (numerically stable, no gimbal singularity)
      - Axial/external rotation via twist decomposition about the
        humerus long axis (DOWN in corrected frame)

    Elbow:
      - Flexion/extension via ZXY Euler decomposition, Z component
        (hinge = medial-lateral = anatomical Z in corrected humerus frame)
    """

    def __init__(self):
        self._ref_anat = {}
        self._filters  = {n: QuaternionFilter() for n in ["chest", "arm", "wrist"]}

    def set_calibration(self, ref_quats: dict):
        """
        Store I-pose reference rotations. Resets filters to discard stale data.
        ref_quats: {"chest": (w,x,y,z), "arm": ..., "wrist": ...}
        """
        for f in self._filters.values():
            f.reset()
        self._ref_anat = {
            name: to_anatomical(q, name)
            for name, q in ref_quats.items()
        }
        print("[ANGLES] Calibration loaded. Filters reset.")

    def compute(self, live_quats: dict) -> dict:
        ZERO = {
            "shoulder_flexion":    0.0,
            "shoulder_abduction":  0.0,
            "external_rotation":   0.0,
            "elbow_flexion":       0.0,
            "_plane_of_elevation": 0.0,
            "_elevation":          0.0,
            "_axial_rotation":     0.0,
            "_shoulder_rot":       Rotation.identity(),
            "_elbow_rot":          Rotation.identity(),
        }
        if not self._ref_anat:
            return ZERO

        # Step 1 — filter in raw quaternion space, then map to anatomical frame
        live_anat = {}
        for name, q in live_quats.items():
            filtered = self._filters[name].update(q)
            live_anat[name] = filtered * MOUNT[name].inv()

        # Step 2 — remove I-pose offset: R_corrected = R_ref.inv() * R_live
        corrected = {
            name: self._ref_anat[name].inv() * live_anat[name]
            for name in ["chest", "arm", "wrist"]
            if name in self._ref_anat and name in live_anat
        }
        if len(corrected) < 3:
            return ZERO

        # Step 3 — relative joint rotations (ISB: R_parent.inv() * R_child)
        shoulder_rot = corrected["chest"].inv() * corrected["arm"]
        elbow_rot    = corrected["arm"].inv()   * corrected["wrist"]

        # Step 4 — Shoulder: swing-twist decomposition
        # ─────────────────────────────────────────────
        # Twist axis = humerus long axis = DOWN in corrected anatomical frame.
        # This is the axis of axial/internal-external rotation.
        # Swing = everything else = the elevation component.
        #
        # DOWN = (0,-1,0) in anatomical frame
        HUMERUS_AXIS = np.array([0., -1., 0.])

        swing, axial_rot = swing_twist(shoulder_rot, HUMERUS_AXIS)

        # Extract elevation angle and plane from the swing component.
        # Swing has no axial component by definition, so it's purely the
        # arm-raising motion. We use ZXY on swing:
        #   Z → plane of elevation (which plane the arm is raised in)
        #   X → elevation angle   (how high)
        swing_euler = swing.as_euler("ZXY", degrees=True)
        plane_elev  = float(swing_euler[0])   # 0°=frontal(ABD), 90°=sagittal(FLEX)
        elevation   = float(swing_euler[1])   # always 0–180°

        # Step 5 — Clinical label assignment
        # ────────────────────────────────────
        # After our MOUNT correction:
        #   plane_elev ≈ 0°  → arm moves in FRONTAL plane  → ABDUCTION
        #   plane_elev ≈ 90° → arm moves in SAGITTAL plane → FLEXION
        # (Opposite of the previous version — verified against VPython renderer)
        in_frontal = abs(plane_elev) < 45 or abs(plane_elev) > 135

        flexion    = elevation if not in_frontal else 0.0
        abduction  = elevation if in_frontal     else 0.0

        # Step 6 — Elbow flexion
        # ───────────────────────
        # ZXY decomposition on the humerus-forearm relative rotation.
        # Z = medial-lateral hinge axis = flexion/extension.
        elbow_euler = elbow_rot.as_euler("ZXY", degrees=True)
        elbow_flex  = float(elbow_euler[0])

        return {
            "shoulder_flexion":    flexion,
            "shoulder_abduction":  abduction,
            "external_rotation":   axial_rot,
            "elbow_flexion":       elbow_flex,
            "_plane_of_elevation": plane_elev,
            "_elevation":          elevation,
            "_axial_rotation":     axial_rot,
            "_shoulder_rot":       shoulder_rot,
            "_elbow_rot":          elbow_rot,
        }


# ── AngleProcessor ────────────────────────────────────────────────────────────

class AngleProcessor:
    """
    Called every frame by GUI and renderer.
    Detects recalibration automatically by tracking dict identity.
    """

    def __init__(self, state, calibration):
        self._state        = state
        self._calibration  = calibration
        self._angles       = JointAngles()
        self._last_cal_id  = None

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
            live_quats = {
                name: self._state.slots[name].get_quaternion()
                for name in ["wrist", "arm", "chest"]
            }

        result = self._angles.compute(live_quats)

        with self._state.lock:
            self._state.update_joint_angles(
                flexion   = result["shoulder_flexion"],
                abduction = result["shoulder_abduction"],
                ext_rot   = result["external_rotation"],
                elbow     = result["elbow_flexion"],
                timestamp = timestamp,
            )