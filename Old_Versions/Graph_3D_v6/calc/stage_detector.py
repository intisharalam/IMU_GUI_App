"""
calc/stage_detector.py
----------------------
Rule-based frozen shoulder stage classifier.

Frozen shoulder has three clinical stages:
  FREEZING  — pain increasing, ROM decreasing or plateau
  FROZEN    — pain stable (moderate-high), ROM plateau (lowest point)
  THAWING   — pain decreasing, ROM improving

Classification rules (applied to last N sessions from SQLite):
  Uses rolling average of pain_post and max_flex over the last 5 sessions.
  Compares to the 5 sessions before that as the trend baseline.

  THAWING  : ROM delta > +5° AND pain delta < 0
  FREEZING : ROM delta < -5° OR (pain delta > +1 AND ROM delta < +2)
  FROZEN   : everything else (stable, or mixed signals)

References:
  Neviaser & Neviaser (1987) — 3-stage model
  Dias et al. (2005) — clinical staging criteria
"""

import sqlite3
from pathlib import Path
from dataclasses import dataclass

DB_PATH = Path(__file__).parent.parent / "data" / "sessions.db"

WINDOW = 5   # sessions per comparison window

STAGE_THAWING  = "THAWING"
STAGE_FROZEN   = "FROZEN"
STAGE_FREEZING = "FREEZING"
STAGE_UNKNOWN  = "UNKNOWN"

STAGE_COLOUR = {
    STAGE_THAWING:  "#00ff41",   # green — recovering
    STAGE_FROZEN:   "#ffaa00",   # amber — plateau
    STAGE_FREEZING: "#ff3333",   # red   — worsening
    STAGE_UNKNOWN:  "#4a7a4a",   # dim   — not enough data
}

STAGE_DESC = {
    STAGE_THAWING:  "ROM improving, pain decreasing — recovery phase.",
    STAGE_FROZEN:   "ROM plateau, pain stable — peak restriction phase.",
    STAGE_FREEZING: "ROM declining or pain increasing — onset/worsening phase.",
    STAGE_UNKNOWN:  "Insufficient session data to classify stage (need ≥10 sessions).",
}


@dataclass
class StageResult:
    stage:      str
    colour:     str
    desc:       str
    rom_delta:  float   # degrees, recent vs previous window
    pain_delta: float   # points, recent vs previous window
    n_sessions: int


def detect_stage() -> StageResult:
    """
    Query SQLite and classify the current clinical stage.
    Returns a StageResult with all context needed for display.
    """
    if not DB_PATH.exists():
        return _unknown(0)

    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT max_flex, pain_post FROM sessions "
            "WHERE max_flex IS NOT NULL AND pain_post IS NOT NULL "
            "ORDER BY id DESC"
        ).fetchall()
        con.close()
    except Exception:
        return _unknown(0)

    n = len(rows)
    if n < WINDOW * 2:
        return _unknown(n)

    recent   = rows[:WINDOW]
    previous = rows[WINDOW:WINDOW * 2]

    rom_recent   = sum(r[0] for r in recent)   / WINDOW
    rom_previous = sum(r[0] for r in previous) / WINDOW
    pain_recent  = sum(r[1] for r in recent)   / WINDOW
    pain_prev    = sum(r[1] for r in previous) / WINDOW

    rom_delta  = rom_recent  - rom_previous   # positive = improving
    pain_delta = pain_recent - pain_prev      # positive = more pain

    if rom_delta > 5 and pain_delta <= 0:
        stage = STAGE_THAWING
    elif rom_delta < -5 or (pain_delta > 1 and rom_delta < 2):
        stage = STAGE_FREEZING
    else:
        stage = STAGE_FROZEN

    return StageResult(
        stage      = stage,
        colour     = STAGE_COLOUR[stage],
        desc       = STAGE_DESC[stage],
        rom_delta  = rom_delta,
        pain_delta = pain_delta,
        n_sessions = n,
    )


def _unknown(n: int) -> StageResult:
    return StageResult(
        stage      = STAGE_UNKNOWN,
        colour     = STAGE_COLOUR[STAGE_UNKNOWN],
        desc       = STAGE_DESC[STAGE_UNKNOWN],
        rom_delta  = 0.0,
        pain_delta = 0.0,
        n_sessions = n,
    )
