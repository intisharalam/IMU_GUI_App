"""
gui/panels/analytics_panel.py
------------------------------
Panel 3 — Analytics and progress tracking.

Tabs: Overview · ROM Trends · Session History · Pain & Function
"""

import sqlite3
import csv
import sys
from datetime import datetime
from pathlib import Path
import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QSizePolicy, QGridLayout, QProgressBar
)
from PyQt5.QtCore import Qt

from ble.ble_state import AppState
from calc.session_recorder import get_streak_and_adherence
from calc.stage_detector import detect_stage, STAGE_COLOUR
from gui.styles import *

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sessions.db"


def _load_sessions():
    if not DB_PATH.exists(): return []
    try:
        con = sqlite3.connect(DB_PATH)
        rows = con.execute(
            "SELECT id,date,exercise,duration_s,reps,"
            "max_flex,max_abd,max_ext_rot,max_elbow,pain_pre,pain_post "
            "FROM sessions ORDER BY id DESC"
        ).fetchall()
        con.close()
        return rows
    except Exception:
        return []


class BigMetric(QFrame):
    def __init__(self, label: str, colour: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(card_style(SURFACE2, BORDER))
        lay = QVBoxLayout(self); lay.setContentsMargins(12,10,12,10); lay.setSpacing(3)
        lbl = QLabel(label); lbl.setStyleSheet(label_style(GREEN3, 12))
        self._val = QLabel("—"); self._val.setStyleSheet(
            f"color:{colour};font-size:26px;font-weight:bold;font-family:'Courier New',monospace;"
        )
        self._delta = QLabel(""); self._delta.setStyleSheet(label_style(GREEN3, 11))
        lay.addWidget(lbl); lay.addWidget(self._val); lay.addWidget(self._delta)

    def set(self, val: str, delta: str = "", delta_pos: bool = True):
        self._val.setText(val)
        if delta:
            col = GREEN if delta_pos else RED
            self._delta.setText(delta)
            self._delta.setStyleSheet(label_style(col, 10))


class AnalyticsPanel(QWidget):
    def __init__(self, state: AppState, recorder, parent=None):
        super().__init__(parent)
        self._state    = state
        self._recorder = recorder
        self._build()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # Title bar
        tb = QWidget(); tb.setFixedHeight(36)
        tb.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(tb); tl.setContentsMargins(16,0,16,0)
        tl.addWidget(QLabel("ANALYTICS").also(lambda l: l.setStyleSheet(label_style(GREEN, 12, bold=True))))
        tl.addStretch()
        export_btn = QPushButton("EXPORT ALL")
        export_btn.setFixedHeight(26)
        export_btn.setStyleSheet(btn_style())
        export_btn.clicked.connect(self._do_export)
        tl.addWidget(export_btn)
        root.addWidget(tb)

        # Tabs
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border:none; background:{BG}; }}
            QTabWidget::tab-bar {{ left:0; }}
        """)
        tabs.addTab(self._build_overview(), "OVERVIEW")
        tabs.addTab(self._build_trends(),   "ROM TRENDS")
        tabs.addTab(self._build_history(),  "SESSION HISTORY")
        tabs.addTab(self._build_pain(),     "PAIN & FUNCTION")
        root.addWidget(tabs, stretch=1)

    def _build_overview(self):
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(16,14,16,14); lay.setSpacing(12)

        lay.addWidget(QLabel("SESSION SUMMARY").also(lambda l: l.setStyleSheet(label_style(GREEN3, 12))))

        # Big metrics row
        mr = QHBoxLayout(); mr.setSpacing(10)
        self._m_flex   = BigMetric("MAX FLEXION",    C_FLEX)
        self._m_abd    = BigMetric("MAX ABDUCTION",  C_ABD)
        self._m_rot    = BigMetric("MAX EXT ROT",    C_ROT)
        self._m_elbow  = BigMetric("MAX ELBOW",      C_ELBOW)
        for m in [self._m_flex, self._m_abd, self._m_rot, self._m_elbow]:
            mr.addWidget(m)
        lay.addLayout(mr)

        # Adherence / streak row
        sr = QHBoxLayout(); sr.setSpacing(10)
        self._m_streak   = BigMetric("CURRENT STREAK",  GREEN)
        self._m_longest  = BigMetric("LONGEST STREAK",  GREEN2)
        self._m_7d       = BigMetric("SESSIONS (7 DAYS)", CYAN)
        self._m_total    = BigMetric("TOTAL SESSIONS",  TEXT2)
        for m in [self._m_streak, self._m_longest, self._m_7d, self._m_total]:
            sr.addWidget(m)
        lay.addLayout(sr)

        # ADL milestones
        lay.addWidget(QLabel("ADL MILESTONES").also(lambda l: l.setStyleSheet(label_style(GREEN3, 12))))
        adl_row = QHBoxLayout(); adl_row.setSpacing(10)
        self._adl = {}
        _adl_defs = [
            ("TOUCH HEAD",     130, C_FLEX),
            ("REACH OVERHEAD", 150, C_ABD),
            ("PUT ON COAT",     60, C_ROT),
        ]
        for name, thresh, colour in _adl_defs:
            f = QFrame(); f.setStyleSheet(card_style(SURFACE2, BORDER))
            fl = QVBoxLayout(f); fl.setContentsMargins(10,8,10,8); fl.setSpacing(6)

            header_row = QHBoxLayout()
            nl = QLabel(name); nl.setStyleSheet(label_style(TEXT2, 11, bold=True))
            tl2 = QLabel(f">={thresh} deg"); tl2.setStyleSheet(label_style(TEXT3, 11))
            header_row.addWidget(nl); header_row.addStretch(); header_row.addWidget(tl2)
            fl.addLayout(header_row)

            bar = QProgressBar()
            bar.setRange(0, 100); bar.setValue(0)
            bar.setFixedHeight(6); bar.setTextVisible(False)
            bar.setStyleSheet(
                f"QProgressBar {{background:{SURFACE3};border:none;border-radius:3px;}}"
                f"QProgressBar::chunk {{background:{colour};border-radius:3px;}}"
            )
            fl.addWidget(bar)

            val_lbl = QLabel("—"); val_lbl.setStyleSheet(label_style(colour, 12, bold=True))
            fl.addWidget(val_lbl)

            adl_row.addWidget(f)
            self._adl[name] = (bar, val_lbl, thresh, colour)
        lay.addLayout(adl_row)

        # Stage detection card
        stage_frame = QFrame(); stage_frame.setStyleSheet(card_style(SURFACE2, BORDER2))
        sfl = QHBoxLayout(stage_frame); sfl.setContentsMargins(14,10,14,10); sfl.setSpacing(14)
        stage_lbl_hdr = QLabel("CLINICAL STAGE")
        stage_lbl_hdr.setStyleSheet(label_style(GREEN3, 11))
        self._stage_badge = QLabel("UNKNOWN")
        self._stage_badge.setStyleSheet(
            f"color:{GREEN3};font-size:18px;font-weight:bold;"
            f"font-family:'Courier New',monospace;letter-spacing:2px;"
        )
        self._stage_desc  = QLabel("Collecting session data...")
        self._stage_desc.setStyleSheet(label_style(GREEN3, 12))
        self._stage_desc.setWordWrap(True)
        self._stage_deltas = QLabel("")
        self._stage_deltas.setStyleSheet(label_style(TEXT3, 11))
        stxt = QVBoxLayout(); stxt.setSpacing(3)
        stxt.addWidget(stage_lbl_hdr)
        stxt.addWidget(self._stage_badge)
        stxt.addWidget(self._stage_desc)
        stxt.addWidget(self._stage_deltas)
        sfl.addLayout(stxt)
        lay.addWidget(stage_frame)
        lay.addStretch()
        return w

    def _build_trends(self):
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(16,14,16,14); lay.setSpacing(12)
        lay.addWidget(QLabel("ROM OVER SESSIONS").also(lambda l: l.setStyleSheet(label_style(GREEN3, 12))))

        self._trend_plot = pg.PlotWidget(background=SURFACE2)
        self._trend_plot.showGrid(x=True, y=True, alpha=0.15)
        self._trend_plot.getAxis("left").setTextPen(GREEN3)
        self._trend_plot.getAxis("bottom").setTextPen(GREEN3)
        self._trend_plot.getAxis("left").setPen(BORDER)
        self._trend_plot.getAxis("bottom").setPen(BORDER)
        self._trend_plot.setLabel("left", "Degrees", color=GREEN3)
        self._trend_plot.setLabel("bottom", "Session #", color=GREEN3)
        self._flex_trend  = self._trend_plot.plot(pen=pg.mkPen(C_FLEX,   width=2), name="Flexion")
        self._abd_trend   = self._trend_plot.plot(pen=pg.mkPen(C_ABD,    width=2), name="Abduction")
        self._rot_trend   = self._trend_plot.plot(pen=pg.mkPen(C_ROT,    width=2), name="Ext Rot")
        self._elbow_trend = self._trend_plot.plot(pen=pg.mkPen(C_ELBOW,  width=2), name="Elbow")
        self._trend_plot.addLegend()
        lay.addWidget(self._trend_plot, stretch=1)
        return w

    def _build_history(self):
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0)
        self._table = QTableWidget()
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            f"QTableWidget{{background:{SURFACE2};alternate-background-color:{SURFACE3};"
            f"color:{TEXT2};border:none;gridline-color:{BORDER};}}"
            f"QTableWidget::item:selected{{background:{GREEN4};color:{GREEN};}}"
        )
        headers = ["#","DATE","EXERCISE","DUR","REPS","FLEX°","ABD°","SMOOTH","PAIN"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        lay.addWidget(self._table)
        return w

    def _build_pain(self):
        w = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(16,14,16,14); lay.setSpacing(12)
        lay.addWidget(QLabel("PAIN SCORE OVER SESSIONS").also(lambda l: l.setStyleSheet(label_style(GREEN3, 12))))
        self._pain_plot = pg.PlotWidget(background=SURFACE2)
        self._pain_plot.showGrid(x=True, y=True, alpha=0.15)
        self._pain_plot.setYRange(0, 10)
        self._pain_plot.getAxis("left").setTextPen(GREEN3)
        self._pain_plot.getAxis("bottom").setTextPen(GREEN3)
        self._pain_plot.getAxis("left").setPen(BORDER)
        self._pain_plot.getAxis("bottom").setPen(BORDER)
        self._pain_pre_curve  = self._pain_plot.plot(pen=pg.mkPen(AMBER, width=2), name="Pre")
        self._pain_post_curve = self._pain_plot.plot(pen=pg.mkPen(RED,   width=2), name="Post")
        self._pain_plot.addLegend()
        lay.addWidget(self._pain_plot, stretch=1)
        return w

    def _do_export(self):
        """Export all session summary data to a timestamped CSV in the data/ folder."""
        out_dir = DB_PATH.parent
        out_dir.mkdir(exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"export_{ts}.csv"

        if not DB_PATH.exists():
            print("[EXPORT] No database found.")
            return
        try:
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT id,date,exercise,duration_s,reps,"
                "max_flex,max_abd,max_ext_rot,max_elbow,"
                "pain_pre,pain_post,csv_file FROM sessions ORDER BY id"
            ).fetchall()
            con.close()
        except Exception as e:
            print(f"[EXPORT] DB error: {e}")
            return

        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Session#", "Date", "Exercise", "Duration(s)", "Reps",
                "MaxFlex(deg)", "MaxAbd(deg)", "MaxExtRot(deg)", "MaxElbow(deg)",
                "PainPre(0-10)", "PainPost(0-10)", "CSVFile"
            ])
            for r in rows:
                writer.writerow(r)

        print(f"[EXPORT] Saved → {out}")
        try:
            import subprocess
            if sys.platform == "win32":
                subprocess.Popen(f'explorer /select,"{out}"')
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(out)])
            else:
                subprocess.Popen(["xdg-open", str(out_dir)])
        except Exception:
            pass

    def refresh(self):
        sessions = _load_sessions()

        with self._state.lock:
            mf = self._state.max_flexion
            ma = self._state.max_abduction
            mr = self._state.max_ext_rot
            me = self._state.max_elbow

        # Overview metrics
        prev = sessions[1] if len(sessions) > 1 else None
        def _delta(cur, prev_val):
            if prev_val is None or prev_val == 0: return "", True
            d = cur - prev_val
            return f"{'↑' if d >= 0 else '↓'} {abs(d):.0f}° vs last", d >= 0

        self._m_flex.set(f"{mf:.0f}°", *_delta(mf, prev[5] if prev else None))
        self._m_abd.set(f"{ma:.0f}°",  *_delta(ma, prev[6] if prev else None))
        self._m_rot.set(f"{mr:.0f}°",  *_delta(mr, prev[7] if prev else None))
        self._m_elbow.set(f"{me:.0f}°",*_delta(me, prev[8] if prev else None))

        # Streak / adherence
        adh = get_streak_and_adherence()
        self._m_streak.set(
            f"{adh['current_streak']} days",
            f"best: {adh['longest_streak']}d", True
        )
        self._m_longest.set(f"{adh['longest_streak']} days")
        self._m_7d.set(str(adh['sessions_7d']))
        self._m_total.set(str(adh['sessions_total']))

        # ADL milestones — bar and label update
        val_map = {"TOUCH HEAD": mf, "REACH OVERHEAD": ma, "PUT ON COAT": mr}
        for name, (bar, val_lbl, thresh, colour) in self._adl.items():
            val = val_map.get(name, 0)
            pct = min(100, int(val / thresh * 100)) if thresh else 0
            done = val >= thresh
            bar.setValue(pct)
            status = "  ✓" if done else ""
            val_lbl.setText(f"{val:.0f}° / {thresh}°{status}")
            col = GREEN if done else (colour if pct > 50 else RED)
            val_lbl.setStyleSheet(label_style(col, 12, bold=True))

        if not sessions: return

        # Stage detection
        sr = detect_stage()
        self._stage_badge.setText(sr.stage)
        self._stage_badge.setStyleSheet(
            f"color:{sr.colour};font-size:18px;font-weight:bold;"
            f"font-family:'Courier New',monospace;letter-spacing:2px;"
        )
        self._stage_desc.setText(sr.desc)
        self._stage_deltas.setText(
            f"ROM Δ {sr.rom_delta:+.1f}°   Pain Δ {sr.pain_delta:+.1f}   "
            f"({sr.n_sessions} sessions analysed)"
        )


        # Trend charts — sessions are DESC from DB, reverse to chronological
        sessions_chron = list(reversed(sessions))
        xs     = list(range(1, len(sessions_chron) + 1))
        flexs  = [r[5] or 0.0 for r in sessions_chron]
        abds   = [r[6] or 0.0 for r in sessions_chron]
        rots   = [r[7] or 0.0 for r in sessions_chron]
        elbows = [r[8] or 0.0 for r in sessions_chron]
        self._flex_trend.setData(xs,  flexs)
        self._abd_trend.setData(xs,   abds)
        self._rot_trend.setData(xs,   rots)
        self._elbow_trend.setData(xs, elbows)

        # Show a note if every value is zero (sessions recorded without live sensors)
        all_zero = all(v == 0.0 for v in flexs + abds + rots + elbows)
        if not hasattr(self, "_trend_zero_lbl"):
            from pyqtgraph import TextItem
            self._trend_zero_lbl = pg.TextItem(
                "No angle data — sessions were recorded without live sensors",
                color=AMBER, anchor=(0.5, 0.5)
            )
            self._trend_plot.addItem(self._trend_zero_lbl)
        self._trend_zero_lbl.setVisible(all_zero)
        if all_zero and xs:
            mid_x = xs[len(xs) // 2]
            self._trend_zero_lbl.setPos(mid_x, 5)

        # Pain chart
        pains_pre  = [r[9]  or 0 for r in sessions_chron]
        pains_post = [r[10] or 0 for r in sessions_chron]
        self._pain_pre_curve.setData(xs,  pains_pre)
        self._pain_post_curve.setData(xs, pains_post)

        # History table
        self._table.setRowCount(len(sessions))
        for i, r in enumerate(sessions):
            vals = [
                str(r[0]),
                (r[1] or "")[:16],
                r[2] or "—",
                f"{r[3]:.0f}s" if r[3] else "—",
                str(r[4]) if r[4] is not None else "—",
                f"{r[5]:.0f}°" if r[5] else "—",
                f"{r[6]:.0f}°" if r[6] else "—",
                "—",
                f"{r[10]}/10" if r[10] is not None else "—",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setForeground(pg.mkColor(TEXT2))
                self._table.setItem(i, j, item)
        self._table.resizeColumnsToContents()

def _also(self, fn):
    fn(self); return self
QLabel.also = _also