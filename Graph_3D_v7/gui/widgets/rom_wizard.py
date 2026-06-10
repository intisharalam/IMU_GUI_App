"""
gui/widgets/rom_wizard.py
--------------------------
Guided ROM measurement wizard — launched from the Connect panel (Step 4).

Four sequential steps, one per movement plane:
  1. Shoulder Flexion   — raise arm FORWARD
  2. Shoulder Abduction — raise arm SIDEWAYS
  3. External Rotation  — rotate arm OUTWARD (elbow at 90°)
  4. Elbow Flexion      — bend elbow toward shoulder

Each step:
  - Shows a stick-figure SVG illustrating the target movement
  - Shows the live angle reading from AppState
  - Waits until the angle exceeds MIN_ANGLE_DEG before starting hold
  - Fills a progress bar over HOLD_SECONDS while the angle is held
  - Records the peak angle and advances to the next step

Results written to:
  - AppState.rom_*_limit  (used immediately by goal sphere)
  - AppState.rom_measured = True
  - SQLite table rom_measurements (persists across app restarts)

Called from connect_panel.py:
    wizard = RomWizard(state, parent=self)
    wizard.exec_()
"""

import sqlite3
import time
from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QWidget
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap

from gui.styles import (
    BG, SURFACE, SURFACE2, SURFACE3, BORDER, BORDER2,
    GREEN, GREEN2, GREEN3, GREEN4, GREEN_DIM,
    TEXT, TEXT2, TEXT3, TEXT_BRIGHT, AMBER, RED, CYAN,
    btn_style, label_style, card_style
)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sessions.db"

HOLD_SECONDS  = 4       # seconds to hold at peak before step completes
MIN_ANGLE_DEG = 10.0    # must exceed this before hold countdown starts
TICK_MS       = 50      # timer interval


def _ensure_rom_table():
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    # Create table with old schema if it doesn't exist at all
    con.execute("""
        CREATE TABLE IF NOT EXISTS rom_measurements (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT,
            flex_deg   REAL,
            abd_deg    REAL,
            rot_deg    REAL,
            elbow_deg  REAL
        )
    """)
    # Migrate: add ext_rot_deg column if coming from old schema
    existing = {row[1] for row in con.execute("PRAGMA table_info(rom_measurements)")}
    if "ext_rot_deg" not in existing:
        con.execute("ALTER TABLE rom_measurements ADD COLUMN ext_rot_deg REAL")
    con.commit()
    con.close()


def _save_rom(flex, abd, ext_rot, elbow):
    _ensure_rom_table()
    from datetime import datetime
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO rom_measurements (date,flex_deg,abd_deg,ext_rot_deg,elbow_deg) "
        "VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(), flex, abd, ext_rot, elbow)
    )
    con.commit(); con.close()


def load_last_rom():
    """Returns dict or None. Called at startup to restore previous ROM."""
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(DB_PATH)
        # Get actual column names to handle both old and new schema
        cols = {row[1] for row in con.execute("PRAGMA table_info(rom_measurements)")}
        ext_rot_col = "ext_rot_deg" if "ext_rot_deg" in cols else "rot_deg"
        row = con.execute(
            f"SELECT flex_deg, abd_deg, {ext_rot_col}, elbow_deg "
            f"FROM rom_measurements ORDER BY id DESC LIMIT 1"
        ).fetchone()
        con.close()
        if row:
            return {
                "flex":    row[0] or 10.0,
                "abd":     row[1] or 10.0,
                "rot":     row[2] or 10.0,
                "elbow":   row[3] or 10.0,
            }
    except Exception as e:
        print(f"[ROM] load_last_rom error: {e}")
    return None


# ── Image assets ──────────────────────────────────────────────────────────────
ROM_IMG_DIR = Path(__file__).parent.parent.parent / "assets" / "rom_wizard"

STEPS = [
    {
        "title":       "STEP 1 OF 4 — SHOULDER FLEXION",
        "instruction": "Raise your RIGHT arm FORWARD as far as\ncomfortably possible. Hold at your maximum.",
        "angle_key":   "flexion",
        "state_attr":  "shoulder_flexion",
        "images":      ["shoulder_flexion.png"],
        "unit":        "°",
        "prompt":      "RAISE YOUR ARM HIGHER",
    },
    {
        "title":       "STEP 2 OF 4 — SHOULDER ABDUCTION",
        "instruction": "Raise your RIGHT arm OUT TO THE SIDE as far\nas comfortably possible. Hold at your maximum.",
        "angle_key":   "abduction",
        "state_attr":  "shoulder_abduction",
        "images":      ["shoulder_abduction.png"],
        "unit":        "°",
        "prompt":      "RAISE YOUR ARM HIGHER",
    },
    {
        "title":       "STEP 3 OF 4 — EXTERNAL ROTATION",
        "instruction": "Bend your elbow to 90°, keep it at your side.\nRotate your forearm OUTWARD. Hold at max.",
        "angle_key":   "ext_rot",
        "state_attr":  "external_rotation",
        "images":      ["external_rotation_1.png", "external_rotation_2.png"],
        "unit":        "°",
        "prompt":      "ROTATE FURTHER OUTWARD",
    },
    {
        "title":       "STEP 4 OF 4 — ELBOW FLEXION",
        "instruction": "Bend your elbow, bringing your hand toward\nyour shoulder as far as possible. Hold.",
        "angle_key":   "elbow",
        "state_attr":  "elbow_flexion",
        "images":      ["elbow_flexion.png"],
        "unit":        "°",
        "prompt":      "BEND YOUR ELBOW FURTHER",
    },
]


class FillBar(QFrame):
    """Horizontal progress bar that fills left-to-right in terminal green."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(18)
        self.setStyleSheet(f"background:{SURFACE3}; border:1px solid {BORDER2}; border-radius:3px;")
        self._fill = QFrame(self)
        self._fill.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {GREEN3}, stop:1 {GREEN});"
            f"border-radius:2px;"
        )
        self._fill.setFixedHeight(14)
        self._fill.move(2, 2)
        self._fill.setFixedWidth(0)
        self._max_w = 0

    def resizeEvent(self, e):
        self._max_w = self.width() - 4
        super().resizeEvent(e)

    def set_fraction(self, frac: float):
        """frac in 0.0–1.0"""
        w = int(max(0.0, min(1.0, frac)) * self._max_w)
        self._fill.setFixedWidth(w)


class RomWizard(QDialog):
    """
    Modal dialog — blocks until all 4 ROM steps are complete or cancelled.
    Results written to AppState and SQLite on completion.
    """

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self._state       = state
        self._step_idx    = 0
        self._results     = {"flexion": 0.0, "abduction": 0.0, "ext_rot": 0.0, "elbow": 0.0}
        self._hold_elapsed = 0.0    # seconds held above threshold
        self._peak_angle  = 0.0
        self._holding     = False

        self.setWindowTitle("ROM MEASUREMENT WIZARD")
        self.setFixedSize(560, 580)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background:{BG}; color:{TEXT}; "
            f"font-family:'Courier New',monospace; border:1px solid {GREEN3}; }}"
        )
        self._build()

        # 50 Hz tick
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._load_step(0)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # Title bar
        title_bar = QWidget(); title_bar.setFixedHeight(36)
        title_bar.setStyleSheet(f"background:{SURFACE}; border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(title_bar); tl.setContentsMargins(16, 0, 16, 0)
        self._title_lbl = QLabel("ROM MEASUREMENT WIZARD")
        self._title_lbl.setStyleSheet(
            f"color:{GREEN}; font-size:12px; font-weight:bold; letter-spacing:2px;"
        )
        tl.addWidget(self._title_lbl); tl.addStretch()
        root.addWidget(title_bar)

        # Step progress dots
        dots_bar = QWidget(); dots_bar.setFixedHeight(32)
        dots_bar.setStyleSheet(f"background:{SURFACE2}; border-bottom:1px solid {BORDER};")
        dl = QHBoxLayout(dots_bar); dl.setContentsMargins(20, 0, 20, 0); dl.setSpacing(8)
        dl.addStretch()
        self._dots = []
        for i in range(4):
            d = QLabel("○"); d.setStyleSheet(label_style(GREEN3, 16))
            dl.addWidget(d); self._dots.append(d)
            if i < 3:
                sep = QLabel("─────"); sep.setStyleSheet(label_style(BORDER2, 10))
                dl.addWidget(sep)
        dl.addStretch()
        root.addWidget(dots_bar)

        # Body
        body = QHBoxLayout(); body.setContentsMargins(20, 16, 20, 16); body.setSpacing(20)

        # Left: SVG + step title + instruction
        left = QVBoxLayout(); left.setSpacing(10)
        self._step_title = QLabel("")
        self._step_title.setStyleSheet(label_style(GREEN3, 9))
        self._svg_widget = None   # removed — images used instead

        # Image display — one or two photos side by side
        self._img_frame = QFrame()
        self._img_frame.setFixedSize(220, 220)
        self._img_frame.setStyleSheet(
            f"background:{SURFACE2}; border:1px solid {BORDER}; border-radius:3px;"
        )
        self._img_row = QHBoxLayout(self._img_frame)
        self._img_row.setContentsMargins(4, 4, 4, 4); self._img_row.setSpacing(4)
        self._img_labels = [QLabel(), QLabel()]
        for lbl in self._img_labels:
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("background:transparent; border:none;")
            self._img_row.addWidget(lbl)
        self._instruction = QLabel("")
        self._instruction.setStyleSheet(label_style(TEXT2, 11))
        self._instruction.setWordWrap(True)
        self._instruction.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        left.addWidget(self._step_title)
        left.addWidget(self._img_frame)
        left.addWidget(self._instruction)
        left.addStretch()

        # Right: live angle + status + fill bar + peak
        right = QVBoxLayout(); right.setSpacing(12)

        # Big live angle display
        ang_frame = QFrame()
        ang_frame.setStyleSheet(card_style(SURFACE2, BORDER2))
        afl = QVBoxLayout(ang_frame); afl.setContentsMargins(16, 14, 16, 14); afl.setSpacing(4)
        lbl_cur = QLabel("CURRENT ANGLE")
        lbl_cur.setStyleSheet(label_style(GREEN3, 9))
        self._angle_lbl = QLabel("0°")
        self._angle_lbl.setStyleSheet(
            f"color:{GREEN}; font-size:52px; font-weight:bold;"
            f" font-family:'Courier New',monospace; letter-spacing:-2px;"
        )
        self._angle_lbl.setAlignment(Qt.AlignCenter)
        lbl_peak = QLabel("PEAK THIS STEP")
        lbl_peak.setStyleSheet(label_style(GREEN3, 9))
        self._peak_lbl = QLabel("0°")
        self._peak_lbl.setStyleSheet(label_style(TEXT_BRIGHT, 18, bold=True))
        self._peak_lbl.setAlignment(Qt.AlignCenter)
        afl.addWidget(lbl_cur); afl.addWidget(self._angle_lbl)
        afl.addWidget(lbl_peak); afl.addWidget(self._peak_lbl)
        right.addWidget(ang_frame)

        # Status text
        self._status_lbl = QLabel(STEPS[self._step_idx]["prompt"])
        self._status_lbl.setStyleSheet(label_style(AMBER, 11, bold=True))
        self._status_lbl.setAlignment(Qt.AlignCenter)
        right.addWidget(self._status_lbl)

        # Hold fill bar
        bar_frame = QFrame()
        bar_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        bfl = QVBoxLayout(bar_frame); bfl.setContentsMargins(12, 10, 12, 10); bfl.setSpacing(6)
        lbl_hold = QLabel("HOLD TIMER")
        lbl_hold.setStyleSheet(label_style(GREEN3, 9))
        self._fill_bar = FillBar()
        self._hold_lbl = QLabel(f"0.0 / {HOLD_SECONDS}.0s")
        self._hold_lbl.setStyleSheet(label_style(GREEN3, 10))
        self._hold_lbl.setAlignment(Qt.AlignCenter)
        bfl.addWidget(lbl_hold); bfl.addWidget(self._fill_bar); bfl.addWidget(self._hold_lbl)
        right.addWidget(bar_frame)

        # Results so far
        res_frame = QFrame()
        res_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        rfl = QVBoxLayout(res_frame); rfl.setContentsMargins(12, 8, 12, 8); rfl.setSpacing(3)
        rfl.addWidget(QLabel("RESULTS SO FAR").also(
            lambda l: l.setStyleSheet(label_style(GREEN3, 9))
        ))
        self._result_labels = {}
        for key, name in [("flexion","FLEXION"),("abduction","ABDUCTION"),
                          ("ext_rot","EXT ROT"),("elbow","ELBOW")]:
            row = QHBoxLayout(); row.setSpacing(4)
            k = QLabel(f"{name}:"); k.setStyleSheet(label_style(GREEN3, 9))
            v = QLabel("—"); v.setStyleSheet(label_style(GREEN3, 10))
            row.addWidget(k); row.addWidget(v); row.addStretch()
            rfl.addLayout(row)
            self._result_labels[key] = v
        right.addWidget(res_frame)
        right.addStretch()

        body.addLayout(left, stretch=5)
        body.addLayout(right, stretch=4)

        body_w = QWidget()
        body_w.setLayout(body)
        root.addWidget(body_w, stretch=1)

        # Footer buttons
        foot = QWidget(); foot.setFixedHeight(52)
        foot.setStyleSheet(f"background:{SURFACE}; border-top:1px solid {BORDER};")
        fl = QHBoxLayout(foot); fl.setContentsMargins(16, 8, 16, 8); fl.setSpacing(10)

        self._skip_btn = QPushButton("SKIP STEP")
        self._skip_btn.setFixedHeight(30)
        self._skip_btn.setStyleSheet(btn_style(SURFACE2, TEXT3, BORDER))
        self._skip_btn.clicked.connect(self._skip_step)

        self._cancel_btn = QPushButton("CANCEL")
        self._cancel_btn.setFixedHeight(30)
        self._cancel_btn.setStyleSheet(btn_style(SURFACE2, RED, BORDER))
        self._cancel_btn.clicked.connect(self.reject)

        fl.addWidget(self._cancel_btn)
        fl.addWidget(self._skip_btn)
        fl.addStretch()

        root.addWidget(foot)

    # ── Step management ───────────────────────────────────────────────────────

    def _load_step_images(self, filenames: list[str]):
        """
        Load one or two images into the image frame.
        Each image is scaled to fit the available height.
        The second label is hidden when there is only one image.
        """
        frame_h = self._img_frame.height() - 8   # subtract margins
        two_images = len(filenames) == 2
        slot_w = (self._img_frame.width() - 12) // 2 if two_images \
                 else (self._img_frame.width() - 8)

        for i, lbl in enumerate(self._img_labels):
            if i < len(filenames):
                path = ROM_IMG_DIR / filenames[i]
                if path.exists():
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        scaled = pixmap.scaled(
                            slot_w, frame_h,
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation
                        )
                        lbl.setPixmap(scaled)
                        lbl.setVisible(True)
                        continue
                # File missing or unreadable — show placeholder text
                lbl.setPixmap(QPixmap())
                lbl.setText(filenames[i])
                lbl.setStyleSheet(f"color:{GREEN3}; font-size:9px; background:transparent; border:none;")
                lbl.setVisible(True)
            else:
                # Hide the second slot when only one image
                lbl.setPixmap(QPixmap())
                lbl.setVisible(False)

    def _load_step(self, idx: int):
        self._step_idx     = idx
        self._hold_elapsed = 0.0
        self._peak_angle   = 0.0
        self._holding      = False

        step = STEPS[idx]
        self._step_title.setText(step["title"])
        self._instruction.setText(step["instruction"])
        self._load_step_images(step["images"])
        self._fill_bar.set_fraction(0.0)
        self._hold_lbl.setText(f"0.0 / {HOLD_SECONDS}.0s")
        self._angle_lbl.setText("0°")
        self._peak_lbl.setText("0°")
        self._status_lbl.setText(STEPS[self._step_idx]["prompt"])
        self._status_lbl.setStyleSheet(label_style(AMBER, 11, bold=True))

        # Update step dots
        for i, dot in enumerate(self._dots):
            if i < idx:
                dot.setText("●"); dot.setStyleSheet(label_style(GREEN, 16))
            elif i == idx:
                dot.setText("◉"); dot.setStyleSheet(label_style(GREEN, 16))
            else:
                dot.setText("○"); dot.setStyleSheet(label_style(GREEN3, 16))

    def _skip_step(self):
        step = STEPS[self._step_idx]
        key = step["angle_key"]
        self._results[key] = self._peak_angle
        self._update_result_labels()
        self._advance()

    def _advance(self):
        if self._step_idx < len(STEPS) - 1:
            self._load_step(self._step_idx + 1)
        else:
            self._finish()

    def _finish(self):
        self._timer.stop()
        # Write to AppState
        with self._state.lock:
            self._state.rom_flex_limit  = max(self._results.get("flexion",   15.0), 15.0)
            self._state.rom_abd_limit   = max(self._results.get("abduction", 15.0), 15.0)
            self._state.rom_rot_limit   = max(self._results.get("ext_rot",   10.0), 10.0)
            self._state.rom_elbow_limit = max(self._results.get("elbow",     15.0), 15.0)
            self._state.rom_measured    = True
        # Persist to SQLite
        _save_rom(
            self._results.get("flexion",   0.0),
            self._results.get("abduction", 0.0),
            self._results.get("ext_rot",   0.0),
            self._results.get("elbow",     0.0),
        )
        print(f"[ROM] Saved: {self._results}")
        self.accept()

    def _update_result_labels(self):
        for key, lbl in self._result_labels.items():
            v = self._results.get(key, 0.0)
            if v > 0:
                lbl.setText(f"{v:.0f}°")
                lbl.setStyleSheet(label_style(GREEN, 10, bold=True))
            else:
                lbl.setText("—")
                lbl.setStyleSheet(label_style(GREEN3, 10))

    # ── Per-frame tick ────────────────────────────────────────────────────────

    def _tick(self):
        step = STEPS[self._step_idx]

        with self._state.lock:
            calibrated = self._state.calibrated
            angle = abs(getattr(self._state, step["state_attr"], 0.0))

        if not calibrated:
            self._status_lbl.setText("! CALIBRATE FIRST")
            self._status_lbl.setStyleSheet(label_style(RED, 11, bold=True))
            return

        self._angle_lbl.setText(f"{angle:.0f}°")

        # Track peak
        if angle > self._peak_angle:
            self._peak_angle = angle
        self._peak_lbl.setText(f"{self._peak_angle:.0f}°")

        if angle < MIN_ANGLE_DEG:
            # Not raised enough yet
            self._holding      = False
            self._hold_elapsed = 0.0
            self._fill_bar.set_fraction(0.0)
            self._hold_lbl.setText(f"0.0 / {HOLD_SECONDS}.0s")
            self._status_lbl.setText(STEPS[self._step_idx]["prompt"])
            self._status_lbl.setStyleSheet(label_style(AMBER, 11, bold=True))
            return

        # Arm is raised — accumulate hold time
        self._holding       = True
        self._hold_elapsed += TICK_MS / 1000.0
        frac = self._hold_elapsed / HOLD_SECONDS
        self._fill_bar.set_fraction(frac)
        remaining = max(0.0, HOLD_SECONDS - self._hold_elapsed)
        self._hold_lbl.setText(f"{self._hold_elapsed:.1f} / {HOLD_SECONDS}.0s")

        if self._hold_elapsed < HOLD_SECONDS:
            self._status_lbl.setText(f"HOLD...  {remaining:.1f}s")
            self._status_lbl.setStyleSheet(label_style(GREEN, 11, bold=True))
        else:
            # Step complete
            self._status_lbl.setText("✓ RECORDED!")
            self._status_lbl.setStyleSheet(label_style(GREEN, 12, bold=True))
            key = step["angle_key"]
            self._results[key] = self._peak_angle
            self._update_result_labels()
            QTimer.singleShot(800, self._advance)
            self._timer.stop()
            # restart after advance
            QTimer.singleShot(900, self._timer.start)


def _also(self, fn):
    fn(self); return self

from PyQt5.QtWidgets import QLabel
QLabel.also = _also