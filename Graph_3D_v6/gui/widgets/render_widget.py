"""
gui/render_widget.py  —  v4.1
------------------------------
PyQtGraph OpenGL arm skeleton with phantom body.

COORDINATE SYSTEM — FINAL FIX
══════════════════════════════
pyqtgraph GL axes (right-handed):
  +X = right on screen
  +Y = up on screen
  +Z = toward viewer (out of screen)

Anatomical axes (our frame):
  +X = FORWARD (in front of patient, away from viewer)
  +Y = UP
  +Z = RIGHT (patient's right = screen left from front view)

We want to see the patient from the FRONT, so:
  anatomical UP    (0,1,0) → GL UP    (0,1,0)   ✓
  anatomical RIGHT (0,0,1) → GL RIGHT (+X)
  anatomical FWD   (1,0,0) → GL AWAY  (-Z)

Rotation that achieves this:
  anat X→GL(-Z), anat Y→GL(Y), anat Z→GL(X)
  This is a -90° rotation around Y.

BUT: in I-pose the arm hangs DOWN in the anatomical frame = (0,-1,0).
After -90°Y: (0,-1,0) → (0,-1,0). ✓ Still down on screen.

Problem that was visible: shoulder was placed at GL (+X+Y,0) which is
top-right in the GL frame. From the front view the shoulder should be
at screen-right (+X) and elevated (+Y). That is correct, but the
CAMERA was looking from azimuth=30 which is a front-right angle showing
the torso from the side — so the arm appeared to go along the horizontal.

FIX: camera azimuth=0 (directly in front), small elevation so we see
the arm hang down. Also swap shoulder to +X side (screen-right = 
patient right from front view is screen-LEFT, but since we're showing
the patient's right arm, it should be on the viewer's left — set
shoulder at -X+Y to keep it left on screen).

Actually the simplest mental model: place everything, then set camera
looking straight at the patient (azimuth=180 looks from front in pyqtgraph,
azimuth=0 looks from behind). We use azimuth=225 to get front-left view
which shows the right arm on the right side of the screen.

PHANTOM BODY:
  Static grey GL shapes approximating head, neck, spine, pelvis, left arm.
  Drawn once, never updated. Gives spatial reference for the moving arm.

OPTION B — 45° Y rotation:
  All static geometry (phantom body, shoulder position, coordinate frame)
  is passed through R45Y before being placed in GL space.
  WORLD_ROT is updated to incorporate the extra 45° so the live arm
  segments rotate to match.
"""

import time
import numpy as np
from scipy.spatial.transform import Rotation

import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt5.QtGui import QVector3D
from PyQt5.QtCore import pyqtSignal

from calc.joint_angles import MOUNT, to_anatomical, QuaternionFilter
from gui.widgets.exercise_guide import ExerciseGuide

UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25

# ── Colours ───────────────────────────────────────────────────────────────────
C_TORSO      = (0.75, 0.75, 0.80, 0.20)
C_UPPER_ARM  = (0.22, 0.55, 0.95, 1.0)   # clear sky blue
C_FOREARM    = (0.38, 0.22, 0.82, 1.0)   # rich violet
C_SHOULDER   = (0.95, 0.70, 0.10, 1.0)   # warm amber
C_ELBOW      = (0.08, 0.75, 0.48, 1.0)   # teal green
C_WRIST      = (0.92, 0.25, 0.25, 1.0)   # coral red
C_PHANTOM    = (0.62, 0.65, 0.70, 0.60)  # blue-grey, more opaque
BG_COLOR     = '#ffffff'                  # ← string form, always works

DOWN_NP    = np.array([0., -1., 0.])
IDENTITY_Q = (1., 0., 0., 0.)

W_ROT = 15

# ── WORLD_ROT ─────────────────────────────────────────────────────────────────
# Pure anatomical → GL display frame: -90°Y
# (anat X=FWD → GL -Z, anat Y=UP → GL Y, anat Z=RIGHT → GL X)
_BASE_ROT = Rotation.from_euler("Y", -90 + W_ROT*2, degrees=True)

# ── _R45Y — display orientation offset ────────────────────────────────────────
# W_ROT rotates the entire scene (static body AND live arm) around Y.
# Applied as a second step after _BASE_ROT so both use exactly the same transform.
_R45Y = Rotation.from_euler("Y", W_ROT, degrees=True)

# Combined rotation applied to live arm vectors: first anatomical→GL, then offset
WORLD_ROT = _R45Y * _BASE_ROT

def _r(pos):
    """Rotate a static geometry position by W_ROT around Y (display offset)."""
    return _R45Y.apply(np.asarray(pos, dtype=float))

DIR_JUMP_THRESH = 0.35


# ── Mesh helpers ──────────────────────────────────────────────────────────────
# def _box_mesh(w, h, d):
#     """Solid filled box centred at origin with dimensions w×h×d."""
#     x, y, z = w/2, h/2, d/2
#     verts = np.array([
#         [-x,-y,-z],[ x,-y,-z],[ x, y,-z],[-x, y,-z],  # back
#         [-x,-y, z],[ x,-y, z],[ x, y, z],[-x, y, z],  # front
#     ], dtype=np.float32)
#     faces = np.array([
#         [0,1,2],[0,2,3],  # back
#         [4,6,5],[4,7,6],  # front
#         [0,4,5],[0,5,1],  # bottom
#         [2,6,7],[2,7,3],  # top
#         [1,5,6],[1,6,2],  # right
#         [0,3,7],[0,7,4],  # left
#     ], dtype=np.uint32)
#     return gl.GLMeshItem(
#         meshdata=gl.MeshData(vertexes=verts, faces=faces),
#         smooth=False)

def _torso_mesh():
    """Solid filled cylinder approximating the torso."""
    return _cylinder_mesh(
        start  = _r([0,  0.23, 0]),
        end    = _r([0, -0.25, 0]),
        radius = 0.13,
        segs   = 20,
    )

def _sphere_mesh(radius, rows=12, cols=12):
    verts, faces = [], []
    for r in range(rows + 1):
        lat = np.pi * r / rows - np.pi / 2
        for c in range(cols):
            lon = 2 * np.pi * c / cols
            verts.append([radius * np.cos(lat) * np.cos(lon),
                          radius * np.sin(lat),
                          radius * np.cos(lat) * np.sin(lon)])
    verts = np.array(verts, dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            a = r*cols+c; b = r*cols+(c+1)%cols
            d = (r+1)*cols+c; e = (r+1)*cols+(c+1)%cols
            faces += [[a,b,e],[a,e,d]]
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=verts, faces=np.array(faces, dtype=np.uint32)),
        smooth=True)


def _cylinder_mesh(start, end, radius, segs=14):
    axis = end - start; L = np.linalg.norm(axis)
    if L < 1e-6:
        return gl.GLMeshItem()
    z = axis / L
    ref = np.array([1,0,0]) if abs(z[0]) < 0.9 else np.array([0,1,0])
    x = np.cross(ref, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    angles = np.linspace(0, 2*np.pi, segs, endpoint=False)
    rb = start + radius*(np.outer(np.cos(angles),x) + np.outer(np.sin(angles),y))
    rt = end   + radius*(np.outer(np.cos(angles),x) + np.outer(np.sin(angles),y))
    verts = np.vstack([rb, rt]).astype(np.float32)
    faces = []
    for i in range(segs):
        n = (i+1)%segs
        faces += [[i,n,segs+n],[i,segs+n,segs+i]]
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=verts, faces=np.array(faces, dtype=np.uint32)),
        smooth=False)


def _ellipsoid_mesh(rx, ry, rz, rows=10, cols=10):
    verts, faces = [], []
    for r in range(rows + 1):
        lat = np.pi * r / rows - np.pi / 2
        for c in range(cols):
            lon = 2 * np.pi * c / cols
            verts.append([rx * np.cos(lat) * np.cos(lon),
                          ry * np.sin(lat),
                          rz * np.cos(lat) * np.sin(lon)])
    verts = np.array(verts, dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            a = r*cols+c; b = r*cols+(c+1)%cols
            d = (r+1)*cols+c; e = (r+1)*cols+(c+1)%cols
            faces += [[a,b,e],[a,e,d]]
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=verts, faces=np.array(faces, dtype=np.uint32)),
        smooth=True)


class RenderWidget(QWidget):
    goal_achieved = pyqtSignal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state        = state
        self._filters      = {n: QuaternionFilter() for n in ["chest","arm","wrist"]}
        self._prev_upper   = np.array([0., -UPPER_ARM_LEN, 0.])
        self._prev_fore    = np.array([0., -FOREARM_LEN,   0.])
        self._goal_pos     = None
        self._goal_active  = False
        self._rom_mode     = False
        self._rom_max      = {"flex":0.,"abd":0.,"rot":0.,"elbow":0.}
        self._playback_frames = []
        self._playback_idx    = 0
        self._playing         = False
        self._recording_geom  = False
        self._geom_buf        = []
        # Guide playback — stops after 2 patient reps detected
        self._guide_rep_target   = 2
        self._guide_reps_seen    = 0
        self._guide_last_reps    = 0   # session_reps value last tick
        self._build_ui()
        self._build_scene()
        self._guide = ExerciseGuide(self._view)

    # ── UI shell ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        hdr = QLabel("3D Motion Capture")
        hdr.setStyleSheet(
            "color:#000000; font-size:11px; font-weight:bold;"
            " padding:3px 8px; background:#ffffff; border-bottom:1px solid #dddddd;"
        )
        lay.addWidget(hdr)

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor(BG_COLOR)
        self._view.setCameraPosition(distance=1, elevation=90, azimuth=270)
        self._view.opts['center'] = pg.Vector(0, 0.15, 0)
        lay.addWidget(self._view, stretch=1)

        bar = QWidget()
        bar.setStyleSheet("background:#ffffff;")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8,3,8,3); bl.setSpacing(6)
        self._status_lbl = QLabel("Waiting for sensors...")
        self._status_lbl.setStyleSheet("color:#000000; font-size:12px;")
        self._goal_lbl = QLabel("")
        self._goal_lbl.setStyleSheet("color:#aa2200; font-size:12px; font-weight:bold;")
        self._play_btn = QPushButton("▶ Replay")
        self._play_btn.setFixedHeight(24)
        self._play_btn.setEnabled(False)
        self._play_btn.setStyleSheet(
            "QPushButton{background:#d0ece0;color:#006633;border:1px solid #88bbaa;"
            " border-radius:3px;font-size:12px;padding:1px 8px;}"
            "QPushButton:hover{background:#bbddcc;}"
            "QPushButton:disabled{background:#e8e8e8;color:#aaa;border-color:#ccc;}"
        )
        self._play_btn.clicked.connect(self._toggle_playback)
        bl.addWidget(self._status_lbl, stretch=1)
        bl.addWidget(self._goal_lbl)
        bl.addWidget(self._play_btn)
        bar.setFixedHeight(30)
        lay.addWidget(bar)

    # ── Scene ─────────────────────────────────────────────────────────────────

    def _build_scene(self):
        # Grid
        grid = gl.GLGridItem()
        grid.setSize(4, 4); grid.setSpacing(0.20, 0.20)
        grid.setColor((160, 165, 180, 80))
        grid.rotate(90, 1, 0, 0)
        grid.translate(0, -0.95, 0)
        self._view.addItem(grid)

        # Coordinate frame — axes rotated by _R45Y to match static geometry
        for vec, color in [
            (_r((0.1, 0, 0)),   (255,   0,   0, 255)),
            (_r((0,   0.1, 0)), (  0, 255,   0, 255)),
            (_r((0,   0, 0.1)), (  0,   0, 255, 255)),
        ]:
            # Coordinate axes — thinner and more subtle
            arrow = gl.GLLinePlotItem(
                pos=np.array([[0,0,0], vec]),
                color=color, width=1.5, antialias=True
            )
            self._view.addItem(arrow)

        # Phantom body
        self._build_phantom_body()

        # Shoulder — _r() rotates the position into the new frame
        self._shoulder_pos = _r([-0.16, 0.38, 0.0])

        self._sph_shoulder = _sphere_mesh(0.040); self._sph_shoulder.setColor(C_SHOULDER)
        self._sph_elbow    = _sphere_mesh(0.033); self._sph_elbow.setColor(C_ELBOW)
        self._sph_wrist    = _sphere_mesh(0.027); self._sph_wrist.setColor(C_WRIST)
        for s in [self._sph_shoulder, self._sph_elbow, self._sph_wrist]:
            self._view.addItem(s)
        self._sph_shoulder.translate(*self._shoulder_pos)

        elbow0 = self._shoulder_pos + np.array([0, -UPPER_ARM_LEN, 0])
        wrist0 = elbow0 + np.array([0, -FOREARM_LEN, 0])
        self._sph_elbow.translate(*elbow0)
        self._sph_wrist.translate(*wrist0)

        self._upper_mesh = self._fore_mesh = None
        self._rebuild_bones(elbow0, wrist0)

    def _build_phantom_body(self):
        """
        Static grey body reference. All positions passed through _r() to apply
        the -45° Y rotation before placement in GL space.
        """
        def _add(mesh, colour=C_PHANTOM):
            mesh.setColor(colour)
            mesh.setGLOptions('translucent')
            self._view.addItem(mesh)

        # Head
        head = _ellipsoid_mesh(0.09, 0.11, 0.09)
        head.translate(*_r([0, 0.62, 0]))
        _add(head)

        # Neck
        neck = _cylinder_mesh(_r([0, 0.38, 0]), _r([0, 0.50, 0]), 0.040)
        _add(neck)

        # Torso
        # torso = _box_mesh(0.26, 0.48, 0.16)
        torso = _torso_mesh()
        torso.setColor(C_PHANTOM)
        torso.setGLOptions('translucent')
        centre = _r([0, 0.14, 0])
        torso.translate(centre[0], centre[1], centre[2])
        self._view.addItem(torso)

        # Pelvis
        pelvis = _ellipsoid_mesh(0.15, 0.10, 0.11)
        pelvis.translate(*_r([0, -0.16, 0]))
        _add(pelvis)

        # Left arm (phantom)
        l_shoulder = _r([0.15, 0.38, 0.0])
        l_elbow    = _r([0.15, 0.10, 0.0])
        l_wrist    = _r([0.15, -0.12, 0.0])
        _add(_cylinder_mesh(l_shoulder, l_elbow, 0.025))
        _add(_cylinder_mesh(l_elbow,    l_wrist, 0.020))
        ls = _sphere_mesh(0.038); ls.translate(*l_shoulder); _add(ls)
        le = _sphere_mesh(0.030); le.translate(*l_elbow);    _add(le)
        lw = _sphere_mesh(0.025); lw.translate(*l_wrist);    _add(lw)

        # Upper legs
        for sx in [0.08, -0.08]:
            hip  = _r([sx, -0.22, 0.0])
            knee = _r([sx, -0.60, 0.0])
            _add(_cylinder_mesh(hip, knee, 0.040))
            kn = _sphere_mesh(0.042); kn.translate(*knee); _add(kn)

    def _rebuild_bones(self, elbow, wrist):
        for attr in ["_upper_mesh", "_fore_mesh"]:
            m = getattr(self, attr)
            if m is not None:
                self._view.removeItem(m)
        self._upper_mesh = _cylinder_mesh(self._shoulder_pos, elbow, 0.026)
        self._fore_mesh  = _cylinder_mesh(elbow, wrist, 0.021)
        self._upper_mesh.setColor(C_UPPER_ARM)
        self._fore_mesh.setColor(C_FOREARM)
        self._view.addItem(self._upper_mesh)
        self._view.addItem(self._fore_mesh)

    # ── Goal sphere ───────────────────────────────────────────────────────────

    # ── Exercise guide ────────────────────────────────────────────────────────

    def set_exercise_guide(self, ex) -> None:
        """Load and start the recorded guide for the given ExerciseDef (if one exists)."""
        self._guide_reps_seen = 0
        self._guide_last_reps = 0
        found = self._guide.load(ex.name if ex else "", self._shoulder_pos)
        if found:
            self._guide.start()

    def clear_exercise_guide(self) -> None:
        """Remove the ghost-arm guide from the scene."""
        self._guide.stop()

    def record_guide(self, ex_name: str) -> None:
        """Save current _geom_buf as the guide for ex_name."""
        from gui.widgets.exercise_guide import save_guide
        if self._geom_buf:
            path = save_guide(ex_name, list(self._geom_buf))
            print(f"[GUIDE] Saved {len(self._geom_buf)} frames to {path}")

    def set_goal(self, wrist_target_anat: np.ndarray):
        pass  # goal sphere removed

    def clear_goal(self):
        self._goal_active = False
        self._goal_lbl.setText("")

    # ── ROM mode ──────────────────────────────────────────────────────────────

    def _toggle_rom_mode(self):
        if not self._rom_mode:
            self._rom_mode = True
            self._rom_max = {"flex":0.,"abd":0.,"rot":0.,"elbow":0.}
        else:
            self._rom_mode = False
            if self._state:
                with self._state.lock:
                    self._state.rom_flex_limit  = max(self._rom_max["flex"],  10.)
                    self._state.rom_abd_limit   = max(self._rom_max["abd"],   10.)
                    self._state.rom_rot_limit   = max(self._rom_max["rot"],   10.)
                    self._state.rom_elbow_limit = max(self._rom_max["elbow"], 10.)
                    self._state.rom_measured    = True
            print(f"[ROM] Result: {self._rom_max}")

    def get_rom_result(self):
        return dict(self._rom_max)

    # ── Playback ──────────────────────────────────────────────────────────────

    def start_geometry_recording(self):
        self._geom_buf = []; self._recording_geom = True

    def stop_geometry_recording(self):
        self._recording_geom = False
        self._playback_frames = list(self._geom_buf)
        if self._playback_frames:
            self._play_btn.setEnabled(True)
        print(f"[REC] {len(self._playback_frames)} geometry frames recorded.")

    def _toggle_playback(self):
        if not self._playback_frames:
            return
        if not self._playing:
            self._playing = True
            self._playback_idx = 0
            self._play_btn.setText("⏹ Stop")
        else:
            self._playing = False
            self._play_btn.setText("▶ Replay")

    # ── Per-frame ─────────────────────────────────────────────────────────────

    def refresh(self):
        if self._playing:
            self._tick_playback()
        else:
            self._tick_live()

    def _tick_playback(self):
        if not self._playback_frames:
            self._playing = False; return
        if self._playback_idx >= len(self._playback_frames):
            self._playing = False
            self._play_btn.setText("▶ Replay")
            self._playback_idx = 0; return
        upper_dir, fore_dir = self._playback_frames[self._playback_idx]
        self._playback_idx += 1
        elbow_pos = self._shoulder_pos + upper_dir
        wrist_pos = elbow_pos + fore_dir
        self._sph_elbow.resetTransform(); self._sph_elbow.translate(*elbow_pos)
        self._sph_wrist.resetTransform(); self._sph_wrist.translate(*wrist_pos)
        self._rebuild_bones(elbow_pos, wrist_pos)

    def _tick_live(self):
        if not self._state: return

        with self._state.lock:
            calibrated = self._state.calibrated
            q_raw = {n: self._state.slots[n].get_quaternion()
                     for n in ["chest","arm","wrist"]}
            q_ref = ({n: self._state.calibration_quats.get(n, IDENTITY_Q)
                      for n in ["chest","arm","wrist"]}
                     if calibrated else
                     {n: IDENTITY_Q for n in ["chest","arm","wrist"]})

            flex  = self._state.shoulder_flexion
            abd   = self._state.shoulder_abduction
            rot   = self._state.external_rotation
            elbow = self._state.elbow_flexion

            cal_id = id(self._state.calibration_quats) if calibrated else 0
            if not hasattr(self, '_last_cal_id_render'):
                self._last_cal_id_render = 0
            if cal_id != self._last_cal_id_render:
                self._last_cal_id_render = cal_id
                self._prev_upper = np.array([0., -UPPER_ARM_LEN, 0.])
                self._prev_fore  = np.array([0., -FOREARM_LEN,   0.])

        if self._rom_mode:
            self._rom_max["flex"]  = max(self._rom_max["flex"],  abs(flex))
            self._rom_max["abd"]   = max(self._rom_max["abd"],   abs(abd))
            self._rom_max["rot"]   = max(self._rom_max["rot"],   abs(rot))
            self._rom_max["elbow"] = max(self._rom_max["elbow"], abs(elbow))

        live = {n: self._filters[n].update(q_raw[n]) * MOUNT[n].inv()
                for n in ["chest","arm","wrist"]}
        ref  = {n: to_anatomical(q_ref[n], n) for n in ["chest","arm","wrist"]}
        corr = {n: ref[n].inv() * live[n] for n in ["chest","arm","wrist"]}

        shoulder_rot = corr["chest"].inv() * corr["arm"]
        elbow_rot    = corr["arm"].inv()   * corr["wrist"]

        upper_anat = shoulder_rot.apply(DOWN_NP) * UPPER_ARM_LEN
        fore_anat  = (shoulder_rot * elbow_rot).apply(DOWN_NP) * FOREARM_LEN
        upper_disp = WORLD_ROT.apply(upper_anat)
        fore_disp  = WORLD_ROT.apply(fore_anat)

        def _safe(new, prev):
            if np.linalg.norm(new) < 1e-6: return prev
            nn = new / np.linalg.norm(new)
            pn = prev / (np.linalg.norm(prev) + 1e-9)
            return prev if abs(np.dot(nn, pn)) < (1. - DIR_JUMP_THRESH) else new

        upper_disp = _safe(upper_disp, self._prev_upper)
        fore_disp  = _safe(fore_disp,  self._prev_fore)
        self._prev_upper = upper_disp
        self._prev_fore  = fore_disp

        elbow_pos = self._shoulder_pos + upper_disp
        wrist_pos = elbow_pos + fore_disp

        self._sph_elbow.resetTransform(); self._sph_elbow.translate(*elbow_pos)
        self._sph_wrist.resetTransform(); self._sph_wrist.translate(*wrist_pos)
        self._rebuild_bones(elbow_pos, wrist_pos)

        if self._recording_geom:
            self._geom_buf.append((upper_disp.copy(), fore_disp.copy()))

        # Guide playback — tick and check rep count to stop after 2
        if self._guide.active:
            with self._state.lock:
                current_reps = self._state.session_reps
            new_reps = current_reps - self._guide_last_reps
            if new_reps > 0:
                self._guide_reps_seen += new_reps
                self._guide_last_reps = current_reps
            if self._guide_reps_seen >= self._guide_rep_target:
                self._guide.stop()
            else:
                self._guide.tick()



        n_conn = sum(1 for n in ["chest","arm","wrist"]
                     if self._state.slots[n].connected)
        if calibrated:
            self._status_lbl.setText("✓ Calibrated — tracking live")
            self._status_lbl.setStyleSheet("color:#007744; font-size:12px;")
        else:
            self._status_lbl.setText(f"Not calibrated  ({n_conn}/3 connected)")
            self._status_lbl.setStyleSheet("color:#ccaa00; font-size:12px;")