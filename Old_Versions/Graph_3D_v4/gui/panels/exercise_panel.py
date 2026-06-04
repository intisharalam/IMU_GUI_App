"""
gui/panels/exercise_panel.py
----------------------------
Panel 1 — Exercise selection and session configuration.

Left sidebar: exercise library list.
Right: exercise detail — description, 3D demo placeholder,
       set/rep/hold steppers, ROM target display, pain selector,
       Start Session button.

Emits start_session_requested(exercise, pain_pre, sets, reps).
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QListWidget, QListWidgetItem, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal

import sqlite3
from pathlib import Path
from ble.ble_state import AppState
from gui.styles import *

DB_PATH     = Path(__file__).parent.parent.parent / "data"   / "sessions.db"
ASSETS_DIR  = Path(__file__).parent.parent.parent / "assets"

# Maps exercise name → image filename under ASSETS_DIR.
# Add an entry here as each image becomes available.
EXERCISE_IMAGES: dict[str, str] = {
    "CROSS-BODY STRETCH": "Cross_Body_Stretch.png",
    "ELBOW CURL":         "Elbow_Curl.png",
    "FINGER WALL CRAWL":  "Finger_Wall_Crawl.png",
    "PENDULUM SWING":     "Pendulum_Swing.png",
    "TOWEL STRETCH":     "towel_stretch.png",
    "FLEXION RAISE":     "flexion_raise.png",
    "ABDUCTION RAISE":     "abduction_raise.png",
}

# Each entry: (name, difficulty, description, primary_angle, min_pain, max_pain)
#
# min_pain / max_pain define the pain band in which this exercise is appropriate.
#   - Pendulum:           pain 4–10  (gentle, pain-dominant phase)
#   - Easy stretches:     pain 3–8   (moderate pain, building range)
#   - Moderate raises:    pain 0–6   (lower pain, active mobilisation)
#   - Hard rotations:     pain 0–4   (recovery / thawing phase)
#
# primary_angle: which AppState ROM value drives the ROM TARGET display.
EXERCISES = [
    # name                   diff        description                                                              primary     min  max
    ("PENDULUM SWING",       "Easy",     "Lean forward, arm hangs and swings gently under gravity. "
                                         "Decompresses the glenohumeral joint. Best for high-pain days.",         "flexion",   4,  10),
    ("ELBOW CURL",           "Easy",     "Bend elbow toward shoulder and return slowly. "
                                         "Maintains elbow mobility and warms up the arm without "
                                         "loading the shoulder.",                                                 "elbow",     3,   8),
    ("FINGER WALL CRAWL",    "Easy",     "Face a wall and walk fingers upward as far as comfortable. "
                                         "Builds active flexion range gradually. Physiotherapist's "
                                         "primary exercise for AC.",                                              "flexion",   3,   8),
    ("CROSS-BODY STRETCH",   "Easy",     "Use the good arm to gently draw the affected arm across "
                                         "the chest. Stretches the posterior capsule. Hold 20–30 s.",             "abduction", 3,   7),
    ("TOWEL STRETCH",        "Moderate", "Hold a towel behind the back — good hand above, affected "
                                         "hand below. Gently pull upward to stretch internal rotation.",          "ext_rot",   2,   6),
    ("FLEXION RAISE",        "Moderate", "Raise arm forward in the sagittal plane, as high as "
                                         "comfortable. Primary measure: shoulder flexion arc.",                   "flexion",   0,   6),
    ("ABDUCTION RAISE",      "Moderate", "Raise arm sideways in the frontal plane. "
                                         "Primary measure: shoulder abduction arc.",                              "abduction", 0,   6),
    ("DOORWAY STRETCH",      "Moderate", "Stand in a doorway, arm at 90°, and lean gently forward. "
                                         "Passive stretch targeting anterior capsule and pectorals.",             "flexion",   0,   5),
    ("EXTERNAL ROTATION",    "Hard",     "Elbow at side, bent to 90°. Rotate forearm outward "
                                         "against gentle resistance or gravity. "
                                         "Targets the most restricted plane in AC.",                             "ext_rot",   0,   4),
    ("BEHIND-BACK REACH",    "Hard",     "Reach the affected arm behind the back and slide hand "
                                         "upward along the spine. Measures combined internal "
                                         "rotation and extension. Advanced recovery exercise.",                   "ext_rot",   0,   3),
]

DIFF_COLOUR = {"Easy": GREEN5, "Moderate": AMBER, "Hard": RED}


def _exercise_counts() -> dict[str, tuple[int, str | None]]:
    """
    Return {exercise_name: (session_count, last_date_iso)} for all exercises
    that appear in the sessions DB. Exercises with no history are not in the dict.
    """
    if not DB_PATH.exists():
        return {}
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT exercise, COUNT(*) as n, MAX(date) as last "
            "FROM sessions GROUP BY exercise"
        ).fetchall()
        con.close()
        return {r[0]: (r[1], r[2]) for r in rows}
    except Exception:
        return {}


def _suggested_exercise(pain: int) -> str | None:
    """
    Return the name of the exercise that should be highlighted as the
    suggested pick for this session.

    Logic (in priority order):
      1. Only consider exercises currently allowed for this pain level.
      2. Prefer the exercise with the oldest last-session date
         (i.e. hasn't been done in the longest time).
      3. Break ties by lowest total session count.
      4. If all allowed exercises are equally untried, return the first one.
    """
    allowed = [
        name for name, diff, desc, primary, mn, mx in EXERCISES
        if mn <= pain <= mx
    ]
    if not allowed:
        return None

    counts = _exercise_counts()   # {name: (count, last_date)}

    def sort_key(name):
        if name not in counts:
            return (0, "")          # never done → highest priority (sort first)
        n, last = counts[name]
        return (1, last, n)         # done before → sort by oldest date, then fewest

    return sorted(allowed, key=sort_key)[0]


class StepperWidget(QWidget):
    def __init__(self, label: str, value: int, min_v: int, max_v: int, parent=None):
        super().__init__(parent)
        self._val = value; self._min = min_v; self._max = max_v
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet(label_style(TEXT3, 12))
        self._minus = QPushButton("−"); self._minus.setFixedSize(22,22)
        self._minus.setStyleSheet(btn_style(SURFACE3, GREEN3, BORDER))
        self._disp  = QLabel(str(value)); self._disp.setFixedWidth(28)
        self._disp.setAlignment(Qt.AlignCenter)
        self._disp.setStyleSheet(label_style(GREEN, 13, bold=True))
        self._plus  = QPushButton("+"); self._plus.setFixedSize(22,22)
        self._plus.setStyleSheet(btn_style(SURFACE3, GREEN3, BORDER))
        self._minus.clicked.connect(self._dec)
        self._plus.clicked.connect(self._inc)
        lay.addWidget(lbl); lay.addStretch()
        lay.addWidget(self._minus); lay.addWidget(self._disp); lay.addWidget(self._plus)

    def _dec(self):
        if self._val > self._min: self._val -= 1; self._disp.setText(str(self._val))
    def _inc(self):
        if self._val < self._max: self._val += 1; self._disp.setText(str(self._val))
    @property
    def value(self): return self._val


class PainSelector(QWidget):
    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self._val = 0
        self._on_change = on_change
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(3)
        lbl = QLabel("PAIN:"); lbl.setStyleSheet(label_style(TEXT3, 15))
        lay.addWidget(lbl)
        self._btns = []
        for i in range(11):
            b = QPushButton(str(i)); b.setFixedSize(32, 32)
            col = GREEN5 if i <= 3 else (AMBER if i <= 6 else RED)

            b.setStyleSheet(
                f"QPushButton{{background:{SURFACE3};color:{col};border:1px solid {BORDER};"
                f"border-radius:3px;font-size:15px;font-family:'Courier New',monospace;"
                f"padding:0px;text-align:center;}}"
                f"QPushButton:checked{{background:{col};color:{SURFACE};border:1px solid {col};}}"
                f"QPushButton:hover{{background:{col}22;border:1px solid {col};}}"
            )

            b.setCheckable(True)
            b.clicked.connect(lambda _, i=i: self._select(i))
            self._btns.append(b)
            lay.addWidget(b)
        self._select(0)

    def _select(self, idx):
        self._val = idx
        for i, b in enumerate(self._btns):
            b.setChecked(i == idx)
        if self._on_change:
            self._on_change(idx)

    @property
    def value(self): return self._val


class ExercisePanel(QWidget):
    start_session_requested = pyqtSignal(str, int, int, int)  # exercise, pain, sets, reps

    def __init__(self, state: AppState, rep_detector, parent=None):
        super().__init__(parent)
        self._state   = state
        self._repdet  = rep_detector
        self._sel_idx = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Panel title
        tb = QWidget(); tb.setFixedHeight(36)
        tb.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(tb); tl.setContentsMargins(16,0,16,0)
        tl.addWidget(QLabel("EXERCISE LIBRARY").also(
            lambda l: l.setStyleSheet(label_style(GREEN, 12, bold=True))
        ))
        tl.addStretch()
        root.addWidget(tb)

        body = QHBoxLayout(); body.setContentsMargins(0,0,0,0); body.setSpacing(0)

        # ── Left: exercise list ───────────────────────────────────────────────
        left_w = QWidget(); left_w.setFixedWidth(240)
        left_w.setStyleSheet(f"background:{SURFACE};border-right:1px solid {BORDER};")
        llay = QVBoxLayout(left_w); llay.setContentsMargins(8,10,8,10); llay.setSpacing(4)
        hdr = QLabel("EXERCISES"); hdr.setStyleSheet(label_style(GREEN3, 12))
        llay.addWidget(hdr)
        self._ex_list = QListWidget()
        self._ex_list.setStyleSheet(
            f"QListWidget{{background:{SURFACE};border:none;color:{TEXT2};}}"
            f"QListWidget::item{{padding:8px 6px;border-bottom:1px solid {BORDER};}}"
            f"QListWidget::item:selected{{background:{GREEN4};color:{GREEN};border-left:2px solid {GREEN};}}"
            f"QListWidget::item:hover{{background:{SURFACE3};}}"
        )
        for name, diff, desc, primary, mn, mx in EXERCISES:
            item = QListWidgetItem(f"{name}")
            self._ex_list.addItem(item)
        self._ex_list.setCurrentRow(0)
        self._ex_list.currentRowChanged.connect(self._on_select)

        # Status label — shows pain filter state and suggestion hint
        self._pain_lbl = QLabel("ALL EXERCISES AVAILABLE")
        self._pain_lbl.setStyleSheet(label_style(GREEN5, 9))
        self._pain_lbl.setWordWrap(True)
        llay.addWidget(self._pain_lbl)

        # Legend for the suggestion star
        legend = QLabel("★ = suggested for this session")
        legend.setStyleSheet(label_style(AMBER, 9, bold=False))
        llay.addWidget(legend)

        llay.addWidget(self._ex_list, stretch=1)
        body.addWidget(left_w)

        # ── Right: detail ─────────────────────────────────────────────────────
        right_w = QWidget()
        rlay = QVBoxLayout(right_w); rlay.setContentsMargins(20,14,20,0); rlay.setSpacing(10)

        # Hero card — name, difficulty badge, description only
        hero = QFrame(); hero.setStyleSheet(card_style(SURFACE2, BORDER2))
        hl = QHBoxLayout(hero); hl.setContentsMargins(14,12,14,12); hl.setSpacing(14)

        hero_txt = QVBoxLayout(); hero_txt.setSpacing(4)
        self._hero_name = QLabel("FLEXION RAISE")
        self._hero_name.setStyleSheet(label_style(GREEN, 15, bold=True))
        self._hero_diff = QLabel("● MODERATE")
        self._hero_diff.setStyleSheet(label_style(AMBER, 11))
        self._hero_desc = QLabel("")
        self._hero_desc.setStyleSheet(label_style(TEXT3, 12))
        self._hero_desc.setWordWrap(True)
        hero_txt.addWidget(self._hero_name)
        hero_txt.addWidget(self._hero_diff)
        hero_txt.addWidget(self._hero_desc)
        hero_txt.addStretch()
        hl.addLayout(hero_txt, stretch=1)
        rlay.addWidget(hero)

        # Config row
        cfg_row = QHBoxLayout(); cfg_row.setSpacing(12)
        cfg_frame = QFrame(); cfg_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        cf = QVBoxLayout(cfg_frame); cf.setContentsMargins(12,10,12,10); cf.setSpacing(6)
        cf.addWidget(QLabel("CONFIGURATION").also(lambda l: l.setStyleSheet(label_style(GREEN3, 11))))
        self._sets_w  = StepperWidget("SETS",  3, 1, 10)
        self._reps_w  = StepperWidget("REPS", 10, 1, 30)
        self._hold_w  = StepperWidget("HOLD(s)", 3, 1, 10)
        cf.addWidget(self._sets_w); cf.addWidget(self._reps_w); cf.addWidget(self._hold_w)

        rom_frame = QFrame(); rom_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        rf = QVBoxLayout(rom_frame); rf.setContentsMargins(12,10,12,10); rf.setSpacing(5)
        rf.addWidget(QLabel("ROM TARGET").also(lambda l: l.setStyleSheet(label_style(GREEN3, 11))))
        self._rom_flex_lbl  = self._stat_row(rf, "FLEXION ROM")
        self._rom_abd_lbl   = self._stat_row(rf, "ABDUCTION ROM")
        self._rom_goal_lbl  = self._stat_row(rf, "SESSION GOAL")
        self._rom_meas_lbl  = QLabel("NOT MEASURED")
        self._rom_meas_lbl.setStyleSheet(label_style(RED, 11))
        rf.addWidget(self._rom_meas_lbl)

        cfg_row.addWidget(cfg_frame, stretch=1)
        cfg_row.addWidget(rom_frame, stretch=1)
        rlay.addLayout(cfg_row)

        # ── Exercise image ────────────────────────────────────────────────────
        img_frame = QFrame()
        img_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        img_lay = QHBoxLayout(img_frame)
        img_lay.setContentsMargins(12, 10, 12, 10)

        self._ex_img = QLabel()
        self._ex_img.setFixedHeight(200)
        self._ex_img.setAlignment(Qt.AlignCenter)
        self._ex_img.setStyleSheet(
            f"background:transparent; border:none; color:{GREEN3}; font-size:11px;"
        )
        img_lay.addWidget(self._ex_img)
        rlay.addWidget(img_frame)

        # Pain + start
        foot = QWidget(); foot.setFixedHeight(54)
        foot.setStyleSheet(f"background:{SURFACE};border-top:1px solid {BORDER};")
        fl = QHBoxLayout(foot); fl.setContentsMargins(20,8,20,8); fl.setSpacing(12)
        self._pain_sel = PainSelector(on_change=self._apply_pain_filter)
        self._start_btn = QPushButton("▶  START SESSION")
        self._start_btn.setFixedHeight(36)
        self._start_btn.setStyleSheet(
            f"QPushButton{{background:{GREEN4};color:{GREEN};border:1px solid {GREEN3};"
            f"border-radius:3px;font-size:13px;font-weight:bold;"
            f"font-family:'Courier New',monospace;padding:5px 24px;}}"
            f"QPushButton:hover{{background:{GREEN3};color:{BG};}}"
        )
        self._start_btn.clicked.connect(self._on_start)
        fl.addWidget(self._pain_sel, stretch=1)
        fl.addWidget(self._start_btn)

        rlay.addStretch()
        body.addWidget(right_w, stretch=1)
        root.addLayout(body, stretch=1)
        root.addWidget(foot)

        self._update_detail(0)
        self._apply_pain_filter(0)   # initial pass — all exercises available, suggestion computed

    def _load_exercise_image(self, name: str):
        """Load and display the exercise photo, or show a 'no image' placeholder."""
        from PyQt5.QtGui import QPixmap
        filename = EXERCISE_IMAGES.get(name)
        if filename:
            path = ASSETS_DIR / filename
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    # Scale to fit within the label height, preserve aspect ratio
                    scaled = pixmap.scaledToHeight(
                        self._ex_img.height(), Qt.SmoothTransformation
                    )
                    self._ex_img.setPixmap(scaled)
                    self._ex_img.setToolTip(name)
                    return
        # Placeholder — image not available yet
        self._ex_img.setPixmap(QPixmap())   # clear any previous image
        self._ex_img.setText("No image available yet")

    def _stat_row(self, layout, label):
        row = QHBoxLayout(); row.setSpacing(4)
        lbl = QLabel(f"{label}:"); lbl.setStyleSheet(label_style(GREEN3, 11))
        val = QLabel("—"); val.setStyleSheet(label_style(TEXT_BRIGHT, 12, bold=True))
        row.addWidget(lbl); row.addWidget(val); row.addStretch()
        layout.addLayout(row)
        return val

    def _apply_pain_filter(self, pain: int):
        """Show/dim exercises based on current pain score, and badge the suggestion."""
        from PyQt5.QtGui import QColor, QFont
        from PyQt5.QtCore import Qt

        suggestion = _suggested_exercise(pain)

        available_indices = []
        for i, (name, diff, desc, primary, mn, mx) in enumerate(EXERCISES):
            item = self._ex_list.item(i)
            if item is None:
                continue
            allowed = mn <= pain <= mx
            if allowed:
                item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setForeground(QColor(TEXT))
                available_indices.append(i)

                # Suggestion highlight — amber background + label
                if name == suggestion:
                    item.setBackground(QColor("#fff8e8"))   # pale amber tint
                    item.setText(f"{name}  ★")
                else:
                    item.setBackground(QColor(SURFACE))
                    item.setText(name)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                item.setForeground(QColor(GREEN_DIM))
                item.setBackground(QColor(SURFACE2))
                item.setText(name)

        # Update the pain status label
        if hasattr(self, '_pain_lbl'):
            if pain <= 3:
                self._pain_lbl.setText("ALL EXERCISES AVAILABLE")
                self._pain_lbl.setStyleSheet(label_style(GREEN5, 9))
            elif pain <= 6:
                self._pain_lbl.setText(f"PAIN {pain}/10 — HARD EXERCISES RESTRICTED")
                self._pain_lbl.setStyleSheet(label_style(AMBER, 9))
            else:
                self._pain_lbl.setText(f"PAIN {pain}/10 — GENTLE EXERCISES ONLY")
                self._pain_lbl.setStyleSheet(label_style(RED, 9))

        # If suggestion exists and nothing is selected, pre-select it
        cur = self._ex_list.currentRow()
        cur_item = cur >= 0 and self._ex_list.item(cur)
        if cur_item and not (cur_item.flags() & Qt.ItemIsEnabled):
            # Current selection just got locked — move to suggestion or first available
            target = next(
                (i for i, (name, *_) in enumerate(EXERCISES) if name == suggestion),
                available_indices[0] if available_indices else 0
            )
            self._ex_list.setCurrentRow(target)

    def _on_select(self, idx):
        self._sel_idx = idx
        self._update_detail(idx)
        self._repdet.set_exercise(EXERCISES[idx][0])

    def _update_detail(self, idx):
        name, diff, desc, primary, mn, mx = EXERCISES[idx]
        self._hero_name.setText(name)
        self._hero_diff.setText(f"● {diff.upper()}   Pain {mn}–{mx}")
        self._hero_diff.setStyleSheet(label_style(DIFF_COLOUR.get(diff, GREEN3), 10))
        self._hero_desc.setText(desc)
        self._load_exercise_image(name)
        self._load_exercise_image(name)

    def _on_start(self):
        with self._state.lock:
            if not self._state.calibrated:
                return
        name = EXERCISES[self._sel_idx][0]
        self.start_session_requested.emit(
            name, self._pain_sel.value,
            self._sets_w.value, self._reps_w.value
        )

    def refresh(self):
        with self._state.lock:
            rom_flex  = self._state.rom_flex_limit
            rom_abd   = self._state.rom_abd_limit
            measured  = getattr(self._state, 'rom_measured', False)

        self._rom_flex_lbl.setText(f"{rom_flex:.0f}°")
        self._rom_abd_lbl.setText(f"{rom_abd:.0f}°")

        # ROM goal uses the primary axis for the selected exercise
        primary = EXERCISES[self._sel_idx][3]
        base = rom_flex if primary in ("flexion", "elbow") else rom_abd
        goal = base * 0.9
        self._rom_goal_lbl.setText(f"{goal:.0f}°  (90% of measured)")

        if measured:
            self._rom_meas_lbl.setText("✓ ROM MEASURED")
            self._rom_meas_lbl.setStyleSheet(label_style(GREEN5, 12))
        else:
            self._rom_meas_lbl.setText("MEASURE ROM FIRST (Connect panel)")
            self._rom_meas_lbl.setStyleSheet(label_style(AMBER, 12))

# monkey-patch for QLabel.also
def _also(self, fn):
    fn(self); return self
QLabel.also = _also