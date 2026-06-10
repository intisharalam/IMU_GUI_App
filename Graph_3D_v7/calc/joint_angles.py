"""
joint_angles.py  —  v6
----------------------
Computes shoulder and elbow joint angles from three IMU quaternions.

KEY CHANGE vs v5
════════════════
v5 assigned the full elevation magnitude to whichever plane "won" a 45°
threshold test (binary either/or). This meant diagonal movements (arm
raised both forward and sideways simultaneously) read correctly on only
one axis — the other was forced to zero.

v6 replaces the threshold with independent atan2 projections onto each
anatomical reference axis. Both flexion and abduction are non-zero
simultaneously for diagonal movements, matching clinical goniometry.

ANGLE DEFINITIONS (right arm, patient faces viewer)
════════════════════════════════════════════════════
All angles measured from I-pose (arm hanging down) = 0°.
Signed: positive = anatomical positive direction, negative = reverse.

  shoulder_flexion:
    Forward elevation in the sagittal plane.
    = atan2(dot(u, FORWARD), dot(u, DOWN))   [signed ±180°]
    +ve = forward (flexion), −ve = backward (extension)

  shoulder_abduction:
    Lateral elevation in the frontal plane.
    = atan2(dot(u, RIGHT), dot(u, DOWN))     [signed ±180°]
    +ve = abduction (away from body), −ve = adduction (crossing midline)
    RIGHT is negated for left arm so sign stays anatomically correct.

  elevation (combined):
    Total angle of upper arm from vertical, regardless of plane.
    = arccos(dot(upper_arm_unit, DOWN_unit))  — always 0–180°

  plane_of_elevation:
    Which plane the arm is raised in. 0° = pure abduction (frontal),
    90° = pure flexion (sagittal).

  external_rotation:
    Twist of the humerus about its own long axis.
    Derived from swing_twist, same as before. Positive = external.

  elbow_flexion:
    Angle between upper arm and forearm segments.
    = arccos(dot(upper_arm_unit, forearm_unit)) — always 0°–180°.
    Positive always (unsigned for clinical use).

  _sagittal_weight, _frontal_weight:
    Normalised 0–1 blend weights indicating how much of the arm's
    horizontal motion is in each plane. Sum to 1. Used by the renderer
    to drive plane alpha. Equal (0.5 each) for pure diagonal or overhead.

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
JUMP_THRESH_DEG    = 45.0   # max single-frame change before outlier gate fires
FLEX_ABD_CLAMP  = 175.0  # soft ceiling before atan2 hits 180° and sticks
EXT_ROT_CLAMP   = 170.0  # external rotation ceiling (slightly tighter, less ROM expected)

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

MOUNT_CHEST = _mount(sx=DOWN, sy=LEFT, sz=FWD)
MOUNT_ARM   = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT_WRIST = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
# Left arm: sensors are flipped 180° around the DOWN axis — FWD→BWD, RIGHT→LEFT
MOUNT_ARM_LEFT   = _mount(sx=DOWN, sy=BACK, sz=LEFT)
MOUNT_WRIST_LEFT = _mount(sx=DOWN, sy=BACK, sz=LEFT)

# Default (right arm) — kept for backward-compat imports in render_widget etc.
MOUNT = {"chest": MOUNT_CHEST, "arm": MOUNT_ARM, "wrist": MOUNT_WRIST}

def get_mount(side: str) -> dict:
    """Return the correct mount dict for 'right' or 'left' affected side."""
    if side == "left":
        return {"chest": MOUNT_CHEST, "arm": MOUNT_ARM_LEFT, "wrist": MOUNT_WRIST_LEFT}
    return MOUNT


def _to_rot(q_wxyz):
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w])

def to_anatomical(q_wxyz, sensor_name: str, mount: dict | None = None) -> Rotation:
    """Raw sensor quaternion → shared anatomical frame. MOUNT applied once.
    Pass a mount dict (from get_mount()) to use the correct side; defaults to right."""
    m = mount if mount is not None else MOUNT
    return _to_rot(q_wxyz) * m[sensor_name].inv()


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
    return swing, np.clip(sign * np.degrees(angle_rad), -EXT_ROT_CLAMP, EXT_ROT_CLAMP)


def angles_from_direction(upper_dir_anat: np.ndarray,
                           fore_dir_anat:  np.ndarray,
                           shoulder_rot:   Rotation,
                           side: str = "right") -> dict:
    """
    Derive all joint angles from the arm segment direction vectors.

    Flexion and abduction are computed as independent atan2 projections
    onto the sagittal and frontal planes respectively. Both are non-zero
    simultaneously for diagonal arm movements — no binary plane assignment.

    Sign convention (right arm):
      flexion   +ve = forward (flexion),  −ve = backward (extension)
      abduction +ve = sideways (abduction), −ve = across body (adduction)
    Left arm: the RIGHT reference is negated so signs stay anatomically
    correct despite the mirrored mount.

    Parameters
    ----------
    upper_dir_anat : (3,) unit vector — direction of upper arm in
                     anatomical frame (shoulder_rot applied to DOWN).
    fore_dir_anat  : (3,) unit vector — direction of forearm in anatomical
                     frame ((shoulder_rot * elbow_rot) applied to DOWN).
    shoulder_rot   : Rotation — used only for external rotation extraction.
    side           : "right" | "left"
    """
    # ── Normalise ─────────────────────────────────────────────────────────────
    u = upper_dir_anat / (np.linalg.norm(upper_dir_anat) + 1e-9)
    f = fore_dir_anat  / (np.linalg.norm(fore_dir_anat)  + 1e-9)

    # ── Reference components ──────────────────────────────────────────────────
    # DOWN  = (0,-1,0) → dot(u, DOWN) = -u[1]  (positive when arm hangs down)
    # FWD   = (1, 0,0) → dot(u, FWD)  =  u[0]  (positive when arm forward)
    # RIGHT = (0, 0,1) → dot(u, RIGHT) = u[2]  (positive when arm sideways)
    # For the left arm the mount mirrors X (FWD axis), so forward motion
    # produces u[0] < 0. We negate it so flexion stays positive-forward.
    # Abduction is mirrored too: left arm abducts toward -Z, so negate u[2].
    down_comp = np.dot(u, DOWN)           # = -u[1]; positive = hanging
    fwd_comp  = -u[0] if side == "left" else u[0]
    right_comp = -u[2] if side == "left" else u[2]

    # ── Shoulder flexion (sagittal plane projection) ───────────────────────────
    # atan2(forward_component, down_component)
    #   arm hanging down → atan2(0, 1) = 0°   ✓
    #   arm straight fwd → atan2(1, 0) = 90°  ✓
    #   arm overhead     → atan2(0,-1) = 180° ✓
    #   arm behind       → negative            ✓
    flexion   = np.clip(np.degrees(np.arctan2(fwd_comp,   down_comp)), -FLEX_ABD_CLAMP, FLEX_ABD_CLAMP)

    # ── Shoulder abduction (frontal plane projection) ─────────────────────────
    # atan2(sideways_component, down_component)
    abduction = np.clip(np.degrees(np.arctan2(right_comp, down_comp)), -FLEX_ABD_CLAMP, FLEX_ABD_CLAMP)

    # ── Elevation (total angle from vertical) — kept for legacy consumers ──────
    cos_elev  = np.clip(np.dot(u, DOWN), -1.0, 1.0)
    elevation = np.degrees(np.arccos(cos_elev))   # always 0–180°

    # ── Plane of elevation ─────────────────────────────────────────────────────
    horiz_len = np.sqrt(fwd_comp**2 + right_comp**2)
    if horiz_len > 0.01:
        plane_elev = np.degrees(np.arctan2(abs(fwd_comp), abs(right_comp)))
    else:
        plane_elev = 0.0   # arm vertical — plane undefined

    # ── Plane blend weights for renderer alpha ────────────────────────────────
    # Proportional to how much of the horizontal motion is in each plane.
    # Both → 0.5 when arm is diagonal or straight up/down (honest answer).
    if horiz_len > 0.01:
        sagittal_w = abs(fwd_comp)   / horiz_len   # 0 = pure frontal, 1 = pure sagittal
        frontal_w  = abs(right_comp) / horiz_len   # 0 = pure sagittal, 1 = pure frontal
    else:
        sagittal_w = frontal_w = 0.5

    # ── External rotation (swing-twist about humerus long axis) ───────────────
    _, ext_rot = swing_twist(shoulder_rot, u)

    # ── Elbow flexion ─────────────────────────────────────────────────────────
    cos_elbow  = np.clip(np.dot(u, f), -1.0, 1.0)
    elbow_flex = np.degrees(np.arccos(cos_elbow))   # always 0–180°

    return {
        "shoulder_flexion":    flexion,
        "shoulder_abduction":  abduction,
        "external_rotation":   ext_rot,
        "elbow_flexion":       elbow_flex,
        "_plane_of_elevation": plane_elev,
        "_elevation":          elevation,
        "_axial_rotation":     ext_rot,
        "_sagittal_weight":    sagittal_w,
        "_frontal_weight":     frontal_w,
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

    def set_calibration(self, ref_quats: dict, mount: dict, side: str = "right"):
        for f in self._filters.values(): f.reset()
        self._mount = mount
        self._side  = side
        self._ref_anat = {n: to_anatomical(q, n, mount) for n, q in ref_quats.items()}
        self._prev = {k: 0.0 for k in self._prev}
        print(f"[ANGLES] Calibration loaded. Filters reset. Side={side}")

    def compute(self, live_quats: dict) -> dict:
        ZERO = dict(
            shoulder_flexion=0.0, shoulder_abduction=0.0,
            external_rotation=0.0, elbow_flexion=0.0,
            _plane_of_elevation=0.0, _elevation=0.0, _axial_rotation=0.0,
            _sagittal_weight=0.5, _frontal_weight=0.5,
            _shoulder_rot=Rotation.identity(), _elbow_rot=Rotation.identity()
        )
        if not self._ref_anat: return ZERO
        mount = getattr(self, "_mount", MOUNT)

        # Step 1 — filter + mount (identical to renderer)
        live_anat = {n: self._filters[n].update(q) * mount[n].inv()
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
        result = angles_from_direction(upper_dir, fore_dir, shoulder_rot,
                                       side=getattr(self, "_side", "right"))
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
    """Called every frame. Detects recalibration by dict identity or side change."""
    def __init__(self, state, calibration):
        self._state       = state
        self._calibration = calibration
        self._angles      = JointAngles()
        self._last_cal_id = None
        self._last_side   = None

    def update(self, timestamp: float):
        with self._state.lock:
            calibrated = self._state.calibrated
            cal_id     = id(self._state.calibration_quats)
            cal_quats  = dict(self._state.calibration_quats) if calibrated else {}
            side       = self._state.affected_side

        mount = get_mount(side)

        # Recalibrate if quaternion dict changed OR affected side changed
        if calibrated and (cal_id != self._last_cal_id or side != self._last_side):
            self._angles.set_calibration(cal_quats, mount, side)
            self._last_cal_id = cal_id
            self._last_side   = side

        if not calibrated or self._last_cal_id is None:
            return

        with self._state.lock:
            live_quats = {n: self._state.slots[n].get_quaternion()
                          for n in ["wrist", "arm", "chest"]}

        result = self._angles.compute(live_quats)

        # Extract trunk lean from corrected chest rotation
        # Chest roll = lateral tilt = compensatory movement indicator
        try:
            q_chest = live_quats["chest"]
            with self._state.lock:
                q_ref_chest = self._state.calibration_quats.get("chest", (1,0,0,0))
            live_c = to_anatomical(q_chest, "chest", mount)
            ref_c  = to_anatomical(q_ref_chest, "chest", mount)
            corr_c = ref_c.inv() * live_c
            trunk_euler = corr_c.as_euler("XYZ", degrees=True)
            trunk_lean  = float(abs(trunk_euler[2]))   # lateral roll
        except Exception:
            trunk_lean = 0.0

        with self._state.lock:
            self._state.update_joint_angles(
                flexion    = result["shoulder_flexion"],
                abduction  = result["shoulder_abduction"],
                ext_rot    = result["external_rotation"],
                elbow      = result["elbow_flexion"],
                timestamp  = timestamp,
                plane      = result["_plane_of_elevation"],
                trunk_lean = trunk_lean,
            )