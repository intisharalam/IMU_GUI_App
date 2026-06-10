"""
T26 - Data Export
==================
Requirement : FR-09
Pass criterion:
  - Exactly 3 rows after seeding 3 sessions
  - Correct headers and values in exported CSV

Running: python Test_Scripts/T26_Data_Export.py
"""

import sys, os, sqlite3, csv, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Graph_3D_v6"))

import calc.session_recorder as sr_module
from calc.session_recorder import SessionRecorder
from pathlib import Path


# Mirror the export logic from the GUI settings panel
def export_all_sessions(db_path, out_path):
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT date, exercise, duration_s, reps, max_flex, max_abd, "
        "max_ext_rot, max_elbow, pain_pre, pain_post "
        "FROM sessions ORDER BY id ASC"
    ).fetchall()
    con.close()
    headers = ["date", "exercise", "duration_s", "reps", "max_flex",
               "max_abd", "max_ext_rot", "max_elbow", "pain_pre", "pain_post"]
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    return headers, rows


SEED_SESSIONS = [
    dict(exercise="FLEXION RAISE",  pain_pre=3, pain_post=2,
         frames=[(45.0, 0.0, 0.0, 25.0)]*10, reps=5),
    dict(exercise="ABDUCTION RAISE", pain_pre=5, pain_post=4,
         frames=[(0.0, 60.0, 0.0, 20.0)]*10, reps=4),
    dict(exercise="PENDULUM SWING",  pain_pre=7, pain_post=6,
         frames=[(20.0, 0.0, 0.0, 15.0)]*10, reps=8),
]


def run_test() -> bool:
    print("=" * 60)
    print("T26 - Data Export")
    print("=" * 60)

    passed = True
    tmp_dir = tempfile.mkdtemp(prefix="t26_test_")
    original_data_dir = sr_module.DATA_DIR
    original_db_path  = sr_module.DB_PATH
    sr_module.DATA_DIR = Path(tmp_dir)
    sr_module.DB_PATH  = sr_module.DATA_DIR / "sessions.db"

    try:
        # Seed 3 sessions
        for s in SEED_SESSIONS:
            rec = SessionRecorder()
            rec.start_session(exercise=s["exercise"], pain_pre=s["pain_pre"])
            for i, (fl, ab, er, el) in enumerate(s["frames"]):
                rec.record_frame(i * 0.02, fl, ab, er, el)
            for _ in range(s["reps"]):
                rec.increment_reps()
            rec.end_session(pain_post=s["pain_post"])

        out_path = Path(tmp_dir) / "export.csv"
        headers, rows = export_all_sessions(sr_module.DB_PATH, out_path)

        # Check row count
        ok_count = len(rows) == 3
        print(f"  [{'PASS' if ok_count else 'FAIL'}]  Row count: {len(rows)}  (expected 3)")
        if not ok_count: passed = False

        # Check headers
        expected_headers = ["date", "exercise", "duration_s", "reps", "max_flex",
                            "max_abd", "max_ext_rot", "max_elbow", "pain_pre", "pain_post"]
        ok_headers = headers == expected_headers
        print(f"  [{'PASS' if ok_headers else 'FAIL'}]  Headers correct: {headers}")
        if not ok_headers: passed = False

        # Verify each row matches the seeded data
        for i, (row, seed) in enumerate(zip(rows, SEED_SESSIONS)):
            exercise_col = row[1]
            reps_col     = int(row[3])
            pain_pre_col = int(row[8])
            pain_post_col= int(row[9])
            ok = (exercise_col == seed["exercise"] and
                  reps_col     == seed["reps"] and
                  pain_pre_col == seed["pain_pre"] and
                  pain_post_col== seed["pain_post"])
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}]  Row {i+1}: exercise={exercise_col}  "
                  f"reps={reps_col}  pain_pre={pain_pre_col}  pain_post={pain_post_col}")
            if not ok: passed = False

        # Verify the CSV file on disk matches
        with open(out_path, newline="") as f:
            csv_rows = list(csv.reader(f))
        ok_file = len(csv_rows) == 4  # 1 header + 3 data
        print(f"  [{'PASS' if ok_file else 'FAIL'}]  CSV file rows: {len(csv_rows)}  (expected 4 incl. header)")
        if not ok_file: passed = False

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

def test_t26_data_export():
    assert run_test()
