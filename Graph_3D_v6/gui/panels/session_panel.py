"""
gui/panels/session_panel.py
---------------------------
Panel 2 — Active session (full-screen during exercise).

Changes vs v5
─────────────
- EndSessionDialog uses a matching colour-coded PainSelector (not QSpinBox)
- Smoothness widget is now a full scrolling line graph (80px, axes shown)
- Set completion: when reps_target reached, rest overlay appears (5s timer)
  then a Resume button. Auto-increment set counter.
- Guide phantom persists all reps; managed in sync with set state here.
- User button: REC starts recording, REPLAY replays (one-time, reusable).
- Guide button: records therapist movement → assets/guides/<exercise>.pkl
  Hidden behind SHOW_GUIDE_RECORD_BTN flag.
- Guide phantom auto-loads on session start; stops after 2 patient reps.
"""

import time
import numpy as np
from pathlib import Path
from scipy.spatial.transform import Rotation

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QDialog, QDialogButtonBox,
    QSizePolicy, QStackedWidget
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap
import pyqtgraph as pg

from ble.ble_state import AppState
from calc.exercise_library import get_exercise, ExerciseDef
from gui.styles import *
from gui.widgets.render_widget import RenderWidget

from ble.ble_manager import CMD_HAPTIC_SET, CMD_HAPTIC_REST_END

DOWN_NP = np.array([0., -1., 0.])
GOAL_ROM_FRACTION = 0.90

REST_COUNTDOWN_S = 5        # seconds of auto-countdown before Resume appears


def _goal_pos(flex_deg, abd_deg, upper=0.30, fore=0.25):
    fr = Rotation.from_euler("Z", -flex_deg, degrees=True)
    ar = Rotation.from_euler("X",  abd_deg,  degrees=True)
    return (ar * fr).apply(DOWN_NP) * (upper + fore)


# ── Pain selector (shared between start and end dialogs) ──────────────────────

class PainSelector(QWidget):
    def __init__(self, on_change=None, parent=None):
        super().__init__(parent)
        self._val = 0
        self._on_change = on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        lbl = QLabel("PAIN:")
        lbl.setStyleSheet(label_style(TEXT3, 12))
        lay.addWidget(lbl)
        self._btns = []
        for i in range(11):
            b = QPushButton(str(i))
            b.setFixedSize(28, 28)
            col = GREEN5 if i <= 3 else (AMBER if i <= 6 else RED)
            b.setStyleSheet(
                f"QPushButton{{background:{SURFACE3};color:{col};border:1px solid {BORDER};"
                f"border-radius:3px;font-size:13px;font-family:'Courier New',monospace;"
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
    def value(self):
        return self._val

    def set_value(self, v):
        self._select(max(0, min(10, v)))


# ── End-session dialog with matching pain selector ────────────────────────────

class EndSessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("POST-SESSION")
        self.setFixedWidth(420)
        self.setStyleSheet(
            f"background:{BG};color:{TEXT};font-family:'Courier New',monospace;"
        )
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.addWidget(
            QLabel("HOW IS YOUR PAIN NOW?").also(
                lambda l: l.setStyleSheet(label_style(GREEN3, 11))
            )
        )
        self._pain = PainSelector()
        lay.addWidget(self._pain)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @property
    def pain(self):
        return self._pain.value


# ── Angle box ─────────────────────────────────────────────────────────────────

class AngleBox(QFrame):
    def __init__(self, label, colour, parent=None):
        super().__init__(parent)
        self.setStyleSheet(card_style(SURFACE3, BORDER))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(2)
        lbl = QLabel(label)
        lbl.setStyleSheet(label_style(colour, 11))
        self._val = QLabel("0°")
        self._val.setStyleSheet(
            f"color:{colour};font-size:22px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        self._max = QLabel("max: 0°")
        self._max.setStyleSheet(label_style(GREEN3, 12))
        lay.addWidget(lbl)
        lay.addWidget(self._val)
        lay.addWidget(self._max)

    def update(self, val, max_val):
        self._val.setText(f"{val:.0f}°")
        self._max.setText(f"max: {max_val:.0f}°")


# ── Rest overlay (shown between sets) ────────────────────────────────────────

class RestOverlay(QWidget):
    """
    Shown over the HUD when a set is complete.
    Counts down REST_COUNTDOWN_S seconds then shows a Resume button.
    """
    resume_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background:rgba(248,248,248,230);border:none;"
        )
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(12)

        self._set_lbl = QLabel("SET COMPLETE")
        self._set_lbl.setStyleSheet(label_style(GREEN5, 16, bold=True))
        self._set_lbl.setAlignment(Qt.AlignCenter)

        self._countdown_lbl = QLabel("")
        self._countdown_lbl.setStyleSheet(
            f"color:{GREEN};font-size:42px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        self._countdown_lbl.setAlignment(Qt.AlignCenter)

        self._resume_btn = QPushButton("▶  RESUME NEXT SET")
        self._resume_btn.setFixedHeight(38)
        self._resume_btn.setStyleSheet(
            f"QPushButton{{background:{GREEN4};color:{GREEN};border:1px solid {GREEN3};"
            f"border-radius:3px;font-size:13px;font-weight:bold;"
            f"font-family:'Courier New',monospace;padding:5px 20px;}}"
            f"QPushButton:hover{{background:{GREEN3};color:{BG};}}"
        )
        self._resume_btn.setVisible(False)
        self._resume_btn.clicked.connect(self.resume_clicked)

        lay.addWidget(self._set_lbl)
        lay.addWidget(self._countdown_lbl)
        lay.addWidget(self._resume_btn)

        self._t_start = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)

    def start(self, completed_set: int, total_sets: int):
        self._set_lbl.setText(f"SET {completed_set} / {total_sets} COMPLETE")
        self._resume_btn.setVisible(False)
        self._countdown_lbl.setText(str(REST_COUNTDOWN_S))
        self._t_start = time.monotonic()
        self._timer.start()
        self.setVisible(True)

    def _tick(self):
        elapsed = time.monotonic() - self._t_start
        remaining = REST_COUNTDOWN_S - elapsed
        if remaining > 0:
            self._countdown_lbl.setText(f"{remaining:.0f}")
        else:
            self._timer.stop()
            self._countdown_lbl.setText("REST")
            self._resume_btn.setVisible(True)

    def hide_overlay(self):
        self._timer.stop()
        self.setVisible(False)


# ── Main panel ────────────────────────────────────────────────────────────────

class SessionPanel(QWidget):
    session_ended  = pyqtSignal(int)
    record_toggled = pyqtSignal(bool)
    goal_achieved  = pyqtSignal()

    def __init__(self, state, ble, calibration, recorder, rep_detector, parent=None):
        super().__init__(parent)
        self._state       = state
        self._ble         = ble
        self._cal         = calibration
        self._rec         = recorder
        self._repdet      = rep_detector
        self._t_start     = None
        self._running     = False
        self._sets_total  = 3
        self._reps_total  = 10
        self._current_set = 1
        self._current_ex: ExerciseDef | None = None
        self._recording        = False
        self._guide_recording  = False
        self._resting          = False
        self._current_ex_name  = ""
        self._build()

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── 3D render ─────────────────────────────────────────────────────────
        self._render = RenderWidget(self._state)
        self._render.goal_achieved.connect(self.goal_achieved)
        self._render.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self._render, stretch=1)

        # ── HUD column ────────────────────────────────────────────────────────
        hud_container = QWidget()
        hud_container.setFixedWidth(260)

        # Stack: normal HUD + rest overlay (both same size)
        self._hud_stack = QStackedWidget(hud_container)
        hud_outer = QVBoxLayout(hud_container)
        hud_outer.setContentsMargins(0, 0, 0, 0)
        hud_outer.setSpacing(0)
        hud_outer.addWidget(self._hud_stack)

        # --- Normal HUD page ---
        hud = QWidget()
        hud.setStyleSheet(f"background:{SURFACE};border-left:1px solid {BORDER};")
        hl = QVBoxLayout(hud)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        # Top bar
        top = QWidget(); top.setFixedHeight(40)
        top.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(top); tl.setContentsMargins(10, 0, 10, 0); tl.setSpacing(8)
        self._ex_lbl = QLabel("—")
        self._ex_lbl.setStyleSheet(label_style(GREEN3, 14))
        self._end_btn = QPushButton("END")
        self._end_btn.setFixedSize(48, 26)
        self._end_btn.setStyleSheet(
            btn_style(RED, "#fff", RED, "#aa1111") + "QPushButton { padding: 0px; }"
        )
        self._end_btn.clicked.connect(self._on_end)
        tl.addWidget(self._ex_lbl, stretch=1)
        tl.addWidget(self._end_btn)
        hl.addWidget(top)

        def _section(label):
            w = QWidget(); w.setFixedHeight(28)
            w.setStyleSheet(f"background:{SURFACE2};border-bottom:1px solid {BORDER};")
            l = QHBoxLayout(w); l.setContentsMargins(10, 0, 10, 0)
            l.addWidget(QLabel(f"── {label}").also(
                lambda lb: lb.setStyleSheet(label_style(GREEN3, 12))
            ))
            hl.addWidget(w)

        # Timer / set / rep block
        _section("TIMER")
        tblock = QWidget()
        tbl = QVBoxLayout(tblock); tbl.setContentsMargins(12, 8, 12, 4); tbl.setSpacing(2)
        self._timer_lbl = QLabel("00:00")
        self._timer_lbl.setStyleSheet(
            f"color:{GREEN};font-size:32px;font-weight:bold;"
            f"font-family:'Courier New',monospace;letter-spacing:2px;"
        )
        self._set_lbl = QLabel("SET  1 / 3")
        self._set_lbl.setStyleSheet(label_style(GREEN3, 12))
        self._reps_lbl = QLabel("REPS  0 / 10")
        self._reps_lbl.setStyleSheet(
            f"color:{GREEN};font-size:20px;font-weight:bold;"
            f"font-family:'Courier New',monospace;"
        )
        self._hold_bar = pg.PlotWidget(background=SURFACE3)
        self._hold_bar.setFixedHeight(12)
        self._hold_bar.hideAxis('left'); self._hold_bar.hideAxis('bottom')
        self._hold_bar.setMouseEnabled(x=False, y=False); self._hold_bar.hideButtons()
        self._hold_curve = self._hold_bar.plot(pen=pg.mkPen(CYAN, width=2))
        self._hold_lbl = QLabel("HOLD — 0.0 s")
        self._hold_lbl.setStyleSheet(label_style(CYAN, 12))

        tbl.addWidget(self._timer_lbl)
        tbl.addWidget(self._set_lbl)
        tbl.addWidget(self._reps_lbl)
        tbl.addWidget(self._hold_bar)
        tbl.addWidget(self._hold_lbl)
        hl.addWidget(tblock)

        # Live angle boxes
        _section("LIVE ANGLES")
        ag = QWidget()
        agl = QHBoxLayout(ag); agl.setContentsMargins(8, 6, 8, 6); agl.setSpacing(6)
        self._a_flex  = AngleBox("FLEX",    C_FLEX)
        self._a_abd   = AngleBox("ABD",     C_ABD)
        agl.addWidget(self._a_flex); agl.addWidget(self._a_abd)
        hl.addWidget(ag)
        ag2 = QWidget()
        ag2l = QHBoxLayout(ag2); ag2l.setContentsMargins(8, 0, 8, 6); ag2l.setSpacing(6)
        self._a_rot   = AngleBox("EXT ROT", C_ROT)
        self._a_elbow = AngleBox("ELBOW",   C_ELBOW)
        ag2l.addWidget(self._a_rot); ag2l.addWidget(self._a_elbow)
        hl.addWidget(ag2)

        # Smoothness — scrolling line graph
        _section("SMOOTHNESS")
        sm = QWidget()
        sml = QVBoxLayout(sm); sml.setContentsMargins(8, 6, 8, 4); sml.setSpacing(2)
        self._smooth_plot = pg.PlotWidget(background=SURFACE2)
        self._smooth_plot.setFixedHeight(80)
        self._smooth_plot.setMouseEnabled(x=False, y=False)
        self._smooth_plot.hideButtons()
        self._smooth_plot.showGrid(x=False, y=True, alpha=0.3)
        self._smooth_plot.setYRange(0, 100)
        self._smooth_plot.hideAxis('bottom')
        ax = self._smooth_plot.getAxis('left')
        ax.setTicks([[(0,'0'),(50,'50'),(100,'100')]])
        ax.setTextPen(pg.mkPen(GREEN3))
        ax.setPen(pg.mkPen(BORDER))
        self._smooth_curve = self._smooth_plot.plot(
            pen=pg.mkPen(C_FLEX, width=2)
        )
        self._smooth_data = [0.0] * 100
        sm_row = QHBoxLayout(); sm_row.setSpacing(4)
        sm_row.addWidget(self._smooth_plot, stretch=1)
        self._smooth_val = QLabel("—")
        self._smooth_val.setFixedWidth(28)
        self._smooth_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._smooth_val.setStyleSheet(label_style(C_FLEX, 10, bold=True))
        sm_row.addWidget(self._smooth_val)
        sml.addLayout(sm_row)
        hl.addWidget(sm)

        # Trunk lean
        _section("TRUNK LEAN")
        tl_widget = QWidget()
        tl_lay = QHBoxLayout(tl_widget)
        tl_lay.setContentsMargins(10, 6, 10, 6); tl_lay.setSpacing(8)
        self._trunk_val = QLabel("0°")
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
            lbl.setContentsMargins(10, 1, 10, 1)
            hl.addWidget(lbl)
            self._log_labels.append(lbl)

        # Exercise image
        _section("EXERCISE")
        self._ex_img = QLabel()
        self._ex_img.setFixedHeight(100)
        self._ex_img.setAlignment(Qt.AlignCenter)
        self._ex_img.setStyleSheet(
            f"background:transparent;border:none;color:{GREEN3};font-size:11px;padding:4px;"
        )
        hl.addWidget(self._ex_img)

        hl.addStretch()

        # ── User record / replay buttons ──────────────────────────────────────
        SHOW_GUIDE_RECORD_BTN = True   # flip to False once guides are recorded

        rb_wrap = QWidget()
        rbl = QHBoxLayout(rb_wrap); rbl.setContentsMargins(8, 4, 8, 2); rbl.setSpacing(6)

        self._user_rec_btn = QPushButton("⏺  REC")
        self._user_rec_btn.setFixedHeight(28)
        self._user_rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
        self._user_rec_btn.setToolTip("Record your movement")
        self._user_rec_btn.clicked.connect(self._on_user_rec)

        self._user_play_btn = QPushButton("▶  REPLAY")
        self._user_play_btn.setFixedHeight(28)
        self._user_play_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
        self._user_play_btn.setToolTip("Replay your last recording")
        self._user_play_btn.setEnabled(False)
        self._user_play_btn.clicked.connect(self._on_user_play)

        rbl.addWidget(self._user_rec_btn, stretch=1)
        rbl.addWidget(self._user_play_btn, stretch=1)
        hl.addWidget(rb_wrap)

        # ── Guide record button (therapist only) ──────────────────────────────
        if SHOW_GUIDE_RECORD_BTN:
            gb_wrap = QWidget()
            gbl = QHBoxLayout(gb_wrap); gbl.setContentsMargins(8, 2, 8, 6)
            self._guide_rec_btn = QPushButton("⏺  RECORD GUIDE")
            self._guide_rec_btn.setFixedHeight(28)
            self._guide_rec_btn.setStyleSheet(btn_style(SURFACE2, AMBER, BORDER))
            self._guide_rec_btn.setToolTip("Record movement as guide for this exercise")
            self._guide_rec_btn.clicked.connect(self._on_guide_rec)
            gbl.addWidget(self._guide_rec_btn)
            hl.addWidget(gb_wrap)
        else:
            self._guide_rec_btn = None

        self._hud_stack.addWidget(hud)

        # --- Rest overlay page ---
        self._rest_overlay = RestOverlay()
        self._rest_overlay.resume_clicked.connect(self._on_resume)
        self._hud_stack.addWidget(self._rest_overlay)

        root.addWidget(hud_container)

    # ── Session lifecycle ─────────────────────────────────────────────────────

    def begin_session(self, exercise: str, sets: int, reps: int):
        self._t_start      = time.monotonic()
        self._running      = True
        self._resting      = False
        self._sets_total   = sets
        self._reps_total   = reps
        self._current_set  = 1
        self._current_ex   = get_exercise(exercise)
        self._current_ex_name = exercise
        self._ex_lbl.setText(exercise)
        self._load_ex_image(self._current_ex)
        self._update_mode_widgets()
        self.update_goal_sphere()
        self._render.set_exercise_guide(self._current_ex)
        self._hud_stack.setCurrentIndex(0)   # show normal HUD

    def _load_ex_image(self, ex):
        if ex is None:
            self._ex_img.setPixmap(QPixmap()); self._ex_img.setText(""); return
        img_path = ex.image_path
        if img_path:
            pix = QPixmap(str(img_path))
            if not pix.isNull():
                self._ex_img.setPixmap(
                    pix.scaledToHeight(self._ex_img.height(), Qt.SmoothTransformation)
                )
                self._ex_img.setText(""); return
        self._ex_img.setPixmap(QPixmap())
        self._ex_img.setText(
            ex.description[:80] + "…" if ex and len(ex.description) > 80
            else (ex.description if ex else "")
        )

    def _update_mode_widgets(self):
        is_hold = self._current_ex is not None and self._current_ex.is_hold_exercise
        self._reps_lbl.setVisible(not is_hold)
        self._hold_bar.setVisible(is_hold)
        self._hold_lbl.setVisible(is_hold)
        if is_hold:
            self._hold_curve.setData([0.0])
            self._hold_lbl.setText("HOLD — 0.0 s")

    def update_goal_sphere(self):
        ex = self._current_ex
        if ex is None or not ex.has_goal:
            self._render.clear_goal(); return
        with self._state.lock:
            rom_flex = self._state.rom_flex_limit
            rom_abd  = self._state.rom_abd_limit
        flex_target = min(ex.goal_flex_deg, rom_flex) * GOAL_ROM_FRACTION
        abd_target  = min(ex.goal_abd_deg,  rom_abd)  * GOAL_ROM_FRACTION
        self._render.set_goal(_goal_pos(flex_target, abd_target))

    def stop_recording(self):
        """Stop any active recording (user or guide). Called by main_window on session end."""
        if self._recording:
            self._recording = False
            self._render.stop_geometry_recording()
            self._user_rec_btn.setText("⏺  REC")
            self._user_rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
            self._user_play_btn.setEnabled(bool(self._render._playback_frames))
        if self._guide_recording:
            self._guide_recording = False
            self._render.stop_geometry_recording()
            self._render.record_guide(self._current_ex_name)
            if self._guide_rec_btn:
                self._guide_rec_btn.setText("⏺  RECORD GUIDE")
                self._guide_rec_btn.setStyleSheet(btn_style(SURFACE2, AMBER, BORDER))

    # ── Set completion ────────────────────────────────────────────────────────

    def notify_set_complete(self):
        """Called by main_window when reps target for this set is reached."""
        if self._resting:
            return
        self._resting = True
        self._render.clear_exercise_guide()
        if self._current_set >= self._sets_total:
            # All sets done — end session automatically
            self._on_end()
            return
        self._ble.send_haptic_all(CMD_HAPTIC_SET)
        self._rest_overlay.start(self._current_set, self._sets_total)
        self._hud_stack.setCurrentIndex(1)

    def _on_resume(self):
        self._current_set += 1
        self._resting = False
        self._ble.send_haptic_all(CMD_HAPTIC_REST_END)
        self._hud_stack.setCurrentIndex(0)
        # Re-show the guide for the next set
        if self._current_ex is not None:
            self._render.set_exercise_guide(self._current_ex)
        self._set_lbl.setText(f"SET  {self._current_set} / {self._sets_total}")

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

        elapsed = int(now - self._t_start)
        m, s = divmod(elapsed, 60)
        self._timer_lbl.setText(f"{m:02d}:{s:02d}")
        self._set_lbl.setText(f"SET  {self._current_set} / {self._sets_total}")

        ex = self._current_ex
        if ex is not None and ex.is_hold_exercise:
            prog = self._repdet.hold_progress
            hold_data = [prog * 100]
            self._hold_curve.setData(hold_data)
            self._hold_lbl.setText(
                f"HOLD — {self._repdet.hold_elapsed:.1f} s"
                f" / {ex.hold_duration_s:.0f} s"
            )
        else:
            # Show reps in current set only (reps resets at set boundary in main_window)
            reps_in_set = reps % self._reps_total if self._reps_total else reps
            self._reps_lbl.setText(f"REPS  {reps_in_set} / {self._reps_total}")

        self._a_flex.update(flex, mf)
        self._a_abd.update(abd, ma)
        self._a_rot.update(rot, mr)
        self._a_elbow.update(elbow, me)

        # Smoothness scrolling graph
        smooth_key = (ex.smooth_angle if ex else "flexion")
        smooth_angle_map = {"flexion": flex, "abduction": abd,
                            "ext_rot": rot, "elbow": elbow}
        cur_val = smooth_angle_map.get(smooth_key, flex)
        prev_key = f"_prev_{smooth_key}"
        prev_val = getattr(self, prev_key, cur_val)
        setattr(self, prev_key, cur_val)
        jerk = min(5.0, abs(cur_val - prev_val))
        smooth = max(0, int(100 - jerk * 20))
        self._smooth_data.append(float(smooth))
        if len(self._smooth_data) > 100:
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
        self._render.refresh()

    # ── Internal callbacks ────────────────────────────────────────────────────

    def _on_end(self):
        self._running = False
        self._resting = False
        self._rest_overlay.hide_overlay()
        self._render.clear_exercise_guide()
        dlg = EndSessionDialog(self)
        pain_post = dlg.pain if dlg.exec_() == QDialog.Accepted else 0
        self.session_ended.emit(pain_post)

    # ── User record / replay ─────────────────────────────────────────────────

    def _on_user_rec(self):
        if not self._recording:
            # Start recording
            self._recording = True
            if self._guide_recording:          # stop guide rec if running
                self._guide_recording = False
                self._render.stop_geometry_recording()
                self._render.record_guide(self._current_ex_name)
                if self._guide_rec_btn:
                    self._guide_rec_btn.setText("⏺  RECORD GUIDE")
                    self._guide_rec_btn.setStyleSheet(btn_style(SURFACE2, AMBER, BORDER))
            self._render.start_geometry_recording()
            self._user_rec_btn.setText("■  STOP REC")
            self._user_rec_btn.setStyleSheet(btn_style(RED, "#fff", RED))
            self._user_play_btn.setEnabled(False)
            self.record_toggled.emit(True)
        else:
            # Stop recording
            self._recording = False
            self._render.stop_geometry_recording()
            self._user_rec_btn.setText("⏺  REC")
            self._user_rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
            self._user_play_btn.setEnabled(bool(self._render._playback_frames))
            self.record_toggled.emit(False)

    def _on_user_play(self):
        self._render._toggle_playback()

    # ── Guide record ──────────────────────────────────────────────────────────

    def _on_guide_rec(self):
        if not self._guide_recording:
            # Start guide recording
            self._guide_recording = True
            if self._recording:                # stop user rec if running
                self._recording = False
                self._render.stop_geometry_recording()
                self._user_rec_btn.setText("⏺  REC")
                self._user_rec_btn.setStyleSheet(btn_style(SURFACE2, GREEN3, BORDER))
                self._user_play_btn.setEnabled(bool(self._render._playback_frames))
            self._render.start_geometry_recording()
            self._guide_rec_btn.setText("■  STOP GUIDE REC")
            self._guide_rec_btn.setStyleSheet(btn_style(RED, "#fff", RED))
        else:
            # Stop + save guide
            self._guide_recording = False
            self._render.stop_geometry_recording()
            self._render.record_guide(self._current_ex_name)
            self._guide_rec_btn.setText("⏺  RECORD GUIDE")
            self._guide_rec_btn.setStyleSheet(btn_style(SURFACE2, AMBER, BORDER))


def _also(self, fn):
    fn(self); return self
QLabel.also = _also