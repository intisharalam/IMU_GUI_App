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

UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25

# ── Colours ───────────────────────────────────────────────────────────────────
C_TORSO      = (0.75, 0.75, 0.80, 0.20)
C_UPPER_ARM  = (0.27, 0.71, 1.00, 1.0)
C_FOREARM    = (0.70, 0.31, 1.00, 1.0)
C_SHOULDER   = (1.00, 0.78, 0.00, 1.0)
C_ELBOW      = (0.00, 0.85, 0.50, 1.0)
C_WRIST      = (1.00, 0.24, 0.24, 1.0)
C_GOAL_FAR   = (1.00, 0.24, 0.24, 0.45)
C_GOAL_NEAR  = (1.00, 0.85, 0.00, 0.55)
C_GOAL_HIT   = (0.00, 0.85, 0.40, 0.70)
C_PHANTOM    = (0.55, 0.60, 0.65, 0.35)
BG_COLOR     = (0.10, 0.10, 0.12, 1.0)

C_CORRIDOR_TUBE  = (0.00, 0.60, 0.25, 0.12)
C_CORRIDOR_EDGE  = (0.00, 1.00, 0.41, 0.50)
C_CORRIDOR_WARN  = (1.00, 0.67, 0.00, 0.40)
CORRIDOR_RADIUS  = 0.055
CORRIDOR_STEPS   = 20

DOWN_NP    = np.array([0., -1., 0.])
IDENTITY_Q = (1., 0., 0., 0.)

# ── WORLD_ROT ─────────────────────────────────────────────────────────────────
# Original -90° Y maps anatomical → GL display frame.
# Additional -45° Y (Option B) rotates the live arm to match the static geometry.
# Combined: -90 + -45 = -135° around Y.
WORLD_ROT = Rotation.from_euler("Y", -25, degrees=True)

# ── R45Y — applied to all static geometry ─────────────────────────────────────
# Rotates a point 45° around Y so the phantom body & shoulder align with WORLD_ROT.
_R45Y = Rotation.from_euler("Y", 25, degrees=True)

def _r(pos):
    """Rotate a position vector by -45° around Y (Option B static geometry helper)."""
    return _R45Y.apply(np.asarray(pos, dtype=float))

DIR_JUMP_THRESH = 0.35
GOAL_HIT_DIST   = 0.06
GOAL_HOLD_S     = 3.0


# ── Mesh helpers ──────────────────────────────────────────────────────────────

def _corridor_arc_mesh(shoulder: np.ndarray, goal: np.ndarray,
                       n_steps: int = CORRIDOR_STEPS,
                       tube_r: float = CORRIDOR_RADIUS):
    goal_dir = goal - shoulder
    goal_dist = np.linalg.norm(goal_dir)
    if goal_dist < 0.01:
        return gl.GLMeshItem()
    goal_dir /= goal_dist

    start_dir = np.array([0., -1., 0.])

    cross = np.cross(start_dir, goal_dir)
    cross_len = np.linalg.norm(cross)
    if cross_len < 1e-6:
        pts = np.linspace(shoulder, shoulder + goal_dir * goal_dist, n_steps)
    else:
        angle = np.arccos(np.clip(np.dot(start_dir, goal_dir), -1, 1))
        pts = []
        for i in range(n_steps + 1):
            t = i / n_steps
            k = cross / cross_len
            d = (start_dir * np.cos(angle * t) +
                 np.cross(k, start_dir) * np.sin(angle * t) +
                 k * np.dot(k, start_dir) * (1 - np.cos(angle * t)))
            dist = goal_dist * t
            pts.append(shoulder + d * dist)
        pts = np.array(pts)

    verts = []; faces = []
    segs = 8
    angles = np.linspace(0, 2 * np.pi, segs, endpoint=False)

    for i in range(len(pts)):
        if i < len(pts) - 1:
            tangent = pts[i+1] - pts[i]
        else:
            tangent = pts[i] - pts[i-1]
        tangent_len = np.linalg.norm(tangent)
        if tangent_len < 1e-8:
            continue
        t = tangent / tangent_len
        ref = np.array([1,0,0]) if abs(t[0]) < 0.9 else np.array([0,1,0])
        bx = np.cross(ref, t); bx /= np.linalg.norm(bx)
        by = np.cross(t, bx)
        ring = [pts[i] + tube_r * (np.cos(a)*bx + np.sin(a)*by) for a in angles]
        verts.extend(ring)

    verts = np.array(verts, dtype=np.float32)
    n_rings = len(verts) // segs
    for r in range(n_rings - 1):
        for s in range(segs):
            a = r*segs + s
            b = r*segs + (s+1)%segs
            c = (r+1)*segs + s
            d = (r+1)*segs + (s+1)%segs
            faces += [[a,b,d],[a,d,c]]

    if len(verts) < 3 or not faces:
        return gl.GLMeshItem()

    return gl.GLMeshItem(
        meshdata=gl.MeshData(
            vertexes=verts,
            faces=np.array(faces, dtype=np.uint32)
        ),
        smooth=True
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
        self._corridor_mesh = None
        self._goal_active  = False
        self._goal_hit_t   = None
        self._rom_mode     = False
        self._rom_max      = {"flex":0.,"abd":0.,"rot":0.,"elbow":0.}
        self._playback_frames = []
        self._playback_idx    = 0
        self._playing         = False
        self._recording_geom  = False
        self._geom_buf        = []
        self._build_ui()
        self._build_scene()

    # ── UI shell ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        hdr = QLabel("3D SKELETON")
        hdr.setStyleSheet(
            "color:#aaccff; font-size:11px; font-weight:bold;"
            " padding:3px 8px; background:#16161e; border-bottom:1px solid #2a2a3a;"
        )
        lay.addWidget(hdr)

        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor(BG_COLOR)
        self._view.setCameraPosition(distance=1, elevation=90, azimuth=270)
        self._view.opts['center'] = pg.Vector(0, 0.15, 0)
        lay.addWidget(self._view, stretch=1)

        bar = QWidget()
        bar.setStyleSheet("background:#16161e;")
        bl = QHBoxLayout(bar); bl.setContentsMargins(8,3,8,3); bl.setSpacing(6)
        self._status_lbl = QLabel("Waiting for sensors...")
        self._status_lbl.setStyleSheet("color:#ccaa00; font-size:12px;")
        self._goal_lbl = QLabel("")
        self._goal_lbl.setStyleSheet("color:#cc4400; font-size:12px; font-weight:bold;")
        self._play_btn = QPushButton("▶ Replay")
        self._play_btn.setFixedHeight(24)
        self._play_btn.setEnabled(False)
        self._play_btn.setStyleSheet(
            "QPushButton{background:#003322;color:#00cc88;border:1px solid #005533;"
            " border-radius:3px;font-size:12px;padding:1px 8px;}"
            "QPushButton:hover{background:#004433;}"
            "QPushButton:disabled{background:#222;color:#555;border-color:#333;}"
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
        grid.setColor((80, 80, 100, 140))
        grid.rotate(90, 1, 0, 0)
        grid.translate(0, -0.95, 0)
        self._view.addItem(grid)

        # Coordinate frame — axes rotated by _R45Y to match static geometry
        for vec, color in [
            (_r((0.1, 0, 0)),   (255,   0,   0, 255)),
            (_r((0,   0.1, 0)), (  0, 255,   0, 255)),
            (_r((0,   0, 0.1)), (  0,   0, 255, 255)),
        ]:
            arrow = gl.GLLinePlotItem(
                pos=np.array([[0,0,0], vec]),
                color=color, width=3, antialias=True
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

        # Goal sphere
        self._goal_sphere = _sphere_mesh(0.060)
        self._goal_sphere.setColor(C_GOAL_FAR)
        self._goal_sphere.setVisible(False)
        self._view.addItem(self._goal_sphere)

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

        # Torso — GLBoxItem doesn't support arbitrary rotation so we
        # build it as a cylinder approximation via its centre point
        torso = gl.GLBoxItem(size=QVector3D(0.26, 0.48, 0.16), color=C_PHANTOM)
        # Compute rotated bottom-left-back corner
        centre = _r([0, 0.14, 0])
        torso.translate(centre[0] - 0.13, centre[1] - 0.24, centre[2] - 0.08)
        torso.setGLOptions('translucent')
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

    def set_goal(self, wrist_target_anat: np.ndarray):
        gl_pos = WORLD_ROT.apply(wrist_target_anat) + self._shoulder_pos
        self._goal_pos = gl_pos
        self._goal_active = True
        self._goal_hit_t = None
        self._goal_sphere.resetTransform()
        self._goal_sphere.translate(*gl_pos)
        self._goal_sphere.setColor(C_GOAL_FAR)
        self._goal_sphere.setVisible(True)
        self._update_corridor(gl_pos)

    def clear_goal(self):
        self._goal_active = False
        self._goal_pos = None
        self._goal_hit_t = None
        self._goal_sphere.setVisible(False)
        self._goal_lbl.setText("")
        self._clear_corridor()

    # ── Corridor ──────────────────────────────────────────────────────────────

    def _update_corridor(self, goal_gl: np.ndarray):
        self._clear_corridor()
        mesh = _corridor_arc_mesh(self._shoulder_pos, goal_gl)
        mesh.setColor(C_CORRIDOR_TUBE)
        mesh.setGLOptions('translucent')
        self._view.addItem(mesh)
        self._corridor_mesh = mesh

    def _clear_corridor(self):
        if self._corridor_mesh is not None:
            self._view.removeItem(self._corridor_mesh)
            self._corridor_mesh = None

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

        if self._goal_active and self._goal_pos is not None:
            dist = np.linalg.norm(wrist_pos - self._goal_pos)
            now  = time.monotonic()
            if dist < GOAL_HIT_DIST:
                self._goal_sphere.setColor(C_GOAL_HIT)
                if self._goal_hit_t is None:
                    self._goal_hit_t = now
                held = now - self._goal_hit_t
                rem  = max(0., GOAL_HOLD_S - held)
                self._goal_lbl.setText(f"Hold! {rem:.1f}s")
                self._goal_lbl.setStyleSheet("color:#00cc66; font-size:10px; font-weight:bold;")
                if held >= GOAL_HOLD_S:
                    self.goal_achieved.emit()
                    self._goal_hit_t = None
                    self.clear_goal()
            elif dist < GOAL_HIT_DIST * 4:
                self._goal_sphere.setColor(C_GOAL_NEAR)
                self._goal_hit_t = None
                self._goal_lbl.setText(f"Close — {dist*100:.0f}cm")
                self._goal_lbl.setStyleSheet("color:#ccaa00; font-size:10px;")
            else:
                self._goal_sphere.setColor(C_GOAL_FAR)
                self._goal_hit_t = None
                self._goal_lbl.setText(f"Goal: {dist*100:.0f}cm")
                self._goal_lbl.setStyleSheet("color:#cc4400; font-size:10px;")

        n_conn = sum(1 for n in ["chest","arm","wrist"]
                     if self._state.slots[n].connected)
        if calibrated:
            self._status_lbl.setText("✓ Calibrated — tracking live")
            self._status_lbl.setStyleSheet("color:#00cc66; font-size:12px;")
        else:
            self._status_lbl.setText(f"Not calibrated  ({n_conn}/3 connected)")
            self._status_lbl.setStyleSheet("color:#ccaa00; font-size:12px;")