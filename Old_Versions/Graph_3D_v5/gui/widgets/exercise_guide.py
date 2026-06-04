"""
gui/widgets/exercise_guide.py
------------------------------
Static ghost-arm guide for the active exercise.

A second semi-transparent arm skeleton (upper arm, forearm, elbow sphere,
wrist sphere) is drawn at the target pose for the current exercise so the
patient can see exactly where their arm needs to go.

Architecture
────────────
ExerciseGuide owns its GL items and is handed the GLViewWidget to add them
to.  RenderWidget creates one instance and calls:

    guide.set_pose(ex, shoulder_pos)   — on session start / exercise change
    guide.clear()                       — on session end

Geometry pipeline
─────────────────
All positions are computed in GL display space using the same WORLD_ROT and
_r() helper that render_widget uses for the live arm.  The shoulder_pos is
passed in from RenderWidget so the ghost arm originates from exactly the
same point as the live arm.

Per-exercise pose definitions
──────────────────────────────
Each supported exercise has a _pose_*() method that returns:
    (upper_dir_gl, fore_dir_gl)
Both are 3-D vectors in GL space, lengths = UPPER_ARM_LEN / FOREARM_LEN.

Unsupported exercises → guide stays hidden.

Reaching the goal sphere
────────────────────────
session_panel._goal_pos() places the goal sphere at:
    WORLD_ROT.apply(DOWN * (UPPER + FORE)) rotated by the exercise angles

The ghost wrist = shoulder_pos + upper_dir_gl + fore_dir_gl must equal that
same point.  All _pose_*() methods use the identical rotation pipeline, so
the wrist lands exactly on the goal sphere centre.
"""

import numpy as np
from scipy.spatial.transform import Rotation

import pyqtgraph.opengl as gl

UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25
DOWN_NP       = np.array([0., -1., 0.])

# Set to False to disable the ghost-arm guide globally
SHOW_EXERCISE_GUIDE = False

# Ghost colour — light grey, clearly translucent
C_GHOST = (0.70, 0.72, 0.75, 0.30)

# Combined anatomical→GL rotation (same as render_widget.WORLD_ROT)
_WORLD_ROT = Rotation.from_euler("Y", -135, degrees=True)


def _seg(flex_deg: float, abd_deg: float,
         elbow_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute (upper_dir_gl, fore_dir_gl) for a given shoulder pose.

    flex_deg   — shoulder flexion  (rotation around Z in anatomical frame)
    abd_deg    — shoulder abduction (rotation around X in anatomical frame)
    elbow_deg  — elbow flexion     (additional rotation of forearm around Z)

    Both returned vectors are in GL display space and have the correct
    physical lengths (UPPER_ARM_LEN, FOREARM_LEN).
    """
    shoulder_rot = Rotation.from_euler("ZX", [-flex_deg, abd_deg], degrees=True)
    elbow_rot    = Rotation.from_euler("Z",  -elbow_deg,           degrees=True)

    upper_anat = shoulder_rot.apply(DOWN_NP) * UPPER_ARM_LEN
    fore_anat  = (shoulder_rot * elbow_rot).apply(DOWN_NP) * FOREARM_LEN

    return _WORLD_ROT.apply(upper_anat), _WORLD_ROT.apply(fore_anat)


# ── Mesh helpers (thin wrappers used only here) ───────────────────────────────

def _cylinder(start: np.ndarray, end: np.ndarray, radius: float, segs: int = 12):
    axis = end - start
    L = np.linalg.norm(axis)
    if L < 1e-6:
        return gl.GLMeshItem()
    z = axis / L
    ref = np.array([1, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1, 0])
    x = np.cross(ref, z); x /= np.linalg.norm(x)
    y = np.cross(z, x)
    angles = np.linspace(0, 2 * np.pi, segs, endpoint=False)
    rb = start + radius * (np.outer(np.cos(angles), x) + np.outer(np.sin(angles), y))
    rt = end   + radius * (np.outer(np.cos(angles), x) + np.outer(np.sin(angles), y))
    verts = np.vstack([rb, rt]).astype(np.float32)
    faces = []
    for i in range(segs):
        n = (i + 1) % segs
        faces += [[i, n, segs + n], [i, segs + n, segs + i]]
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=verts,
                             faces=np.array(faces, dtype=np.uint32)),
        smooth=False,
    )


def _sphere(radius: float, rows: int = 8, cols: int = 8):
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
            faces += [[a, b, e], [a, e, d]]
    return gl.GLMeshItem(
        meshdata=gl.MeshData(vertexes=verts,
                             faces=np.array(faces, dtype=np.uint32)),
        smooth=True,
    )


# ── Per-exercise pose functions ───────────────────────────────────────────────
# Each returns (upper_dir_gl, fore_dir_gl).
# Naming: _pose_<EXERCISE_NAME_SNAKE>

def _pose_pendulum_swing():
    """Arm swings forward ~30° from vertical."""
    return _seg(flex_deg=30, abd_deg=0)

def _pose_elbow_curl():
    """
    Elbow curl: upper arm hangs vertically (shoulder at 0°),
    forearm bent 90° at elbow toward shoulder.
    The shoulder stays in I-pose; only the elbow flexes.
    """
    return _seg(flex_deg=0, abd_deg=0, elbow_deg=90)

def _pose_finger_wall_crawl():
    """Arm raised forward to 90° (arm horizontal, pointing forward)."""
    return _seg(flex_deg=90, abd_deg=0)

def _pose_cross_body_stretch():
    """
    Arm drawn across body — modelled as abduction into the cross-body plane.
    40° abduction brings the arm across the midline at a comfortable angle.
    """
    return _seg(flex_deg=0, abd_deg=40)

def _pose_flexion_raise():
    """Arm raised forward to 90°."""
    return _seg(flex_deg=90, abd_deg=0)

def _pose_abduction_raise():
    """Arm raised sideways to 90° (shoulder height)."""
    return _seg(flex_deg=0, abd_deg=90)

def _pose_external_rotation():
    """
    Elbow tucked at side, bent 90°, forearm rotated outward ~45°.
    Upper arm stays vertical; elbow flexes 90°; we rotate the shoulder
    externally by giving a small abduction + negative elbow angle to
    simulate the outward rotation in the display frame.
    """
    # Upper arm stays down. Forearm rotates outward (external rotation
    # in anatomical frame = rotation around long axis of upper arm).
    # Approximate with elbow_deg=-45 (forearm swings outward from neutral).
    return _seg(flex_deg=0, abd_deg=0, elbow_deg=-45)


# ── Pose dispatch table ───────────────────────────────────────────────────────

_POSE_FN = {
    "PENDULUM SWING":    _pose_pendulum_swing,
    "ELBOW CURL":        _pose_elbow_curl,
    "FINGER WALL CRAWL": _pose_finger_wall_crawl,
    "CROSS-BODY STRETCH":_pose_cross_body_stretch,
    "FLEXION RAISE":     _pose_flexion_raise,
    "ABDUCTION RAISE":   _pose_abduction_raise,
    "EXTERNAL ROTATION": _pose_external_rotation,
}


# ── ExerciseGuide ─────────────────────────────────────────────────────────────

class ExerciseGuide:
    """
    Manages the ghost-arm GL items for the active exercise.

    Usage (from RenderWidget):
        self._guide = ExerciseGuide(self._view)
        ...
        self._guide.set_pose(ex, self._shoulder_pos)
        self._guide.clear()
    """

    def __init__(self, view: gl.GLViewWidget):
        self._view         = view
        self._items: list  = []   # all currently added GL items
        self._active       = False

    # ── Public API ────────────────────────────────────────────────────────────

    def set_pose(self, ex, shoulder_pos: np.ndarray) -> None:
        """
        Build and display the ghost arm for exercise `ex`.
        `shoulder_pos` must be the GL-space shoulder position from RenderWidget.
        Does nothing if the exercise has no guide defined.
        """
        self.clear()

        if not SHOW_EXERCISE_GUIDE:
            return

        pose_fn = _POSE_FN.get(ex.name if ex else None)
        if pose_fn is None:
            return

        upper_dir, fore_dir = pose_fn()
        elbow_pos = shoulder_pos + upper_dir
        wrist_pos = elbow_pos   + fore_dir

        upper_cyl = _cylinder(shoulder_pos, elbow_pos, radius=0.026)
        fore_cyl  = _cylinder(elbow_pos,    wrist_pos, radius=0.021)
        elbow_sph = _sphere(0.033)
        wrist_sph = _sphere(0.027)

        elbow_sph.translate(*elbow_pos)
        wrist_sph.translate(*wrist_pos)

        for item in [upper_cyl, fore_cyl, elbow_sph, wrist_sph]:
            item.setColor(C_GHOST)
            item.setGLOptions("translucent")
            self._view.addItem(item)
            self._items.append(item)

        self._active = True

    def clear(self) -> None:
        """Remove all ghost-arm items from the scene."""
        for item in self._items:
            self._view.removeItem(item)
        self._items.clear()
        self._active = False

    @property
    def active(self) -> bool:
        return self._active