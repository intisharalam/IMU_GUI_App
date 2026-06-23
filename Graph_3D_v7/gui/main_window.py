"""
gui/main_window.py
------------------
Top-level QMainWindow shell. Owns ONLY:
  - The left sidebar navigation (5 nav items)
  - The QStackedWidget that holds all 5 panels
  - The 50 Hz QTimer that ticks every panel
  - The title bar (top strip)
  - Wiring signals between panels

Panels never talk to each other. They only read/write AppState.
The main window switches which panel is visible via the sidebar.

Layout:
  ┌─────────┬────────────────────────────────────────┐
  │         │                                        │
  │ Sidebar │   Active Panel (QStackedWidget)        │
  │  Nav    │   Panel 0: Connect                     │
  │  72 px  │   Panel 1: Exercise                    │
  │         │   Panel 2: Session  (full-screen)      │
  │         │   Panel 3: Analytics                   │
  │         │   Panel 4: Settings                    │
  └─────────┴────────────────────────────────────────┘
"""

import time
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont

from ble.ble_manager import (
    BLEManager,
    CMD_HAPTIC_REP, CMD_HAPTIC_HOLD, CMD_HAPTIC_SET,
    CMD_HAPTIC_REST_START, CMD_HAPTIC_REST_END,
    CMD_HAPTIC_ROM, CMD_HAPTIC_DEVIATION, CMD_HAPTIC_CALIBRATED,
    CMD_HAPTIC_SESSION,
)

from ble.ble_state import AppState
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from calc.rep_detector import RepDetector
from calc.session_recorder import SessionRecorder
from calc.exercise_library import get_exercise, ExerciseDef

from gui.styles import (
    GLOBAL_QSS, BG, SURFACE, SURFACE2, BORDER, BORDER2,
    GREEN, GREEN2, GREEN3, GREEN4, GREEN_DIM,
    TEXT, TEXT2, TEXT3, TEXT_BRIGHT, AMBER, RED, CYAN,
    SIDEBAR_W, btn_style, label_style
)
from gui.panels.connect_panel   import ConnectPanel
from gui.widgets.rom_wizard import load_last_rom
from gui.panels.exercise_panel  import ExercisePanel
from gui.panels.session_panel   import SessionPanel
from gui.panels.analytics_panel import AnalyticsPanel
from gui.panels.settings_panel  import SettingsPanel

WIN_W, WIN_H = 800, 1000
HAPTIC_ROM_FRACTION = 0.90
TRUNK_LEAN_LIMIT    = 10.0   # degrees lateral trunk tilt before correction haptic

# Sidebar nav items: (icon_text, label, panel_index)
NAV_ITEMS = [
    ("", "CONNECT",   0),
    ("", "EXERCISE",  1),
    ("", "SESSION",   2),
    ("", "ANALYTICS", 3),
    ("", "SETTINGS",  4),
]


class SidebarButton(QPushButton):
    """Single nav item in the left sidebar."""

    def __init__(self, icon: str, label: str, panel_idx: int, parent=None):
        super().__init__(parent)
        self._panel_idx = panel_idx
        self._active = False
        self.setFixedSize(SIDEBAR_W, 70)
        self.setCheckable(False)
        self._icon  = icon
        self._label = label
        self._update_style()

    def set_active(self, active: bool):
        self._active = active
        self._update_style()

    def _update_style(self):
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {GREEN4};
                    border: none;
                    border-left: 3px solid {GREEN};
                    color: {GREEN};
                    font-family: 'Courier New', monospace;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 0;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-left: 3px solid transparent;
                    color: {GREEN3};
                    font-family: 'Courier New', monospace;
                    font-size: 10px;
                    padding: 0;
                }}
                QPushButton:hover {{
                    background: {GREEN4};
                    color: {TEXT2};
                    border-left: 3px solid {GREEN3};
                }}
            """)
        self.setText(f"{self._label}")


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, ble: BLEManager,
                 calibration: Calibration, angle_processor: AngleProcessor):
        super().__init__()
        self._state    = state
        self._ble      = ble
        self._cal      = calibration
        self._angles   = angle_processor
        self._recorder = SessionRecorder()
        self._repdet   = RepDetector()
        self._last_haptic_t       = 0.0
        self._last_deviation_t    = 0.0   # separate cooldown for deviation haptics
        self._last_trunk_t        = 0.0   # separate cooldown for trunk-lean haptics
        self._hold_drop_since     = None  # hysteresis timer for hold dropout
        self._recording      = False
        self._active_ex: ExerciseDef | None = None   # set at session start
        self._session_sets   = 3
        self._session_reps   = 10
        self._session_set_reps_done = 0   # reps completed in current set

        self.setWindowTitle("Frozen Shoulder Rehab System")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(GLOBAL_QSS)

        self._restore_rom()
        self._build_ui()
        self._navigate(0)
        self._restore_rom()
        self._start_timer()

    # ── UI Construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        root_lay.addWidget(self._make_title_bar())

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)

        body_lay.addWidget(self._make_sidebar())
        body_lay.addWidget(self._make_stack(), stretch=1)

        root_lay.addWidget(body, stretch=1)

    def _make_title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(0)   # hidden title bar
        bar.setStyleSheet(f"background:{SURFACE}; border-bottom:1px solid {BORDER};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(0)

        title = QLabel("Frozen Shoulder Rehab")
        title.setStyleSheet(
            f"color:{GREEN}; font-size:13px; font-weight:bold;"
            f" letter-spacing:3px; font-family:'Courier New',monospace;"
        )
        sub = QLabel("  |  3× XIAO nRF52840  |  BNO085  |  DRV2605L  |  BLE UART")
        sub.setStyleSheet(f"color:{GREEN3}; font-size:10px;")

        self._title_status = QLabel("[ DISCONNECTED ]")
        self._title_status.setStyleSheet(
            f"color:{RED}; font-size:10px; font-family:'Courier New',monospace;"
        )

        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addStretch()
        lay.addWidget(self._title_status)
        return bar

    def _make_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(SIDEBAR_W)
        sidebar.setStyleSheet(f"background:{SURFACE}; border-right:1px solid {BORDER};")
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._nav_btns = []
        for icon, label, idx in NAV_ITEMS:
            btn = SidebarButton(icon, label, idx)
            btn.clicked.connect(lambda checked, i=idx: self._navigate(i))
            lay.addWidget(btn)
            self._nav_btns.append(btn)

        lay.addStretch()

        ver = QLabel("v7.0")
        ver.setAlignment(Qt.AlignCenter)
        ver.setFixedHeight(28)
        ver.setStyleSheet(
            f"color:{GREEN3}; font-size:10px; border-top:1px solid {BORDER};"
        )
        lay.addWidget(ver)
        return sidebar

    def _make_stack(self):
        self._stack = QStackedWidget()

        self._connect_panel   = ConnectPanel(self._state, self._ble, self._cal)
        self._exercise_panel  = ExercisePanel(self._state, self._repdet)
        self._session_panel   = SessionPanel(
            self._state, self._ble, self._cal, self._recorder, self._repdet
        )
        self._analytics_panel = AnalyticsPanel(self._state, self._recorder)
        self._settings_panel  = SettingsPanel(self._state, self._ble, self._cal)

        self._stack.addWidget(self._connect_panel)
        self._stack.addWidget(self._exercise_panel)
        self._stack.addWidget(self._session_panel)
        self._stack.addWidget(self._analytics_panel)
        self._stack.addWidget(self._settings_panel)

        self._exercise_panel.start_session_requested.connect(self._on_start_session)
        self._session_panel.session_ended.connect(self._on_session_ended)
        self._session_panel.session_cancelled.connect(self._on_session_cancelled)
        self._session_panel.record_toggled.connect(self._on_record_toggled)
        self._session_panel.goal_achieved.connect(self._on_goal_achieved)
        self._connect_panel.calibrate_requested.connect(self._on_calibrate)
        self._connect_panel.rom_completed.connect(self._on_rom_completed)
        self._cal.set_complete_callback(self._on_calibration_complete)

        return self._stack

    # ── Navigation ────────────────────────────────────────────────────────────

    def _navigate(self, idx: int):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_btns):
            btn.set_active(i == idx)

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.setInterval(20)   # 50 Hz
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        now = time.monotonic()
        self._angles.update(now)

        with self._state.lock:
            calibrated = self._state.calibrated
            n_conn     = sum(1 for n in ["wrist","arm","chest"]
                            if self._state.slots[n].connected)
            active     = self._state.session_active
            flex       = self._state.shoulder_flexion
            abd        = self._state.shoulder_abduction
            rot        = self._state.external_rotation
            elbow      = self._state.elbow_flexion
            rom_flex   = self._state.rom_flex_limit
            rom_abd    = self._state.rom_abd_limit
            plane      = self._state.plane_of_elevation
            trunk_lean = self._state.trunk_lean_deg

        # Title bar status
        if n_conn == 3 and calibrated:
            self._title_status.setText("[ 3/3 CONNECTED  |  CALIBRATED ]")
            self._title_status.setStyleSheet(
                f"color:{GREEN}; font-size:11px; font-family:'Courier New',monospace;"
            )
        elif n_conn > 0:
            self._title_status.setText(f"[ {n_conn}/3 CONNECTED ]")
            self._title_status.setStyleSheet(
                f"color:{AMBER}; font-size:11px; font-family:'Courier New',monospace;"
            )
        else:
            self._title_status.setText("[ SCANNING... ]")
            self._title_status.setStyleSheet(
                f"color:{RED}; font-size:11px; font-family:'Courier New',monospace;"
            )

        # Per-session logic driven entirely by ExerciseDef — no string matching
        ex = self._active_ex
        if active and calibrated and ex is not None:

            # ── Rep detection ─────────────────────────────────────────────────
            if not ex.is_hold_exercise:
                if self._repdet.update(flex, abd, rot, elbow, now, trunk_lean):
                    self._session_set_reps_done += 1
                    self._recorder.increment_reps()
                    with self._state.lock:
                        self._state.session_reps = self._repdet.count
                        hap_rep = self._state.haptic_rep
                    # ── Set completion check ──────────────────────────────────
                    set_done = (self._session_reps > 0 and
                                self._session_set_reps_done >= self._session_reps and
                                not self._session_panel._resting)
                    if set_done:
                        self._session_set_reps_done = 0
                        self._trigger_haptic(now, "Set complete — rest", CMD_HAPTIC_REST_END)
                        self._session_panel.notify_set_complete()
                    elif hap_rep:
                        self._trigger_haptic(now, "Rep complete", CMD_HAPTIC_REP)

            # ── Hold detection ────────────────────────────────────────────────
            # Activity signal: use the exercise's smooth_angle so we respond to
            # the specific motion rather than noisy ext_rot or elbow drift.
            # Dropout hysteresis: 2 seconds below threshold before cancelling,
            # so a brief wobble doesn't reset the progress bar mid-hold.
            else:
                angle_map = {
                    "flexion":   abs(flex),
                    "abduction": abs(abd),
                    "ext_rot":   abs(rot),
                    "elbow":     abs(elbow),
                }
                hold_activity = angle_map.get(ex.smooth_angle, abs(abd))
                ARM_THRESHOLD = 8.0   # degrees — arm must be raised this much

                if hold_activity >= ARM_THRESHOLD:
                    self._hold_drop_since = None   # arm is up — clear dropout timer

                    if not self._repdet._hold_start:
                        self._repdet.start_hold()

                    if self._repdet.hold_complete:
                        # Hold finished — count it
                        self._session_set_reps_done += 1
                        self._recorder.increment_reps()
                        with self._state.lock:
                            self._state.session_reps += 1
                            hap_hold = self._state.haptic_hold
                        if hap_hold:
                            self._trigger_haptic(now, "Hold complete", CMD_HAPTIC_HOLD)
                        # completed=True sets wait-for-drop so next hold only begins
                        # after the arm lowers, not immediately on the next tick.
                        self._repdet.cancel_hold(completed=True)

                        # Set completion — same logic as rep exercises
                        set_done = (self._session_reps > 0 and
                                    self._session_set_reps_done >= self._session_reps and
                                    not self._session_panel._resting)
                        if set_done:
                            self._session_set_reps_done = 0
                            self._trigger_haptic(now, "Set complete — rest",
                                                 CMD_HAPTIC_REST_END)
                            self._session_panel.notify_set_complete()

                else:
                    # Arm has dropped — start dropout timer if not already running
                    self._repdet.notify_arm_dropped()   # clear wait-for-drop guard
                    if self._hold_drop_since is None:
                        self._hold_drop_since = now
                    elif now - self._hold_drop_since >= 2.0:
                        # Below threshold for 2 full seconds — cancel the hold
                        self._repdet.cancel_hold()
                        self._hold_drop_since = None

            # ── ROM boundary haptic ───────────────────────────────────────────
            with self._state.lock:
                rom_goal_frac  = self._state.rom_goal_fraction
                trunk_limit    = self._state.trunk_lean_limit
                hap_rom        = self._state.haptic_rom
                hap_dev        = self._state.haptic_deviation
                hap_trunk      = self._state.haptic_trunk
            if hap_rom and (abs(flex) >= rom_goal_frac * rom_flex or
                    abs(abd) >= rom_goal_frac * rom_abd):
                self._trigger_haptic(now, "ROM limit reached", CMD_HAPTIC_ROM)

            # ── Trunk lean haptic ─────────────────────────────────────────────
            # Checked BEFORE plane deviation and on its own cooldown, bypassing
            # the shared _trigger_haptic() 1s gate. Trunk lean and plane
            # deviation are frequently true on the same frame (leaning to
            # compensate also throws off the plane), and previously both
            # routed through one global cooldown — whichever fired first
            # (always deviation, since it was checked first) silently starved
            # the other for the rest of that 1s window. Trunk lean is the more
            # actionable correction, so it now gets priority and its own timer.
            trunk_fired = False
            if hap_trunk and ex.check_trunk_lean and trunk_lean > trunk_limit:
                if now - self._last_trunk_t > 3.0:
                    self._last_trunk_t = now
                    trunk_fired = True
                    self._repdet.notify_haptic(now)
                    self._ble.send_haptic_all(CMD_HAPTIC_DEVIATION)
                    with self._state.lock:
                        self._state.haptic_log.append(
                            (now, f"Trunk lean {trunk_lean:.0f}° — stand straight"))
                        if len(self._state.haptic_log) > 30:
                            self._state.haptic_log.pop(0)

            # ── Form deviation haptic (from ExerciseDef, not string matching) ─
            # Only fires when the arm is back near neutral (elevation < 25°) so
            # it does not interrupt a rep mid-way and trigger the haptic lockout.
            # plane_elev: 0° = pure abduction (frontal), 90° = pure flexion (sagittal).
            # Skipped this frame if trunk lean already fired — avoids stacking
            # two buzzes back-to-back for what is usually the same root cause.
            elevation_deg = max(abs(flex), abs(abd))
            if (not trunk_fired and hap_dev and elevation_deg < 25
                    and ex.expected_plane is not None):
                deviation = False
                reason    = ""
                if ex.expected_plane == "sagittal" and plane < 30:
                    deviation = True
                    reason    = "Deviation: raise forward"
                elif ex.expected_plane == "frontal" and plane > 60:
                    deviation = True
                    reason    = "Deviation: raise sideways"
                if deviation and now - self._last_deviation_t > 3.0:
                    self._last_deviation_t = now
                    self._trigger_haptic(now, reason, CMD_HAPTIC_DEVIATION)

        # CSV recording
        if self._recording and active:
            self._recorder.record_frame(now, flex, abd, rot, elbow)

        # Tick visible panel (session always ticks for live 3D)
        current = self._stack.currentIndex()
        panels = [
            self._connect_panel,
            self._exercise_panel,
            self._session_panel,
            self._analytics_panel,
            self._settings_panel,
        ]
        panels[current].refresh()
        if current != 2:
            self._session_panel.refresh_render_only()

    # ── Haptic ────────────────────────────────────────────────────────────────

    def _trigger_haptic(self, now: float, reason: str, payload: bytes = CMD_HAPTIC_REP):
        if now - self._last_haptic_t < 1.0:
            return
        self._last_haptic_t = now
        self._repdet.notify_haptic(now)
        self._ble.send_haptic_all(payload)
        with self._state.lock:
            self._state.haptic_log.append((now, reason))
            if len(self._state.haptic_log) > 30:
                self._state.haptic_log.pop(0)

    # ── Signal callbacks ──────────────────────────────────────────────────────

    def _on_calibrate(self):
        ok = self._cal.capture()
        if ok:
            # Haptic at START of calibration (button press feedback)
            self._ble.send_haptic_all(CMD_HAPTIC_CALIBRATED)
        else:
            print("[MAIN] Calibration failed — check connections.")

    def _on_calibration_complete(self):
        """Called from the Calibration background thread when capture finishes."""
        # CMD_HAPTIC_CALIBRATED is a Double Click — distinctive enough for both
        # start and end. Fire it again so the user knows capture is done.
        self._ble.send_haptic_all(CMD_HAPTIC_CALIBRATED)

    def _on_start_session(self, exercise: str, pain_pre: int, sets: int, reps: int):
        self._active_ex = get_exercise(exercise)
        self._session_sets  = sets
        self._session_set_reps_done = 0
        self._hold_drop_since       = None
        self._repdet.reset()
        self._repdet.set_exercise(self._active_ex)

        if self._active_ex is not None and self._active_ex.is_hold_exercise:
            # reps carries hold duration in seconds — store separately
            self._repdet.set_hold_duration(reps)
            self._session_reps = 1          # 1 completed hold = 1 set done
        else:
            self._session_reps = reps       # normal rep target per set

        self._recorder.start_session(exercise=exercise, pain_pre=pain_pre)
        self._recording = True
        with self._state.lock:
            self._state.session_active   = True
            self._state.session_reps     = 0
            self._state.current_exercise = exercise
        self._session_panel.begin_session(exercise, sets, reps)
        self._navigate(2)
        self._ble.send_haptic_all(CMD_HAPTIC_SESSION)

    def _on_session_ended(self, pain_post: int):
        # Stop frame capture first so record_frame has been called right up to
        # this point, then end_session sees fully populated max-angle accumulators.
        self._recording = False
        self._session_panel.stop_recording()
        self._recorder.end_session(pain_post=pain_post)
        with self._state.lock:
            self._state.session_active = False
        self._active_ex = None
        self._navigate(3)

    def _on_session_cancelled(self):
        """Pain dialog was dismissed — session ended prematurely, discard all data."""
        self._recording = False
        self._session_panel.stop_recording()
        # Discard CSV and don't write to SQLite
        self._recorder._discard()
        with self._state.lock:
            self._state.session_active = False
        self._active_ex = None
        self._navigate(1)   # back to exercise panel, not analytics

    def _on_goal_achieved(self):
        self._trigger_haptic(time.monotonic(), "Goal reached!", CMD_HAPTIC_SESSION)
        with self._state.lock:
            self._state.rom_flex_limit = min(
                self._state.rom_flex_limit * 1.05, 180.
            )
            self._state.rom_abd_limit = min(
                self._state.rom_abd_limit * 1.05, 180.
            )
        self._session_panel.update_goal_sphere()

    def _on_record_toggled(self, recording: bool):
        self._recording = recording

    def _restore_rom(self):
        """Load last measured ROM from SQLite so it persists across restarts."""
        data = load_last_rom()
        if data:
            with self._state.lock:
                self._state.rom_flex_limit    = data["flex"]
                self._state.rom_abd_limit     = data["abd"]
                self._state.rom_rot_limit     = data["rot"]
                self._state.rom_int_rot_limit = data.get("int_rot", 10.0)
                self._state.rom_elbow_limit   = data["elbow"]
                self._state.rom_measured      = True
            print(f"[ROM] Restored from DB: {data}")

    def _on_rom_completed(self):
        """Called after ROM wizard finishes — navigate to Exercise panel."""
        self._navigate(1)

    def closeEvent(self, event):
        self._timer.stop()
        if self._recorder.is_active:
            self._recorder.end_session()
        self._ble.stop()
        event.accept()