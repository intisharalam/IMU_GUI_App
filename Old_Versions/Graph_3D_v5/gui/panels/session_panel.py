"""
gui/panels/session_panel.py
---------------------------
Panel 2 — Active session (full-screen during exercise).

Layout:
  ┌─────────────────────────────────┬──────────────────┐
  │   3D Skeleton (render_widget)   │  Session HUD     │
  │   full-height, stretches        │  260px fixed     │
  │                                 │  ─────────────── │
  │                                 │  Exercise image  │
  │                                 │  (below HUD)     │
  └─────────────────────────────────┴──────────────────┘

No sidebar shown during session — the main window hides it.
Emits session_ended(pain_post) when End Session is pressed.
Emits goal_achieved() to be forwarded to main_window.
"""

import time
import numpy as np
from pathlib import Path
from scipy.spatial.transform import Rotation

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QDialog, QDialogButtonBox,
    QFormLayout, QSpinBox, QSizePolicy, QProgressBar
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
import pyqtgraph as pg

from ble.ble_state import AppState
from calc.exercise_library import get_exercise, ExerciseDef
from gui.styles import *
from gui.widgets.render_widget import RenderWidget

DOWN_NP = np.array([0., -1., 0.])

# Fraction of ROM limit at which the goal sphere is placed
GOAL_ROM_FRACTION = 0.90


def _goal_pos(flex_deg: float, abd_deg: float,
              upper: float = 0.30, fore: float = 0.25) -> np.ndarray:
    """
    Compute wrist position in anatomical space for a given shoulder angle.
    flex_deg and abd_deg are the shoulder flexion / abduction in degrees.
    """
    fr = Rotation.from_euler("Z", -flex_deg, degrees=True)
    ar = Rotation.from_euler("X",  abd_deg,  degrees=True)
    return (ar * fr).apply(DOWN_NP) * (upper + fore)


# ── Dialogs ───────────────────────────────────────────────────────────────────

class EndSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("POST-SESSION")
        self.setFixedWidth(300)
        self.setStyleSheet(f"background:{BG};color:{TEXT};font-family:'Courier New',monospace;")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("PAIN SCORE (post-session):").also(
            lambda l: l.setStyleSheet(label_style(GREEN3, 10))
        ))
        self._pain = QSpinBox(); self._pain.setRange(0, 10); self._pain.setValue(0)
        lay.addWidget(self._pain)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @property
    def pain(self): return self._pain.value()


# ── Small widgets ─────────────────────────────────────────────────────────────

class AngleBox(QFrame):
    def __init__(self, label: str, colour: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(card_style(SURFACE3, BORDER))
        lay = QVBoxLayout(self); lay.setContentsMargins(8,6,8,6); lay.setSpacing(2)
        lbl = QLabel(label); lbl.setStyleSheet(label_style(colour, 11))
        self._val = QLabel("0°")
        self._val.setStyleSheet(
            f"color:{colour};font-size:22px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        self._max = QLabel("max: 0°"); self._max.setStyleSheet(label_style(GREEN3, 12))
        lay.addWidget(lbl); lay.addWidget(self._val); lay.addWidget(self._max)

    def update(self, val: float, max_val: float):
        self._val.setText(f"{val:.0f}°")
        self._max.setText(f"max: {max_val:.0f}°")


# ── Main panel ────────────────────────────────────────────────────────────────

class SessionPanel(QWidget):
    session_ended  = pyqtSignal(int)    # pain_post
    record_toggled = pyqtSignal(bool)
    goal_achieved  = pyqtSignal()

    def __init__(self, state: AppState, ble, calibration,
                 recorder, rep_detector, parent=None):
        super().__init__(parent)
        self._state      = state
        self._ble        = ble
        self._cal        = calibration
        self._rec        = recorder
        self._repdet     = rep_detector
        self._t_start    = None
        self._running    = False
        self._sets_total = 3
        self._reps_total = 10
        self._current_set = 1
        self._current_ex: ExerciseDef | None = None
        self._recording  = False
        self._build()

    def _build(self):
        root = QHBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── 3D render (left, expands) ─────────────────────────────────────────
        self._render = RenderWidget(self._state)
        self._render.goal_achieved.connect(self.goal_achieved)
        self._render.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._render, stretch=1)

        # ── HUD column (right, fixed width) ───────────────────────────────────
        hud = QWidget(); hud.setFixedWidth(260)
        hud.setStyleSheet(f"background:{SURFACE};border-left:1px solid {BORDER};")
        hl = QVBoxLayout(hud); hl.setContentsMargins(0,0,0,0); hl.setSpacing(0)

        # Top bar: exercise name + END button
        top = QWidget(); top.setFixedHeight(40)
        top.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(top); tl.setContentsMargins(10,0,10,0); tl.setSpacing(8)
        self._ex_lbl = QLabel("—")
        self._ex_lbl.setStyleSheet(label_style(GREEN3, 14))
        self._end_btn = QPushButton("END")
        self._end_btn.setFixedSize(48, 26)
        self._end_btn.setStyleSheet(
            btn_style(RED, "#fff", RED, "#aa1111") + "QPushButton { padding: 0px; }"
        )
        self._end_btn.clicked.connect(self._on_end)
        tl.addWidget(self._ex_lbl, stretch=1); tl.addWidget(self._end_btn)
        hl.addWidget(top)

        def _section(label):
            w = QWidget(); w.setFixedHeight(28)
            w.setStyleSheet(f"background:{SURFACE2};border-bottom:1px solid {BORDER};")
            l = QHBoxLayout(w); l.setContentsMargins(10,0,10,0)
            l.addWidget(QLabel(f"── {label}").also(
                lambda lb: lb.setStyleSheet(label_style(GREEN3, 12))
            ))
            hl.addWidget(w)

        # Timer + rep/hold counter
        _section("TIMER")
        tblock = QWidget()
        tbl = QVBoxLayout(tblock); tbl.setContentsMargins(12,8,12,4); tbl.setSpacing(2)
        self._timer_lbl = QLabel("00:00")
        self._timer_lbl.setStyleSheet(
            f"color:{GREEN};font-size:32px;font-weight:bold;"
            f"font-family:'Courier New',monospace;letter-spacing:2px;"
        )
        self._set_lbl = QLabel("SET  1 / 3")
        self._set_lbl.setStyleSheet(label_style(GREEN3, 12))
        self._reps_lbl = QLabel("REPS  0")
        self._reps_lbl.setStyleSheet(
            f"color:{GREEN};font-size:20px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        # Hold-timer progress bar (shown only for hold exercises)
        self._hold_bar = QProgressBar()
        self._hold_bar.setRange(0, 100)
        self._hold_bar.setValue(0)
        self._hold_bar.setFixedHeight(14)
        self._hold_bar.setTextVisible(False)
        self._hold_bar.setStyleSheet(
            f"QProgressBar{{background:{SURFACE3};border:1px solid {BORDER};border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{CYAN};border-radius:2px;}}"
        )
        self._hold_lbl = QLabel("HOLD — 0.0 s")
        self._hold_lbl.setStyleSheet(label_style(CYAN, 12))

        tbl.addWidget(self._timer_lbl)
        tbl.addWidget(self._set_lbl)
        tbl.addWidget(self._reps_lbl)
        tbl.addWidget(self._hold_bar)
        tbl.addWidget(self._hold_lbl)
        hl.addWidget(tblock)

        # Live angle readouts
        _section("LIVE ANGLES")
        ag = QWidget()
        agl = QHBoxLayout(ag); agl.setContentsMargins(8,6,8,6); agl.setSpacing(6)
        self._a_flex  = AngleBox("FLEX",    C_FLEX)
        self._a_abd   = AngleBox("ABD",     C_ABD)
        agl.addWidget(self._a_flex); agl.addWidget(self._a_abd)
        hl.addWidget(ag)
        ag2 = QWidget()
        ag2l = QHBoxLayout(ag2); ag2l.setContentsMargins(8,0,8,6); ag2l.setSpacing(6)
        self._a_rot   = AngleBox("EXT ROT", C_ROT)
        self._a_elbow = AngleBox("ELBOW",   C_ELBOW)
        ag2l.addWidget(self._a_rot); ag2l.addWidget(self._a_elbow)
        hl.addWidget(ag2)

        # Smoothness bar
        _section("SMOOTHNESS")
        sm = QWidget()
        sml = QHBoxLayout(sm); sml.setContentsMargins(10,6,10,6); sml.setSpacing(6)
        self._smooth_bar = pg.PlotWidget(background=SURFACE2)
        self._smooth_bar.setFixedHeight(28)
        self._smooth_bar.hideAxis('left'); self._smooth_bar.hideAxis('bottom')
        self._smooth_bar.setMouseEnabled(x=False, y=False); self._smooth_bar.hideButtons()
        self._smooth_curve = self._smooth_bar.plot(pen=pg.mkPen(GREEN, width=1.5))
        self._smooth_data  = [0.0] * 50
        self._smooth_val   = QLabel("—")
        self._smooth_val.setFixedWidth(32)
        self._smooth_val.setStyleSheet(label_style(GREEN, 11, bold=True))
        sml.addWidget(self._smooth_bar, stretch=1); sml.addWidget(self._smooth_val)
        hl.addWidget(sm)

        # Trunk lean
        _section("TRUNK LEAN")
        tl_widget = QWidget()
        tl_lay = QHBoxLayout(tl_widget)
        tl_lay.setContentsMargins(10,6,10,6); tl_lay.setSpacing(8)
        self._trunk_val    = QLabel("0°")
        self._trunk_val.setStyleSheet(
            f"color:{GREEN};font-size:20px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        self._trunk_status = QLabel("UPRIGHT")
        self._trunk_status.setStyleSheet(label_style(GREEN5, 11))
        self._trunk_status.setWordWrap(True)
        tl_lay.addWidget(self._trunk_val)
        tl_lay.addWidget(self._trunk_status, stretch=1)
        hl.addWidget(tl_widget)

        # Haptic log
        _section("HAPTIC LOG")
        self._log_labels = []
        for _ in range(4):
            lbl = QLabel("—")
            lbl.setStyleSheet(label_style(GREEN3, 12))
            lbl.setContentsMargins(10,1,10,1)
            hl.addWidget(lbl)
            self._log_labels.append(lbl)

        # ── Exercise image ────────────────────────────────────────────────────
        _section("EXERCISE")
        self._ex_img = QLabel()
        self._ex_img.setFixedHeight(130)
        self._ex_img.setAlignment(Qt.AlignCenter)
        self._ex_img.setStyleSheet(
            f"background:transparent;border:none;color:{GREEN3};font-size:11px;"
            f"padding:4px;"
        )
        hl.addWidget(self._ex_img)

        hl.addStretch()

        # Record button
        rb_wrap = QWidget()
        rbl = QHBoxLayout(rb_wrap); rbl.setContentsMargins(8,6,8,6)
        self._rec_btn = QPushButton("○  RECORD")
        self._rec_btn.setFixedHeight(28)
        self._rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
        self._rec_btn.clicked.connect(self._toggle_record)
        rbl.addWidget(self._rec_btn)
        hl.addWidget(rb_wrap)

        root.addWidget(hud)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def begin_session(self, exercise: str, sets: int, reps: int):
        self._t_start     = time.monotonic()
        self._running     = True
        self._sets_total  = sets
        self._reps_total  = reps
        self._current_set = 1
        self._current_ex  = get_exercise(exercise)

        self._ex_lbl.setText(exercise)
        self._load_ex_image(self._current_ex)
        self._update_mode_widgets()
        self.update_goal_sphere()
        self._render.set_exercise_guide(self._current_ex)

    def _load_ex_image(self, ex: ExerciseDef | None):
        """Show the exercise image in the HUD, or a placeholder."""
        if ex is None:
            self._ex_img.setPixmap(QPixmap())
            self._ex_img.setText("")
            return
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
        self._ex_img.setText(ex.description[:80] + "…" if ex and len(ex.description) > 80
                             else (ex.description if ex else ""))

    def _update_mode_widgets(self):
        """Show rep counter OR hold progress bar depending on exercise type."""
        is_hold = (self._current_ex is not None and self._current_ex.is_hold_exercise)
        self._reps_lbl.setVisible(not is_hold)
        self._hold_bar.setVisible(is_hold)
        self._hold_lbl.setVisible(is_hold)
        if is_hold:
            self._hold_bar.setValue(0)
            self._hold_lbl.setText("HOLD — 0.0 s")

    def update_goal_sphere(self):
        """
        Place the goal sphere using the active ExerciseDef's goal angles,
        scaled to 90 % of the patient's measured ROM.
        """
        ex = self._current_ex
        if ex is None or not ex.has_goal:
            self._render.clear_goal()
            return

        with self._state.lock:
            rom_flex = self._state.rom_flex_limit
            rom_abd  = self._state.rom_abd_limit

        # Scale the exercise's nominal goal by the patient's ROM
        flex_target = min(ex.goal_flex_deg, rom_flex) * GOAL_ROM_FRACTION
        abd_target  = min(ex.goal_abd_deg,  rom_abd)  * GOAL_ROM_FRACTION

        self._render.set_goal(_goal_pos(flex_target, abd_target))

    def stop_recording(self):
        if self._recording:
            self._recording = False
            self._render.stop_geometry_recording()
            self._rec_btn.setText("◉  RECORD")
            self._rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))

    # ── Per-frame refresh ─────────────────────────────────────────────────────

    def refresh(self):
        self._render.refresh()
        if not self._running:
            return
        now = time.monotonic()

        with self._state.lock:
            flex  = self._state.shoulder_flexion
            abd   = self._state.shoulder_abduction
            rot   = self._state.external_rotation
            elbow = self._state.elbow_flexion
            mf    = self._state.max_flexion
            ma    = self._state.max_abduction
            mr    = self._state.max_ext_rot
            me    = self._state.max_elbow
            reps  = self._state.session_reps
            hlog  = list(self._state.haptic_log)[-4:]
            trunk = self._state.trunk_lean_deg

        # Timer
        elapsed = int(now - self._t_start)
        m, s = divmod(elapsed, 60)
        self._timer_lbl.setText(f"{m:02d}:{s:02d}")
        self._set_lbl.setText(f"SET  {self._current_set} / {self._sets_total}")

        # Rep / hold counter
        ex = self._current_ex
        if ex is not None and ex.is_hold_exercise:
            prog = self._repdet.hold_progress
            self._hold_bar.setValue(int(prog * 100))
            self._hold_lbl.setText(
                f"HOLD — {self._repdet.hold_elapsed:.1f} s"
                f" / {ex.hold_duration_s:.0f} s"
            )
        else:
            self._reps_lbl.setText(f"REPS  {reps}")

        # Angle boxes
        self._a_flex.update(flex, mf)
        self._a_abd.update(abd, ma)
        self._a_rot.update(rot, mr)
        self._a_elbow.update(elbow, me)

        # Smoothness — diff the exercise's primary angle (not always flexion)
        smooth_angle_map = {"flexion": flex, "abduction": abd,
                            "ext_rot": rot, "elbow": elbow}
        smooth_key = (ex.smooth_angle if ex else "flexion")
        cur_val = smooth_angle_map.get(smooth_key, flex)
        prev_key = f"_prev_{smooth_key}"
        prev_val = getattr(self, prev_key, cur_val)
        setattr(self, prev_key, cur_val)
        jerk = min(5.0, abs(cur_val - prev_val))
        smooth = max(0, int(100 - jerk * 20))
        self._smooth_data.append(float(smooth))
        if len(self._smooth_data) > 50:
            self._smooth_data.pop(0)
        self._smooth_curve.setData(self._smooth_data)
        self._smooth_val.setText(str(smooth))

        # Trunk lean
        self._trunk_val.setText(f"{trunk:.0f}°")
        if trunk < 10.0:
            self._trunk_val.setStyleSheet(
                f"color:{GREEN5};font-size:20px;font-weight:bold;"
                f"font-family:'Courier New',monospace;"
            )
            self._trunk_status.setText("UPRIGHT")
            self._trunk_status.setStyleSheet(label_style(GREEN5, 11))
        elif trunk < 15.0:
            self._trunk_val.setStyleSheet(
                f"color:{AMBER};font-size:20px;font-weight:bold;"
                f"font-family:'Courier New',monospace;"
            )
            self._trunk_status.setText("LEANING")
            self._trunk_status.setStyleSheet(label_style(AMBER, 11))
        else:
            self._trunk_val.setStyleSheet(
                f"color:{RED};font-size:20px;font-weight:bold;"
                f"font-family:'Courier New',monospace;"
            )
            self._trunk_status.setText("STAND STRAIGHT")
            self._trunk_status.setStyleSheet(label_style(RED, 11, bold=True))

        # Haptic log
        for i, lbl in enumerate(self._log_labels):
            if i < len(hlog):
                ts, reason = hlog[-(i+1)]
                age = int(now - ts)
                lbl.setText(f"  {reason}  ({age}s ago)")
                lbl.setStyleSheet(label_style(GREEN2 if i == 0 else GREEN3, 12))
            else:
                lbl.setText("  —")
                lbl.setStyleSheet(label_style(GREEN3, 12))

    def refresh_render_only(self):
        """Called by main_window even when session panel isn't visible."""
        self._render.refresh()

    # ── Internal callbacks ────────────────────────────────────────────────────

    def _on_end(self):
        self._running = False
        self._render.clear_exercise_guide()
        dlg = EndSessionDialog(self)
        pain_post = dlg.pain if dlg.exec_() == QDialog.Accepted else 0
        self.session_ended.emit(pain_post)

    def _toggle_record(self):
        self._recording = not self._recording
        if self._recording:
            self._render.start_geometry_recording()
            self._rec_btn.setText("☐  STOP REC")
            self._rec_btn.setStyleSheet(btn_style(RED, "#fff", RED))
        else:
            self._render.stop_geometry_recording()
            self._rec_btn.setText("○ RECORD")
            self._rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
        self.record_toggled.emit(self._recording)


def _also(self, fn):
    fn(self); return self
QLabel.also = _also