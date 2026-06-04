"""
session_recorder.py  —  v3
--------------------------
Records session data to CSV (per-session) and SQLite (sessions index).

CSV:  data/session_YYYYMMDD_HHMMSS.csv
      Columns: timestamp_s, flexion, abduction, ext_rot, elbow

SQLite: data/sessions.db
        Table sessions: id, date, duration_s, max_flex, max_abd,
                        max_ext_rot, max_elbow, reps, pain_pre,
                        pain_post, exercise, csv_file

Usage:
    rec = SessionRecorder()
    rec.start_session(exercise="Shoulder Flexion Raise", pain_pre=3)
    rec.record_frame(timestamp, flex, abd, rot, elbow)   # called every frame
    summary = rec.end_session(pain_post=2)
"""

import csv, sqlite3, time, os
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH  = DATA_DIR / "sessions.db"


def _ensure_db():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT,
            exercise    TEXT,
            duration_s  REAL,
            reps        INTEGER,
            max_flex    REAL,
            max_abd     REAL,
            max_ext_rot REAL,
            max_elbow   REAL,
            pain_pre    INTEGER,
            pain_post   INTEGER,
            csv_file    TEXT
        )
    """)
    con.commit()
    con.close()


class SessionRecorder:
    def __init__(self):
        _ensure_db()
        self._csv_file   = None
        self._csv_writer = None
        self._csv_handle = None
        self._active     = False
        self._t_start    = None
        self._exercise   = ""
        self._pain_pre   = 0
        self._reps       = 0
        self._max        = {"flex": 0.0, "abd": 0.0, "rot": 0.0, "elbow": 0.0}

    def start_session(self, exercise: str = "", pain_pre: int = 0):
        if self._active:
            self.end_session()
        DATA_DIR.mkdir(exist_ok=True)
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = DATA_DIR / f"session_{ts}.csv"
        self._csv_handle = open(filename, "w", newline="")
        self._csv_writer = csv.writer(self._csv_handle)
        self._csv_writer.writerow(["timestamp_s","flexion","abduction","ext_rot","elbow"])
        self._csv_file  = str(filename)
        self._t_start   = time.monotonic()
        self._exercise  = exercise
        self._pain_pre  = pain_pre
        self._reps      = 0
        self._max       = {"flex": 0.0, "abd": 0.0, "rot": 0.0, "elbow": 0.0}
        self._active    = True
        print(f"[REC] Session started → {filename.name}")

    def record_frame(self, timestamp: float,
                     flex: float, abd: float, rot: float, elbow: float):
        if not self._active:
            return
        self._csv_writer.writerow([f"{timestamp:.4f}",
                                   f"{flex:.2f}", f"{abd:.2f}",
                                   f"{rot:.2f}", f"{elbow:.2f}"])
        self._max["flex"]  = max(self._max["flex"],  abs(flex))
        self._max["abd"]   = max(self._max["abd"],   abs(abd))
        self._max["rot"]   = max(self._max["rot"],   abs(rot))
        self._max["elbow"] = max(self._max["elbow"], abs(elbow))

    def increment_reps(self):
        self._reps += 1

    def end_session(self, pain_post: int = 0) -> dict:
        if not self._active:
            return {}
        self._csv_handle.flush()
        self._csv_handle.close()
        duration = time.monotonic() - self._t_start
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            INSERT INTO sessions
            (date, exercise, duration_s, reps, max_flex, max_abd,
             max_ext_rot, max_elbow, pain_pre, pain_post, csv_file)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now().isoformat(),
            self._exercise,
            round(duration, 1),
            self._reps,
            round(self._max["flex"],  1),
            round(self._max["abd"],   1),
            round(self._max["rot"],   1),
            round(self._max["elbow"], 1),
            self._pain_pre,
            pain_post,
            self._csv_file,
        ))
        con.commit()
        con.close()
        self._active = False
        summary = {
            "duration_s": duration, "reps": self._reps,
            "max_flex": self._max["flex"], "max_abd": self._max["abd"],
            "max_ext_rot": self._max["rot"], "max_elbow": self._max["elbow"],
        }
        print(f"[REC] Session saved. Duration={duration:.0f}s reps={self._reps}")
        return summary

    def get_last_session_maxima(self) -> dict:
        """Returns max angles from the most recent completed session for progress delta."""
        con = sqlite3.connect(DB_PATH)
        row = con.execute("""
            SELECT max_flex, max_abd, max_ext_rot, max_elbow
            FROM sessions ORDER BY id DESC LIMIT 1
        """).fetchone()
        con.close()
        if row:
            return {"max_flex": row[0], "max_abd": row[1],
                    "max_ext_rot": row[2], "max_elbow": row[3]}
        return {}

    @property
    def is_active(self):
        return self._active

    @property
    def reps(self):
        return self._reps
