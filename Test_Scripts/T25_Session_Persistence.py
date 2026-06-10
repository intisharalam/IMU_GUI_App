"""
T25 - Session Persistence
==========================
Requirement : FR-09
Pass criterion:
  - SQLite row correct (exercise, reps, pain scores, max angles)
  - CSV written with correct headers
  - No data loss on flush

Running: python Test_Scripts/T25_Session_Persistence.py
"""

import sys, os, sqlite3, csv, tempfile, shutil, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

import calc.session_recorder as sr_module
from calc.session_recorder import SessionRecorder


def run_test() -> bool:
    print("=" * 60)
    print("T25 - Session Persistence")
    print("=" * 60)

    passed = True

    # Redirect data dir to a temp folder so we don't pollute the real database
    tmp_dir = tempfile.mkdtemp(prefix="t25_test_")
    original_data_dir = sr_module.DATA_DIR
    original_db_path  = sr_module.DB_PATH
    sr_module.DATA_DIR = type(sr_module.DATA_DIR)(tmp_dir)
    sr_module.DB_PATH  = sr_module.DATA_DIR / "sessions.db"

    try:
        rec = SessionRecorder()
        rec.start_session(exercise="FLEXION RAISE", pain_pre=4)

        # Feed 20 synthetic frames at ~50Hz
        t0 = 0.0
        for i in range(20):
            rec.record_frame(t0 + i * 0.02, 45.0, 0.0, 0.0, 30.0)

        # Simulate 3 reps
        for _ in range(3):
            rec.increment_reps()

        summary = rec.end_session(pain_post=2)

        # ── Check SQLite row ───────────────────────────────────────────────
        con = sqlite3.connect(sr_module.DB_PATH)
        rows = con.execute(
            "SELECT exercise, reps, max_flex, max_elbow, pain_pre, pain_post "
            "FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchall()
        con.close()

        ok_row = len(rows) == 1
        print(f"  [{'PASS' if ok_row else 'FAIL'}]  SQLite row written")
        if not ok_row: passed = False

        if ok_row:
            exercise, reps, max_flex, max_elbow, pain_pre, pain_post = rows[0]
            checks = [
                ("exercise",  exercise == "FLEXION RAISE",  exercise,  "FLEXION RAISE"),
                ("reps",      reps == 3,                    reps,      3),
                ("max_flex",  abs(max_flex - 45.0) < 0.5,  max_flex,  45.0),
                ("max_elbow", abs(max_elbow - 30.0) < 0.5, max_elbow, 30.0),
                ("pain_pre",  pain_pre == 4,                pain_pre,  4),
                ("pain_post", pain_post == 2,               pain_post, 2),
            ]
            for field, ok, got, expected in checks:
                status = "PASS" if ok else "FAIL"
                print(f"  [{status}]  {field}: {got}  (expected {expected})")
                if not ok: passed = False

        # ── Check CSV ──────────────────────────────────────────────────────
        csv_files = list(sr_module.DATA_DIR.glob("session_*.csv"))
        ok_csv = len(csv_files) == 1
        print(f"  [{'PASS' if ok_csv else 'FAIL'}]  CSV file written  ({len(csv_files)} found)")
        if not ok_csv: passed = False

        if ok_csv:
            with open(csv_files[0], newline="") as f:
                reader = csv.reader(f)
                rows_csv = list(reader)

            expected_header = ["timestamp_s", "flexion", "abduction", "ext_rot", "elbow"]
            ok_header = rows_csv[0] == expected_header
            print(f"  [{'PASS' if ok_header else 'FAIL'}]  CSV headers: {rows_csv[0]}")
            if not ok_header: passed = False

            ok_frames = len(rows_csv) - 1 == 20   # header + 20 data rows
            print(f"  [{'PASS' if ok_frames else 'FAIL'}]  CSV rows: {len(rows_csv)-1}  (expected 20)")
            if not ok_frames: passed = False

    finally:
        sr_module.DATA_DIR = original_data_dir
        sr_module.DB_PATH  = original_db_path
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print()
    print(f"  RESULT: {'PASS' if passed else 'FAIL'}")
    print("=" * 60)
    return passed


if __name__ == "__main__":
    sys.exit(0 if run_test() else 1)

def test_t25_session_persistence():
    assert run_test()
