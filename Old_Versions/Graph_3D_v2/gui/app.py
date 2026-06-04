"""
gui/app.py  —  v4.1
--------------------
New layout (1600 × 950):

  ┌──────────┬────────────────────┬───────────────────────────────┐
  │  IMU     │                    │  Clinical Metrics plot        │
  │  Status  │   3D Skeleton      │  (top-right, ~55% of right)   │
  │  +       │   (left half)      ├───────────────────────────────┤
  │  Angles  │                    │  Session / Exercise / Timer   │
  │          │                    │  Progress / Recording         │
  │          │                    │  (bottom-right, ~45% of right)│
  └──────────┴────────────────────┴───────────────────────────────┘
  [Haptic All] [Sync All] [Calibrate]  ✓ Calibrated        [Quit]

Right column is a vertical splitter: metrics plot on top, session panel below.
IMU panel is narrow on the far left.
"""

import time, numpy as np
from scipy.spatial.transform import Rotation
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QSplitter, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from calc.rep_detector import RepDetector
from calc.session_recorder import SessionRecorder
from gui.imu_panel import IMUPanel
from gui.render_widget import RenderWidget
from gui.metrics_panel import MetricsPanel
from gui.session_panel import SessionPanel

WIN_W, WIN_H = 1600, 950
IMU_W        = 195    # left narrow column
# 3D render gets remaining width after IMU panel and right column
RIGHT_W      = 560    # right column (metrics + session)

GLOBAL_STYLE = """
QMainWindow, QWidget {
    background: #f0f0f4;
    color: #1a1a2a;
    font-family: 'Segoe UI', 'Arial', sans-serif;
    font-size: 12px;
}
QFrame {
    background: #f5f5f8;
    border: 1px solid #d0d0da;
    border-radius: 4px;
}
QPushButton {
    background: #e8e8f0;
    color: #1a1a2a;
    border: 1px solid #c0c0d0;
    border-radius: 4px;
    padding: 5px 12px;
    font-size: 11px;
}
QPushButton:hover   { background: #d8d8ec; }
QPushButton:pressed { background: #c8c8e0; }
QPushButton:disabled { background: #e0e0e0; color: #999; border-color: #ccc; }
QLabel  { background: transparent; border: none; }
QListWidget {
    background: #ffffff;
    border: 1px solid #d0d0da;
    border-radius: 3px;
    color: #1a1a2a;
}
QListWidget::item:selected { background: #cce4ff; color: #1a6aaa; }
QSpinBox {
    background: #fff;
    color: #1a1a2a;
    border: 1px solid #c0c0d0;
    padding: 2px;
}
QSplitter::handle { background: #d0d0da; }
QTableWidget {
    background: #fff;
    gridline-color: #e0e0ea;
    color: #1a1a2a;
}
QHeaderView::section {
    background: #e8e8f4;
    color: #1a1a2a;
    border: 1px solid #d0d0da;
    padding: 3px;
}
"""

HAPTIC_ROM_FRACTION = 0.90
DOWN_NP = np.array([0., -1., 0.])


def _compute_goal_wrist_pos(flex_deg, abd_deg, upper_len, fore_len):
    flex_rot = Rotation.from_euler("Z", -flex_deg, degrees=True)
    abd_rot  = Rotation.from_euler("X",  abd_deg,  degrees=True)
    direction = (abd_rot * flex_rot).apply(DOWN_NP)
    return direction * (upper_len + fore_len)


class App(QMainWindow):
    def __init__(self, state: AppState, ble: BLEManager,
                 calibration: Calibration, angle_processor: AngleProcessor):
        super().__init__()
        self._state    = state
        self._ble      = ble
        self._cal      = calibration
        self._angles   = angle_processor
        self._recorder = SessionRecorder()
        self._repdet   = RepDetector()
        self._last_haptic_t = 0.0
        self._recording = False

        self.setWindowTitle("Frozen Shoulder Rehab  —  v4")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(GLOBAL_STYLE)
        self._build_ui()
        self._start_timer()

    def _build_ui(self):
        root = QWidget(); self.setCentralWidget(root)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)

        rl.addWidget(self._make_title())

        # ── Main content row ──────────────────────────────────────────────────
        main = QWidget()
        ml = QHBoxLayout(main)
        ml.setContentsMargins(4, 4, 4, 0); ml.setSpacing(4)

        # Left: IMU status panel (narrow)
        self._imu_panel = IMUPanel(self._state, self._ble)
        self._imu_panel.setFixedWidth(IMU_W)

        # Centre: 3D render (takes all remaining horizontal space)
        self._render_widget = RenderWidget(self._state)
        self._render_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Right column: metrics (top) + session panel (bottom) in a splitter
        right_col = QSplitter(Qt.Vertical)
        right_col.setFixedWidth(RIGHT_W)

        self._metrics_panel = MetricsPanel(self._state, self._cal)
        self._session_panel = SessionPanel(
            self._state, self._recorder, self._repdet
        )
        right_col.addWidget(self._metrics_panel)
        right_col.addWidget(self._session_panel)
        right_col.setSizes([520, 380])   # metrics taller than session panel

        ml.addWidget(self._imu_panel)
        ml.addWidget(self._render_widget, stretch=1)
        ml.addWidget(right_col)

        rl.addWidget(main, stretch=1)
        rl.addWidget(self._make_bottom())

        # Wire signals
        self._render_widget.goal_achieved.connect(self._on_goal_achieved)
        self._session_panel.exercise_changed.connect(self._on_exercise_changed)
        self._session_panel.record_toggled.connect(self._on_record_toggled)
        self._session_panel.session_started.connect(self._on_session_start)
        self._session_panel.session_ended.connect(self._on_session_end)

    def _make_title(self):
        bar = QWidget(); bar.setFixedHeight(30)
        bar.setStyleSheet("background:#e4e6f0; border-bottom:1px solid #c8cad8;")
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 0, 12, 0)
        t = QLabel("FROZEN SHOULDER REHAB")
        t.setStyleSheet("color:#1a6aaa; font-weight:bold; font-size:12px;")
        s = QLabel("  —  3× XIAO nRF52840  |  BNO085  |  DRV2605L  |  BLE")
        s.setStyleSheet("color:#888; font-size:10px;")
        lay.addWidget(t); lay.addWidget(s); lay.addStretch()
        return bar

    def _make_bottom(self):
        bar = QWidget(); bar.setFixedHeight(44)
        bar.setStyleSheet("background:#e4e6f0; border-top:1px solid #c8cad8;")
        lay = QHBoxLayout(bar); lay.setContentsMargins(12, 4, 12, 4); lay.setSpacing(8)

        def _btn(label, bg, fg="#fff"):
            b = QPushButton(label)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};font-weight:bold;"
                f" border:none;border-radius:4px;padding:5px 16px;}}"
                f"QPushButton:hover{{filter:brightness(1.1);}}"
            )
            return b

        btn_haptic = _btn("Haptic All",  "#cc8800")
        btn_sync   = _btn("Sync All",    "#1a6aaa")
        btn_cal    = _btn("Calibrate",   "#007744")
        btn_quit   = _btn("Quit",        "#cc2222")

        btn_haptic.clicked.connect(self._ble.send_haptic_all)
        btn_sync.clicked.connect(self._ble.send_sync_all)
        btn_cal.clicked.connect(self._on_calibrate)
        btn_quit.clicked.connect(self.close)

        self._cal_status = QLabel("Not calibrated")
        self._cal_status.setStyleSheet("color:#cc8800; font-size:11px;")

        lay.addWidget(btn_haptic); lay.addWidget(btn_sync); lay.addWidget(btn_cal)
        lay.addWidget(self._cal_status); lay.addStretch(); lay.addWidget(btn_quit)
        return bar

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
            cap        = self._cal.is_capturing()
            flex  = self._state.shoulder_flexion
            abd   = self._state.shoulder_abduction
            rot   = self._state.external_rotation
            elbow = self._state.elbow_flexion
            active    = self._state.session_active
            rom_flex  = self._state.rom_flex_limit
            rom_abd   = self._state.rom_abd_limit

        # Bottom bar calibration label
        if cap:
            self._cal_status.setText("Capturing 3s — hold I-pose...")
            self._cal_status.setStyleSheet("color:#1a6aaa; font-size:11px;")
        elif calibrated:
            self._cal_status.setText("✓ Calibrated")
            self._cal_status.setStyleSheet("color:#007744; font-size:11px;")
        else:
            self._cal_status.setText("Not calibrated")
            self._cal_status.setStyleSheet("color:#cc8800; font-size:11px;")

        # Rep detection + haptic
        if active and calibrated:
            if self._repdet.update(flex, abd, rot, elbow, now):
                with self._state.lock:
                    self._state.session_reps = self._repdet.count
                self._trigger_haptic(now, "Rep complete")
            if (abs(flex) >= HAPTIC_ROM_FRACTION * rom_flex or
                    abs(abd) >= HAPTIC_ROM_FRACTION * rom_abd):
                self._trigger_haptic(now, "ROM limit reached")

        # CSV recording
        if self._recording and active:
            self._recorder.record_frame(now, flex, abd, rot, elbow)

        self._imu_panel.refresh()
        self._render_widget.refresh()
        self._metrics_panel.refresh()
        self._session_panel.refresh()

    def _trigger_haptic(self, now, reason):
        if now - self._last_haptic_t < 1.0: return
        self._last_haptic_t = now
        self._ble.send_haptic_all()
        with self._state.lock:
            self._state.haptic_log.append((now, reason))
            if len(self._state.haptic_log) > 20:
                self._state.haptic_log.pop(0)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_calibrate(self):
        ok = self._cal.capture()
        if not ok:
            print("[GUI] Calibration failed — sensors not all connected.")

    def _on_exercise_changed(self, name):
        with self._state.lock:
            self._state.current_exercise = name
        self._update_goal_sphere()

    def _on_session_start(self):
        self._update_goal_sphere()

    def _on_session_end(self):
        self._render_widget.clear_goal()

    def _update_goal_sphere(self):
        with self._state.lock:
            exercise  = self._state.current_exercise
            rom_flex  = self._state.rom_flex_limit  * 0.9
            rom_abd   = self._state.rom_abd_limit   * 0.9

        if "Flexion" in exercise or "Pendulum" in exercise:
            wrist_anat = _compute_goal_wrist_pos(rom_flex, 0, 0.30, 0.25)
        elif "Abduction" in exercise:
            wrist_anat = _compute_goal_wrist_pos(0, rom_abd, 0.30, 0.25)
        else:
            self._render_widget.clear_goal()
            return
        self._render_widget.set_goal(wrist_anat)

    def _on_goal_achieved(self):
        self._trigger_haptic(time.monotonic(), "Goal reached!")
        with self._state.lock:
            self._state.rom_flex_limit = min(self._state.rom_flex_limit * 1.05, 180.)
            self._state.rom_abd_limit  = min(self._state.rom_abd_limit  * 1.05, 180.)
        self._update_goal_sphere()

    def _on_record_toggled(self, recording):
        self._recording = recording
        if recording:
            self._render_widget.start_geometry_recording()
        else:
            self._render_widget.stop_geometry_recording()

    def closeEvent(self, event):
        self._timer.stop()
        if self._recorder.is_active:
            self._recorder.end_session()
        self._ble.stop()
        event.accept()