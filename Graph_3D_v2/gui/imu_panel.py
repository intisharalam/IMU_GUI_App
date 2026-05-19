"""
gui/imu_panel.py
----------------
Left panel — three sensor columns (WRIST, ARM, CHEST).

Each column shows:
  - Connection status dot + label
  - BLE address, packet count, sync offset
  - Haptic / Sync buttons
  - Scrolling Roll / Pitch / Yaw plot (PyQtGraph)

Only READS from AppState. Button callbacks fire through BLEManager.
"""

import time
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from ble.ble_state import AppState, SLOT_NAMES

# ── Colours ───────────────────────────────────────────────────────────────────
BG       = "#000000"
PANEL_BG = "#0a0a0a"
BORDER   = "#232330"
TEXT     = "#f0f0f0"
DIM      = "#6e6e82"
C_GREEN  = "#00ffa0"
C_RED    = "#ff3c3c"
C_AMBER  = "#ffc800"
C_ACCENT = "#00b4ff"

C_ROLL   = "#00b4ff"
C_PITCH  = "#00ffa0"
C_YAW    = "#ffc800"

PLOT_WINDOW_S = 10.0
LABELS = {"wrist": "WRIST", "arm": "ARM", "chest": "CHEST"}


class SensorColumn(QWidget):
    """One sensor column — status card + buttons + scrolling RPY plot."""

    def __init__(self, slot_name: str, ble_manager, parent=None):
        super().__init__(parent)
        self._name = slot_name
        self._ble  = ble_manager
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)

        # ── Status card ───────────────────────────────────────────────────────
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(2)

        # Dot + label row
        row1 = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {C_RED}; font-size: 14px;")
        lbl = QLabel(LABELS[self._name])
        lbl.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold; font-size: 12px;")
        row1.addWidget(self._dot)
        row1.addWidget(lbl)
        row1.addStretch()
        card_layout.addLayout(row1)

        self._status_lbl  = QLabel("Searching...")
        self._status_lbl.setStyleSheet(f"color: {C_RED}; font-size: 11px;")
        self._addr_lbl    = QLabel("")
        self._addr_lbl.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        self._packets_lbl = QLabel("Packets: —")
        self._packets_lbl.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        self._sync_lbl    = QLabel("Sync: —")
        self._sync_lbl.setStyleSheet(f"color: {DIM}; font-size: 10px;")

        card_layout.addWidget(self._status_lbl)
        card_layout.addWidget(self._addr_lbl)
        card_layout.addWidget(self._packets_lbl)
        card_layout.addWidget(self._sync_lbl)
        layout.addWidget(card)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._haptic_btn = QPushButton("Haptic")
        self._sync_btn   = QPushButton("Sync")
        self._haptic_btn.setFixedHeight(26)
        self._sync_btn.setFixedHeight(26)
        self._haptic_btn.clicked.connect(lambda: self._ble.send_haptic(self._name))
        self._sync_btn.clicked.connect(lambda: self._ble.send_sync(self._name))
        btn_row.addWidget(self._haptic_btn)
        btn_row.addWidget(self._sync_btn)
        layout.addLayout(btn_row)

        # ── Scrolling RPY plot ────────────────────────────────────────────────
        pg.setConfigOptions(antialias=True)
        plot = pg.PlotWidget(background=PANEL_BG)
        plot.setLabel("left", "deg")
        plot.setLabel("bottom", "s")
        plot.showGrid(x=False, y=True, alpha=0.15)
        plot.setYRange(-180, 180)
        plot.setXRange(-PLOT_WINDOW_S, 0)
        plot.getAxis("bottom").setStyle(showValues=False)
        plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Disable mouse interaction on the small plots
        plot.setMouseEnabled(x=False, y=False)
        plot.hideButtons()

        self._roll_curve  = plot.plot(pen=pg.mkPen(C_ROLL,  width=1), name="Roll")
        self._pitch_curve = plot.plot(pen=pg.mkPen(C_PITCH, width=1), name="Pitch")
        self._yaw_curve   = plot.plot(pen=pg.mkPen(C_YAW,   width=1), name="Yaw")
        layout.addWidget(plot, stretch=1)

        # Colour legend
        leg = QHBoxLayout()
        for colour, txt in [(C_ROLL,"Roll"), (C_PITCH,"Pitch"), (C_YAW,"Yaw")]:
            dot = QLabel("■")
            dot.setStyleSheet(f"color: {colour}; font-size: 10px;")
            t = QLabel(txt)
            t.setStyleSheet(f"color: {DIM}; font-size: 10px;")
            leg.addWidget(dot)
            leg.addWidget(t)
        leg.addStretch()
        layout.addLayout(leg)

    def refresh(self, slot, now: float):
        """Called every frame. slot is the IMUSlot for this sensor."""
        # Status
        if slot.connected:
            self._dot.setStyleSheet(f"color: {C_GREEN}; font-size: 14px;")
            self._status_lbl.setText("Connected")
            self._status_lbl.setStyleSheet(f"color: {C_GREEN}; font-size: 11px;")
            self._addr_lbl.setText(slot.address or "")
        else:
            self._dot.setStyleSheet(f"color: {C_RED}; font-size: 14px;")
            self._status_lbl.setText("Searching...")
            self._status_lbl.setStyleSheet(f"color: {C_RED}; font-size: 11px;")
            self._addr_lbl.setText("")

        self._packets_lbl.setText(f"Packets: {slot.packet_count or '—'}")
        sync_str = f"Sync: {slot.sync_offset_ms:+.1f} ms" if slot.sync_offset_ms is not None else "Sync: —"
        self._sync_lbl.setText(sync_str)

        # Haptic button flash
        if slot.haptic_active:
            self._haptic_btn.setStyleSheet(
                f"QPushButton {{ background: {C_AMBER}; color: #000; border: none; border-radius: 4px; }}"
            )
        else:
            self._haptic_btn.setStyleSheet("")

        # Plot
        t_list, r_list, p_list, y_list = slot.get_plot_data()
        if t_list:
            cutoff = now - PLOT_WINDOW_S
            start  = next((i for i, t in enumerate(t_list) if t >= cutoff), 0)
            xs = [t - now for t in t_list[start:]]
            self._roll_curve.setData(xs, r_list[start:])
            self._pitch_curve.setData(xs, p_list[start:])
            self._yaw_curve.setData(xs, y_list[start:])
        else:
            self._roll_curve.setData([], [])
            self._pitch_curve.setData([], [])
            self._yaw_curve.setData([], [])


class IMUPanel(QWidget):
    """
    Left panel. Three SensorColumn widgets side by side.
    """

    def __init__(self, state: AppState, ble_manager, parent=None):
        super().__init__(parent)
        self._state = state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Section label
        hdr = QLabel("IMU STATUS")
        hdr.setStyleSheet(f"color: {C_ACCENT}; font-size: 11px; font-weight: bold; padding: 4px 8px;")
        layout.addWidget(hdr)

        cols = QHBoxLayout()
        cols.setSpacing(4)
        cols.setContentsMargins(4, 0, 4, 4)
        self._columns = {
            name: SensorColumn(name, ble_manager)
            for name in SLOT_NAMES
        }
        for name in SLOT_NAMES:
            cols.addWidget(self._columns[name])
        layout.addLayout(cols, stretch=1)

    def refresh(self):
        now = time.monotonic()
        with self._state.lock:
            for name in SLOT_NAMES:
                slot = self._state.slots[name]
                self._columns[name].refresh(slot, now)
