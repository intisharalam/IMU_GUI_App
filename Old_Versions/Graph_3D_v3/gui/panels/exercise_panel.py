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

from ble.ble_state import AppState
from gui.styles import *

EXERCISES = [
    ("FLEXION RAISE",      "🔼", "Moderate", "Raise arm forward in sagittal plane. Primary: shoulder flexion."),
    ("ABDUCTION",          "↔",  "Moderate", "Raise arm sideways in frontal plane. Primary: shoulder abduction."),
    ("EXTERNAL ROTATION",  "⟳",  "Hard",     "Rotate arm outward with elbow at 90°. Primary: external rotation."),
    ("PENDULUM SWING",     "〜",  "Easy",     "Lean forward, arm hangs and swings gently under gravity."),
    ("ELBOW CURL",         "💪",  "Easy",     "Bend elbow toward shoulder and return. Primary: elbow flexion."),
]

DIFF_COLOUR = {"Easy": GREEN5, "Moderate": AMBER, "Hard": RED}


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
    def __init__(self, parent=None):
        super().__init__(parent)
        self._val = 0
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
        for name, icon, diff, _ in EXERCISES:
            item = QListWidgetItem(f"{name}")
            self._ex_list.addItem(item)
        self._ex_list.setCurrentRow(0)
        self._ex_list.currentRowChanged.connect(self._on_select)
        llay.addWidget(self._ex_list, stretch=1)
        body.addWidget(left_w)

        # ── Right: detail ─────────────────────────────────────────────────────
        right_w = QWidget()
        rlay = QVBoxLayout(right_w); rlay.setContentsMargins(20,14,20,0); rlay.setSpacing(10)

        # Hero card
        hero = QFrame(); hero.setStyleSheet(card_style(SURFACE2, BORDER2))
        hl = QHBoxLayout(hero); hl.setContentsMargins(14,12,14,12); hl.setSpacing(14)
        # self._hero_icon = QLabel("🔼")
        # self._hero_icon.setStyleSheet(
        #     f"font-size:40px;background:{SURFACE3};border:1px solid {BORDER};"
        #     f"border-radius:3px;padding:10px;min-width:70px;text-align:center;"
        # )
        # self._hero_icon.setAlignment(Qt.AlignCenter)
        # self._hero_icon.setFixedSize(80,80)
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
        # hl.addWidget(self._hero_icon) 
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

        # Pain + start
        foot = QWidget(); foot.setFixedHeight(54)
        foot.setStyleSheet(f"background:{SURFACE};border-top:1px solid {BORDER};")
        fl = QHBoxLayout(foot); fl.setContentsMargins(20,8,20,8); fl.setSpacing(12)
        self._pain_sel = PainSelector()
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

    def _stat_row(self, layout, label):
        row = QHBoxLayout(); row.setSpacing(4)
        lbl = QLabel(f"{label}:"); lbl.setStyleSheet(label_style(GREEN3, 11))
        val = QLabel("—"); val.setStyleSheet(label_style(TEXT_BRIGHT, 12, bold=True))
        row.addWidget(lbl); row.addWidget(val); row.addStretch()
        layout.addLayout(row)
        return val

    def _on_select(self, idx):
        self._sel_idx = idx
        self._update_detail(idx)
        self._repdet.set_exercise(EXERCISES[idx][0])

    def _update_detail(self, idx):
        name, icon, diff, desc = EXERCISES[idx]
        self._hero_name.setText(name)
        # self._hero_icon.setText(icon)
        self._hero_diff.setText(f"● {diff.upper()}")
        self._hero_diff.setStyleSheet(label_style(DIFF_COLOUR.get(diff, GREEN3), 10))
        self._hero_desc.setText(desc)

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
        goal = rom_flex * 0.9 if "FLEX" in EXERCISES[self._sel_idx][0] else rom_abd * 0.9
        self._rom_goal_lbl.setText(f"{goal:.0f}°  (90%)")
        if measured:
            self._rom_meas_lbl.setText("✓ ROM MEASURED")
            self._rom_meas_lbl.setStyleSheet(label_style(GREEN, 12))
        else:
            self._rom_meas_lbl.setText("MEASURE ROM FIRST (Connect panel)")
            self._rom_meas_lbl.setStyleSheet(label_style(AMBER, 12))

# monkey-patch for QLabel.also
def _also(self, fn):
    fn(self); return self
QLabel.also = _also
