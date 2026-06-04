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

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
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
        self._last_haptic_t  = 0.0
        self._recording      = False
        self._active_ex: ExerciseDef | None = None   # set at session start

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

        ver = QLabel("v5.0")
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
        self._session_panel.record_toggled.connect(self._on_record_toggled)
        self._session_panel.goal_achieved.connect(self._on_goal_achieved)
        self._connect_panel.calibrate_requested.connect(self._on_calibrate)
        self._connect_panel.rom_completed.connect(self._on_rom_completed)

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
                if self._repdet.update(flex, abd, rot, elbow, now):
                    with self._state.lock:
                        self._state.session_reps = self._repdet.count
                    self._trigger_haptic(now, "Rep complete")

            # ── Hold detection ────────────────────────────────────────────────
            else:
                # Use the primary tracked angle to determine hold state
                angle_map = {"flexion": flex, "abduction": abd,
                             "ext_rot": rot, "elbow": elbow}
                hold_val = abs(angle_map.get(ex.smooth_angle, flex))
                # Arm must be at ≥ 50% of the enter threshold to count as holding
                if hold_val >= ex.rep_enter_deg * 0.5:
                    if not self._repdet._hold_start:
                        self._repdet.start_hold()
                    if self._repdet.hold_complete:
                        with self._state.lock:
                            self._state.session_reps += 1
                        self._trigger_haptic(now, "Hold complete")
                        self._repdet.cancel_hold()
                else:
                    self._repdet.cancel_hold()

            # ── ROM boundary haptic ───────────────────────────────────────────
            if (abs(flex) >= HAPTIC_ROM_FRACTION * rom_flex or
                    abs(abd) >= HAPTIC_ROM_FRACTION * rom_abd):
                self._trigger_haptic(now, "ROM limit reached")

            # ── Form deviation haptic (from ExerciseDef, not string matching) ─
            if abs(flex) > 20 or abs(abd) > 20:   # only check when arm is raised
                if ex.expected_plane == "sagittal" and plane < 30:
                    self._trigger_haptic(now, "Deviation: move forward")
                elif ex.expected_plane == "frontal" and plane > 60:
                    self._trigger_haptic(now, "Deviation: move sideways")

            # ── Trunk lean haptic ─────────────────────────────────────────────
            if ex.check_trunk_lean and trunk_lean > TRUNK_LEAN_LIMIT:
                self._trigger_haptic(now, f"Trunk lean {trunk_lean:.0f}° — stand straight")

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

    def _trigger_haptic(self, now: float, reason: str):
        if now - self._last_haptic_t < 1.0:
            return
        self._last_haptic_t = now
        self._ble.send_haptic_all()
        with self._state.lock:
            self._state.haptic_log.append((now, reason))
            if len(self._state.haptic_log) > 30:
                self._state.haptic_log.pop(0)

    # ── Signal callbacks ──────────────────────────────────────────────────────

    def _on_calibrate(self):
        ok = self._cal.capture()
        if not ok:
            print("[MAIN] Calibration failed — check connections.")

    def _on_start_session(self, exercise: str, pain_pre: int, sets: int, reps: int):
        self._active_ex = get_exercise(exercise)
        self._repdet.reset()
        self._repdet.set_exercise(self._active_ex)
        self._recorder.start_session(exercise=exercise, pain_pre=pain_pre)
        with self._state.lock:
            self._state.session_active   = True
            self._state.session_reps     = 0
            self._state.current_exercise = exercise
        self._session_panel.begin_session(exercise, sets, reps)
        self._navigate(2)

    def _on_session_ended(self, pain_post: int):
        self._recorder.end_session(pain_post=pain_post)
        with self._state.lock:
            self._state.session_active = False
        self._active_ex = None
        if self._recording:
            self._recording = False
            self._session_panel.stop_recording()
        self._navigate(3)

    def _on_goal_achieved(self):
        self._trigger_haptic(time.monotonic(), "Goal reached!")
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
