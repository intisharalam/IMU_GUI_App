"""
gui/metrics_panel.py
--------------------
Right panel — clinical metrics, joint angle plot, session stats.

Sections (top to bottom):
  1. Four max-angle cards (flexion, abduction, ext rot, elbow)
  2. Scrolling joint angle plot (PyQtGraph, 10 s window)
  3. Session stats: rep count, session timer, current exercise
  4. ADL milestone indicators
  5. Haptic event indicators (on rep complete / ROM limit / deviation)

Only READS from AppState. Calibration status from Calibration object.
"""

import time
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QGridLayout, QSizePolicy, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

from ble.ble_state import AppState

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
C_PURPLE = "#b450ff"

C_FLEX   = "#00b4ff"
C_ABD    = "#00ffa0"
C_ROT    = "#ffc800"
C_ELBOW  = "#b450ff"

PLOT_WINDOW_S = 10.0

ADL_THRESHOLDS = {
    "Touch head":      ("max_flexion",   130.0),
    "Reach overhead":  ("max_abduction", 150.0),
    "Put on coat":     ("max_ext_rot",    60.0),
}


def _card(title: str, colour: str):
    """Returns (frame, now_label, max_label) for an angle readout card."""
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
    )
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(2)

    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"color: {colour}; font-size: 11px; font-weight: bold;")

    now_row = QHBoxLayout()
    now_key = QLabel("Now:")
    now_key.setStyleSheet(f"color: {DIM}; font-size: 11px;")
    now_val = QLabel("—°")
    now_val.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: bold;")
    now_row.addWidget(now_key)
    now_row.addWidget(now_val)
    now_row.addStretch()

    max_row = QHBoxLayout()
    max_key = QLabel("Max:")
    max_key.setStyleSheet(f"color: {DIM}; font-size: 11px;")
    max_val = QLabel("—°")
    max_val.setStyleSheet(f"color: {colour}; font-size: 13px; font-weight: bold;")
    max_row.addWidget(max_key)
    max_row.addWidget(max_val)
    max_row.addStretch()

    layout.addWidget(title_lbl)
    layout.addLayout(now_row)
    layout.addLayout(max_row)
    return frame, now_val, max_val


class MetricsPanel(QWidget):
    """Right panel — all clinical metrics."""

    def __init__(self, state: AppState, calibration, parent=None):
        super().__init__(parent)
        self._state       = state
        self._calibration = calibration
        self._session_start = time.monotonic()
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(6)

        # Header
        hdr = QLabel("CLINICAL METRICS")
        hdr.setStyleSheet(
            f"color: {C_ACCENT}; font-size: 11px; font-weight: bold; padding: 4px 4px 2px 4px;"
        )
        layout.addWidget(hdr)

        # ── Angle cards ───────────────────────────────────────────────────────
        cards_widget = QWidget()
        cards_layout = QHBoxLayout(cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(4)

        self._card_flex,  self._flex_now,  self._flex_max  = _card("Shldr Flex",  C_FLEX)
        self._card_abd,   self._abd_now,   self._abd_max   = _card("Abduction",   C_ABD)
        self._card_rot,   self._rot_now,   self._rot_max   = _card("Ext. Rot",    C_ROT)
        self._card_elbow, self._elbow_now, self._elbow_max = _card("Elbow Flex",  C_ELBOW)

        for card in [self._card_flex, self._card_abd, self._card_rot, self._card_elbow]:
            cards_layout.addWidget(card)
        layout.addWidget(cards_widget)

        # ── Calibration status ────────────────────────────────────────────────
        self._cal_lbl = QLabel("Not calibrated — connect all sensors first")
        self._cal_lbl.setStyleSheet(f"color: {C_AMBER}; font-size: 11px; padding: 2px 4px;")
        layout.addWidget(self._cal_lbl)

        # ── Joint angle scrolling plot ────────────────────────────────────────
        self._plot = pg.PlotWidget(background=PANEL_BG)
        self._plot.setLabel("left", "deg")
        self._plot.showGrid(x=False, y=True, alpha=0.15)
        self._plot.setYRange(-180, 180)
        self._plot.setXRange(-PLOT_WINDOW_S, 0)
        self._plot.getAxis("bottom").setStyle(showValues=False)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.hideButtons()
        self._plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._flex_curve  = self._plot.plot(pen=pg.mkPen(C_FLEX,   width=1.5), name="Flexion")
        self._abd_curve   = self._plot.plot(pen=pg.mkPen(C_ABD,    width=1.5), name="Abduction")
        self._rot_curve   = self._plot.plot(pen=pg.mkPen(C_ROT,    width=1.5), name="Ext Rot")
        self._elbow_curve = self._plot.plot(pen=pg.mkPen(C_PURPLE, width=1.5), name="Elbow")
        layout.addWidget(self._plot, stretch=1)

        # Legend row
        leg = QHBoxLayout()
        for colour, label in [
            (C_FLEX, "Flexion"), (C_ABD, "Abduction"),
            (C_ROT, "Ext Rot"),  (C_ELBOW, "Elbow")
        ]:
            dot = QLabel("■")
            dot.setStyleSheet(f"color: {colour}; font-size: 10px;")
            t = QLabel(label + "  ")
            t.setStyleSheet(f"color: {DIM}; font-size: 10px;")
            leg.addWidget(dot); leg.addWidget(t)
        leg.addStretch()
        layout.addLayout(leg)

        # ── Session stats row ─────────────────────────────────────────────────
        stats = QHBoxLayout()
        stats.setSpacing(4)

        self._reps_card    = self._stat_card("Reps",    "0",         TEXT)
        self._time_card    = self._stat_card("Time",    "00:00",     TEXT)
        self._exname_card  = self._stat_card("Exercise","—",         C_ACCENT)

        for w in [self._reps_card[0], self._time_card[0], self._exname_card[0]]:
            stats.addWidget(w)
        layout.addLayout(stats)

        # ── ADL milestones ────────────────────────────────────────────────────
        adl_frame = QFrame()
        adl_frame.setStyleSheet(
            f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        adl_layout = QVBoxLayout(adl_frame)
        adl_layout.setContentsMargins(8, 6, 8, 6)
        adl_layout.setSpacing(3)

        adl_hdr = QLabel("ADL Milestones (session best)")
        adl_hdr.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        adl_layout.addWidget(adl_hdr)

        adl_row = QHBoxLayout()
        self._adl_labels = {}
        for name, (_, threshold) in ADL_THRESHOLDS.items():
            lbl = QLabel(f"[!] {name} (≥{threshold:.0f}°)")
            lbl.setStyleSheet(f"color: {C_RED}; font-size: 10px;")
            adl_row.addWidget(lbl)
            self._adl_labels[name] = lbl
        adl_row.addStretch()
        adl_layout.addLayout(adl_row)
        layout.addWidget(adl_frame)

    # ── Stat card helper ──────────────────────────────────────────────────────

    def _stat_card(self, title: str, value: str, colour: str):
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {PANEL_BG}; border: 1px solid {BORDER}; border-radius: 4px; }}"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"color: {DIM}; font-size: 10px;")
        val = QLabel(value)
        val.setStyleSheet(f"color: {colour}; font-size: 16px; font-weight: bold;")
        v.addWidget(t)
        v.addWidget(val)
        return frame, val   # (widget, value_label)

    # ── Per-frame refresh ─────────────────────────────────────────────────────

    def refresh(self):
        now = time.monotonic()

        with self._state.lock:
            calibrated  = self._state.calibrated
            flex        = self._state.shoulder_flexion
            abd         = self._state.shoulder_abduction
            ext_rot     = self._state.external_rotation
            elbow       = self._state.elbow_flexion
            max_flex    = self._state.max_flexion
            max_abd     = self._state.max_abduction
            max_rot     = self._state.max_ext_rot
            max_elbow   = self._state.max_elbow
            t_list      = list(self._state.angle_times)
            flex_h      = list(self._state.flexion_hist)
            abd_h       = list(self._state.abduction_hist)
            rot_h       = list(self._state.ext_rot_hist)
            elbow_h     = list(self._state.elbow_hist)

        # Calibration status
        if calibrated:
            self._cal_lbl.setText("✓ Calibrated")
            self._cal_lbl.setStyleSheet(f"color: {C_GREEN}; font-size: 11px; padding: 2px 4px;")
        else:
            self._cal_lbl.setText("Not calibrated — connect all sensors first")
            self._cal_lbl.setStyleSheet(f"color: {C_AMBER}; font-size: 11px; padding: 2px 4px;")

        # Angle cards
        self._flex_now.setText(f"{flex:+.1f}°")
        self._abd_now.setText(f"{abd:+.1f}°")
        self._rot_now.setText(f"{ext_rot:+.1f}°")
        self._elbow_now.setText(f"{elbow:+.1f}°")
        self._flex_max.setText(f"{max_flex:.1f}°")
        self._abd_max.setText(f"{max_abd:.1f}°")
        self._rot_max.setText(f"{max_rot:.1f}°")
        self._elbow_max.setText(f"{max_elbow:.1f}°")

        # Scrolling plot
        if t_list:
            cutoff  = now - PLOT_WINDOW_S
            start   = next((i for i, t in enumerate(t_list) if t >= cutoff), 0)
            xs = [t - now for t in t_list[start:]]
            self._flex_curve.setData(xs, flex_h[start:])
            self._abd_curve.setData(xs, abd_h[start:])
            self._rot_curve.setData(xs, rot_h[start:])
            self._elbow_curve.setData(xs, elbow_h[start:])
        else:
            for curve in [self._flex_curve, self._abd_curve,
                          self._rot_curve, self._elbow_curve]:
                curve.setData([], [])

        # Session timer
        elapsed = int(now - self._session_start)
        m, s = divmod(elapsed, 60)
        self._time_card[1].setText(f"{m:02d}:{s:02d}")

        # ADL milestones
        adl_vals = {"max_flexion": max_flex, "max_abduction": max_abd, "max_ext_rot": max_rot}
        for name, (key, threshold) in ADL_THRESHOLDS.items():
            achieved = adl_vals[key] >= threshold
            lbl = self._adl_labels[name]
            if achieved:
                lbl.setText(f"[✓] {name}")
                lbl.setStyleSheet(f"color: {C_GREEN}; font-size: 10px;")
            else:
                lbl.setText(f"[!] {name} (≥{threshold:.0f}°)")
                lbl.setStyleSheet(f"color: {C_RED}; font-size: 10px;")
