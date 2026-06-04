"""
gui/imu_panel.py  —  v3
-----------------------
Left panel — IMU connection status + live angle readouts.
Replaces the full RPY scrolling plots (moved to metrics panel).
Layout: three compact sensor cards stacked vertically + live angle grid.
"""

import time
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QGridLayout
)
from PyQt5.QtCore import Qt
from ble.ble_state import AppState, SLOT_NAMES

BG = "#f0f0f4"; PANEL_BG = "#f5f5f8"; BORDER = "#d0d0da"
TEXT = "#1a1a2a"; DIM = "#888888"
C_GREEN = "#007744"; C_RED = "#cc2222"; C_AMBER = "#cc8800"
C_ACCENT = "#1a6aaa"; C_PURPLE = "#b450ff"
C_FLEX = "#00b4ff"; C_ABD = "#00ffa0"; C_ROT = "#ffc800"; C_ELBOW = "#b450ff"

LABELS = {"wrist": "WRIST", "arm": "ARM", "chest": "CHEST"}


class SensorCard(QFrame):
    def __init__(self, slot_name, ble_manager, parent=None):
        super().__init__(parent)
        self._name = slot_name
        self._ble  = ble_manager
        self.setStyleSheet(
            f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        row = QHBoxLayout()
        self._dot = QLabel("●")
        self._dot.setStyleSheet(f"color: {C_RED}; font-size: 13px;")
        name_lbl = QLabel(LABELS[slot_name])
        name_lbl.setStyleSheet(f"color: {C_ACCENT}; font-weight: bold; font-size: 11px;")
        row.addWidget(self._dot); row.addWidget(name_lbl); row.addStretch()
        lay.addLayout(row)

        self._status  = QLabel("Searching...")
        self._status.setStyleSheet(f"color: {C_RED}; font-size: 10px;")
        self._addr    = QLabel("")
        self._addr.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        self._packets = QLabel("Packets: —")
        self._packets.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        lay.addWidget(self._status)
        lay.addWidget(self._addr)
        lay.addWidget(self._packets)

        btn_row = QHBoxLayout()
        self._haptic_btn = QPushButton("Haptic")
        self._sync_btn   = QPushButton("Sync")
        self._haptic_btn.setFixedHeight(22)
        self._sync_btn.setFixedHeight(22)
        self._haptic_btn.setStyleSheet("font-size: 10px;")
        self._sync_btn.setStyleSheet("font-size: 10px;")
        self._haptic_btn.clicked.connect(lambda: self._ble.send_haptic(self._name))
        self._sync_btn.clicked.connect(lambda: self._ble.send_sync(self._name))
        btn_row.addWidget(self._haptic_btn)
        btn_row.addWidget(self._sync_btn)
        lay.addLayout(btn_row)

    def refresh(self, slot):
        if slot.connected:
            self._dot.setStyleSheet(f"color: {C_GREEN}; font-size: 13px;")
            self._status.setText("Connected")
            self._status.setStyleSheet(f"color: {C_GREEN}; font-size: 10px;")
            self._addr.setText(slot.address or "")
        else:
            self._dot.setStyleSheet(f"color: {C_RED}; font-size: 13px;")
            self._status.setText("Searching...")
            self._status.setStyleSheet(f"color: {C_RED}; font-size: 10px;")
            self._addr.setText("")
        self._packets.setText(f"Packets: {slot.packet_count or '—'}")
        if slot.haptic_active:
            self._haptic_btn.setStyleSheet(
                f"font-size:10px; background:{C_AMBER}; color:#000; border:none; border-radius:3px;"
            )
        else:
            self._haptic_btn.setStyleSheet("font-size: 10px;")


class IMUPanel(QWidget):
    def __init__(self, state: AppState, ble_manager, parent=None):
        super().__init__(parent)
        self._state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(6)

        hdr = QLabel("IMU STATUS")
        hdr.setStyleSheet(f"color: {C_ACCENT}; font-size: 11px; font-weight: bold;")
        lay.addWidget(hdr)

        self._cards = {}
        for name in SLOT_NAMES:
            card = SensorCard(name, ble_manager)
            self._cards[name] = card
            lay.addWidget(card)

        # ── Calibration status ────────────────────────────────────────────────
        self._cal_lbl = QLabel("Not calibrated")
        self._cal_lbl.setStyleSheet(
            f"color: {C_AMBER}; font-size: 10px; padding: 2px 4px;"
        )
        lay.addWidget(self._cal_lbl)

        # ── Live angle readout grid ───────────────────────────────────────────
        angle_frame = QFrame()
        angle_frame.setStyleSheet(
            f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        grid = QGridLayout(angle_frame)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setSpacing(4)

        defs = [
            ("Flex",    C_FLEX,   0, 0),
            ("Abd",     C_ABD,    0, 1),
            ("Ext Rot", C_ROT,    1, 0),
            ("Elbow",   C_ELBOW,  1, 1),
        ]
        self._angle_labels = {}
        for name, colour, row, col in defs:
            box = QFrame()
            box.setStyleSheet(
                f"QFrame {{ background: #0f0f18; border: 0.5px solid #1e1e2e; border-radius: 3px; }}"
            )
            bl = QVBoxLayout(box)
            bl.setContentsMargins(5, 3, 5, 3); bl.setSpacing(1)
            lbl_name = QLabel(name)
            lbl_name.setStyleSheet(f"color: {DIM}; font-size: 9px;")
            lbl_val  = QLabel("—°")
            lbl_val.setStyleSheet(f"color: {colour}; font-size: 14px; font-weight: bold;")
            bl.addWidget(lbl_name); bl.addWidget(lbl_val)
            grid.addWidget(box, row, col)
            self._angle_labels[name] = lbl_val

        # Session max row
        grid.addWidget(QLabel(""), 2, 0, 1, 2)  # spacer
        hdr2 = QLabel("Session Max")
        hdr2.setStyleSheet(f"color: {DIM}; font-size: 9px;")
        grid.addWidget(hdr2, 3, 0, 1, 2)
        self._max_lbl = QLabel("—")
        self._max_lbl.setStyleSheet(f"color: #606070; font-size: 9px;")
        self._max_lbl.setWordWrap(True)
        grid.addWidget(self._max_lbl, 4, 0, 1, 2)

        lay.addWidget(angle_frame)
        lay.addStretch()

        # ── Haptic log ────────────────────────────────────────────────────────
        log_frame = QFrame()
        log_frame.setStyleSheet(
            f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        log_lay = QVBoxLayout(log_frame)
        log_lay.setContentsMargins(8, 6, 8, 6); log_lay.setSpacing(2)
        lhdr = QLabel("Haptic Log")
        lhdr.setStyleSheet(f"color: {DIM}; font-size: 9px; font-weight: bold;")
        log_lay.addWidget(lhdr)
        self._log_labels = []
        for _ in range(3):
            lbl = QLabel("—")
            lbl.setStyleSheet(f"color: #404050; font-size: 9px;")
            log_lay.addWidget(lbl)
            self._log_labels.append(lbl)
        lay.addWidget(log_frame)

    def refresh(self):
        with self._state.lock:
            for name in SLOT_NAMES:
                self._cards[name].refresh(self._state.slots[name])
            calibrated = self._state.calibrated
            capturing  = getattr(self._state, '_calibration_capturing', False)
            flex  = self._state.shoulder_flexion
            abd   = self._state.shoulder_abduction
            rot   = self._state.external_rotation
            elbow = self._state.elbow_flexion
            mf = self._state.max_flexion; ma = self._state.max_abduction
            mr = self._state.max_ext_rot; me = self._state.max_elbow
            hlog = list(self._state.haptic_log)[-3:]

        # Cal status
        if capturing:
            self._cal_lbl.setText("Capturing 3s — hold I-pose...")
            self._cal_lbl.setStyleSheet(f"color: {C_ACCENT}; font-size: 10px; padding: 2px 4px;")
        elif calibrated:
            self._cal_lbl.setText("✓ Calibrated")
            self._cal_lbl.setStyleSheet(f"color: {C_GREEN}; font-size: 10px; padding: 2px 4px;")
        else:
            self._cal_lbl.setText("Not calibrated")
            self._cal_lbl.setStyleSheet(f"color: {C_AMBER}; font-size: 10px; padding: 2px 4px;")

        self._angle_labels["Flex"].setText(f"{flex:+.1f}°")
        self._angle_labels["Abd"].setText(f"{abd:+.1f}°")
        self._angle_labels["Ext Rot"].setText(f"{rot:+.1f}°")
        self._angle_labels["Elbow"].setText(f"{elbow:+.1f}°")
        self._max_lbl.setText(
            f"Flex {mf:.0f}°  Abd {ma:.0f}°\nRot {mr:.0f}°  Elbow {me:.0f}°"
        )

        # Haptic log
        for i, lbl in enumerate(self._log_labels):
            if i < len(hlog):
                ts, reason = hlog[-(i+1)]
                lbl.setText(f"{reason}")
                lbl.setStyleSheet(f"color: #808090; font-size: 9px;")
            else:
                lbl.setText("—")
                lbl.setStyleSheet(f"color: #404050; font-size: 9px;")
