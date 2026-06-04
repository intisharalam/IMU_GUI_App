"""
gui/panels/connect_panel.py
---------------------------
Panel 0 — Device setup wizard.

  Step 1: Bluetooth ON
  Step 2: Connect IMUs (auto-scans via BLEManager)
  Step 3: I-Pose Calibration
  Step 4: Measure ROM
  Step 5: Ready

Displays live sensor cards (packet count, RSSI, sync offset, sparkline).
Emits calibrate_requested when Calibrate button is pressed.

Layout
------
  [title bar]
  [body row]
    left: wizard steps + calibration card
    right: IMU sensor cards
  [placement row]  ← sensor placement image, always visible
"""

import time
from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGridLayout, QSizePolicy, QProgressBar, QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QByteArray
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap
from PyQt5.QtSvg import QSvgWidget
import pyqtgraph as pg

from ble.ble_state import AppState, SLOT_NAMES
from gui.styles import *
from gui.widgets.rom_wizard import RomWizard, load_last_rom

# Resolve image path relative to this file so it works from any working dir
_ASSETS_DIR = Path(__file__).parent.parent.parent / "assets/connect_panel"
_PLACEMENT_IMG = _ASSETS_DIR / "Sensor_Placements_2.png"


def _svg_ipose() -> str:
    """Stick figure in I-pose — arms hanging straight down at sides."""
    return """
<svg viewBox="0 0 80 180" xmlns="http://www.w3.org/2000/svg">
  <style>
    .b { stroke: #00ff41; stroke-width: 2.5; fill: none; stroke-linecap: round; }
  </style>
  <!-- Head -->
  <circle cx="40" cy="18" r="12" class="b"/>
  <!-- Spine -->
  <line x1="40" y1="30" x2="40" y2="100" class="b"/>
  <!-- Left arm hanging -->
  <line x1="40" y1="45" x2="18" y2="80" class="b"/>
  <line x1="18" y1="80" x2="14" y2="108" class="b"/>
  <!-- Right arm hanging -->
  <line x1="40" y1="45" x2="62" y2="80" class="b"/>
  <line x1="62" y1="80" x2="66" y2="108" class="b"/>
  <!-- Legs V -->
  <line x1="40" y1="100" x2="22" y2="168" class="b"/>
  <line x1="40" y1="100" x2="58" y2="168" class="b"/>
</svg>
""".strip()


class SparklineWidget(pg.PlotWidget):
    """Tiny scrolling quaternion-noise sparkline per IMU."""
    def __init__(self, colour=GREEN, parent=None):
        super().__init__(parent, background=SURFACE2)
        self.setFixedHeight(32)
        self.hideAxis('left'); self.hideAxis('bottom')
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        self.setMenuEnabled(False)
        pen = pg.mkPen(colour, width=1)
        self._curve = self.plot(pen=pen)
        self._data = [0.0] * 60

    def push(self, value: float):
        self._data.append(value)
        if len(self._data) > 60:
            self._data.pop(0)
        self._curve.setData(self._data)


class IMUSensorCard(QFrame):
    """One card per IMU sensor."""

    def __init__(self, slot_name: str, ble_manager, parent=None):
        super().__init__(parent)
        self._name = slot_name
        self._ble  = ble_manager
        self.setStyleSheet(card_style(SURFACE2, BORDER))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        # Header row
        hrow = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color:{RED}; font-size:14px; border:none; background:transparent;")
        self._name_lbl = QLabel(slot_name.upper())
        self._name_lbl.setStyleSheet(label_style(GREEN3, 14, bold=True))
        self._status = QLabel("SCANNING")
        self._status.setStyleSheet(label_style(RED, 10))
        hrow.addWidget(self._dot)
        hrow.addWidget(self._name_lbl)
        hrow.addStretch()
        hrow.addWidget(self._status)
        lay.addLayout(hrow)

        # Address
        self._addr = QLabel("")
        self._addr.setStyleSheet(label_style(GREEN3, 9))
        lay.addWidget(self._addr)

        # Stats grid
        sg = QGridLayout(); sg.setSpacing(4)
        self._packets = self._stat_pair(sg, "PKT", 0, 0)
        self._sync    = self._stat_pair(sg, "SYNC", 0, 1)
        self._rssi    = self._stat_pair(sg, "RSSI", 1, 0)
        self._haptic  = self._stat_pair(sg, "HAPTIC", 1, 1)
        lay.addLayout(sg)

        # Sparkline
        col = {"wrist": CYAN, "arm": GREEN5, "chest": AMBER}.get(slot_name, GREEN)
        self._spark = SparklineWidget(colour=col)
        lay.addWidget(self._spark)

        # Buttons
        brow = QHBoxLayout(); brow.setSpacing(6)
        self._btn_haptic = QPushButton("HAPTIC")
        self._btn_sync   = QPushButton("SYNC")
        self._btn_haptic.setFixedHeight(24)
        self._btn_sync.setFixedHeight(24)
        self._btn_haptic.setStyleSheet(btn_style(SURFACE, AMBER, BORDER2, GREEN4))
        self._btn_sync.setStyleSheet(btn_style())
        self._btn_haptic.clicked.connect(lambda: self._ble.send_haptic(self._name))
        self._btn_sync.clicked.connect(lambda: self._ble.send_sync(self._name))
        brow.addWidget(self._btn_haptic)
        brow.addWidget(self._btn_sync)
        lay.addLayout(brow)

        self._last_packet = 0

    def _stat_pair(self, grid, label, row, col):
        box = QHBoxLayout(); box.setSpacing(3)
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet(label_style(GREEN3, 11))
        val = QLabel("—")
        val.setStyleSheet(label_style(TEXT2, 12))
        box.addWidget(lbl); box.addWidget(val); box.addStretch()
        grid.addLayout(box, row, col)
        return val

    def refresh(self, slot):
        if slot.connected:
            self._dot.setStyleSheet(f"color:{GREEN5}; font-size:14px; border:none; background:transparent;")
            self._status.setText("CONNECTED")
            self._status.setStyleSheet(label_style(GREEN5, 10))
            self._addr.setText(slot.address or "")
            self._packets.setText(str(slot.packet_count))
            sync = f"{slot.sync_offset_ms:+.1f}ms" if slot.sync_offset_ms else "—"
            self._sync.setText(sync)
            self._rssi.setText("—")
            haptic_txt = "ACTIVE" if slot.haptic_active else "READY"
            haptic_col = AMBER if slot.haptic_active else TEXT3
            self._haptic.setText(haptic_txt)
            self._haptic.setStyleSheet(label_style(haptic_col, 10))
            pkt = slot.packet_count
            self._spark.push(float(pkt - self._last_packet))
            self._last_packet = pkt
        else:
            self._dot.setStyleSheet(f"color:{RED}; font-size:14px; border:none; background:transparent;")
            self._status.setText("SCANNING")
            self._status.setStyleSheet(label_style(RED, 10))
            self._addr.setText("")
            for w in [self._packets, self._sync, self._rssi]:
                w.setText("—")
            self._spark.push(0.0)
            self._last_packet = 0


class WizardStep(QFrame):
    def __init__(self, number: int, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(card_style(SURFACE2, BORDER))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6); lay.setSpacing(10)

        self._num = QLabel(str(number))
        self._num.setFixedSize(26, 26)
        self._num.setAlignment(Qt.AlignCenter)
        self._num.setStyleSheet(
            f"background:{SURFACE3};color:{GREEN3};border:1px solid {BORDER2};"
            f"border-radius:13px;font-size:13px;font-weight:bold;font-family:'Courier New',monospace;"
        )
        lay.addWidget(self._num)

        txt = QVBoxLayout(); txt.setSpacing(1)
        self._title = QLabel(title)
        self._title.setStyleSheet(label_style(TEXT2, 13, bold=True))
        self._sub = QLabel(subtitle)
        self._sub.setStyleSheet(label_style(GREEN3, 11))
        txt.addWidget(self._title); txt.addWidget(self._sub)
        lay.addLayout(txt); lay.addStretch()

        self._badge = QLabel("")
        self._badge.setStyleSheet(label_style(GREEN3, 12))
        lay.addWidget(self._badge)

    def set_done(self):
        self._num.setStyleSheet(
            f"background:{GREEN5};color:{GREEN4};border:1px solid {GREEN};"
            f"border-radius:13px;font-size:13px;font-weight:bold;font-family:'Courier New',monospace;"
        )
        self._title.setStyleSheet(label_style(GREEN4, 11, bold=True))
        self._num.setText("✓")
        self.setStyleSheet(card_style("#001a00", GREEN3))

    def set_active(self):
        self.setStyleSheet(card_style(GREEN4, GREEN3))
        self._num.setStyleSheet(
            f"background:{GREEN3};color:{BG};border:1px solid {GREEN2};"
            f"border-radius:13px;font-size:13px;font-weight:bold;font-family:'Courier New',monospace;"
        )

    def set_badge(self, text: str, colour=GREEN3):
        self._badge.setText(text)
        self._badge.setStyleSheet(label_style(colour, 11))


class SensorPlacementWidget(QFrame):
    """
    Bottom-of-panel strip showing the sensor placement photo with
    per-sensor labels overlaid. Always visible.
    """
    # Fixed display height for the strip — tall enough to read, short enough
    # not to crowd the wizard content above.
    STRIP_HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.STRIP_HEIGHT)
        self.setStyleSheet(f"background:{SURFACE2}; border-top: 1px solid {BORDER};")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 8, 16, 8)
        outer.setSpacing(16)

        # ── Left: section label + three placement blurbs ─────────────────────
        info_col = QVBoxLayout()
        info_col.setSpacing(6)

        hdr = QLabel("SENSOR PLACEMENT")
        hdr.setStyleSheet(label_style(GREEN5, 12, bold=True))
        info_col.addWidget(hdr)

        placements = [
            ("IMU_CHEST",  AMBER,  "Flat on sternum — USB port facing down"),
            ("IMU_ARM",    GREEN5, "Outside of upper arm, midway shoulder→elbow"),
            ("IMU_WRIST",  CYAN,   "Back of forearm, just above wrist — aligned along bone"),
        ]
        for name, colour, desc in placements:
            row = QHBoxLayout(); row.setSpacing(6)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{colour}; font-size:11px; border:none; background:transparent;")
            dot.setFixedWidth(14)
            name_lbl = QLabel(name)
            name_lbl.setStyleSheet(label_style(colour, 11, bold=True))
            name_lbl.setFixedWidth(88)
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet(label_style(TEXT3, 11))
            desc_lbl.setWordWrap(True)
            row.addWidget(dot)
            row.addWidget(name_lbl)
            row.addWidget(desc_lbl, stretch=1)
            info_col.addLayout(row)

        info_col.addStretch()
        outer.addLayout(info_col, stretch=2)

        # ── Right: placement photo, scaled to fit the strip height ───────────
        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignCenter)
        self._img_lbl.setStyleSheet("background:transparent; border:none;")
        self._img_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._load_image()
        outer.addWidget(self._img_lbl, stretch=3)

    def _load_image(self):
        if not _PLACEMENT_IMG.exists():
            self._img_lbl.setText(
                f"<span style='color:{RED};font-size:11px;'>"
                f"Image not found:<br>{_PLACEMENT_IMG}</span>"
            )
            return
        pixmap = QPixmap(str(_PLACEMENT_IMG))
        if pixmap.isNull():
            self._img_lbl.setText(
                f"<span style='color:{RED};font-size:11px;'>Could not load image.</span>"
            )
            return
        # Scale to fit the available height (minus vertical margins), keep aspect
        target_h = self.STRIP_HEIGHT - 16
        scaled = pixmap.scaledToHeight(target_h, Qt.SmoothTransformation)
        self._img_lbl.setPixmap(scaled)


class ConnectPanel(QWidget):
    calibrate_requested = pyqtSignal()
    rom_completed = pyqtSignal()

    def __init__(self, state: AppState, ble_manager, calibration, parent=None):
        super().__init__(parent)
        self._state = state
        self._ble   = ble_manager
        self._cal   = calibration
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setFixedHeight(36)
        title_bar.setStyleSheet(f"background:{SURFACE}; border-bottom:1px solid {BORDER};")
        tb = QHBoxLayout(title_bar); tb.setContentsMargins(16, 0, 16, 0)
        t = QLabel("DEVICE SETUP")
        t.setStyleSheet(label_style(GREEN5, 12, bold=True))
        self._conn_status = QLabel("0/3 connected")
        self._conn_status.setStyleSheet(label_style(RED, 11))
        tb.addWidget(t); tb.addStretch(); tb.addWidget(self._conn_status)
        root.addWidget(title_bar)

        # ── Body row (wizard left + IMU cards right) ──────────────────────────
        body = QWidget()
        bl = QHBoxLayout(body); bl.setContentsMargins(16, 14, 16, 14); bl.setSpacing(14)

        # Left: wizard steps + calibration
        left = QVBoxLayout(); left.setSpacing(6)
        left.addWidget(QLabel("SETUP WIZARD").also(
            lambda l: l.setStyleSheet(label_style(GREEN3, 12))
        ))

        self._steps = []
        defs = [
            (1, "BLUETOOTH",    "Adapter detected"),
            (2, "CONNECT IMUs", "Scanning for IMU_WRIST / ARM / CHEST"),
            (3, "CALIBRATE",    "Stand in I-pose and press Calibrate"),
            (4, "MEASURE ROM",  "Move arm to find baseline range"),
            (5, "READY",        "Begin your first session"),
        ]
        for n, t2, s in defs:
            w = WizardStep(n, t2, s)
            self._steps.append(w)
            left.addWidget(w)

        left.addSpacing(10)

        # Calibration card — text + buttons only, no image in card
        cal_frame = QFrame()
        cal_frame.setStyleSheet(card_style(SURFACE2, GREEN3))
        cf = QHBoxLayout(cal_frame); cf.setContentsMargins(12, 10, 12, 10); cf.setSpacing(12)

        cal_txt = QVBoxLayout(); cal_txt.setSpacing(4)
        cal_ttl = QLabel("I-POSE CALIBRATION")
        cal_ttl.setStyleSheet(label_style(GREEN, 12, bold=True))
        cal_sub = QLabel("Stand upright, arm hanging naturally. 3-second average.")
        cal_sub.setStyleSheet(label_style(TEXT3, 12))
        cal_sub.setWordWrap(True)

        self._cal_fill = QProgressBar()
        self._cal_fill.setRange(0, 100)
        self._cal_fill.setValue(0)
        self._cal_fill.setFixedHeight(8)
        self._cal_fill.setTextVisible(False)
        self._cal_fill.setStyleSheet(f"""
            QProgressBar {{
                background: {SURFACE3};
                border: 1px solid {BORDER2};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {RED}, stop:1 {GREEN5});
                border-radius: 2px;
            }}
        """)

        cal_txt.addWidget(cal_ttl)
        cal_txt.addWidget(cal_sub)
        cal_txt.addWidget(self._cal_fill)

        cal_btn_col = QVBoxLayout(); cal_btn_col.setSpacing(5)

        self._cal_btn = QPushButton("CALIBRATE")
        self._cal_btn.setFixedSize(130, 32)
        self._cal_btn.setStyleSheet(f"""
            QPushButton {{ background:{GREEN5}; color:#ffffff; border:none; border-radius:3px;}}
            QPushButton:hover {{ background:#1a8855; }}
            QPushButton:pressed {{ background:#147044; }}
        """)
        self._cal_btn.clicked.connect(self.calibrate_requested)

        self._rom_btn = QPushButton("MEASURE ROM")
        self._rom_btn.setFixedSize(130, 28)
        self._rom_btn.setStyleSheet(f"""
            QPushButton {{ background:{CYAN}; color:#ffffff; border:none; border-radius:3px;}}
            QPushButton:hover {{ background:#005599; }}
            QPushButton:pressed {{ background:#004488; }}
        """)
        self._rom_btn.clicked.connect(self._on_rom_btn)

        cal_btn_col.addWidget(self._cal_btn)
        cal_btn_col.addWidget(self._rom_btn)

        cf.addLayout(cal_txt, stretch=1)
        cf.addLayout(cal_btn_col)
        left.addWidget(cal_frame)

        self._cal_status_lbl = QLabel("NOT CALIBRATED")
        self._cal_status_lbl.setStyleSheet(label_style(RED, 10))
        left.addWidget(self._cal_status_lbl)

        # ── Sensor placement + I-pose columns ────────────────────────────────
        left.addSpacing(12)

        IMG_HEIGHT = 460

        placement_row = QHBoxLayout(); placement_row.setSpacing(16)

        # Column 1 — sensor placement photo
        col1 = QVBoxLayout(); col1.setSpacing(6); col1.setAlignment(Qt.AlignTop)
        col1_hdr = QLabel("SENSOR PLACEMENT")
        col1_hdr.setStyleSheet(label_style(GREEN3, 11, bold=True))
        col1_hdr.setAlignment(Qt.AlignHCenter)
        self._placement_img = QLabel()
        self._placement_img.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self._placement_img.setStyleSheet("background:transparent; border:none;")
        self._load_placement_image(height=IMG_HEIGHT)
        col1.addWidget(col1_hdr)
        col1.addWidget(self._placement_img, alignment=Qt.AlignHCenter)
        placement_row.addLayout(col1, stretch=1)

        # Column 2 — I-pose calibration stance
        col2 = QVBoxLayout(); col2.setSpacing(6); col2.setAlignment(Qt.AlignTop)
        col2_hdr = QLabel("I-POSE STANCE")
        col2_hdr.setStyleSheet(label_style(GREEN3, 11, bold=True))
        col2_hdr.setAlignment(Qt.AlignHCenter)
        col2.addWidget(col2_hdr)
        _ipose_path = _ASSETS_DIR / "I_Pose.png"
        _ipose_px = QPixmap(str(_ipose_path)) if _ipose_path.exists() else QPixmap()
        if not _ipose_px.isNull():
            self._ipose_img = QLabel()
            self._ipose_img.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            self._ipose_img.setStyleSheet("background:transparent; border:none;")
            self._ipose_img.setPixmap(
                _ipose_px.scaledToHeight(IMG_HEIGHT, Qt.SmoothTransformation)
            )
            col2.addWidget(self._ipose_img, alignment=Qt.AlignHCenter)
        else:
            self._ipose_img = QSvgWidget()
            self._ipose_img.setFixedSize(80, 180)
            self._ipose_img.setStyleSheet("background:transparent; border:none;")
            self._ipose_img.load(QByteArray(_svg_ipose().encode("utf-8")))
            col2.addWidget(self._ipose_img, alignment=Qt.AlignHCenter)
        placement_row.addLayout(col2, stretch=1)

        left.addLayout(placement_row)
        left.addStretch()

        # ── Vertical divider ──────────────────────────────────────────────────
        divider = QFrame()
        divider.setFrameShape(QFrame.VLine)
        divider.setFrameShadow(QFrame.Plain)
        divider.setStyleSheet(f"color: {BORDER2}; background: {BORDER2}; max-width: 1px;")
        divider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        # Right: IMU cards
        right = QVBoxLayout(); right.setSpacing(8)
        hdr_r = QLabel("IMU SENSORS")
        hdr_r.setStyleSheet(label_style(GREEN3, 12))
        right.addWidget(hdr_r)

        self._imu_cards = {}
        for name in SLOT_NAMES:
            card = IMUSensorCard(name, self._ble)
            self._imu_cards[name] = card
            right.addWidget(card)
        right.addStretch()

        bl.addLayout(left, stretch=6)
        bl.addWidget(divider)
        bl.addLayout(right, stretch=3)
        root.addWidget(body, stretch=1)

    def _load_placement_image(self, height: int = 220):
        """Load and scale the sensor placement photo into self._placement_img."""
        if not _PLACEMENT_IMG.exists():
            self._placement_img.setText(
                f"<span style='color:{RED};font-size:10px;'>Image not found</span>"
            )
            return
        pixmap = QPixmap(str(_PLACEMENT_IMG))
        if pixmap.isNull():
            self._placement_img.setText(
                f"<span style='color:{RED};font-size:10px;'>Load error</span>"
            )
            return
        scaled = pixmap.scaledToHeight(height, Qt.SmoothTransformation)
        self._placement_img.setPixmap(scaled)

    def _on_rom_btn(self):
        """Launch the guided ROM wizard dialog."""
        if not self._state.calibrated:
            return
        wizard = RomWizard(self._state, parent=self)
        if wizard.exec_() == wizard.Accepted:
            self.rom_completed.emit()

    def refresh(self):
        with self._state.lock:
            calibrated = self._state.calibrated
            capturing  = self._cal.is_capturing()
            n_conn = sum(1 for n in SLOT_NAMES if self._state.slots[n].connected)
            slots  = {n: self._state.slots[n] for n in SLOT_NAMES}

        # Wizard step states
        self._steps[0].set_done()
        if n_conn == 3:
            self._steps[1].set_done()
            self._steps[1].set_badge("3/3", GREEN)
        else:
            self._steps[1].set_active()
            self._steps[1].set_badge(f"{n_conn}/3", AMBER)

        if calibrated:
            self._steps[2].set_done()
        elif n_conn == 3:
            self._steps[2].set_active()

        if getattr(self._state, 'rom_measured', False):
            self._steps[3].set_done()
        elif calibrated:
            self._steps[3].set_active()

        if calibrated and getattr(self._state, 'rom_measured', False):
            self._steps[4].set_done()
            self._steps[4].set_badge("GO", GREEN)

        # Conn status
        col = GREEN5 if n_conn == 3 else (AMBER if n_conn > 0 else RED)
        self._conn_status.setText(f"{n_conn}/3 connected")
        self._conn_status.setStyleSheet(label_style(col, 11))

        # Cal status + countdown bar
        if capturing:
            from calc.calibration import CAL_WINDOW_S
            remaining = self._cal.time_remaining_s()
            elapsed   = CAL_WINDOW_S - remaining
            pct = int(elapsed / CAL_WINDOW_S * 100)
            self._cal_fill.setValue(pct)
            self._cal_status_lbl.setText(f"HOLD I-POSE...  {remaining:.1f}s remaining")
            self._cal_status_lbl.setStyleSheet(label_style(AMBER, 10, bold=True))
        elif calibrated:
            self._cal_fill.setValue(100)
            self._cal_status_lbl.setText("✓ CALIBRATED")
            self._cal_status_lbl.setStyleSheet(label_style(GREEN, 10))
        else:
            self._cal_fill.setValue(0)
            self._cal_status_lbl.setText("NOT CALIBRATED")
            self._cal_status_lbl.setStyleSheet(label_style(RED, 10))

        # IMU cards
        for name in SLOT_NAMES:
            self._imu_cards[name].refresh(slots[name])


# monkey-patch QLabel.also for one-liner styling in list comprehensions
def _also(self, fn):
    fn(self); return self
QLabel.also = _also