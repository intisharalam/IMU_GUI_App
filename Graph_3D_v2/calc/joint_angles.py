"""
joint_angles.py
---------------
Computes shoulder and elbow joint angles from three IMU quaternions.

ISB convention: Wu et al., Journal of Biomechanics 38(5), 2005.

──────────────────────────────────────────────────────────────────────────────
KEY FIX: SINGLE mounting application
──────────────────────────────────────────────────────────────────────────────

The mounting correction must be applied ONCE — to convert raw sensor
quaternions into the anatomical frame. It must NOT be re-applied separately
to reference and live quaternions, because that causes the chest rotation
to only partially cancel, making body rotation leak into joint angles.

Correct pipeline:
  R_anat        = R_raw * MOUNT.inv()          # sensor → anatomical, once
  R_corrected   = R_ref_anat.inv() * R_live_anat   # remove I-pose offset
  shoulder_rot  = R_chest_corr.inv() * R_arm_corr  # relative joint rotation

The shoulder_rot is now PURELY the arm motion relative to the chest,
completely independent of how the whole body is oriented.

──────────────────────────────────────────────────────────────────────────────
SENSOR AXIS LAYOUT  (from sensor placement photo, I-pose)
──────────────────────────────────────────────────────────────────────────────

  IMU_CHEST     sensor-X→DOWN  sensor-Y→RIGHT  sensor-Z→BACK*
  IMU_UPPER_ARM sensor-X→DOWN  sensor-Y→FWD    sensor-Z→RIGHT*
  IMU_FOREARM   sensor-X→DOWN  sensor-Y→FWD    sensor-Z→RIGHT*

  * Z derived from right-hand rule: cross(X,Y) = Z
    cross(DOWN, RIGHT) = BACK   ✓
    cross(DOWN, FWD)   = RIGHT  ✓

Anatomical frame: X=FORWARD, Y=UP, Z=RIGHT (patient's anatomical right)
"""

import numpy as np
from scipy.spatial.transform import Rotation


# ── Mounting rotation builder ─────────────────────────────────────────────────

def _mount(sx, sy, sz):
    """
    Build mounting Rotation from sensor axis vectors in anatomical frame.
    Columns = [sx|sy|sz]: maps FROM sensor frame TO anatomical frame.
    Must be right-handed: cross(sx,sy) == sz  →  det == +1.
    """
    mat = np.column_stack([sx, sy, sz]).astype(float)
    det = np.linalg.det(mat)
    assert abs(det - 1.0) < 1e-6, (
        f"Mounting matrix not right-handed (det={det:.4f}). "
        f"Right-hand rule: cross({sx},{sy}) = {np.cross(sx,sy)}, got sz={sz}"
    )
    return Rotation.from_matrix(mat)


# ── Anatomical frame basis vectors ────────────────────────────────────────────
FWD   = np.array([ 1,  0,  0])
UP    = np.array([ 0,  1,  0])
RIGHT = np.array([ 0,  0,  1])
DOWN  = np.array([ 0, -1,  0])
LEFT  = np.array([ 0,  0, -1])
BACK  = np.array([-1,  0,  0])


# ── Per-sensor mounting rotations ─────────────────────────────────────────────
# cross(DOWN, RIGHT) = BACK   → chest Z = BACK
# cross(DOWN, FWD)   = RIGHT  → arm/wrist Z = RIGHT
MOUNT_CHEST = _mount(sx=DOWN, sy=RIGHT, sz=BACK)
MOUNT_ARM   = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)
MOUNT_WRIST = _mount(sx=DOWN, sy=FWD,   sz=RIGHT)

MOUNT = {"chest": MOUNT_CHEST, "arm": MOUNT_ARM, "wrist": MOUNT_WRIST}


def _to_rot(q_wxyz):
    """(w,x,y,z) tuple → scipy Rotation.  scipy uses (x,y,z,w) internally."""
    w, x, y, z = q_wxyz
    return Rotation.from_quat([x, y, z, w])


def to_anatomical(q_wxyz, sensor_name: str) -> Rotation:
    """
    Convert a raw sensor quaternion into the shared anatomical frame.
    This is the ONLY place MOUNT is applied.

    R_anat = R_raw * MOUNT[sensor].inv()
    """
    return _to_rot(q_wxyz) * MOUNT[sensor_name].inv()


# ── JointAngles ───────────────────────────────────────────────────────────────

class JointAngles:
    """
    Computes ISB-compliant shoulder and elbow angles.

    All intermediate rotations stay as quaternions (Rotation objects).
    Euler decomposition happens only at the final readout step, so
    body rotation never leaks into joint angles.
    """

    def __init__(self):
        self._ref_anat = {}   # calibration refs already in anatomical frame

    def set_calibration(self, ref_quats: dict):
        """
        Convert I-pose reference quaternions into anatomical frame and store.
        Called once when the user presses Calibrate.

        ref_quats: {"chest": (w,x,y,z), "arm": ..., "wrist": ...}
        """
        self._ref_anat = {
            name: to_anatomical(q, name)
            for name, q in ref_quats.items()
        }
        print("[ANGLES] Calibration references loaded.")

    def compute(self, live_quats: dict) -> dict:
        """
        Compute joint angles for one frame.

        live_quats: {"chest": (w,x,y,z), "arm": ..., "wrist": ...}
        Returns dict of angles in degrees.
        """
        ZERO = {
            "shoulder_flexion":    0.0,
            "shoulder_abduction":  0.0,
            "external_rotation":   0.0,
            "elbow_flexion":       0.0,
            "_plane_of_elevation": 0.0,
            "_elevation":          0.0,
            "_axial_rotation":     0.0,
            # Corrected segment rotations for the renderer (Rotation objects)
            "_rot_chest":  Rotation.identity(),
            "_rot_arm":    Rotation.identity(),
            "_rot_wrist":  Rotation.identity(),
            "_shoulder_rot": Rotation.identity(),
            "_elbow_rot":    Rotation.identity(),
        }
        if not self._ref_anat:
            return ZERO

        # Step 1 — convert live quaternions into anatomical frame (MOUNT applied once)
        live_anat = {
            name: to_anatomical(q, name)
            for name, q in live_quats.items()
        }

        # Step 2 — remove I-pose offset per sensor
        # R_corrected = R_ref.inv() * R_live
        # After this, any rigid-body rotation of the whole torso cancels in
        # the relative joint step below.
        corrected = {
            name: self._ref_anat[name].inv() * live_anat[name]
            for name in ["chest", "arm", "wrist"]
            if name in self._ref_anat and name in live_anat
        }

        if len(corrected) < 3:
            return ZERO

        # Step 3 — relative joint rotations (ISB right-multiply convention)
        # R_rel = R_parent.inv() * R_child
        # Body rotation cancels here: (chest_corr).inv() * arm_corr
        # = (ref_chest.inv() * live_chest).inv() * (ref_arm.inv() * live_arm)
        # When body rotates by B: live_chest = B*ref_chest, live_arm = B*ref_arm
        # → (ref_chest.inv() * B.inv() * B * ref_arm) = ref_chest.inv() * ref_arm = identity ✓
        shoulder_rot = corrected["chest"].inv() * corrected["arm"]
        elbow_rot    = corrected["arm"].inv()   * corrected["wrist"]

        # Step 4 — ISB Euler decompositions (only done here, not in renderer)
        plane_elev, elevation, axial_rot = self._xzy_shoulder(shoulder_rot)
        elbow_flex                        = self._z_elbow(elbow_rot)

        in_sagittal = abs(plane_elev) < 45 or abs(plane_elev) > 135

        return {
            "shoulder_flexion":    elevation if in_sagittal else 0.0,
            "shoulder_abduction":  elevation if not in_sagittal else 0.0,
            "external_rotation":   axial_rot,
            "elbow_flexion":       elbow_flex,
            "_plane_of_elevation": plane_elev,
            "_elevation":          elevation,
            "_axial_rotation":     axial_rot,
            # Pass corrected Rotation objects to renderer — no Euler needed there
            "_rot_chest":    corrected["chest"],
            "_rot_arm":      corrected["arm"],
            "_rot_wrist":    corrected["wrist"],
            "_shoulder_rot": shoulder_rot,
            "_elbow_rot":    elbow_rot,
        }

    # ── ISB decompositions ────────────────────────────────────────────────────

    def _xzy_shoulder(self, rot: Rotation):
        """
        Y-X-Y Euler decomposition for humerothoracic joint (Wu 2005, Fig 7).
        Returns (plane_of_elevation, elevation_angle, axial_rotation) in degrees.
        """
        a = rot.as_euler("XZY", degrees=True)
        return float(a[0]), float(a[1]), float(a[2])

    def _z_elbow(self, rot: Rotation):
        """
        Z-X-Y decomposition for elbow flexion/extension.
        Z component = flexion(+) / extension(−).
        """
        a = rot.as_euler("ZXY", degrees=True)
        return float(a[0])


# ── AngleProcessor ────────────────────────────────────────────────────────────

class AngleProcessor:
    """
    Called every frame by the render loop and GUI.
    Reads AppState → computes angles → writes results back to AppState.
    """

    def __init__(self, state, calibration):
        self._state       = state
        self._calibration = calibration
        self._angles      = JointAngles()
        self._cal_loaded  = False

    def update(self, timestamp: float):
        if not self._cal_loaded and self._calibration.is_calibrated():
            self._angles.set_calibration(self._calibration.get_references())
            self._cal_loaded = True

        if not self._cal_loaded:
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