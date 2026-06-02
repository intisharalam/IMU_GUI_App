"""
calc/exercise_library.py
------------------------
Single source of truth for every exercise in the system.

To add a new exercise, append one ExerciseDef to EXERCISES at the bottom
of this file.  Nothing else needs to change anywhere else.

ExerciseDef fields
──────────────────
name            str   — unique display name, shown in lists and HUD
difficulty      str   — "Easy" | "Moderate" | "Hard"
description     str   — one-paragraph instruction shown in exercise panel
image_file      str|None  — filename under assets/ (or None if no image yet)

── Pain gating ───────────────────────────────────────────────────────────
min_pain        int   — exercise only shown when pain_pre >= min_pain
max_pain        int   — exercise only shown when pain_pre <= max_pain

── Rep counting ──────────────────────────────────────────────────────────
rep_angle       str|None  — which angle drives rep detection:
                            "flexion" | "abduction" | "ext_rot" | "elbow"
                            None = hold/stretch exercise (no rep counter)
rep_enter_deg   float — angle must EXCEED this to register arm as raised
rep_exit_deg    float — angle must DROP BELOW this to complete a rep
hold_duration_s float — for None-rep exercises: target hold time in seconds
                        (0 = no hold timer shown)

── Goal sphere ───────────────────────────────────────────────────────────
goal_flex_deg   float — shoulder flexion component of wrist target
                        (set to 0 for non-flexion exercises)
goal_abd_deg    float — shoulder abduction component of wrist target
                        (set to 0 for non-abduction exercises)
                The goal sphere is only shown when at least one of these
                is > 0 AND the ROM has been measured.
                Both are multiplied by GOAL_ROM_FRACTION (0.90) at
                render time so the target sits just inside the ROM limit.

── Form checking ─────────────────────────────────────────────────────────
expected_plane  str|None  — "sagittal" | "frontal" | None
                "sagittal": haptic fires if plane_of_elevation < 30°
                            (arm drifting sideways during flexion)
                "frontal":  haptic fires if plane_of_elevation > 60°
                            (arm drifting forward during abduction)
                None:       no plane check
check_trunk_lean bool  — True fires a haptic when trunk lean > 10°
                         (relevant for abduction raises)

── Smoothness tracking ───────────────────────────────────────────────────
smooth_angle    str   — which angle to diff for the smoothness bar:
                        "flexion" | "abduction" | "ext_rot" | "elbow"
                        (defaults to rep_angle if not specified,
                         falls back to "flexion")
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


ASSETS_DIR = Path(__file__).parent.parent / "assets"


@dataclass(frozen=True)
class ExerciseDef:
    # ── Identity ──────────────────────────────────────────────────────────────
    name:           str
    difficulty:     str
    description:    str
    image_file:     Optional[str] = None

    # ── Pain gating ───────────────────────────────────────────────────────────
    min_pain:       int   = 0
    max_pain:       int   = 10

    # ── Rep counting ──────────────────────────────────────────────────────────
    rep_angle:      Optional[str] = "flexion"  # None = hold exercise
    rep_enter_deg:  float = 30.0
    rep_exit_deg:   float = 15.0
    hold_duration_s: float = 0.0   # >0 activates hold-timer mode

    # ── Goal sphere ───────────────────────────────────────────────────────────
    goal_flex_deg:  float = 0.0
    goal_abd_deg:   float = 0.0

    # ── Form checking ─────────────────────────────────────────────────────────
    expected_plane:    Optional[str] = None   # "sagittal" | "frontal" | None
    check_trunk_lean:  bool = False

    # ── Smoothness bar ────────────────────────────────────────────────────────
    smooth_angle:   str = "flexion"

    # ── Convenience ───────────────────────────────────────────────────────────
    @property
    def image_path(self) -> Optional[Path]:
        """Full path to the exercise image, or None if missing."""
        if not self.image_file:
            return None
        p = ASSETS_DIR / self.image_file
        return p if p.exists() else None

    @property
    def is_hold_exercise(self) -> bool:
        """True for stretches counted by time, not reps."""
        return self.rep_angle is None

    @property
    def has_goal(self) -> bool:
        """True if this exercise uses a goal sphere."""
        return self.goal_flex_deg > 0 or self.goal_abd_deg > 0


# ── Exercise library ──────────────────────────────────────────────────────────
# Add new exercises here.  Order determines display order in the exercise panel.

EXERCISES: list[ExerciseDef] = [

    ExerciseDef(
        name        = "PENDULUM SWING",
        difficulty  = "Easy",
        description = (
            "Lean forward so the arm hangs freely under gravity. "
            "Let the weight of the arm create gentle traction on the "
            "glenohumeral joint. Swing slowly in small circles or "
            "forward/back arcs. Best for high-pain days."
        ),
        image_file  = "Pendulum_Swing.png",
        min_pain    = 4, max_pain = 10,
        rep_angle   = "flexion",
        rep_enter_deg = 20.0, rep_exit_deg = 8.0,
        goal_flex_deg = 30.0,
        smooth_angle  = "flexion",
    ),

    ExerciseDef(
        name        = "ELBOW CURL",
        difficulty  = "Easy",
        description = (
            "Sit or stand with the upper arm at your side. "
            "Bend the elbow, bringing the hand toward the shoulder, "
            "then lower slowly. Maintains elbow mobility and warms "
            "up the arm without loading the shoulder."
        ),
        image_file  = "Elbow_Curl.png",
        min_pain    = 3, max_pain = 8,
        rep_angle   = "elbow",
        rep_enter_deg = 60.0, rep_exit_deg = 20.0,
        smooth_angle  = "elbow",
    ),

    ExerciseDef(
        name        = "FINGER WALL CRAWL",
        difficulty  = "Easy",
        description = (
            "Stand facing a wall. Place fingertips on the surface and "
            "walk them upward as far as comfortable, then lower slowly. "
            "Builds active flexion range gradually. The physiotherapist's "
            "primary exercise for adhesive capsulitis."
        ),
        image_file  = "Finger_Wall_Crawl.png",
        min_pain    = 3, max_pain = 8,
        rep_angle   = "flexion",
        rep_enter_deg = 30.0, rep_exit_deg = 15.0,
        goal_flex_deg = 90.0,
        expected_plane = "sagittal",
        smooth_angle   = "flexion",
    ),

    ExerciseDef(
        name        = "CROSS-BODY STRETCH",
        difficulty  = "Easy",
        description = (
            "Use the good arm to gently draw the affected arm across "
            "the chest until a stretch is felt in the back of the "
            "shoulder. Hold for 20–30 seconds. "
            "Stretches the posterior capsule."
        ),
        image_file  = "Cross_Body_Stretch.png",
        min_pain    = 3, max_pain = 7,
        rep_angle   = None,          # hold exercise
        hold_duration_s = 25.0,
        goal_abd_deg = 40.0,
        smooth_angle = "abduction",
    ),

    ExerciseDef(
        name        = "TOWEL STRETCH",
        difficulty  = "Moderate",
        description = (
            "Hold a towel behind the back — good hand at the top, "
            "affected hand at the bottom. Gently pull the towel upward "
            "with the good hand to stretch internal rotation. "
            "Hold for 20–30 seconds."
        ),
        image_file  = "towel_stretch.png",
        min_pain    = 2, max_pain = 6,
        rep_angle   = None,          # hold exercise
        hold_duration_s = 25.0,
        smooth_angle = "ext_rot",
    ),

    ExerciseDef(
        name        = "FLEXION RAISE",
        difficulty  = "Moderate",
        description = (
            "Raise the arm forward in the sagittal plane as high as "
            "comfortable, then lower slowly. "
            "Primary measure: shoulder flexion arc."
        ),
        image_file  = "flexion_raise.png",
        min_pain    = 0, max_pain = 6,
        rep_angle   = "flexion",
        rep_enter_deg = 30.0, rep_exit_deg = 15.0,
        goal_flex_deg = 90.0,
        expected_plane = "sagittal",
        smooth_angle   = "flexion",
    ),

    ExerciseDef(
        name        = "ABDUCTION RAISE",
        difficulty  = "Moderate",
        description = (
            "Raise the arm sideways in the frontal plane as high as "
            "comfortable, then lower slowly. "
            "Primary measure: shoulder abduction arc."
        ),
        image_file  = "abduction_raise.png",
        min_pain    = 0, max_pain = 6,
        rep_angle   = "abduction",
        rep_enter_deg = 30.0, rep_exit_deg = 15.0,
        goal_abd_deg   = 90.0,
        expected_plane = "frontal",
        check_trunk_lean = True,
        smooth_angle   = "abduction",
    ),

    ExerciseDef(
        name        = "DOORWAY STRETCH",
        difficulty  = "Moderate",
        description = (
            "Stand in a doorway with the arm at 90° against the frame. "
            "Lean gently forward until a stretch is felt across the "
            "front of the chest and shoulder. Hold for 20–30 seconds. "
            "Targets the anterior capsule and pectorals."
        ),
        image_file  = None,
        min_pain    = 0, max_pain = 5,
        rep_angle   = None,
        hold_duration_s = 25.0,
        goal_flex_deg = 70.0,
        smooth_angle  = "flexion",
    ),

    ExerciseDef(
        name        = "EXTERNAL ROTATION",
        difficulty  = "Hard",
        description = (
            "Keep the elbow tucked at your side and bent to 90°. "
            "Rotate the forearm outward against gentle resistance or "
            "gravity, then return slowly. "
            "Targets the most restricted plane in adhesive capsulitis."
        ),
        image_file  = None,
        min_pain    = 0, max_pain = 4,
        rep_angle   = "ext_rot",
        rep_enter_deg = 20.0, rep_exit_deg = 8.0,
        smooth_angle  = "ext_rot",
    ),

    ExerciseDef(
        name        = "BEHIND-BACK REACH",
        difficulty  = "Hard",
        description = (
            "Reach the affected arm behind the back and slide the hand "
            "upward along the spine as far as comfortable. "
            "Measures combined internal rotation and extension. "
            "Advanced recovery exercise for the thawing phase."
        ),
        image_file  = None,
        min_pain    = 0, max_pain = 3,
        rep_angle   = "ext_rot",
        rep_enter_deg = 15.0, rep_exit_deg = 5.0,
        smooth_angle  = "ext_rot",
    ),
]


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_exercise(name: str) -> Optional[ExerciseDef]:
    """Return the ExerciseDef with this name, or None if not found."""
    for ex in EXERCISES:
        if ex.name == name:
            return ex
    return None


def exercises_for_pain(pain: int) -> list[ExerciseDef]:
    """Return all exercises appropriate for the given pain level."""
    return [ex for ex in EXERCISES if ex.min_pain <= pain <= ex.max_pain]
