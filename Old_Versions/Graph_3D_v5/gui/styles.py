"""
gui/styles.py
-------------
Single source of truth for all colours, fonts, and Qt stylesheets.

Theme: retro terminal green on black — phosphor CRT aesthetic.
Every panel imports from here. Change one value, everything updates.
"""

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#f8f8f8"     # off-white background
SURFACE     = "#ffffff"     # pure white panels
SURFACE2    = "#f2f2f2"     # card backgrounds
SURFACE3    = "#e8e8e8"     # hover/active surfaces
BORDER      = "#d8d8d8"     # light grey border
BORDER2     = "#c0c0c0"     # slightly darker border for emphasis

# Greys — main text family
GREEN       = "#1a1a1a"     # primary text (replaces bright green)
GREEN2      = "#333333"     # secondary text
GREEN3      = "#888888"     # muted text, labels
GREEN4      = "#eeeeee"     # subtle backgrounds, hover
GREEN5      = "#22aa66"     # subtle backgrounds, hover
GREEN_DIM   = "#cccccc"     # disabled / inactive

TEXT        = "#1a1a1a"     # main readable text
TEXT2       = "#444444"     # secondary text
TEXT3       = "#999999"     # muted / dim text
TEXT_BRIGHT = "#000000"     # bold highlights, numbers

# Accents
AMBER       = "#e07800"     # warnings, pain scores
RED         = "#cc2222"     # errors, disconnect, end session
CYAN        = "#0077cc"     # special highlight (symmetry, ROM)

# pyqtgraph plot line colours
C_FLEX      = "#2266cc"     # flexion     — blue
C_ABD       = "#22aa66"     # abduction   — green
C_ROT       = "#e07800"     # ext rot     — amber
C_ELBOW     = "#9933cc"     # elbow       — purple

# ── Sizes ─────────────────────────────────────────────────────────────────────
SIDEBAR_W   = 68            # px — left nav sidebar width
FONT_MONO   = "'Courier New', 'Consolas', monospace"
FONT_BODY   = "'Courier New', 'Consolas', monospace"   # keep mono throughout

# ── Global Qt stylesheet ──────────────────────────────────────────────────────
# Applied once in main_window.py. All panels inherit it.
GLOBAL_QSS = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Courier New', Consolas, monospace;
    font-size: 16px;
}}
QFrame {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}
QLabel {{
    background: transparent;
    border: none;
    color: {TEXT};
    font-weight: bold;
}}
QPushButton {{
    background: {SURFACE2};
    color: {GREEN};
    border: 1px solid {GREEN3};
    border-radius: 3px;
    padding: 5px 14px;
    font-family: 'Courier New', Consolas, monospace;
    font-size: 14px;
}}
QPushButton:hover  {{ background: {GREEN4}; border-color: {GREEN2}; color: {GREEN}; }}
QPushButton:pressed {{ background: {BG}; color: {TEXT_BRIGHT}; }}
QPushButton:disabled {{ background: {SURFACE}; color: {GREEN_DIM}; border-color: {BORDER}; }}
QListWidget {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    color: {TEXT2};
}}
QListWidget::item:hover     {{ background: {GREEN4}; }}
QListWidget::item:selected  {{ background: {GREEN4}; color: {GREEN}; border-left: 2px solid {GREEN}; }}
QScrollBar:vertical {{
    background: {BG}; width: 6px; border: none;
}}
QScrollBar::handle:vertical {{
    background: {GREEN3}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{ background: {BORDER}; }}
QTableWidget {{
    background: {SURFACE2};
    gridline-color: {BORDER};
    color: {TEXT2};
    border: 1px solid {BORDER};
}}
QHeaderView::section {{
    background: {SURFACE3};
    color: {GREEN3};
    border: 1px solid {BORDER};
    padding: 3px;
    font-size: 13px;
}}
QSpinBox, QLineEdit {{
    background: {SURFACE2};
    color: {GREEN};
    border: 1px solid {GREEN3};
    border-radius: 2px;
    padding: 2px 4px;
}}
QDialog {{
    background: {BG};
    color: {TEXT};
    border: 1px solid {GREEN3};
}}
QDialogButtonBox QPushButton {{
    min-width: 80px;
}}
QTabBar::tab {{
    background: {SURFACE2};
    color: {TEXT3};
    border: 1px solid {BORDER};
    padding: 6px 14px;
    margin-right: 2px;
    font-size: 14px;
}}
QTabBar::tab:selected {{
    background: {GREEN4};
    color: {GREEN};
    border-bottom: 2px solid {GREEN};
}}
QTabBar::tab:hover {{ background: {GREEN4}; color: {TEXT2}; }}
"""

# ── Convenience helpers ───────────────────────────────────────────────────────

def btn_style(bg=SURFACE2, fg=GREEN, border=GREEN3, hover_bg=GREEN4):
    """Returns an inline QPushButton stylesheet string."""
    return (
        f"QPushButton{{background:{bg};color:{fg};border:1px solid {border};"
        f"border-radius:3px;padding:5px 14px;font-family:'Courier New',monospace;"
        f"font-size:11px;}}"
        f"QPushButton:hover{{background:{hover_bg};border-color:{GREEN2};}}"
        f"QPushButton:pressed{{background:{BG};}}"
    )

def label_style(colour=TEXT, size=14, bold=True):
    w = "bold" if bold else "normal"
    return f"color:{colour};font-size:{size}px;font-weight:{w};background:transparent;border:none;"

def card_style(bg=SURFACE, border=BORDER):
    return f"background:{bg};border:1px solid {border};border-radius:3px;"

def section_header(text: str) -> str:
    """Returns a formatted section divider label text for terminal style."""
    return f"── {text.upper()} "
