"""
gui/panels/settings_panel.py
-----------------------------
Panel 4 — Settings.

All controls write directly to AppState so changes take effect immediately
without a restart. Sections:
  · Patient Profile    — affected side, ROM readout
  · Haptic Feedback    — per-event on/off toggles (live)
  · Exercise Defaults  — trunk lean limit slider, ROM goal fraction slider,
                         default sets / reps (pre-fill exercise panel)
  · Data               — export CSV, clear all session data
"""

import csv, os, sqlite3, subprocess, sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QSlider, QMessageBox
)
from PyQt5.QtCore import Qt

from ble.ble_state import AppState
from gui.styles import *

DB_PATH = Path(__file__).parent.parent.parent / "data" / "sessions.db"


# ── Shared primitives ─────────────────────────────────────────────────────────

class _Div(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background:{BORDER};")


class _SectionHeader(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setStyleSheet(
            f"background:{SURFACE2};"
            f"border-bottom:1px solid {BORDER};"
            f"border-top:1px solid {BORDER};"
        )
        lay = QHBoxLayout(self); lay.setContentsMargins(16, 0, 16, 0)
        lay.addWidget(
            QLabel(f"── {title}").also(
                lambda l: l.setStyleSheet(label_style(GREEN3, 11))
            )
        )


class _Row(QWidget):
    """Label + subtitle on the left, optional control on the right."""
    def __init__(self, title, subtitle, control=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(52)
        self.setStyleSheet(f"background:{SURFACE};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)
        txt = QVBoxLayout(); txt.setSpacing(2)
        t = QLabel(title); t.setStyleSheet(label_style(TEXT2, 13, bold=True))
        s = QLabel(subtitle); s.setStyleSheet(label_style(GREEN3, 11))
        txt.addWidget(t); txt.addWidget(s)
        lay.addLayout(txt, stretch=1)
        if control:
            lay.addWidget(control)


class Toggle(QWidget):
    """Simple ON/OFF pill that writes to a state attribute immediately."""

    def __init__(self, state: AppState, attr: str, parent=None):
        super().__init__(parent)
        self._state = state
        self._attr  = attr
        with state.lock:
            self._on = getattr(state, attr, True)
        self.setFixedSize(50, 26)
        self.setCursor(Qt.PointingHandCursor)
        self._lbl = QLabel(self)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setGeometry(0, 0, 50, 26)
        self._lbl.setStyleSheet("background:transparent;font-size:10px;font-weight:bold;")
        self._repaint()

    def mousePressEvent(self, _):
        self._on = not self._on
        with self._state.lock:
            setattr(self._state, self._attr, self._on)
        self._repaint()

    def _repaint(self):
        if self._on:
            self.setStyleSheet(
                f"background:{GREEN};border:1px solid {GREEN};"
                f"border-radius:13px;"
            )
            self._lbl.setText("ON")
            self._lbl.setStyleSheet(
                f"background:transparent;color:#000000;"
                f"font-size:10px;font-weight:bold;"
            )
        else:
            self.setStyleSheet(
                f"background:{SURFACE3};border:1px solid {BORDER};"
                f"border-radius:13px;"
            )
            self._lbl.setText("OFF")
            self._lbl.setStyleSheet(
                f"background:transparent;color:{TEXT3};"
                f"font-size:10px;font-weight:bold;"
            )


class _LiveSlider(QWidget):
    """Slider that writes a float to state.attr in [min_v, max_v] and shows the value."""

    def __init__(self, state: AppState, attr: str,
                 min_v: float, max_v: float, step: float,
                 fmt: str = "{:.0f}", suffix: str = "",
                 parent=None):
        super().__init__(parent)
        self._state = state
        self._attr  = attr
        self._min   = min_v
        self._max   = max_v
        self._step  = step
        self._fmt   = fmt
        self._suffix = suffix

        with state.lock:
            cur = float(getattr(state, attr, min_v))

        n_steps = int(round((max_v - min_v) / step))
        cur_tick = int(round((cur - min_v) / step))

        self._sl = QSlider(Qt.Horizontal)
        self._sl.setRange(0, n_steps)
        self._sl.setValue(cur_tick)
        self._sl.setFixedWidth(100)
        self._sl.setStyleSheet(
            f"QSlider::groove:horizontal{{background:{SURFACE3};height:4px;border-radius:2px;}}"
            f"QSlider::handle:horizontal{{background:{GREEN};width:14px;height:14px;"
            f"margin:-5px 0;border-radius:7px;}}"
            f"QSlider::sub-page:horizontal{{background:{GREEN3};border-radius:2px;}}"
        )
        self._lbl = QLabel()
        self._lbl.setFixedWidth(48)
        self._lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._lbl.setStyleSheet(label_style(GREEN, 12, bold=True))
        self._refresh_label(cur_tick)
        self._sl.valueChanged.connect(self._on_change)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._sl)
        lay.addWidget(self._lbl)

    def _on_change(self, tick: int):
        val = self._min + tick * self._step
        with self._state.lock:
            setattr(self._state, self._attr, val)
        self._refresh_label(tick)

    def _refresh_label(self, tick: int):
        val = self._min + tick * self._step
        self._lbl.setText(self._fmt.format(val) + self._suffix)


class _Stepper(QWidget):
    """Integer stepper that writes to a state attribute immediately."""

    def __init__(self, state: AppState, attr: str,
                 min_v: int, max_v: int, parent=None):
        super().__init__(parent)
        self._state = state
        self._attr  = attr
        self._min   = min_v
        self._max   = max_v
        with state.lock:
            self._val = int(getattr(state, attr, min_v))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._minus = QPushButton("−")
        self._minus.setFixedSize(26, 26)
        self._minus.setStyleSheet(btn_style(SURFACE3, GREEN3, BORDER))
        self._disp = QLabel(str(self._val))
        self._disp.setFixedWidth(30)
        self._disp.setAlignment(Qt.AlignCenter)
        self._disp.setStyleSheet(label_style(GREEN, 14, bold=True))
        self._plus = QPushButton("+")
        self._plus.setFixedSize(26, 26)
        self._plus.setStyleSheet(btn_style(SURFACE3, GREEN3, BORDER))

        self._minus.clicked.connect(self._dec)
        self._plus.clicked.connect(self._inc)

        lay.addWidget(self._minus)
        lay.addWidget(self._disp)
        lay.addWidget(self._plus)

    def _dec(self):
        if self._val > self._min:
            self._val -= 1
            self._commit()

    def _inc(self):
        if self._val < self._max:
            self._val += 1
            self._commit()

    def _commit(self):
        self._disp.setText(str(self._val))
        with self._state.lock:
            setattr(self._state, self._attr, self._val)


# ── Side toggle (two-button exclusive) ───────────────────────────────────────

class _SideToggle(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self._state = state
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._r = QPushButton("RIGHT")
        self._l = QPushButton("LEFT")
        for b in (self._r, self._l):
            b.setFixedHeight(26)
        self._r.clicked.connect(lambda: self._select("right"))
        self._l.clicked.connect(lambda: self._select("left"))
        lay.addWidget(self._r)
        lay.addWidget(self._l)
        with state.lock:
            side = state.affected_side
        self._apply(side)

    def _select(self, side: str):
        with self._state.lock:
            self._state.affected_side = side
        self._apply(side)

    def _apply(self, side: str):
        active = (
            f"QPushButton{{background:{GREEN};color:#ffffff;border:1px solid {GREEN};"
            f"border-radius:3px;padding:3px 10px;font-size:12px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{GREEN2};}}"
        )
        inactive = btn_style(SURFACE2, TEXT3, BORDER)
        self._r.setStyleSheet(active   if side == "right" else inactive)
        self._l.setStyleSheet(inactive if side == "right" else active)


# ── Main panel ────────────────────────────────────────────────────────────────

class SettingsPanel(QWidget):
    def __init__(self, state: AppState, ble, calibration, parent=None):
        super().__init__(parent)
        self._state = state
        self._ble   = ble
        self._cal   = calibration
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Title bar
        tb = QWidget(); tb.setFixedHeight(36)
        tb.setStyleSheet(f"background:{SURFACE};border-bottom:1px solid {BORDER};")
        tl = QHBoxLayout(tb); tl.setContentsMargins(16, 0, 16, 0)
        tl.addWidget(
            QLabel("SETTINGS").also(lambda l: l.setStyleSheet(label_style(GREEN, 12, bold=True)))
        )
        tl.addStretch()
        root.addWidget(tb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background:{BG};border:none;")
        inner = QWidget(); inner.setStyleSheet(f"background:{BG};")
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 20)
        il.setSpacing(0)

        # ── Patient Profile ───────────────────────────────────────────────────
        il.addWidget(_SectionHeader("PATIENT PROFILE"))
        il.addWidget(_Div())

        il.addWidget(_Row(
            "AFFECTED SIDE", "Recorded with session data (mirroring not yet applied)",
            _SideToggle(self._state)
        ))
        il.addWidget(_Div())

        self._rom_lbl = QLabel("—")
        self._rom_lbl.setStyleSheet(label_style(GREEN, 11))
        self._rom_lbl.setWordWrap(True)
        rom_row = _Row("MEASURED ROM", "From last ROM measurement", self._rom_lbl)
        rom_row.setFixedHeight(68)
        il.addWidget(rom_row)
        il.addWidget(_Div())

        # ── Haptic Feedback ───────────────────────────────────────────────────
        il.addWidget(_SectionHeader("HAPTIC FEEDBACK"))

        haptic_rows = [
            ("haptic_rep",       "REP COMPLETE",       "Vibrate when a full rep is detected"),
            ("haptic_hold",      "HOLD COMPLETE",      "Vibrate when a timed stretch is done"),
            ("haptic_rom",       "ROM LIMIT",          "Buzz when approaching measured ROM"),
            ("haptic_deviation", "PLANE DEVIATION",    "Buzz when movement leaves intended plane"),
            ("haptic_trunk",     "TRUNK LEAN",         "Buzz when torso tilts too much"),
        ]
        for attr, label, sub in haptic_rows:
            il.addWidget(_Div())
            il.addWidget(_Row(label, sub, Toggle(self._state, attr)))
        il.addWidget(_Div())

        # ── Exercise Defaults ─────────────────────────────────────────────────
        il.addWidget(_SectionHeader("EXERCISE DEFAULTS"))
        il.addWidget(_Div())

        il.addWidget(_Row(
            "DEFAULT SETS", "Pre-filled when starting any exercise",
            _Stepper(self._state, "default_sets", 1, 10)
        ))
        il.addWidget(_Div())
        il.addWidget(_Row(
            "DEFAULT REPS", "Pre-filled when starting any exercise",
            _Stepper(self._state, "default_reps", 1, 30)
        ))
        il.addWidget(_Div())
        il.addWidget(_Row(
            "TRUNK LEAN LIMIT",
            "Degrees of torso tilt before correction haptic fires",
            _LiveSlider(self._state, "trunk_lean_limit", 5, 25, 1, suffix="°")
        ))
        il.addWidget(_Div())

        # ── Data ──────────────────────────────────────────────────────────────
        il.addWidget(_SectionHeader("DATA"))
        il.addWidget(_Div())

        db_short = f".../{DB_PATH.parent.name}/{DB_PATH.name}"
        db_row = _Row("DATABASE  [ SQLite ]", db_short)
        db_row.setToolTip(str(DB_PATH))
        il.addWidget(db_row)
        il.addWidget(_Div())

        export_btn = QPushButton("EXPORT ALL → CSV")
        export_btn.setFixedHeight(28)
        export_btn.setStyleSheet(btn_style())
        export_btn.clicked.connect(self._do_export)
        il.addWidget(_Row("EXPORT SESSIONS", "Save all sessions as a CSV file", export_btn))
        il.addWidget(_Div())

        clear_btn = QPushButton("CLEAR ALL DATA")
        clear_btn.setFixedHeight(28)
        clear_btn.setStyleSheet(btn_style(SURFACE2, RED, RED))
        clear_btn.clicked.connect(self._do_clear)
        il.addWidget(_Row("CLEAR SESSION DATA", "Permanently delete all recorded sessions", clear_btn))
        il.addWidget(_Div())

        il.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)

    # ── Export ────────────────────────────────────────────────────────────────

    def _do_export(self):
        out_dir = DB_PATH.parent
        out_dir.mkdir(exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"export_{ts}.csv"

        if not DB_PATH.exists():
            print("[EXPORT] No database found."); return
        try:
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT id,date,exercise,duration_s,reps,"
                "max_flex,max_abd,max_ext_rot,max_elbow,"
                "pain_pre,pain_post FROM sessions ORDER BY id"
            ).fetchall()
            con.close()
        except Exception as e:
            print(f"[EXPORT] DB error: {e}"); return

        with open(out, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Session#", "Date", "Exercise", "Duration(s)", "Reps",
                "MaxFlex(deg)", "MaxAbd(deg)", "MaxExtRot(deg)", "MaxElbow(deg)",
                "PainPre(0-10)", "PainPost(0-10)"
            ])
            for r in rows:
                writer.writerow(r)

        print(f"[EXPORT] Saved → {out}")
        try:
            if sys.platform == "win32":
                os.startfile(str(out_dir))
            elif sys.platform == "darwin":
                subprocess.call(["open", str(out_dir)])
            else:
                subprocess.call(["xdg-open", str(out_dir)])
        except Exception:
            pass

    # ── Clear ─────────────────────────────────────────────────────────────────

    def _do_clear(self):
        reply = QMessageBox.question(
            self, "Clear session data",
            "This will permanently delete all recorded sessions.\n\nAre you sure?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )
        if reply != QMessageBox.Yes:
            return
        try:
            con = sqlite3.connect(DB_PATH)
            con.execute("DELETE FROM sessions")
            con.commit()
            con.close()
            print("[SETTINGS] All session data cleared.")
        except Exception as e:
            print(f"[SETTINGS] Clear failed: {e}")

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        with self._state.lock:
            rf       = self._state.rom_flex_limit
            ra       = self._state.rom_abd_limit
            rr       = self._state.rom_rot_limit
            ri       = self._state.rom_int_rot_limit
            re       = self._state.rom_elbow_limit
            measured = self._state.rom_measured
        if measured:
            self._rom_lbl.setText(
                f"FLEX {rf:.0f}°  ABD {ra:.0f}°  "
                f"EXT ROT {rr:.0f}°  INT ROT {ri:.0f}°  ELBOW {re:.0f}°"
            )
            self._rom_lbl.setStyleSheet(label_style(GREEN, 11))
        else:
            self._rom_lbl.setText("NOT MEASURED")
            self._rom_lbl.setStyleSheet(label_style(RED, 11))


def _also(self, fn):
    fn(self); return self
QLabel.also = _also