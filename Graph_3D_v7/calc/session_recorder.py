"""
session_recorder.py  —  v4
--------------------------
Records session data to CSV (per-session) and SQLite (sessions index).

CSV:  data/session_YYYYMMDD_HHMMSS.csv
      Columns: timestamp_s, flexion, abduction, ext_rot, elbow

SQLite: data/sessions.db
        Table sessions: id, date, exercise, duration_s, reps,
                        max_flex, max_abd, max_ext_rot, max_elbow,
                        pain_pre, pain_post

Usage:
    rec = SessionRecorder()
    rec.start_session(exercise="Shoulder Flexion Raise", pain_pre=3)
    rec.record_frame(timestamp, flex, abd, rot, elbow)   # called every frame
    rec.increment_reps()                                  # called on each rep
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
            pain_post   INTEGER
        )
    """)
    # Migration: drop csv_file column from older databases that still have it.
    cols = [row[1] for row in con.execute("PRAGMA table_info(sessions)").fetchall()]
    if "csv_file" in cols:
        # SQLite < 3.35 has no DROP COLUMN — rebuild the table instead.
        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions_new (
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
                pain_post   INTEGER
            )
        """)
        con.execute("""
            INSERT INTO sessions_new
                (id, date, exercise, duration_s, reps,
                 max_flex, max_abd, max_ext_rot, max_elbow, pain_pre, pain_post)
            SELECT  id, date, exercise, duration_s, reps,
                    max_flex, max_abd, max_ext_rot, max_elbow, pain_pre, pain_post
            FROM sessions
        """)
        con.execute("DROP TABLE sessions")
        con.execute("ALTER TABLE sessions_new RENAME TO sessions")
        print("[DB] Migrated sessions table — removed csv_file column.")
    con.commit()
    con.close()


class SessionRecorder:
    def __init__(self):
        _ensure_db()
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

    def _discard(self):
        """Close and delete the in-progress CSV. Do not write to SQLite."""
        if not self._active:
            return
        try:
            self._csv_handle.close()
            csv_path = Path(self._csv_handle.name)
            if csv_path.exists():
                csv_path.unlink()
                print(f"[REC] Session discarded — {csv_path.name} deleted.")
        except Exception as e:
            print(f"[REC] Discard error: {e}")
        finally:
            self._active = False

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
             max_ext_rot, max_elbow, pain_pre, pain_post)
            VALUES (?,?,?,?,?,?,?,?,?,?)
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


def get_streak_and_adherence() -> dict:
    """
    Compute session adherence stats from the sessions table.

    Returns:
        current_streak  : int  — consecutive days with at least one session
        longest_streak  : int
        sessions_7d     : int  — sessions in the last 7 days
        sessions_total  : int
    """
    from pathlib import Path
    from datetime import datetime, timedelta
    import sqlite3

    db = Path(__file__).parent.parent / "data" / "sessions.db"
    if not db.exists():
        return {"current_streak": 0, "longest_streak": 0,
                "sessions_7d": 0, "sessions_total": 0}
    try:
        con = sqlite3.connect(db)
        rows = con.execute(
            "SELECT date FROM sessions ORDER BY id DESC"
        ).fetchall()
        con.close()
    except Exception:
        return {"current_streak": 0, "longest_streak": 0,
                "sessions_7d": 0, "sessions_total": 0}

    if not rows:
        return {"current_streak": 0, "longest_streak": 0,
                "sessions_7d": 0, "sessions_total": 0}

    # Parse dates, deduplicate to one-per-day
    dates = set()
    for (d,) in rows:
        try:
            dates.add(datetime.fromisoformat(d).date())
        except Exception:
            pass

    today   = datetime.now().date()
    sorted_dates = sorted(dates, reverse=True)

    # Current streak — walk backwards from today
    streak = 0
    check  = today
    for d in sorted_dates:
        if d == check or d == check - timedelta(days=1):
            streak += 1
            check = d
        elif d < check - timedelta(days=1):
            break

    # Longest streak
    longest = 1; run = 1
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i-1] - sorted_dates[i]).days == 1:
            run += 1; longest = max(longest, run)
        else:
            run = 1

    # Last 7 days
    cutoff = today - timedelta(days=7)
    sessions_7d = sum(1 for d in dates if d >= cutoff)

    return {
        "current_streak":  streak,
        "longest_streak":  longest,
        "sessions_7d":     sessions_7d,
        "sessions_total":  len(rows),
    }