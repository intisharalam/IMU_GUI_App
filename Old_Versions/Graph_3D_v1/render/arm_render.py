"""
arm_render.py
-------------
Live 3D arm skeleton visualisation using VPython.

Draws:
  - Torso box   (chest reference, static)
  - Upper arm   : cylinder from shoulder → elbow
  - Forearm     : cylinder from elbow    → wrist
  - Joints      : coloured spheres at shoulder, elbow, wrist

Pipeline per frame (mirrors joint_angles.py exactly):
  1. Read raw (w,x,y,z) quaternions from AppState.
  2. Remap into anatomical frame:  R_anat = R_sensor * MOUNT[sensor].inv()
  3. Remove I-pose offset:         R_corrected = R_ref.inv() * R_live
  4. Relative rotations (ISB):
       shoulder = R_chest.inv() * R_arm
       elbow    = R_arm.inv()   * R_wrist
  5. Rotate the DOWN direction vector:
       upper arm world dir = shoulder.apply(DOWN)
       forearm world dir   = (shoulder * elbow).apply(DOWN)   ← kinematic chain

Usage:
    renderer = ArmRender(state, angle_processor)
    renderer.run()      # blocks until window is closed

Standalone test (no hardware):
    python render/arm_render.py
"""

import sys
import time

try:
    import vpython as vp
except ImportError:
    print("[ArmRender] vpython not installed.  Fix:  pip install vpython")
    sys.exit(1)

import numpy as np
from scipy.spatial.transform import Rotation

# Import mounting rotations and helpers from joint_angles — single source of truth.
try:
    from calc.joint_angles import to_anatomical
except ImportError:
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from calc.joint_angles import to_anatomical


# ── Segment dimensions (metres) ───────────────────────────────────────────────
UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25
BONE_RADIUS   = 0.025
JOINT_RADIUS  = 0.038
TORSO_SIZE    = vp.vector(0.08, 0.20, 0.12)   # depth × height × width (X=depth into scene, Z=width left-right)

# ── Colours (match the AMOLED GUI palette) ────────────────────────────────────
C_TORSO      = vp.color.white
C_UPPER_ARM  = vp.vector(0.27, 0.71, 1.00)   # cyan-blue  ↔ GUI flexion
C_FOREARM    = vp.vector(0.70, 0.31, 1.00)   # purple     ↔ GUI elbow
C_SHOULDER   = vp.vector(1.00, 0.78, 0.00)   # amber      ↔ GUI ext. rot.
C_ELBOW      = vp.vector(0.00, 1.00, 0.63)   # mint green ↔ GUI abduction
C_WRIST      = vp.vector(1.00, 0.24, 0.24)   # red
C_BACKGROUND = vp.vector(0.04, 0.04, 0.04)   # near-black

RENDER_HZ = 50

# In I-pose the arm hangs straight down.
DOWN_NP = np.array([0., -1., 0.])

# ── World display rotation ────────────────────────────────────────────────────
# The anatomical frame (X=fwd, Y=up, Z=right) does not align with VPython's
# display frame (X=right, Y=up, Z=toward-viewer).
# A +90° rotation around Y maps:
#   anatomical RIGHT (0,0,1) → VPython RIGHT (+X)  ✓ arm-to-side shows as right
#   anatomical FWD   (1,0,0) → VPython BACK  (-Z)  ✓ fwd flexion goes into scene
#   anatomical UP    (0,1,0) → VPython UP    (+Y)  ✓ unchanged
# Applied to every segment direction before passing to VPython.
WORLD_ROT = Rotation.from_euler("Y", 90, degrees=True)

IDENTITY_ROT = Rotation.identity()
IDENTITY_Q   = (1., 0., 0., 0.)


def _np_to_vp(v):
    """Convert a numpy 3-vector to a vpython vector."""
    return vp.vector(float(v[0]), float(v[1]), float(v[2]))


class ArmRender:
    """
    3D arm skeleton renderer.

    Parameters
    ----------
    state           : AppState or None   (None = standalone test with identity quats)
    angle_processor : AngleProcessor or None
        If provided, .update(now) is called each frame so AppState joint angles
        stay current alongside the visual.
    """

    def __init__(self, state=None, angle_processor=None):
        self._state  = state
        self._angles = angle_processor

    def run(self):
        """Build the VPython scene and enter the render loop. Blocks until closed."""

        # ── Scene setup ───────────────────────────────────────────────────────
        scene = vp.canvas(
            title      = "<b>Arm Skeleton — Frozen Shoulder Rehab</b>",
            width      = 700,
            height     = 580,
            background = C_BACKGROUND,
            resizable  = True,
        )
        scene.camera.pos  = vp.vector(0,  0.10, -1.0)
        scene.camera.axis = vp.vector(0, -0.05,  1.0)
        scene.up          = vp.vector(0,  1,     0)

        # ── Static torso ──────────────────────────────────────────────────────
        vp.box(pos=vp.vector(0, 0, 0), size=TORSO_SIZE,
               color=C_TORSO, opacity=0.20)

        # Shoulder joint: top-right of torso.
        # After WORLD_ROT, anatomical RIGHT = VPython +X, so shoulder is at +X side.
        shoulder_pos = vp.vector(TORSO_SIZE.x / 2 + JOINT_RADIUS,
                                 TORSO_SIZE.y / 2,
                                 0)

        # ── Joints (spheres) ──────────────────────────────────────────────────
        shoulder_sphere = vp.sphere(
            pos=shoulder_pos, radius=JOINT_RADIUS, color=C_SHOULDER)
        elbow_sphere = vp.sphere(
            pos=shoulder_pos + vp.vector(0, -UPPER_ARM_LEN, 0),
            radius=JOINT_RADIUS, color=C_ELBOW)
        wrist_sphere = vp.sphere(
            pos=elbow_sphere.pos + vp.vector(0, -FOREARM_LEN, 0),
            radius=JOINT_RADIUS, color=C_WRIST)

        # ── Bones (cylinders) ─────────────────────────────────────────────────
        upper_arm = vp.cylinder(
            pos=shoulder_pos,
            axis=vp.vector(0, -UPPER_ARM_LEN, 0),
            radius=BONE_RADIUS, color=C_UPPER_ARM)
        forearm = vp.cylinder(
            pos=elbow_sphere.pos,
            axis=vp.vector(0, -FOREARM_LEN, 0),
            radius=BONE_RADIUS, color=C_FOREARM)

        # ── Floating status label ─────────────────────────────────────────────
        status_label = vp.label(
            pos=vp.vector(0, -0.38, 0),
            text="Waiting for sensors...",
            color=vp.color.yellow, height=14, font="monospace", box=False)

        # Debug overlay — shows live ISB euler angles in the scene
        debug_label = vp.label(
            pos=vp.vector(0, -0.50, 0),
            text="",
            color=vp.color.cyan, height=12, font="monospace", box=False)

        _last_print = [0.0]

        # ── Caption: Calibrate button + live status text ──────────────────────
        scene.append_to_caption("\n")
        vp.button(
            text="  Calibrate (I-Pose)  ",
            bind=self._on_calibrate,
            color=vp.color.black,
            background=vp.vector(0.00, 1.00, 0.63),   # mint green
        )
        scene.append_to_caption("   ")
        self._cal_text = vp.wtext(text="Not calibrated")
        scene.append_to_caption(
            "\n\n"
            "<b>Key:</b>  "
            "<span style='color:#45b5ff'>■</span> Upper arm &nbsp;"
            "<span style='color:#b34fff'>■</span> Forearm &nbsp;"
            "<span style='color:#ffc700'>●</span> Shoulder &nbsp;"
            "<span style='color:#00ffa0'>●</span> Elbow &nbsp;"
            "<span style='color:#ff3d3d'>●</span> Wrist\n"
            "Drag to orbit  |  Scroll to zoom"
        )

        # ── Render loop ───────────────────────────────────────────────────────
        while True:
            vp.rate(RENDER_HZ)
            now = time.monotonic()

            # Keep AppState joint angles current (used by GUI metrics panel)
            if self._angles is not None:
                self._angles.update(now)

            # Fetch all quaternion data from shared state
            q_raw, q_ref, calibrated = self._read_state()

            # Step 1 — convert to anatomical frame (MOUNT applied once, same as joint_angles.py)
            live = {n: to_anatomical(q_raw[n], n) for n in q_raw}
            ref  = {n: to_anatomical(q_ref[n], n) for n in q_ref}

            # Step 2 — remove I-pose offset: R_ref.inv() * R_live
            corrected = {n: ref[n].inv() * live[n] for n in live}

            # Step 3 — relative joint rotations (ISB right-multiply)
            # Body rotation fully cancels here — rotating the whole torso
            # leaves shoulder_rot and elbow_rot unchanged.
            shoulder_rot = corrected["chest"].inv() * corrected["arm"]
            elbow_rot    = corrected["arm"].inv()   * corrected["wrist"]

            # ── Step 4: rotate segment direction vectors ───────────────────────
            # Upper arm: shoulder rotation applied to DOWN
            upper_dir_np = shoulder_rot.apply(DOWN_NP) * UPPER_ARM_LEN

            # Forearm: kinematic chain — shoulder first, then elbow relative to it
            fore_world_rot = shoulder_rot * elbow_rot
            fore_dir_np    = fore_world_rot.apply(DOWN_NP) * FOREARM_LEN

            # Remap anatomical frame → VPython display frame before rendering
            upper_dir = _np_to_vp(WORLD_ROT.apply(upper_dir_np))
            fore_dir  = _np_to_vp(WORLD_ROT.apply(fore_dir_np))

            # ── Step 5: update scene geometry ────────────────────────────────
            upper_arm.pos  = shoulder_pos
            upper_arm.axis = upper_dir

            elbow_pos        = shoulder_pos + upper_dir
            elbow_sphere.pos = elbow_pos

            forearm.pos      = elbow_pos
            forearm.axis     = fore_dir
            wrist_sphere.pos = elbow_pos + fore_dir

            # ── Debug angle overlay ──────────────────────────────────────────
            shou_euler = shoulder_rot.as_euler('YXY', degrees=True)
            elbow_euler = elbow_rot.as_euler('ZXY', degrees=True)
            debug_label.text = (
                f"Shldr Y-X-Y: {shou_euler[0]:+6.1f} {shou_euler[1]:+6.1f} {shou_euler[2]:+6.1f} deg"
                f"  |  Elbow Z: {elbow_euler[0]:+6.1f} deg"
            )
            # Also print to console once per second
            if now - _last_print[0] >= 1.0:
                print(f"[DBG] shoulder YXY: plane={shou_euler[0]:+6.1f} elev={shou_euler[1]:+6.1f} axial={shou_euler[2]:+6.1f} | elbow={elbow_euler[0]:+6.1f}")
                _last_print[0] = now

            # ── Status ────────────────────────────────────────────────────────
            n_connected = self._count_connected()
            if calibrated:
                status_label.text   = "Calibrated — tracking live"
                status_label.color  = vp.color.green
                self._cal_text.text = "✓ Calibrated"
            else:
                msg = f"Not calibrated  ({n_connected}/3 sensors connected)"
                status_label.text   = msg
                status_label.color  = vp.color.yellow
                self._cal_text.text = msg

    # ── Private helpers ───────────────────────────────────────────────────────

    def _read_state(self):
        """
        Returns (q_raw, q_ref, calibrated) dicts.
        All values are (w,x,y,z) tuples.
        Falls back to identity quaternions in standalone mode.
        """
        names = ["chest", "arm", "wrist"]

        if self._state is None:
            q = {n: IDENTITY_Q for n in names}
            return q, q, False

        with self._state.lock:
            calibrated = self._state.calibrated
            q_raw = {n: self._state.slots[n].get_quaternion() for n in names}
            if calibrated:
                q_ref = {
                    n: self._state.calibration_quats.get(n, IDENTITY_Q)
                    for n in names
                }
            else:
                q_ref = {n: IDENTITY_Q for n in names}

        return q_raw, q_ref, calibrated

    def _on_calibrate(self, _btn):
        """Calibrate button pressed in the VPython caption."""
        if self._state is None:
            print("[ArmRender] Standalone mode — nothing to calibrate.")
            return
        with self._state.lock:
            if not self._state.all_connected():
                print("[ArmRender] Cannot calibrate — not all sensors connected.")
                return
            for name in ["wrist", "arm", "chest"]:
                self._state.calibration_quats[name] = \
                    self._state.slots[name].get_quaternion()
            self._state.calibrated = True
        print("[ArmRender] Calibrated successfully.")

    def _count_connected(self):
        if self._state is None:
            return 0
        with self._state.lock:
            return sum(
                1 for n in ["wrist", "arm", "chest"]
                if self._state.slots[n].connected
            )


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Standalone test — identity quaternions, arm shown in I-pose.")
    print("Drag to orbit, scroll to zoom. Close window to quit.\n")
    ArmRender(state=None, angle_processor=None).run()