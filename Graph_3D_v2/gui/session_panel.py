"""
gui/session_panel.py  —  v4
---------------------------
Right column. Fixes:
  - Session timer stops when session ends
  - Record button: white text on green
  - ROM test enforced before first session
  - Progress shown as a plot over sessions
  - Session data file explorer
  - Record + Play wired to render_widget geometry recording
"""

import os, csv, time, sqlite3
from pathlib import Path
from datetime import datetime

import pyqtgraph as pg
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QListWidget, QListWidgetItem,
    QSpinBox, QDialog, QDialogButtonBox, QFormLayout,
    QTabWidget, QTableWidget, QTableWidgetItem,
    QFileDialog, QMessageBox, QSizePolicy, QScrollArea,
    QAbstractItemView
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

PANEL_BG="#f5f5f8"; BORDER="#d0d0da"; DIM="#888"; TEXT="#1a1a2a"
C_GREEN="#00aa66"; C_RED="#cc2222"; C_AMBER="#cc8800"
C_ACCENT="#1a6aaa"; C_PURPLE="#7722aa"

DATA_DIR = Path(__file__).parent.parent / "data"
DB_PATH  = DATA_DIR / "sessions.db"

EXERCISES = [
    "Shoulder Flexion Raise",
    "Shoulder Abduction",
    "External Rotation",
    "Pendulum Swing",
    "Elbow Curl",
]
EXERCISE_DESC = {
    "Shoulder Flexion Raise": "Raise arm forward.\nHold max for 3 s.\nTarget: flexion ROM.",
    "Shoulder Abduction":     "Raise arm sideways.\nHold max for 3 s.\nTarget: abduction ROM.",
    "External Rotation":      "Rotate arm outward.\nElbow at 90°.\nTarget: rotation ROM.",
    "Pendulum Swing":         "Lean forward, let arm hang and swing gently.",
    "Elbow Curl":             "Bend elbow toward shoulder.\nHold max for 3 s.",
}


def _frame(parent=None):
    f = QFrame(parent)
    f.setStyleSheet(
        f"QFrame{{background:{PANEL_BG};border:1px solid {BORDER};border-radius:4px;}}"
    )
    return f


class QuestionnaireDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title); self.setFixedWidth(300)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self._pain  = QSpinBox(); self._pain.setRange(0,10)
        self._stiff = QSpinBox(); self._stiff.setRange(0,10)
        form.addRow("Pain (0–10):", self._pain)
        form.addRow("Stiffness (0–10):", self._stiff)
        lay.addLayout(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @property
    def pain(self): return self._pain.value()
    @property
    def stiffness(self): return self._stiff.value()


class ProgressPlotDialog(QDialog):
    """Shows ROM progress and pain scores over all sessions as a plot."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Progress Over Sessions")
        self.resize(700, 480)
        lay = QVBoxLayout(self)

        sessions = self._load_sessions()
        if not sessions:
            lay.addWidget(QLabel("No sessions recorded yet."))
            return

        xs = list(range(1, len(sessions)+1))

        plot = pg.PlotWidget(background="w")
        plot.setLabel("left", "Degrees / Score")
        plot.setLabel("bottom", "Session #")
        plot.showGrid(x=True, y=True, alpha=0.3)
        plot.addLegend()

        def _series(col, colour, name):
            vals = [r[col] if r[col] is not None else 0 for r in sessions]
            plot.plot(xs, vals, pen=pg.mkPen(colour, width=2), name=name,
                      symbol='o', symbolSize=6, symbolBrush=colour)

        _series("max_flex",    "#1a6aaa", "Flexion max")
        _series("max_abd",     "#00aa66", "Abduction max")
        _series("max_ext_rot", "#cc8800", "Ext Rot max")
        _series("max_elbow",   "#7722aa", "Elbow max")
        _series("pain_post",   "#cc2222", "Pain (post)")

        lay.addWidget(plot)

        close_btn = QPushButton("Close"); close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

    def _load_sessions(self):
        if not DB_PATH.exists(): return []
        try:
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT max_flex,max_abd,max_ext_rot,max_elbow,pain_post FROM sessions ORDER BY id"
            ).fetchall()
            con.close()
            return [{"max_flex":r[0],"max_abd":r[1],"max_ext_rot":r[2],
                     "max_elbow":r[3],"pain_post":r[4]} for r in rows]
        except Exception:
            return []


class SessionExplorerDialog(QDialog):
    """File-explorer-like view of all recorded sessions."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Session Data Explorer")
        self.resize(820, 540)
        lay = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(
            "QTableWidget{background:#fff;alternate-background-color:#f4f6fa;}"
        )
        lay.addWidget(self._table)

        btn_row = QHBoxLayout()
        self._open_btn = QPushButton("Open CSV")
        self._open_btn.clicked.connect(self._open_csv)
        self._plot_btn = QPushButton("Progress Plot")
        self._plot_btn.clicked.connect(self._show_plot)
        close_btn = QPushButton("Close"); close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._open_btn)
        btn_row.addWidget(self._plot_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._load()

    def _load(self):
        if not DB_PATH.exists():
            self._table.setColumnCount(1)
            self._table.setHorizontalHeaderLabels(["Info"])
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem("No sessions yet."))
            return

        try:
            con = sqlite3.connect(DB_PATH)
            rows = con.execute(
                "SELECT id,date,exercise,duration_s,reps,"
                "max_flex,max_abd,max_ext_rot,max_elbow,"
                "pain_pre,pain_post,csv_file FROM sessions ORDER BY id DESC"
            ).fetchall()
            con.close()
        except Exception as e:
            self._table.setRowCount(0); return

        headers = ["#","Date","Exercise","Duration","Reps",
                   "Flex°","Abd°","ExtRot°","Elbow°",
                   "Pain pre","Pain post","CSV"]
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(rows))
        self._rows = rows

        for i, r in enumerate(rows):
            vals = [
                str(r[0]),
                r[1][:19] if r[1] else "—",
                r[2] or "—",
                f"{r[3]:.0f}s" if r[3] else "—",
                str(r[4]) if r[4] is not None else "—",
                f"{r[5]:.1f}" if r[5] else "—",
                f"{r[6]:.1f}" if r[6] else "—",
                f"{r[7]:.1f}" if r[7] else "—",
                f"{r[8]:.1f}" if r[8] else "—",
                str(r[9]) if r[9] is not None else "—",
                str(r[10]) if r[10] is not None else "—",
                os.path.basename(r[11]) if r[11] else "—",
            ]
            for j, v in enumerate(vals):
                self._table.setItem(i, j, QTableWidgetItem(v))

        self._table.resizeColumnsToContents()

    def _open_csv(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows): return
        csv_path = self._rows[row][11]
        if csv_path and Path(csv_path).exists():
            import subprocess, sys
            if sys.platform == "win32":
                os.startfile(csv_path)
            elif sys.platform == "darwin":
                subprocess.call(["open", csv_path])
            else:
                subprocess.call(["xdg-open", csv_path])
        else:
            QMessageBox.warning(self, "Not Found", "CSV file not found.")

    def _show_plot(self):
        ProgressPlotDialog(self).exec_()


class SessionPanel(QWidget):
    exercise_changed = pyqtSignal(str)
    session_started  = pyqtSignal()
    session_ended    = pyqtSignal()
    record_toggled   = pyqtSignal(bool)

    def __init__(self, state, recorder, rep_detector, parent=None):
        super().__init__(parent)
        self._state     = state
        self._recorder  = recorder
        self._repdet    = rep_detector
        self._recording = False
        self._t_session = None
        self._session_running = False
        self._pain_pre  = 0
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4,4,4,4); lay.setSpacing(5)

        # ── Session info ──────────────────────────────────────────────────────
        sf = _frame(); sl = QVBoxLayout(sf)
        sl.setContentsMargins(8,6,8,6); sl.setSpacing(3)
        self._sess_time = QLabel("00:00")
        self._sess_time.setStyleSheet(f"color:{TEXT}; font-size:22px; font-weight:bold;")
        self._reps_lbl = QLabel("Reps: 0")
        self._reps_lbl.setStyleSheet(f"color:{C_GREEN}; font-size:13px; font-weight:bold;")
        sl.addWidget(self._sess_time); sl.addWidget(self._reps_lbl)
        lay.addWidget(sf)

        # ── Exercise library ──────────────────────────────────────────────────
        lbl = QLabel("EXERCISE")
        lbl.setStyleSheet(f"color:{DIM}; font-size:9px; font-weight:bold;")
        lay.addWidget(lbl)
        ef = _frame(); el = QVBoxLayout(ef)
        el.setContentsMargins(4,4,4,4); el.setSpacing(2)
        self._ex_list = QListWidget()
        self._ex_list.setStyleSheet(
            f"QListWidget{{background:#fff;border:none;color:{TEXT};font-size:10px;}}"
            f"QListWidget::item:selected{{background:#cce4ff;color:{C_ACCENT};}}"
        )
        self._ex_list.setFixedHeight(105)
        for ex in EXERCISES:
            self._ex_list.addItem(QListWidgetItem(ex))
        self._ex_list.setCurrentRow(0)
        self._ex_list.currentTextChanged.connect(self._on_exercise_changed)
        el.addWidget(self._ex_list)
        self._desc_lbl = QLabel(EXERCISE_DESC[EXERCISES[0]])
        self._desc_lbl.setStyleSheet(f"color:{DIM}; font-size:9px;")
        self._desc_lbl.setWordWrap(True)
        el.addWidget(self._desc_lbl)
        lay.addWidget(ef)

        # ── Progress vs last session ──────────────────────────────────────────
        lbl2 = QLabel("PROGRESS vs LAST SESSION")
        lbl2.setStyleSheet(f"color:{DIM}; font-size:9px; font-weight:bold;")
        lay.addWidget(lbl2)
        pf = _frame(); pl = QHBoxLayout(pf)
        pl.setContentsMargins(8,6,8,6); pl.setSpacing(6)
        self._prog_labels = {}
        for key, colour in [("Flex",C_ACCENT),("Abd",C_GREEN),("Rot",C_AMBER),("Elbow",C_PURPLE)]:
            b = QVBoxLayout()
            k = QLabel(key); k.setStyleSheet(f"color:{DIM};font-size:9px;")
            v = QLabel("—"); v.setStyleSheet(f"color:{colour};font-size:11px;font-weight:bold;")
            b.addWidget(k); b.addWidget(v)
            pl.addLayout(b)
            self._prog_labels[key] = v
        lay.addWidget(pf)

        # ── Recording ─────────────────────────────────────────────────────────
        lbl3 = QLabel("RECORDING")
        lbl3.setStyleSheet(f"color:{DIM}; font-size:9px; font-weight:bold;")
        lay.addWidget(lbl3)
        rf = _frame(); rl = QHBoxLayout(rf)
        rl.setContentsMargins(8,6,8,6); rl.setSpacing(6)
        self._rec_btn = QPushButton("⏺  Record")
        self._rec_btn.setFixedHeight(26)
        # White text, green background
        self._rec_btn.setStyleSheet(
            "QPushButton{background:#007744;color:#ffffff;border:none;"
            " border-radius:3px;font-size:10px;font-weight:bold;padding:2px 10px;}"
            "QPushButton:hover{background:#009955;}"
        )
        self._rec_btn.clicked.connect(self._toggle_record)
        rl.addWidget(self._rec_btn)
        lay.addWidget(rf)

        lay.addStretch()

        # ── Data explorer button ──────────────────────────────────────────────
        explorer_btn = QPushButton("Session Data Explorer")
        explorer_btn.setStyleSheet(
            f"QPushButton{{background:#e8eaf6;color:{C_ACCENT};border:1px solid #b0b8e0;"
            f" border-radius:3px;font-size:10px;padding:4px 8px;}}"
            f"QPushButton:hover{{background:#d0d8f0;}}"
        )
        explorer_btn.clicked.connect(self._open_explorer)
        lay.addWidget(explorer_btn)

        # ── Session control ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Session")
        self._end_btn   = QPushButton("End Session")
        self._start_btn.setFixedHeight(30); self._end_btn.setFixedHeight(30)
        self._start_btn.setStyleSheet(
            "QPushButton{background:#00aa66;color:#fff;font-weight:bold;"
            " border:none;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#008855;}"
            "QPushButton:disabled{background:#c0c0c0;color:#888;}"
        )
        self._end_btn.setStyleSheet(
            "QPushButton{background:#cc2222;color:#fff;font-weight:bold;"
            " border:none;border-radius:4px;font-size:11px;}"
            "QPushButton:hover{background:#aa1111;}"
            "QPushButton:disabled{background:#c0c0c0;color:#888;}"
        )
        self._end_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start)
        self._end_btn.clicked.connect(self._on_end)
        btn_row.addWidget(self._start_btn); btn_row.addWidget(self._end_btn)
        lay.addLayout(btn_row)
        self._update_progress()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_exercise_changed(self, name):
        self._desc_lbl.setText(EXERCISE_DESC.get(name, ""))
        self._repdet.set_exercise(name)
        self.exercise_changed.emit(name)

    def _on_start(self):
        # Check ROM measured before allowing session start
        rom_ok = False
        if self._state:
            with self._state.lock:
                rom_ok = getattr(self._state, 'rom_measured', False)
        if not rom_ok:
            QMessageBox.warning(self, "ROM Not Measured",
                "Please measure your Range of Motion first.\n\n"
                "Press 'Measure ROM' in the 3D view, move your arm as far "
                "as comfortably possible in each direction, then press "
                "'Stop ROM' to record your baseline.")
            return

        dlg = QuestionnaireDialog("Pre-Session Questionnaire", self)
        self._pain_pre = dlg.pain if dlg.exec_() == QDialog.Accepted else 0

        exercise = (self._ex_list.currentItem().text()
                    if self._ex_list.currentItem() else "")
        self._recorder.start_session(exercise=exercise, pain_pre=self._pain_pre)
        self._repdet.reset(); self._repdet.set_exercise(exercise)
        self._t_session = time.monotonic()
        self._session_running = True
        self._start_btn.setEnabled(False); self._end_btn.setEnabled(True)
        with self._state.lock:
            self._state.session_active   = True
            self._state.session_reps     = 0
            self._state.current_exercise = exercise
        self.session_started.emit()

    def _on_end(self):
        dlg = QuestionnaireDialog("Post-Session Questionnaire", self)
        pain_post = dlg.pain if dlg.exec_() == QDialog.Accepted else 0
        self._recorder.end_session(pain_post=pain_post)
        self._session_running = False   # stop timer
        self._start_btn.setEnabled(True); self._end_btn.setEnabled(False)
        # Stop recording if active
        if self._recording:
            self._toggle_record()
        with self._state.lock:
            self._state.session_active = False
        self._update_progress()
        self.session_ended.emit()

    def _toggle_record(self):
        self._recording = not self._recording
        if self._recording:
            self._rec_btn.setText("⏹  Stop Rec")
            self._rec_btn.setStyleSheet(
                "QPushButton{background:#cc2222;color:#fff;border:none;"
                " border-radius:3px;font-size:10px;font-weight:bold;padding:2px 10px;}"
                "QPushButton:hover{background:#aa1111;}"
            )
        else:
            self._rec_btn.setText("⏺  Record")
            self._rec_btn.setStyleSheet(
                "QPushButton{background:#007744;color:#ffffff;border:none;"
                " border-radius:3px;font-size:10px;font-weight:bold;padding:2px 10px;}"
                "QPushButton:hover{background:#009955;}"
            )
        self.record_toggled.emit(self._recording)

    def _open_explorer(self):
        SessionExplorerDialog(self).exec_()

    def _update_progress(self):
        last = self._recorder.get_last_session_maxima()
        if not last:
            for v in self._prog_labels.values(): v.setText("—")
            return
        with self._state.lock:
            mf=self._state.max_flexion; ma=self._state.max_abduction
            mr=self._state.max_ext_rot; me=self._state.max_elbow

        def _d(cur, prev):
            d = cur - prev
            col = C_GREEN if d >= 0 else C_RED
            return f"{d:+.0f}°", col

        for key, cur, prev_key in [
            ("Flex", mf, "max_flex"), ("Abd", ma, "max_abd"),
            ("Rot",  mr, "max_ext_rot"), ("Elbow", me, "max_elbow")
        ]:
            txt, col = _d(cur, last.get(prev_key, 0))
            self._prog_labels[key].setText(txt)
            self._prog_labels[key].setStyleSheet(
                f"color:{col};font-size:11px;font-weight:bold;"
            )

    # ── Per-frame refresh ─────────────────────────────────────────────────────

    def refresh(self):
        # Timer only ticks while session is running
        if self._session_running and self._t_session is not None:
            elapsed = int(time.monotonic() - self._t_session)
            m, s = divmod(elapsed, 60)
            self._sess_time.setText(f"{m:02d}:{s:02d}")

        with self._state.lock:
            reps = self._state.session_reps
        self._reps_lbl.setText(f"Reps: {reps}")
