"""
gui/panels/exercise_panel.py
----------------------------
Panel 1 — Exercise selection and session configuration.

Left sidebar: exercise library list.
Right: exercise detail — description, exercise image,
       set/rep/hold steppers, ROM target display, pain selector,
       Start Session button.

Exercise data is imported from calc.exercise_library — no
exercise-specific knowledge lives in this file.

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
from calc.exercise_library import EXERCISES, ExerciseDef, exercises_for_pain, get_exercise
from gui.styles import *

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sessions.db"

DIFF_COLOUR = {"Easy": GREEN5, "Moderate": AMBER, "Hard": RED}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _exercise_counts() -> dict[str, tuple[int, str | None]]:
    """Return {name: (count, last_date_iso)} for exercises in the sessions DB."""
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
    Return the name of the best exercise for this pain level.

    Priority: exercises not yet attempted > oldest last-session date >
              fewest total sessions.
    """
    allowed = exercises_for_pain(pain)
    if not allowed:
        return None
    counts = _exercise_counts()

    def sort_key(ex: ExerciseDef):
        if ex.name not in counts:
            return (0, "")
        n, last = counts[ex.name]
        return (1, last or "", n)

    return sorted(allowed, key=sort_key)[0].name


# ── Stepper / pain widgets (unchanged from v5) ────────────────────────────────

class StepperWidget(QWidget):
    def __init__(self, label: str, value: int, min_v: int, max_v: int, parent=None):
        super().__init__(parent)
        self._val = value; self._min = min_v; self._max = max_v
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet(label_style(TEXT3, 12))
        self._minus = QPushButton("−"); self._minus.setFixedSize(22, 22)
        self._minus.setStyleSheet(btn_style(SURFACE3, GREEN3, BORDER))
        self._disp  = QLabel(str(value)); self._disp.setFixedWidth(28)
        self._disp.setAlignment(Qt.AlignCenter)
        self._disp.setStyleSheet(label_style(GREEN, 13, bold=True))
        self._plus  = QPushButton("+"); self._plus.setFixedSize(22, 22)
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
    def set_value(self, v: int):
        v = max(self._min, min(self._max, int(v)))
        self._val = v
        self._disp.setText(str(v))


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


# ── Main panel ────────────────────────────────────────────────────────────────

class ExercisePanel(QWidget):
    start_session_requested = pyqtSignal(str, int, int, int)  # exercise, pain, sets, reps

    def __init__(self, state: AppState, rep_detector, parent=None):
        super().__init__(parent)
        self._state  = state
        self._repdet = rep_detector
        self._sel_idx = 0
        self._build()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Panel title bar
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
        llay.addWidget(QLabel("EXERCISES").also(
            lambda l: l.setStyleSheet(label_style(GREEN3, 12))
        ))

        self._ex_list = QListWidget()
        self._ex_list.setStyleSheet(
            f"QListWidget{{background:{SURFACE};border:none;color:{TEXT2};}}"
            f"QListWidget::item{{padding:8px 6px;border-bottom:1px solid {BORDER};}}"
            f"QListWidget::item:selected{{background:{GREEN4};color:{GREEN};border-left:2px solid {GREEN};}}"
            f"QListWidget::item:hover{{background:{SURFACE3};}}"
        )
        for ex in EXERCISES:
            self._ex_list.addItem(QListWidgetItem(ex.name))
        self._ex_list.setCurrentRow(0)
        self._ex_list.currentRowChanged.connect(self._on_select)

        self._pain_lbl = QLabel("ALL EXERCISES AVAILABLE")
        self._pain_lbl.setStyleSheet(label_style(GREEN5, 9))
        self._pain_lbl.setWordWrap(True)
        llay.addWidget(self._pain_lbl)
        llay.addWidget(QLabel("★ = suggested for this session").also(
            lambda l: l.setStyleSheet(label_style(AMBER, 9, bold=False))
        ))
        llay.addWidget(self._ex_list, stretch=1)
        body.addWidget(left_w)

        # ── Right: detail ─────────────────────────────────────────────────────
        right_w = QWidget()
        rlay = QVBoxLayout(right_w); rlay.setContentsMargins(20,14,20,0); rlay.setSpacing(10)

        # Hero card
        hero = QFrame(); hero.setStyleSheet(card_style(SURFACE2, BORDER2))
        hl = QHBoxLayout(hero); hl.setContentsMargins(14,12,14,12); hl.setSpacing(14)
        hero_txt = QVBoxLayout(); hero_txt.setSpacing(4)
        self._hero_name = QLabel(""); self._hero_name.setStyleSheet(label_style(GREEN, 15, bold=True))
        self._hero_diff = QLabel(""); self._hero_diff.setStyleSheet(label_style(AMBER, 11))
        self._hero_mode = QLabel(""); self._hero_mode.setStyleSheet(label_style(GREEN3, 11))
        self._hero_desc = QLabel(""); self._hero_desc.setStyleSheet(label_style(TEXT3, 12))
        self._hero_desc.setWordWrap(True)
        hero_txt.addWidget(self._hero_name)
        hero_txt.addWidget(self._hero_diff)
        hero_txt.addWidget(self._hero_mode)
        hero_txt.addWidget(self._hero_desc)
        hero_txt.addStretch()
        hl.addLayout(hero_txt, stretch=1)
        rlay.addWidget(hero)

        # Config + ROM row
        cfg_row = QHBoxLayout(); cfg_row.setSpacing(12)

        cfg_frame = QFrame(); cfg_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        cf = QVBoxLayout(cfg_frame); cf.setContentsMargins(12,10,12,10); cf.setSpacing(6)
        cf.addWidget(QLabel("CONFIGURATION").also(lambda l: l.setStyleSheet(label_style(GREEN3, 11))))
        with self._state.lock:
            _def_sets = self._state.default_sets
            _def_reps = self._state.default_reps

        self._sets_w = StepperWidget("SETS",    _def_sets,  1, 10)
        self._reps_w = StepperWidget("REPS",   _def_reps,  1, 30)
        self._hold_w = StepperWidget("HOLD(s)", 25, 5, 120)
        cf.addWidget(self._sets_w); cf.addWidget(self._reps_w); cf.addWidget(self._hold_w)

        rom_frame = QFrame(); rom_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        rf = QVBoxLayout(rom_frame); rf.setContentsMargins(12,10,12,10); rf.setSpacing(5)
        rf.addWidget(QLabel("ROM TARGET").also(lambda l: l.setStyleSheet(label_style(GREEN3, 11))))
        self._rom_flex_lbl = self._stat_row(rf, "FLEXION ROM")
        self._rom_abd_lbl  = self._stat_row(rf, "ABDUCTION ROM")
        self._rom_goal_lbl = self._stat_row(rf, "SESSION GOAL")
        self._rom_meas_lbl = QLabel("NOT MEASURED")
        self._rom_meas_lbl.setStyleSheet(label_style(RED, 11))
        rf.addWidget(self._rom_meas_lbl)

        cfg_row.addWidget(cfg_frame, stretch=1)
        cfg_row.addWidget(rom_frame, stretch=1)
        rlay.addLayout(cfg_row)

        # Exercise image
        img_frame = QFrame(); img_frame.setStyleSheet(card_style(SURFACE2, BORDER))
        img_lay = QHBoxLayout(img_frame); img_lay.setContentsMargins(12,10,12,10)
        self._ex_img = QLabel()
        self._ex_img.setFixedHeight(200)
        self._ex_img.setAlignment(Qt.AlignCenter)
        self._ex_img.setStyleSheet(
            f"background:transparent;border:none;color:{GREEN3};font-size:11px;"
        )
        img_lay.addWidget(self._ex_img)
        rlay.addWidget(img_frame)

        # Pain selector + start button
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
        self._apply_pain_filter(0)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _stat_row(self, layout, label):
        row = QHBoxLayout(); row.setSpacing(4)
        lbl = QLabel(f"{label}:"); lbl.setStyleSheet(label_style(GREEN3, 11))
        val = QLabel("—"); val.setStyleSheet(label_style(TEXT_BRIGHT, 12, bold=True))
        row.addWidget(lbl); row.addWidget(val); row.addStretch()
        layout.addLayout(row)
        return val

    def _load_image(self, ex: ExerciseDef):
        """Show exercise photo in the detail card, or a placeholder text."""
        from PyQt5.QtGui import QPixmap
        img_path = ex.image_path
        if img_path:
            pix = QPixmap(str(img_path))
            if not pix.isNull():
                scaled = pix.scaledToHeight(
                    self._ex_img.height(), Qt.SmoothTransformation
                )
                self._ex_img.setPixmap(scaled)
                self._ex_img.setText("")
                return
        self._ex_img.setPixmap(QPixmap())
        self._ex_img.setText("No image available yet")

    def _apply_pain_filter(self, pain: int):
        """Dim unavailable exercises and badge the suggested one."""
        from PyQt5.QtGui import QColor
        suggestion = _suggested_exercise(pain)
        available_indices = []

        for i, ex in enumerate(EXERCISES):
            item = self._ex_list.item(i)
            if item is None:
                continue
            allowed = ex.min_pain <= pain <= ex.max_pain
            if allowed:
                item.setFlags(item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setForeground(QColor(TEXT))
                available_indices.append(i)
                if ex.name == suggestion:
                    item.setBackground(QColor("#fff8e8"))
                    item.setText(f"{ex.name}  ★")
                else:
                    item.setBackground(QColor(SURFACE))
                    item.setText(ex.name)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                item.setForeground(QColor(GREEN_DIM))
                item.setBackground(QColor(SURFACE2))
                item.setText(ex.name)

        if pain <= 3:
            self._pain_lbl.setText("ALL EXERCISES AVAILABLE")
            self._pain_lbl.setStyleSheet(label_style(GREEN5, 9))
        elif pain <= 6:
            self._pain_lbl.setText(f"PAIN {pain}/10 — HARD EXERCISES RESTRICTED")
            self._pain_lbl.setStyleSheet(label_style(AMBER, 9))
        else:
            self._pain_lbl.setText(f"PAIN {pain}/10 — GENTLE EXERCISES ONLY")
            self._pain_lbl.setStyleSheet(label_style(RED, 9))

        # If current selection is now locked, jump to suggestion / first available
        cur = self._ex_list.currentRow()
        cur_item = self._ex_list.item(cur) if cur >= 0 else None
        if cur_item and not (cur_item.flags() & Qt.ItemIsEnabled):
            target = next(
                (i for i, ex in enumerate(EXERCISES) if ex.name == suggestion),
                available_indices[0] if available_indices else 0,
            )
            self._ex_list.setCurrentRow(target)

    def _on_select(self, idx):
        self._sel_idx = max(0, min(idx, len(EXERCISES) - 1))
        self._update_detail(self._sel_idx)
        ex = EXERCISES[self._sel_idx]
        self._repdet.set_exercise(ex)

    def _update_detail(self, idx):
        ex = EXERCISES[idx]
        self._hero_name.setText(ex.name)
        diff_col = DIFF_COLOUR.get(ex.difficulty, GREEN3)
        self._hero_diff.setText(
            f"● {ex.difficulty.upper()}   Pain {ex.min_pain}–{ex.max_pain}"
        )
        self._hero_diff.setStyleSheet(label_style(diff_col, 10))

        # Mode badge + show/hide steppers based on exercise type
        if ex.is_hold_exercise:
            self._hero_mode.setText(f"⏱  HOLD — target adjustable below")
            self._hero_mode.setStyleSheet(label_style(CYAN, 11))
            self._reps_w.setVisible(False)
            self._hold_w.setVisible(True)
            self._hold_w.set_value(max(1, min(300, int(ex.hold_duration_s))))
        else:
            self._hero_mode.setText(
                f"↕  REPS — track: {ex.rep_angle}  "
                f"enter {ex.rep_enter_deg:.0f}°  exit {ex.rep_exit_deg:.0f}°"
            )
            self._hero_mode.setStyleSheet(label_style(GREEN3, 11))
            self._reps_w.setVisible(True)
            self._hold_w.setVisible(False)

        self._hero_desc.setText(ex.description)
        self._load_image(ex)

    def _on_start(self):
        with self._state.lock:
            if not self._state.calibrated:
                return
        ex = EXERCISES[self._sel_idx]
        reps_or_hold = self._hold_w.value if ex.is_hold_exercise else self._reps_w.value
        self.start_session_requested.emit(
            ex.name,
            self._pain_sel.value,
            self._sets_w.value,
            reps_or_hold,
        )

    def refresh(self):
        with self._state.lock:
            rom_flex = self._state.rom_flex_limit
            rom_abd  = self._state.rom_abd_limit
            measured = getattr(self._state, "rom_measured", False)

        self._rom_flex_lbl.setText(f"{rom_flex:.0f}°")
        self._rom_abd_lbl.setText(f"{rom_abd:.0f}°")

        ex = EXERCISES[self._sel_idx]
        # Show goal based on which axis the exercise targets
        if ex.goal_abd_deg > 0:
            self._rom_goal_lbl.setText(f"{rom_abd:.0f}°  (abduction)")
        elif ex.goal_flex_deg > 0:
            self._rom_goal_lbl.setText(f"{rom_flex:.0f}°  (flexion)")
        else:
            self._rom_goal_lbl.setText("—")

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