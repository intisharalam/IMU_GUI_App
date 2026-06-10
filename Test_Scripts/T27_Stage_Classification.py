"""
T27 - Stage Classification
===========================
Requirement : FR-12
Pass criterion:
  T27a: FREEZING, FROZEN, and THAWING criteria each return the correct label
        when 10 sessions are injected satisfying those criteria.
  T27b: UNKNOWN returned when fewer than 10 sessions are present (9 sessions).

Running: python Test_Scripts/T27_Stage_Classification.py
"""

import sys, os, sqlite3, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

import calc.stage_detector as sd_module
from calc.stage_detector import (
    detect_stage,
    STAGE_THAWING, STAGE_FROZEN, STAGE_FREEZING, STAGE_UNKNOWN,
    WINDOW
)
from pathlib import Path
from datetime import datetime, timedelta


def seed_sessions(db_path, sessions):
    """
    sessions: list of (max_flex, pain_post) tuples, newest first.
    Inserts them with distinct ISO dates so the ORDER BY id DESC works.
    """
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT, exercise TEXT, duration_s REAL, reps INTEGER,
            max_flex REAL, max_abd REAL, max_ext_rot REAL, max_elbow REAL,
            pain_pre INTEGER, pain_post INTEGER
        )
    """)
    base = datetime(2026, 1, 1)
    for i, (max_flex, pain_post) in enumerate(sessions):
        date = (base + timedelta(days=i)).isoformat()
        con.execute(
            "INSERT INTO sessions (date, exercise, duration_s, reps, "
            "max_flex, max_abd, max_ext_rot, max_elbow, pain_pre, pain_post) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date, "FLEXION RAISE", 60.0, 5, max_flex, 0.0, 0.0, 0.0, 3, pain_post)
        )
    con.commit()
    con.close()


def run_with_sessions(sessions) -> str:
    """Inject sessions into a temp DB and return the detected stage label."""
    tmp_dir = tempfile.mkdtemp(prefix="t27_")
    original_db = sd_module.DB_PATH
    sd_module.DB_PATH = Path(tmp_dir) / "sessions.db"
    try:
        seed_sessions(sd_module.DB_PATH, sessions)
        result = detect_stage()
        return result.stage
    finally:
        sd_module.DB_PATH = original_db
        shutil.rmtree(tmp_dir, ignore_errors=True)


def make_sessions(n, rom_trend, pain_trend, base_rom=60.0, base_pain=5.0):
    """
    Build n sessions (ordered oldest→newest so detect_stage sees them right).
    rom_trend: degrees change per session (positive = improving)
    pain_trend: pain change per session (positive = worsening)
    detect_stage orders by id DESC, so we insert oldest first.
    """
    sessions = []
    for i in range(n):
        rom   = base_rom   + rom_trend   * i
        pain  = base_pain  + pain_trend  * i
        sessions.append((round(rom, 1), round(pain, 1)))
    # Return oldest first (detect_stage reads newest first via ORDER BY id DESC)
    return sessions


def run_test() -> bool:
    print("=" * 60)
    print("T27 - Stage Classification")
    print("=" * 60)
    print(f"  WINDOW = {WINDOW} sessions per comparison block")
    print(f"  Minimum sessions required = {WINDOW * 2}")
    print()

    passed = True

    # ── T27a: Positive cases ───────────────────────────────────────────────────
    print("  T27a  Positive cases (10 sessions each)")

    # THAWING: ROM delta > +5° AND pain delta <= 0
    # recent 5 avg ROM higher by >5°, recent pain lower or equal
    # Build 10 sessions: first 5 (older) have ROM=60, pain=6
    #                    last  5 (newer) have ROM=68, pain=5
    thawing_sessions = [(60.0, 6.0)] * 5 + [(68.0, 5.0)] * 5
    stage = run_with_sessions(thawing_sessions)
    ok = stage == STAGE_THAWING
    print(f"  [{'PASS' if ok else 'FAIL'}]  THAWING:  got '{stage}'  "
          f"(ROM delta=+8°, pain delta=-1)")
    if not ok: passed = False

    # FREEZING: ROM delta < -5°
    # recent 5 have lower ROM
    freezing_sessions = [(70.0, 5.0)] * 5 + [(62.0, 5.0)] * 5
    stage = run_with_sessions(freezing_sessions)
    ok = stage == STAGE_FREEZING
    print(f"  [{'PASS' if ok else 'FAIL'}]  FREEZING: got '{stage}'  "
          f"(ROM delta=-8°, pain delta=0)")
    if not ok: passed = False

    # FREEZING via pain gate: pain_delta > 1 AND rom_delta < 2
    freezing_pain_sessions = [(60.0, 4.0)] * 5 + [(61.0, 6.5)] * 5
    stage = run_with_sessions(freezing_pain_sessions)
    ok = stage == STAGE_FREEZING
    print(f"  [{'PASS' if ok else 'FAIL'}]  FREEZING (pain gate): got '{stage}'  "
          f"(ROM delta=+1°, pain delta=+2.5)")
    if not ok: passed = False

    # FROZEN: everything else (stable ROM, stable pain)
    frozen_sessions = [(60.0, 5.0)] * 5 + [(61.0, 5.0)] * 5
    stage = run_with_sessions(frozen_sessions)
    ok = stage == STAGE_FROZEN
    print(f"  [{'PASS' if ok else 'FAIL'}]  FROZEN:   got '{stage}'  "
          f"(ROM delta=+1°, pain delta=0)")
    if not ok: passed = False

    print()

    # ── T27b: UNKNOWN boundary ─────────────────────────────────────────────────
    print("  T27b  UNKNOWN boundary (9 sessions — below minimum of 10)")
    under_sessions = [(60.0, 5.0)] * 9
    stage = run_with_sessions(under_sessions)
    ok = stage == STAGE_UNKNOWN
    print(f"  [{'PASS' if ok else 'FAIL'}]  9 sessions → got '{stage}'  (expected UNKNOWN)")
    if not ok: passed = False

    # Exactly 10 sessions should NOT return UNKNOWN
    exactly_10 = [(60.0, 5.0)] * 5 + [(61.0, 5.0)] * 5
    stage = run_with_sessions(exactly_10)
    ok = stage != STAGE_UNKNOWN
    print(f"  [{'PASS' if ok else 'FAIL'}]  10 sessions → got '{stage}'  (should NOT be UNKNOWN)")
    if not ok: passed = False

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t27_stage_classification():
    assert run_test()
