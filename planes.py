"""
anatomical_planes.py
--------------------
Generates a clean 3D diagram of the three anatomical planes
(sagittal, frontal, transverse) with a stick figure at the centre.
Saves as anatomical_planes.png at 300 DPI.
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch
from mpl_toolkits.mplot3d.proj3d import proj_transform

# ── Figure setup ──────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(10, 10), facecolor="white")
ax  = fig.add_subplot(111, projection="3d")
ax.set_facecolor("white")

S = 1.1   # half-extent of each plane

# ── Planes ────────────────────────────────────────────────────────────────────
# Sagittal (XZ plane at y=0) — divides left/right — flexion/extension
sagittal = [[(-S, 0, -S), (-S, 0, S), (S, 0, S), (S, 0, -S)]]
pc_sag = Poly3DCollection(sagittal, alpha=0.18, linewidths=1.2, edgecolors="#2166ac")
pc_sag.set_facecolor("#AEC6E8")
ax.add_collection3d(pc_sag)

# Frontal (YZ plane at x=0) — divides front/back — abduction/adduction
frontal = [[(0, -S, -S), (0, -S, S), (0, S, S), (0, S, -S)]]
pc_fro = Poly3DCollection(frontal, alpha=0.18, linewidths=1.2, edgecolors="#d6604d")
pc_fro.set_facecolor("#F4A896")
ax.add_collection3d(pc_fro)

# Transverse (XY plane at z=0.3) — divides upper/lower — rotation
transverse = [[(-S, -S, 0.3), (-S, S, 0.3), (S, S, 0.3), (S, -S, 0.3)]]
pc_tra = Poly3DCollection(transverse, alpha=0.18, linewidths=1.2, edgecolors="#4d9a4d")
pc_tra.set_facecolor("#A8D5A8")
ax.add_collection3d(pc_tra)

# ── Plane border lines ────────────────────────────────────────────────────────
def plane_border(pts, col, lw=1.4):
    xs = [p[0] for p in pts] + [pts[0][0]]
    ys = [p[1] for p in pts] + [pts[0][1]]
    zs = [p[2] for p in pts] + [pts[0][2]]
    ax.plot(xs, ys, zs, color=col, lw=lw, alpha=0.7)

plane_border([(-S,0,-S),(-S,0,S),(S,0,S),(S,0,-S)],            "#2166ac")
plane_border([(0,-S,-S),(0,-S,S),(0,S,S),(0,S,-S)],             "#d6604d")
plane_border([(-S,-S,0.3),(-S,S,0.3),(S,S,0.3),(S,-S,0.3)],    "#4d9a4d")

# ── Stick figure ──────────────────────────────────────────────────────────────
lw  = 2.8
col = "#222222"

def limb(x0,y0,z0, x1,y1,z1):
    ax.plot([x0,x1],[y0,y1],[z0,z1], color=col, lw=lw, solid_capstyle="round")

# Head
u, v = np.mgrid[0:2*np.pi:24j, 0:np.pi:16j]
r = 0.13
ax.plot_surface(r*np.cos(u)*np.sin(v), r*np.sin(u)*np.sin(v),
                r*np.cos(v)+1.05, color="#444444", alpha=0.85, linewidth=0)

# Spine
limb(0, 0, 0.30, 0, 0, 0.88)
# Shoulders
limb(0, -0.35, 0.78, 0, 0.35, 0.78)
# Upper arms
limb(0, -0.35, 0.78, 0, -0.60, 0.42)
limb(0,  0.35, 0.78, 0,  0.60, 0.42)
# Forearms
limb(0, -0.60, 0.42, 0, -0.55, 0.10)
limb(0,  0.60, 0.42, 0,  0.55, 0.10)
# Hips
limb(0, -0.22, 0.30, 0,  0.22, 0.30)
# Thighs
limb(0, -0.22, 0.30, 0, -0.20, -0.28)
limb(0,  0.22, 0.30, 0,  0.20, -0.28)
# Shins
limb(0, -0.20, -0.28, 0, -0.21, -0.82)
limb(0,  0.20, -0.28, 0,  0.21, -0.82)
# Feet
limb(0, -0.21, -0.82,  0.14, -0.21, -0.88)
limb(0,  0.21, -0.82,  0.14,  0.21, -0.88)

# ── View / axes (set before any label positioning) ───────────────────────────
ax.set_xlim(-S-0.2, S+0.2)
ax.set_ylim(-S-0.2, S+0.2)
ax.set_zlim(-S-0.2, S+0.2)
ax.set_axis_off()
ax.view_init(elev=18, azim=-55)

# ── Plane labels — placed on the plane edges in 3D ───────────────────────────
# Keep labels close to the plane centre edges so they stay inside the viewport
# Sagittal: label on the top edge of the plane (y=0, z=S)
ax.text(0, 0, S+0.2, "SAGITTAL\nflexion / extension",
        color="#2166ac", fontsize=10, fontweight="bold",
        ha="center", va="bottom", linespacing=1.5)

# Frontal: label on the right edge of the plane (x=0, y=S)
ax.text(0, S+0.2, 0.85, "FRONTAL\nabduction / adduction",
        color="#d6604d", fontsize=10, fontweight="bold",
        ha="left", va="center", linespacing=1.5)

# Transverse: label on the front corner of the plane
ax.text(-S-0.2, -S, 0.38, "TRANSVERSE\nrotation",
        color="#4d9a4d", fontsize=10, fontweight="bold",
        ha="left", va="bottom", linespacing=1.5)

# ── Superior / Inferior arrows (2D figure annotations) ───────────────────────
# These are placed after tight_layout using figure coordinates
fig.subplots_adjust(left=0.05, right=0.95, top=0.92, bottom=0.08)

# ── Legend ────────────────────────────────────────────────────────────────────
legend_elements = [
    mpatches.Patch(facecolor="#AEC6E8", edgecolor="#2166ac", alpha=0.75,
                   label="Sagittal — flexion / extension"),
    mpatches.Patch(facecolor="#F4A896", edgecolor="#d6604d", alpha=0.75,
                   label="Frontal — abduction / adduction"),
    mpatches.Patch(facecolor="#A8D5A8", edgecolor="#4d9a4d", alpha=0.75,
                   label="Transverse — internal / external rotation"),
]
ax.legend(handles=legend_elements, loc="lower left",
          fontsize=10, framealpha=0.95, edgecolor="#bbbbbb",
          bbox_to_anchor=(0.0, 0.0))

# ── Title ─────────────────────────────────────────────────────────────────────
fig.text(0.5, 0.97, "Anatomical Reference Planes",
         ha="center", va="top", fontsize=14, fontweight="bold", color="#222222")

out = "anatomical_planes.png"
plt.savefig(out, dpi=300, bbox_inches="tight", facecolor="white")
print(f"Saved: {out}")