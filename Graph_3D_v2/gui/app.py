"""
gui/app.py
----------
Top-level QMainWindow.

Layout (horizontal, fixed 1600 × 900):
  ┌─────────────┬──────────────────┬──────────────────┐
  │  IMU Panel  │   3-D Skeleton   │  Metrics Panel   │
  │   (300 px)  │    (660 px)      │    (640 px)      │
  └─────────────┴──────────────────┴──────────────────┘

A single QTimer fires at 50 Hz and drives:
  - AngleProcessor.update()   (quaternion → joint angles)
  - IMUPanel.refresh()        (connection status + raw plots)
  - RenderWidget.refresh()    (3-D skeleton)
  - MetricsPanel.refresh()    (angle plots + session stats)

The BLE loop runs on its own background thread (unchanged from
the original architecture) and only writes to AppState under the lock.
This file never touches BLE or maths directly.
"""

import time
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QFrame, QSizePolicy
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QFont, QColor, QPalette

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from gui.imu_panel import IMUPanel
from gui.render_widget import RenderWidget
from gui.metrics_panel import MetricsPanel


# ── Window geometry ───────────────────────────────────────────────────────────
WIN_W = 1600
WIN_H = 900
LEFT_W   = 300
CENTRE_W = 660
RIGHT_W  = WIN_W - LEFT_W - CENTRE_W   # 640

# ── AMOLED colour palette ─────────────────────────────────────────────────────
BG          = "#000000"
PANEL_BG    = "#0a0a0a"
BORDER      = "#232330"
TEXT        = "#f0f0f0"
DIM         = "#6e6e82"
C_GREEN     = "#00ffa0"
C_RED       = "#ff3c3c"
C_AMBER     = "#ffc800"
C_ACCENT    = "#00b4ff"
C_PURPLE    = "#b450ff"

GLOBAL_STYLE = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px;
}}
QFrame {{
    background-color: {PANEL_BG};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
QPushButton {{
    background-color: #191926;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    font-size: 12px;
}}
QPushButton:hover  {{ background-color: #2a2a3a; }}
QPushButton:pressed {{ background-color: #0e0e18; }}
QLabel {{ background-color: transparent; border: none; }}
"""


class App(QMainWindow):
    """
    Main application window.

    Owns the timer, passes events down to the three panels.
    Never accesses AppState directly — always delegates to panels.
    """

    def __init__(self, state: AppState, ble: BLEManager,
                 calibration: Calibration, angle_processor: AngleProcessor):
        super().__init__()
        self._state   = state
        self._ble     = ble
        self._cal     = calibration
        self._angles  = angle_processor

        self.setWindowTitle("Frozen Shoulder Rehab — IMU Monitor")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(GLOBAL_STYLE)

        self._build_ui()
        self._start_timer()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        root_layout.addWidget(self._make_title_bar())

        # Three panels side by side
        panels = QWidget()
        panels_layout = QHBoxLayout(panels)
        panels_layout.setContentsMargins(4, 4, 4, 4)
        panels_layout.setSpacing(4)

        self._imu_panel     = IMUPanel(self._state, self._ble)
        self._render_widget = RenderWidget(self._state)
        self._metrics_panel = MetricsPanel(self._state, self._cal)

        self._imu_panel.setFixedWidth(LEFT_W)
        self._render_widget.setFixedWidth(CENTRE_W)
        self._metrics_panel.setFixedWidth(RIGHT_W)

        panels_layout.addWidget(self._imu_panel)
        panels_layout.addWidget(self._render_widget)
        panels_layout.addWidget(self._metrics_panel)

        root_layout.addWidget(panels, stretch=1)

        # Bottom action bar
        root_layout.addWidget(self._make_bottom_bar())

    def _make_title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet(f"background-color: {PANEL_BG}; border-bottom: 1px solid {BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 12, 0)

        title = QLabel("FROZEN SHOULDER REHAB")
        title.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold; font-size: 13px;")
        sub   = QLabel("  —  3× XIAO nRF52840  |  BNO085  |  DRV2605L  |  BLE")
        sub.setStyleSheet(f"color: {DIM}; font-size: 11px;")

        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addStretch()
        return bar

    def _make_bottom_bar(self):
        bar = QWidget()
        bar.setFixedHeight(44)
        bar.setStyleSheet(f"background-color: {PANEL_BG}; border-top: 1px solid {BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        def styled_btn(label, colour):
            b = QPushButton(label)
            b.setStyleSheet(
                f"QPushButton {{ background-color: {colour}; color: #000; "
                f"font-weight: bold; border: none; border-radius: 4px; padding: 6px 18px; }}"
                f"QPushButton:hover {{ background-color: {colour}cc; }}"
            )
            return b

        btn_haptic   = styled_btn("Haptic All",  C_AMBER)
        btn_sync     = styled_btn("Sync All",    C_ACCENT)
        btn_calibrate = styled_btn("Calibrate",  C_GREEN)
        btn_quit     = styled_btn("Quit",        C_RED)

        btn_haptic.clicked.connect(lambda: self._ble.send_haptic_all())
        btn_sync.clicked.connect(lambda: self._ble.send_sync_all())
        btn_calibrate.clicked.connect(self._on_calibrate)
        btn_quit.clicked.connect(self.close)

        layout.addWidget(btn_haptic)
        layout.addWidget(btn_sync)
        layout.addWidget(btn_calibrate)
        layout.addStretch()
        layout.addWidget(btn_quit)
        return bar

    # ── Timer ─────────────────────────────────────────────────────────────────

    def _start_timer(self):
        self._timer = QTimer(self)
        self._timer.setInterval(20)          # 50 Hz
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        now = time.monotonic()
        self._angles.update(now)
        self._imu_panel.refresh()
        self._render_widget.refresh()
        self._metrics_panel.refresh()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_calibrate(self):
        success = self._cal.capture()
        if not success:
            print("[GUI] Calibration failed — are all sensors connected?")

    def closeEvent(self, event):
        self._timer.stop()
        self._ble.stop()
        event.accept()
