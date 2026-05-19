"""
gui/render_widget.py
--------------------
Centre panel — live 3-D arm skeleton using PyQtGraph OpenGL.

Replaces VPython entirely. Everything lives inside a GLViewWidget
so it sits naturally inside the Qt layout with zero threading issues.

Objects:
  - Torso  : GLBoxItem  (static, semi-transparent)
  - Upper arm : GLLinePlotItem rendered as a thick cylinder via
                two sphere joints + a line — or proper MeshItems
  - Joints : GLScatterPlotItem (spheres)

We use GLLinePlotItem for bones (simplest, no mesh needed) and
GLScatterPlotItem for joints. This avoids needing trimesh or any
external mesh library.

Pipeline per frame (identical to arm_render.py):
  1. Read raw quaternions from AppState under the lock.
  2. right-multiply correction: corr = live * ref.inv()
  3. Relative rotations: shoulder = chest.inv() * arm
                         elbow    = arm.inv()   * wrist
  4. Remap to anatomical via MOUNT conjugation.
  5. Rotate DOWN vector → segment world directions.
  6. Apply WORLD_ROT (+90° Y) to map anatomical → display frame.
  7. Update GLLinePlotItem positions.
"""

import numpy as np
from scipy.spatial.transform import Rotation

import pyqtgraph.opengl as gl
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
from PyQt5.QtCore import Qt

from PyQt5.QtGui import QVector3D
from calc.joint_angles import MOUNT_CHEST, MOUNT_ARM, MOUNT_WRIST, _to_rot

# ── Segment lengths (metres) ──────────────────────────────────────────────────
UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25

# ── Colours (RGBA 0–1) ────────────────────────────────────────────────────────
C_TORSO     = (1.0, 1.0, 1.0, 0.12)
C_UPPER_ARM = (0.27, 0.71, 1.00, 1.0)   # cyan-blue
C_FOREARM   = (0.70, 0.31, 1.00, 1.0)   # purple
C_SHOULDER  = (1.00, 0.78, 0.00, 1.0)   # amber
C_ELBOW     = (0.00, 1.00, 0.63, 1.0)   # mint
C_WRIST     = (1.00, 0.24, 0.24, 1.0)   # red
BG_COLOR    = (0.04, 0.04, 0.04, 1.0)

# ── Constants ─────────────────────────────────────────────────────────────────
DOWN_NP    = np.array([0., -1., 0.])
IDENTITY_Q = (1., 0., 0., 0.)
MOUNT      = {"chest": MOUNT_CHEST, "arm": MOUNT_ARM, "wrist": MOUNT_WRIST}

# Maps anatomical frame (X=fwd,Y=up,Z=right) → VPython/GL display frame
WORLD_ROT  = Rotation.from_euler("Y", 90, degrees=True)


def _sphere_mesh(radius: float, rows: int = 10, cols: int = 10) -> gl.GLMeshItem:
    """Generate a UV-sphere GLMeshItem centred at origin."""
    verts = []
    faces = []
    for r in range(rows + 1):
        lat = np.pi * r / rows - np.pi / 2
        for c in range(cols):
            lon = 2 * np.pi * c / cols
            x = radius * np.cos(lat) * np.cos(lon)
            y = radius * np.sin(lat)
            z = radius * np.cos(lat) * np.sin(lon)
            verts.append([x, y, z])
    verts = np.array(verts, dtype=np.float32)
    for r in range(rows):
        for c in range(cols):
            a = r * cols + c
            b = r * cols + (c + 1) % cols
            d = (r + 1) * cols + c
            e = (r + 1) * cols + (c + 1) % cols
            faces.append([a, b, e])
            faces.append([a, e, d])
    faces = np.array(faces, dtype=np.uint32)
    md = gl.MeshData(vertexes=verts, faces=faces)
    return gl.GLMeshItem(meshdata=md, smooth=True)


def _cylinder_mesh(start: np.ndarray, end: np.ndarray,
                   radius: float, segs: int = 12) -> gl.GLMeshItem:
    """Generate a cylinder GLMeshItem from start to end."""
    axis   = end - start
    length = np.linalg.norm(axis)
    if length < 1e-6:
        return gl.GLMeshItem()

    # Build cylinder along Z, then rotate to align with axis
    z_hat = axis / length
    # Find an orthogonal vector
    ref = np.array([1, 0, 0]) if abs(z_hat[0]) < 0.9 else np.array([0, 1, 0])
    x_hat = np.cross(ref, z_hat); x_hat /= np.linalg.norm(x_hat)
    y_hat = np.cross(z_hat, x_hat)

    angles = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    ring_bottom = start + radius * (np.outer(np.cos(angles), x_hat) +
                                    np.outer(np.sin(angles), y_hat))
    ring_top    = end   + radius * (np.outer(np.cos(angles), x_hat) +
                                    np.outer(np.sin(angles), y_hat))

    verts = np.vstack([ring_bottom, ring_top]).astype(np.float32)
    faces = []
    for i in range(segs):
        n = (i + 1) % segs
        faces.append([i, n, segs + n])
        faces.append([i, segs + n, segs + i])
    faces = np.array(faces, dtype=np.uint32)
    md = gl.MeshData(vertexes=verts, faces=faces)
    return gl.GLMeshItem(meshdata=md, smooth=False)


class RenderWidget(QWidget):
    """
    Centre panel containing the OpenGL arm skeleton and a status label.
    """

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state      = state
        self._calibrated = False
        self._build_ui()
        self._build_scene()

    # ── UI shell ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        hdr = QLabel("3D SKELETON")
        hdr.setStyleSheet(
            "color: #00b4ff; font-size: 11px; font-weight: bold; "
            "padding: 4px 8px; background: #0a0a0a;"
        )
        layout.addWidget(hdr)

        # GL view
        self._view = gl.GLViewWidget()
        self._view.setBackgroundColor(BG_COLOR)
        self._view.setCameraPosition(distance=1.2, elevation=15, azimuth=45)
        layout.addWidget(self._view, stretch=1)

        # Status + calibrate button row
        bar = QWidget()
        bar.setStyleSheet("background: #0a0a0a; border-top: 1px solid #232330;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 4, 8, 4)

        self._status_lbl = QLabel("Waiting for sensors...")
        self._status_lbl.setStyleSheet("color: #ffc800; font-size: 11px;")

        cal_btn = QPushButton("Calibrate (I-Pose)")
        cal_btn.setFixedHeight(28)
        cal_btn.setStyleSheet(
            "QPushButton { background: #00ffa0; color: #000; font-weight: bold; "
            "border: none; border-radius: 4px; padding: 4px 14px; }"
            "QPushButton:hover { background: #00cc80; }"
        )
        cal_btn.clicked.connect(self._on_calibrate)

        bar_layout.addWidget(self._status_lbl, stretch=1)
        bar_layout.addWidget(cal_btn)
        bar.setFixedHeight(36)
        layout.addWidget(bar)

    # ── Scene objects ─────────────────────────────────────────────────────────

    def _build_scene(self):
        """Create all persistent GL objects. Updated each frame by refresh()."""

        # Grid
        grid = gl.GLGridItem()
        grid.setSize(2, 2)
        grid.setSpacing(0.1, 0.1)
        grid.setColor((50, 50, 60, 80))
        self._view.addItem(grid)

        # Torso box (static)
        torso = gl.GLBoxItem(size=QVector3D(0.08, 0.20, 0.12), color=C_TORSO)
        torso.translate(-0.04, -0.10, -0.06)   # centre at origin
        self._view.addItem(torso)

        # Shoulder position (top-right of torso)
        self._shoulder_pos = np.array([0.04 + 0.038, 0.10, 0.0])

        # Joint spheres
        self._sph_shoulder = _sphere_mesh(0.038)
        self._sph_elbow    = _sphere_mesh(0.032)
        self._sph_wrist    = _sphere_mesh(0.026)
        self._set_mesh_color(self._sph_shoulder, C_SHOULDER)
        self._set_mesh_color(self._sph_elbow,    C_ELBOW)
        self._set_mesh_color(self._sph_wrist,    C_WRIST)
        self._view.addItem(self._sph_shoulder)
        self._view.addItem(self._sph_elbow)
        self._view.addItem(self._sph_wrist)

        # Bone cylinders (rebuilt each frame)
        self._upper_arm_mesh = None
        self._forearm_mesh   = None

        # Shoulder sphere stays fixed
        self._sph_shoulder.translate(*self._shoulder_pos)

        # Initial I-pose positions
        elbow_pos = self._shoulder_pos + np.array([0, -UPPER_ARM_LEN, 0])
        wrist_pos = elbow_pos          + np.array([0, -FOREARM_LEN,   0])
        self._sph_elbow.translate(*elbow_pos)
        self._sph_wrist.translate(*wrist_pos)
        self._rebuild_bones(elbow_pos, wrist_pos)

    @staticmethod
    def _set_mesh_color(mesh: gl.GLMeshItem, rgba):
        mesh.setColor(rgba)

    def _rebuild_bones(self, elbow_pos: np.ndarray, wrist_pos: np.ndarray):
        """Remove old bone meshes and add new ones at updated positions."""
        if self._upper_arm_mesh is not None:
            self._view.removeItem(self._upper_arm_mesh)
        if self._forearm_mesh is not None:
            self._view.removeItem(self._forearm_mesh)

        self._upper_arm_mesh = _cylinder_mesh(
            self._shoulder_pos, elbow_pos, radius=0.025)
        self._forearm_mesh   = _cylinder_mesh(
            elbow_pos, wrist_pos, radius=0.020)

        self._set_mesh_color(self._upper_arm_mesh, C_UPPER_ARM)
        self._set_mesh_color(self._forearm_mesh,   C_FOREARM)
        self._view.addItem(self._upper_arm_mesh)
        self._view.addItem(self._forearm_mesh)

    # ── Per-frame update ──────────────────────────────────────────────────────

    def refresh(self):
        q_raw, q_ref, calibrated = self._read_state()
        self._calibrated = calibrated

        # Right-multiply correction: corr = live * ref.inv()
        corr = {
            n: _to_rot(q_raw[n]) * _to_rot(q_ref[n]).inv()
            for n in ["chest", "arm", "wrist"]
        }

        # Relative joint rotations
        shoulder_world = corr["chest"].inv() * corr["arm"]
        elbow_world    = corr["arm"].inv()   * corr["wrist"]

        # Remap into anatomical frame via MOUNT conjugation
        shoulder_rot = MOUNT_CHEST * shoulder_world * MOUNT_CHEST.inv()
        elbow_rot    = MOUNT_ARM   * elbow_world    * MOUNT_ARM.inv()

        # Rotate DOWN vector to get segment directions
        upper_dir_np = shoulder_rot.apply(DOWN_NP) * UPPER_ARM_LEN
        fore_dir_np  = (shoulder_rot * elbow_rot).apply(DOWN_NP) * FOREARM_LEN

        # Remap into display frame
        upper_dir = WORLD_ROT.apply(upper_dir_np)
        fore_dir  = WORLD_ROT.apply(fore_dir_np)

        # Update joint sphere positions
        elbow_pos = self._shoulder_pos + upper_dir
        wrist_pos = elbow_pos + fore_dir

        # Move elbow and wrist spheres (translate is absolute — reset first)
        self._sph_elbow.resetTransform()
        self._sph_elbow.translate(*elbow_pos)
        self._sph_wrist.resetTransform()
        self._sph_wrist.translate(*wrist_pos)

        # Rebuild bone cylinders at new positions
        self._rebuild_bones(elbow_pos, wrist_pos)

        # Status label
        if calibrated:
            self._status_lbl.setText("✓ Calibrated — tracking live")
            self._status_lbl.setStyleSheet("color: #00ffa0; font-size: 11px;")
        else:
            n = self._count_connected()
            self._status_lbl.setText(f"Not calibrated  ({n}/3 connected)")
            self._status_lbl.setStyleSheet("color: #ffc800; font-size: 11px;")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _read_state(self):
        if self._state is None:
            q = {n: IDENTITY_Q for n in ["chest", "arm", "wrist"]}
            return q, q, False
        with self._state.lock:
            calibrated = self._state.calibrated
            q_raw = {n: self._state.slots[n].get_quaternion()
                     for n in ["chest", "arm", "wrist"]}
            q_ref = (
                {n: self._state.calibration_quats.get(n, IDENTITY_Q)
                 for n in ["chest", "arm", "wrist"]}
                if calibrated else
                {n: IDENTITY_Q for n in ["chest", "arm", "wrist"]}
            )
        return q_raw, q_ref, calibrated

    def _count_connected(self):
        if self._state is None:
            return 0
        with self._state.lock:
            return sum(
                1 for n in ["chest", "arm", "wrist"]
                if self._state.slots[n].connected
            )

    def _on_calibrate(self):
        if self._state is None:
            return
        with self._state.lock:
            if not self._state.all_connected():
                print("[RenderWidget] Cannot calibrate — not all sensors connected.")
                return
            for name in ["wrist", "arm", "chest"]:
                self._state.calibration_quats[name] = \
                    self._state.slots[name].get_quaternion()
            self._state.calibrated = True
        print("[RenderWidget] Calibrated.")