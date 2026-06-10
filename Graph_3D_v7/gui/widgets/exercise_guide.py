"""
gui/widgets/exercise_guide.py
------------------------------
Recorded ghost-arm guide for the active exercise.

A list of (upper_dir_gl, fore_dir_gl) frames — recorded by the therapist
and saved to assets/guides/<exercise_name>.pkl — is played back frame by
frame as a semi-transparent ghost arm.  Playback loops continuously until
stop() is called (triggered externally after 2 patient reps are detected).

No static poses.  No hardcoded exercise knowledge.  If no guide file exists
for the current exercise the guide simply stays hidden.

Public API (called by RenderWidget)
────────────────────────────────────
    guide.load(ex_name, shoulder_pos)  — load file + build GL items; no-op if missing
    guide.start()                      — begin looping playback
    guide.stop()                       — hide ghost arm, reset
    guide.tick()                       — advance one frame; call every render tick
    guide.active  → bool
"""

import pickle
from pathlib import Path

import numpy as np
import pyqtgraph.opengl as gl

UPPER_ARM_LEN = 0.30
FOREARM_LEN   = 0.25

GUIDES_DIR = Path(__file__).parent.parent.parent / "assets" / "guides"

# Import W_ROT lazily to avoid circular import — accessed only when ExerciseGuide.load() is called.
# This ensures recorded guide frames are replayed in the same GL frame as the live arm.
def _get_world_rot():
    from gui.widgets.render_widget import WORLD_ROT
    return WORLD_ROT

C_GHOST       = (0.10, 0.60, 0.80, 0.35)
C_GHOST_JOINT = (0.10, 0.60, 0.80, 0.60)


# ── Mesh helpers ──────────────────────────────────────────────────────────────

def _cylinder(start, end, radius, segs=12):
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
        meshdata=gl.MeshData(vertexes=verts, faces=np.array(faces, dtype=np.uint32)),
        smooth=False)


def _sphere(radius, rows=8, cols=8):
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
        meshdata=gl.MeshData(vertexes=verts, faces=np.array(faces, dtype=np.uint32)),
        smooth=True)


def guide_path(ex_name: str) -> Path:
    safe = ex_name.replace(" ", "_").replace("/", "_")
    return GUIDES_DIR / f"{safe}.pkl"


def save_guide(ex_name: str, frames: list) -> Path:
    GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    path = guide_path(ex_name)
    with open(path, "wb") as f:
        pickle.dump(frames, f)
    return path


def load_guide_frames(ex_name: str) -> list | None:
    path = guide_path(ex_name)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


# ── ExerciseGuide ─────────────────────────────────────────────────────────────

class ExerciseGuide:

    def __init__(self, view: gl.GLViewWidget):
        self._view         = view
        self._items        = []
        self._frames       = []      # list of (upper_dir, fore_dir)
        self._idx          = 0
        self._playing      = False
        self._shoulder_pos = None

        # Mutable GL items rebuilt each tick
        self._upper_cyl = None
        self._fore_cyl  = None
        self._elbow_sph = None
        self._wrist_sph = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, ex_name: str, shoulder_pos: np.ndarray) -> bool:
        """
        Load guide frames for ex_name and prepare GL items.
        Returns True if a guide file was found, False otherwise.
        """
        self.stop()
        frames = load_guide_frames(ex_name)
        if not frames:
            return False
        self._frames       = frames
        self._shoulder_pos = shoulder_pos.copy()
        self._idx          = 0

        # Build sphere items once (repositioned each tick)
        self._elbow_sph = _sphere(0.033)
        self._wrist_sph = _sphere(0.027)
        for item in [self._elbow_sph, self._wrist_sph]:
            item.setColor(C_GHOST_JOINT)
            item.setGLOptions("translucent")
            self._view.addItem(item)
            self._items.append(item)

        # Draw first frame immediately so something is visible
        self._draw_frame(self._frames[0])
        return True

    def start(self) -> None:
        """Begin looping playback from the current frame."""
        if self._frames:
            self._playing = True

    def stop(self) -> None:
        """Remove all ghost items and reset state."""
        for item in self._items:
            try:
                self._view.removeItem(item)
            except Exception:
                pass
        self._items.clear()
        self._upper_cyl = None
        self._fore_cyl  = None
        self._elbow_sph = None
        self._wrist_sph = None
        self._frames    = []
        self._idx       = 0
        self._playing   = False

    def tick(self) -> None:
        """Advance one frame. Call every render tick."""
        if not self._playing or not self._frames:
            return
        self._draw_frame(self._frames[self._idx])
        self._idx = (self._idx + 1) % len(self._frames)

    @property
    def active(self) -> bool:
        return self._playing

    # ── Internal ──────────────────────────────────────────────────────────────

    def _draw_frame(self, frame):
        upper_dir, fore_dir = frame
        shoulder  = self._shoulder_pos
        elbow_pos = shoulder + upper_dir
        wrist_pos = elbow_pos + fore_dir

        # Remove old cylinders
        for cyl in [self._upper_cyl, self._fore_cyl]:
            if cyl is not None:
                try:
                    self._view.removeItem(cyl)
                    self._items.remove(cyl)
                except Exception:
                    pass

        self._upper_cyl = _cylinder(shoulder, elbow_pos, radius=0.026)
        self._fore_cyl  = _cylinder(elbow_pos, wrist_pos, radius=0.021)
        for cyl in [self._upper_cyl, self._fore_cyl]:
            cyl.setColor(C_GHOST)
            cyl.setGLOptions("translucent")
            self._view.addItem(cyl)
            self._items.append(cyl)

        self._elbow_sph.resetTransform()
        self._elbow_sph.translate(*elbow_pos)
        self._wrist_sph.resetTransform()
        self._wrist_sph.translate(*wrist_pos)