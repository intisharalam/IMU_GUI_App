"""
app.py
------
Sets up the DearPyGui window and runs the render loop.

Responsibilities:
  - Create the main window (1600 x 1000)
  - Split it into left (IMU panel) and right (metrics panel)
  - Run the render loop: update both panels + angle calculations each frame
  - Handle the bottom bar (Haptic All, Sync All, Calibrate, Quit)

This file knows about the GUI layout. It does not contain any BLE or maths logic.
"""

import time
import dearpygui.dearpygui as dpg

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from gui.imu_panel import IMUPanel
from gui.metrics_panel import MetricsPanel


# ── Window dimensions ────────────────────────────────────────────────────────
WIN_W = 1600
WIN_H = 1000

HALF_W   = WIN_W // 2
BOTTOM_H = 50
TITLE_H  = 40
PANEL_H  = WIN_H - TITLE_H - BOTTOM_H

# ── AMOLED Colour Palette ────────────────────────────────────────────────────
# Background: true black / near-black for AMOLED feel
# Accents: vivid, saturated colours that pop on black
C_BG       = (0,    0,    0,   255)   # true black
C_PANEL    = (10,   10,   10,  255)   # near-black panels
C_BORDER   = (35,   35,   45,  255)   # subtle border
C_TEXT     = (240,  240,  240, 255)   # bright white text
C_DIM      = (110,  110,  130, 255)   # muted secondary text

C_GREEN    = (0,    255,  160, 255)   # vivid mint green
C_RED      = (255,  60,   60,  255)   # vivid red
C_AMBER    = (255,  200,  0,   255)   # vivid amber/yellow
C_ACCENT   = (0,    180,  255, 255)   # vivid cyan-blue
C_PURPLE   = (180,  80,   255, 255)   # vivid purple


class App:
    """
    The top-level application object.
    Creates the window, panels, and runs the frame loop.

    Usage (in main.py):
        app = App(state, ble_manager, calibration, angle_processor)
        app.run()
    """

    def __init__(self, state: AppState, ble_manager: BLEManager,
                 calibration: Calibration, angle_processor: AngleProcessor):
        self._state  = state
        self._ble    = ble_manager
        self._cal    = calibration
        self._angles = angle_processor

        self._imu_panel     = IMUPanel(state, ble_manager)
        self._metrics_panel = MetricsPanel(state, calibration)

    def run(self):
        """Sets up DearPyGui and enters the render loop. Blocks until quit."""
        dpg.create_context()
        dpg.create_viewport(
            title="Frozen Shoulder Rehab — IMU Monitor",
            width=WIN_W,
            height=WIN_H,
            resizable=False
        )
        dpg.setup_dearpygui()

        self._apply_theme()

        with dpg.window(tag="main_window", no_title_bar=True, no_resize=True,
                        no_move=True, no_scrollbar=True,
                        width=WIN_W, height=WIN_H):

            self._build_title_bar()
            self._build_divider()

            self._imu_panel.build(
                x=0, y=TITLE_H,
                width=HALF_W, height=PANEL_H
            )

            self._metrics_panel.build(
                x=HALF_W, y=TITLE_H,
                width=HALF_W, height=PANEL_H
            )

            self._build_bottom_bar()

        dpg.set_primary_window("main_window", True)
        dpg.show_viewport()

        # ── Render loop ───────────────────────────────────────────────────────
        while dpg.is_dearpygui_running():
            now = time.monotonic()
            self._angles.update(now)
            self._imu_panel.update()
            self._metrics_panel.update()
            dpg.render_dearpygui_frame()

        dpg.destroy_context()

    # ── Private: widget builders ─────────────────────────────────────────────

    def _build_title_bar(self):
        with dpg.child_window(pos=(0, 0), width=WIN_W, height=TITLE_H,
                              border=False):
            dpg.add_spacer(height=7)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=12)
                dpg.add_text("FROZEN SHOULDER REHAB", color=C_ACCENT)
                dpg.add_text(
                    "  -  3× XIAO nRF52840  |  BNO085  |  DRV2605L  |  BLE",
                    color=C_DIM
                )

    def _build_divider(self):
        with dpg.draw_layer(parent="main_window"):
            dpg.draw_line(
                p1=(HALF_W, TITLE_H),
                p2=(HALF_W, WIN_H - BOTTOM_H),
                color=C_BORDER,
                thickness=1
            )

    def _build_bottom_bar(self):
        bar_y = WIN_H - BOTTOM_H
        with dpg.child_window(pos=(0, bar_y), width=WIN_W, height=BOTTOM_H,
                              border=False, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=12)

                hb = dpg.add_button(
                    label="  Haptic All  ",
                    callback=lambda: self._ble.send_haptic_all(),
                    height=32, width=140
                )
                self._apply_btn_colour(hb, C_AMBER)

                dpg.add_spacer(width=8)

                sb = dpg.add_button(
                    label="  Sync All  ",
                    callback=lambda: self._ble.send_sync_all(),
                    height=32, width=120
                )
                self._apply_btn_colour(sb, C_ACCENT)

                dpg.add_spacer(width=8)

                cb = dpg.add_button(
                    label="  Calibrate  ",
                    callback=lambda: self._cal.capture(),
                    height=32, width=120
                )
                self._apply_btn_colour(cb, C_GREEN)

                dpg.add_spacer(width=WIN_W - 580)

                qb = dpg.add_button(
                    label="  Quit  ",
                    callback=self._quit,
                    height=32, width=80
                )
                self._apply_btn_colour(qb, C_RED)

    def _quit(self):
        self._ble.stop()
        dpg.stop_dearpygui()

    # ── Private: theming ─────────────────────────────────────────────────────

    def _apply_theme(self):
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,       C_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,        C_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        (18, 18, 22, 255))
                dpg.add_theme_color(dpg.mvThemeCol_Border,         C_BORDER)
                dpg.add_theme_color(dpg.mvThemeCol_Text,           C_TEXT)
                dpg.add_theme_color(dpg.mvThemeCol_Button,         (25, 25, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (45, 45, 65, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (15, 15, 25, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,    C_BG)
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,  C_PANEL)
                # Reduce internal padding so child_window contents fit their
                # declared height without overflowing
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding,  4, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding,    4, 3)
                dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     4, 3)
            with dpg.theme_component(dpg.mvPlot):
                dpg.add_theme_color(dpg.mvPlotCol_PlotBg,     (5, 5, 8, 255))
                dpg.add_theme_color(dpg.mvPlotCol_PlotBorder, C_BORDER)
                dpg.add_theme_color(dpg.mvPlotCol_FrameBg,    (10, 10, 12, 255))
        dpg.bind_theme(t)

    def _apply_btn_colour(self, btn_tag, colour):
        # Darken the colour slightly for hover/active states
        r, g, b, a = colour
        hover  = (min(r + 40, 255), min(g + 40, 255), min(b + 40, 255), a)
        active = (max(r - 30, 0),   max(g - 30, 0),   max(b - 30, 0),   a)
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        colour)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, hover)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  active)
        dpg.bind_item_theme(btn_tag, t)