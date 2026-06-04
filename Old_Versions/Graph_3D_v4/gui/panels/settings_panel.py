import csv, os, subprocess, sys
from datetime import datetime
"""
gui/panels/settings_panel.py
-----------------------------
Panel 4 — Settings.

Sub-sections: Patient Profile · Thresholds · Haptic · Data & Export · Evaluation Tools
"""

from pathlib import Path
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSlider, QSizePolicy
)
from PyQt5.QtCore import Qt

from ble.ble_state import AppState
from gui.styles import *


class SettingsRow(QWidget):
    def __init__(self, title: str, subtitle: str, control: QWidget = None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)
        txt = QVBoxLayout(); txt.setSpacing(2)
        t = QLabel(title); t.setStyleSheet(label_style(TEXT2, 14, bold=True))
        s = QLabel(subtitle); s.setStyleSheet(label_style(GREEN3, 12))
        txt.addWidget(t); txt.addWidget(s)
        lay.addLayout(txt, stretch=1)
        if control:
            lay.addWidget(control)


class Toggle(QWidget):
    def __init__(self, initial=True, parent=None):
        super().__init__(parent)
        self._on = initial
        self.setFixedSize(46, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._update()

    def mousePressEvent(self, _):
        self._on = not self._on; self._update()

    def _update(self):
        col = GREEN if self._on else GREEN3
        self.setStyleSheet(
            f"background:{col if self._on else SURFACE3};"
            f"border:1px solid {col};border-radius:12px;"
        )

    @property
    def value(self): return self._on


class SectionHeader(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setStyleSheet(f"background:{SURFACE2};border-bottom:1px solid {BORDER};border-top:1px solid {BORDER};")
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 0, 16, 0)
        lay.addWidget(QLabel(f"── {title}").also(lambda l: l.setStyleSheet(label_style(GREEN3, 11))))


class SettingsPanel(QWidget):
    def __init__(self, state: AppState, ble, calibration, parent=None):
        super().__init__(parent)
        self._state = state
        self._ble   = ble
        self._cal   = calibration
        self._build()

    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        tb = QWidget(); tb.setFixedHeight(36)
        tb.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(tb); tl.setContentsMargins(16,0,16,0)
        tl.addWidget(QLabel("SETTINGS").also(lambda l: l.setStyleSheet(label_style(GREEN, 12, bold=True))))
        tl.addStretch()
        root.addWidget(tb)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background:{BG};border:none;")
        inner = QWidget(); inner.setStyleSheet(f"background:{BG};")
        il = QVBoxLayout(inner); il.setContentsMargins(0,0,0,20); il.setSpacing(0)

        # ── Patient Profile ───────────────────────────────────────────────────
        il.addWidget(SectionHeader("PATIENT PROFILE"))
        il.addWidget(self._div())

        il.addWidget(self._row("AFFECTED SIDE", "Which shoulder is being treated",
            self._side_toggle()))
        #il.addWidget(self._div())
        il.addWidget(self._row("CONDITION STAGE", "Auto-detected from ROM + pain trend",
            QLabel("FROZEN (auto)").also(lambda l: l.setStyleSheet(label_style(AMBER, 11)))))
        #il.addWidget(self._div())
        self._rom_lbl = QLabel("—")
        self._rom_lbl.setStyleSheet(label_style(GREEN, 11))
        il.addWidget(self._row("MEASURED ROM", "From last ROM measurement", self._rom_lbl))

        # ── Thresholds ────────────────────────────────────────────────────────
        il.addWidget(self._div())
        il.addWidget(SectionHeader("EXERCISE THRESHOLDS"))
        il.addWidget(self._div())

        il.addWidget(self._row("ROM TARGET FRACTION",
            "Goal sphere placed at X% of measured ROM",
            self._slider_pair(90, "90%")))
        #il.addWidget(self._div())
        il.addWidget(self._row("CORRIDOR WIDTH",
            "Degrees off-plane before deviation alert",
            self._slider_pair(15, "±15°")))
        #il.addWidget(self._div())
        il.addWidget(self._row("FATIGUE SENSITIVITY",
            "Rep-to-rep ROM drop to flag fatigue",
            self._slider_pair(5, "5°/rep")))
        #il.addWidget(self._div())
        il.addWidget(self._row("TRUNK LEAN LIMIT",
            "Max torso tilt before correction haptic",
            self._slider_pair(10, "10°")))

        # ── Haptic ────────────────────────────────────────────────────────────
        il.addWidget(self._div())
        il.addWidget(SectionHeader("HAPTIC FEEDBACK"))
        il.addWidget(self._div())

        self._hap = {}
        for key, label, sub in [
            ("rep",      "REP COMPLETE",     "Vibrate when a full rep is detected"),
            ("rom",      "ROM LIMIT WARNING", "Buzz at 90% of ROM limit"),
            ("dev",      "DEVIATION ALERT",   "Buzz when movement leaves corridor"),
            ("fatigue",  "FATIGUE REMINDER",  "Buzz when fatigue drop detected"),
        ]:
            tog = Toggle(True if key != "fatigue" else False)
            self._hap[key] = tog
            il.addWidget(self._row(label, sub, tog))
            #il.addWidget(self._div())

        # ── Data & Export ─────────────────────────────────────────────────────
        il.addWidget(self._div())
        il.addWidget(SectionHeader("DATA & EXPORT"))
        il.addWidget(self._div())

        db_path = Path(__file__).parent.parent.parent / "data" / "sessions.db"
        short_path = f".../{db_path.parent.name}/{db_path.name}"
        db_row = self._row("DATABASE  [ SQLite ]", short_path, None)
        db_row.setToolTip(str(db_path))
        il.addWidget(db_row)
        #il.addWidget(self._div())
        export_btn = QPushButton("EXPORT ALL → CSV")
        export_btn.setFixedHeight(28)
        export_btn.setStyleSheet(btn_style())
        export_btn.clicked.connect(self._do_export)
        il.addWidget(self._row("EXPORT", "All sessions as CSV + summary", export_btn))

        # ── Evaluation Tools ──────────────────────────────────────────────────
        il.addWidget(self._div())
        il.addWidget(SectionHeader("EVALUATION TOOLS"))
        il.addWidget(self._div())

        lat_btn = QPushButton("RUN LATENCY TEST")
        lat_btn.setFixedHeight(28); lat_btn.setStyleSheet(btn_style())
        il.addWidget(self._row("LATENCY MEASUREMENT",
            "Log BLE arrival vs GUI render — NFR-01 ≤100ms", lat_btn))
        #il.addWidget(self._div())
        gon_btn = QPushButton("START LOGGER")
        gon_btn.setFixedHeight(28); gon_btn.setStyleSheet(btn_style())
        il.addWidget(self._row("GONIOMETER COMPARISON",
            "Hold arm at known angle, record error — NFR-02 ≤5°", gon_btn))
        il.addWidget(self._div())

        il.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

    def _row(self, title, subtitle, control=None):
        r = SettingsRow(title, subtitle, control)
        r.setStyleSheet(f"background:{SURFACE};")
        return r

    def _div(self):
        d = QFrame(); d.setFixedHeight(1)
        d.setStyleSheet(f"background:{BORDER};")
        return d

    def _side_toggle(self):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(4)
        self._right_btn = QPushButton("RIGHT")
        self._left_btn  = QPushButton("LEFT")
        self._right_btn.setFixedHeight(26); self._left_btn.setFixedHeight(26)
        self._right_btn.setStyleSheet(btn_style(GREEN4, GREEN, GREEN3))
        self._left_btn.setStyleSheet(btn_style(SURFACE2, TEXT3, BORDER))
        lay.addWidget(self._right_btn); lay.addWidget(self._left_btn)
        return w

    def _slider_pair(self, val, label):
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(6)
        sl = QSlider(Qt.Horizontal); sl.setFixedWidth(80)
        sl.setStyleSheet(
            f"QSlider::groove:horizontal{{background:{SURFACE3};height:4px;border-radius:2px;}}"
            f"QSlider::handle:horizontal{{background:{GREEN};width:12px;height:12px;"
            f"margin:-4px 0;border-radius:6px;}}"
            f"QSlider::sub-page:horizontal{{background:{GREEN3};border-radius:2px;}}"
        )
        lbl = QLabel(label); lbl.setStyleSheet(label_style(GREEN, 11, bold=True))
        lbl.setFixedWidth(46)
        lay.addWidget(sl); lay.addWidget(lbl)
        return w

    def _do_export(self):
        """Export all session data to a timestamped CSV summary file."""
        import sqlite3
        db = Path(__file__).parent.parent.parent / "data" / "sessions.db"
        out_dir = Path(__file__).parent.parent.parent / "data"
        out_dir.mkdir(exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"export_{ts}.csv"

        if not db.exists():
            print("[EXPORT] No database found."); return

        try:
            con = sqlite3.connect(db)
            rows = con.execute(
                "SELECT id,date,exercise,duration_s,reps,"
                "max_flex,max_abd,max_ext_rot,max_elbow,"
                "pain_pre,pain_post,csv_file FROM sessions ORDER BY id"
            ).fetchall()
            con.close()
        except Exception as e:
            print(f"[EXPORT] DB error: {e}"); return

        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Session#","Date","Exercise","Duration(s)","Reps",
                "MaxFlex(deg)","MaxAbd(deg)","MaxExtRot(deg)","MaxElbow(deg)",
                "PainPre(0-10)","PainPost(0-10)","CSVFile"
            ])
            for r in rows:
                writer.writerow(r)

        print(f"[EXPORT] Saved → {out}")
        # Open containing folder
        try:
            if sys.platform == "win32":
                os.startfile(str(out_dir))
            elif sys.platform == "darwin":
                subprocess.call(["open", str(out_dir)])
            else:
                subprocess.call(["xdg-open", str(out_dir)])
        except Exception:
            pass

    def refresh(self):
        with self._state.lock:
            rf = self._state.rom_flex_limit
            ra = self._state.rom_abd_limit
            measured = getattr(self._state, 'rom_measured', False)
        if measured:
            self._rom_lbl.setText(f"FLEX {rf:.0f}°  ABD {ra:.0f}°")
        else:
            self._rom_lbl.setText("NOT MEASURED")
            self._rom_lbl.setStyleSheet(label_style(RED, 11))

def _also(self, fn):
    fn(self); return self
QLabel.also = _also
