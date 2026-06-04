"""
gui/metrics_panel.py  —  v4.1
------------------------------
Clinical metrics plot + ADL milestones. Light theme throughout.
"""

import time
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QSizePolicy
)
from ble.ble_state import AppState

# Light theme colours
BG      = "#f5f5f8"
BORDER  = "#d0d0da"
DIM     = "#666677"
TEXT    = "#1a1a2a"
C_GREEN = "#007744"
C_RED   = "#cc2222"
C_AMBER = "#cc8800"
C_BLUE  = "#1a6aaa"
C_PURPLE= "#7722aa"

C_FLEX  = "#1a6aaa"
C_ABD   = "#007744"
C_ROT   = "#cc8800"
C_ELBOW = "#7722aa"

PLOT_WINDOW_S = 10.0

ADL_THRESHOLDS = {
    "Touch head":     ("max_flexion",   130.0),
    "Reach overhead": ("max_abduction", 150.0),
    "Put on coat":    ("max_ext_rot",    60.0),
}


class MetricsPanel(QWidget):
    def __init__(self, state: AppState, calibration, parent=None):
        super().__init__(parent)
        self._state = state
        self._cal   = calibration
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        hdr = QLabel("CLINICAL METRICS")
        hdr.setStyleSheet(
            f"color:{C_BLUE}; font-size:11px; font-weight:bold; padding:2px 0;"
        )
        lay.addWidget(hdr)

        self._cal_lbl = QLabel("Not calibrated — connect all sensors first")
        self._cal_lbl.setStyleSheet(f"color:{C_AMBER}; font-size:10px;")
        lay.addWidget(self._cal_lbl)

        # Scrolling angle plot — white background, dark axes
        self._plot = pg.PlotWidget(background="w")
        self._plot.getAxis("left").setTextPen("k")
        self._plot.getAxis("bottom").setTextPen("k")
        self._plot.getAxis("left").setPen("k")
        self._plot.getAxis("bottom").setPen("k")
        self._plot.showGrid(x=False, y=True, alpha=0.20)
        self._plot.setYRange(-180, 180)
        self._plot.setXRange(-PLOT_WINDOW_S, 0)
        self._plot.getAxis("bottom").setStyle(showValues=False)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.hideButtons()
        self._plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._flex_c  = self._plot.plot(pen=pg.mkPen(C_FLEX,   width=1.5))
        self._abd_c   = self._plot.plot(pen=pg.mkPen(C_ABD,    width=1.5))
        self._rot_c   = self._plot.plot(pen=pg.mkPen(C_ROT,    width=1.5))
        self._elbow_c = self._plot.plot(pen=pg.mkPen(C_PURPLE, width=1.5))
        lay.addWidget(self._plot, stretch=1)

        # Legend
        leg = QHBoxLayout()
        for col, txt in [(C_FLEX,"Flex"),(C_ABD,"Abd"),(C_ROT,"Ext Rot"),(C_ELBOW,"Elbow")]:
            d = QLabel("■"); d.setStyleSheet(f"color:{col}; font-size:10px;")
            t = QLabel(txt + "  "); t.setStyleSheet(f"color:{DIM}; font-size:10px;")
            leg.addWidget(d); leg.addWidget(t)
        leg.addStretch()
        lay.addLayout(leg)

        # ADL milestones — light frame
        adl = QFrame()
        adl.setStyleSheet(
            f"QFrame{{background:{BG};border:1px solid {BORDER};border-radius:4px;}}"
        )
        al = QVBoxLayout(adl); al.setContentsMargins(8,4,8,4); al.setSpacing(2)
        ah = QLabel("ADL Milestones (session best)")
        ah.setStyleSheet(f"color:{DIM}; font-size:9px; font-weight:bold;")
        al.addWidget(ah)
        arow = QHBoxLayout()
        self._adl = {}
        for name, (_, thresh) in ADL_THRESHOLDS.items():
            lbl = QLabel(f"[!] {name} (≥{thresh:.0f}°)")
            lbl.setStyleSheet(f"color:{C_RED}; font-size:10px;")
            arow.addWidget(lbl)
            self._adl[name] = lbl
        arow.addStretch()
        al.addLayout(arow)
        lay.addWidget(adl)

    def refresh(self):
        now = time.monotonic()
        with self._state.lock:
            calibrated = self._state.calibrated
            mf = self._state.max_flexion; ma = self._state.max_abduction
            mr = self._state.max_ext_rot
            t_list = list(self._state.angle_times)
            fh = list(self._state.flexion_hist)
            ah = list(self._state.abduction_hist)
            rh = list(self._state.ext_rot_hist)
            eh = list(self._state.elbow_hist)

        cap = self._cal.is_capturing() if hasattr(self._cal, 'is_capturing') else False
        if cap:
            self._cal_lbl.setText("Capturing 3s — hold I-pose...")
            self._cal_lbl.setStyleSheet(f"color:{C_BLUE}; font-size:10px;")
        elif calibrated:
            self._cal_lbl.setText("✓ Calibrated")
            self._cal_lbl.setStyleSheet(f"color:{C_GREEN}; font-size:10px;")
        else:
            self._cal_lbl.setText("Not calibrated — connect all sensors first")
            self._cal_lbl.setStyleSheet(f"color:{C_AMBER}; font-size:10px;")

        if t_list:
            cutoff = now - PLOT_WINDOW_S
            start  = next((i for i,t in enumerate(t_list) if t >= cutoff), 0)
            xs = [t - now for t in t_list[start:]]
            self._flex_c.setData(xs, fh[start:])
            self._abd_c.setData(xs, ah[start:])
            self._rot_c.setData(xs, rh[start:])
            self._elbow_c.setData(xs, eh[start:])
        else:
            for c in [self._flex_c, self._abd_c, self._rot_c, self._elbow_c]:
                c.setData([], [])

        adl_vals = {"max_flexion": mf, "max_abduction": ma, "max_ext_rot": mr}
        for name, (key, thresh) in ADL_THRESHOLDS.items():
            done = adl_vals[key] >= thresh
            self._adl[name].setText(f"[✓] {name}" if done else f"[!] {name} (≥{thresh:.0f}°)")
            self._adl[name].setStyleSheet(
                f"color:{C_GREEN if done else C_RED}; font-size:10px;"
            )