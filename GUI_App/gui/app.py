"""
app.py
------
Sets up the DearPyGui window and runs the render loop.

Responsibilities:
  - Create the main window (1920 x 1200)
  - Split it into left (IMU panel) and right (metrics panel)
  - Run the render loop: update both panels + angle calculations each frame
  - Handle the bottom bar (Haptic All, Sync All, Quit)

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
WIN_W = 1920
WIN_H = 1200

HALF_W     = WIN_W // 2     # each panel gets half the width
BOTTOM_H   = 54             # height of the bottom bar
TITLE_H    = 44             # height of the title bar at the top
PANEL_H    = WIN_H - TITLE_H - BOTTOM_H

# ── Colours ──────────────────────────────────────────────────────────────────
C_BG      = (13,  17,  23,  255)
C_PANEL   = (22,  28,  36,  255)
C_BORDER  = (40,  50,  65,  255)
C_TEXT    = (210, 220, 230, 255)
C_DIM     = (90,  105, 120, 255)
C_GREEN   = (50,  220, 120, 255)
C_RED     = (220,  70,  70, 255)
C_AMBER   = (240, 180,  40, 255)
C_ACCENT  = (70,  160, 255, 255)


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
        self._state    = state
        self._ble      = ble_manager
        self._cal      = calibration
        self._angles   = angle_processor

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

        # Apply global dark theme
        self._apply_theme()

        # Build all widgets inside a single fullscreen window
        with dpg.window(tag="main_window", no_title_bar=True, no_resize=True,
                        no_move=True, no_scrollbar=True,
                        width=WIN_W, height=WIN_H):

            self._build_title_bar()
            self._build_divider()

            # Left half: IMU status panel
            self._imu_panel.build(
                x=0, y=TITLE_H,
                width=HALF_W, height=PANEL_H
            )

            # Right half: clinical metrics panel
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

            # 1. Run angle calculations (reads state, writes back computed angles)
            self._angles.update(now)

            # 2. Update both GUI panels with latest values
            self._imu_panel.update()
            self._metrics_panel.update()

            # 3. Render the frame
            dpg.render_dearpygui_frame()

        dpg.destroy_context()

    # ── Private: widget builders ─────────────────────────────────────────────

    def _build_title_bar(self):
        """Thin title bar at the very top of the window."""
        with dpg.child_window(pos=(0, 0), width=WIN_W, height=TITLE_H,
                              border=False):
            dpg.add_spacer(height=8)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=12)
                dpg.add_text("FROZEN SHOULDER REHAB", color=C_ACCENT)
                dpg.add_text(
                    "  —  3× XIAO nRF52840  |  BNO085  |  DRV2605L  |  BLE",
                    color=C_DIM
                )

    def _build_divider(self):
        """Vertical line between the two halves."""
        with dpg.draw_layer(parent="main_window"):
            dpg.draw_line(
                p1=(HALF_W, TITLE_H),
                p2=(HALF_W, WIN_H - BOTTOM_H),
                color=C_BORDER,
                thickness=1
            )

    def _build_bottom_bar(self):
        """Full-width bottom bar with global action buttons."""
        bar_y = WIN_H - BOTTOM_H

        with dpg.child_window(pos=(0, bar_y), width=WIN_W, height=BOTTOM_H,
                              border=False):
            dpg.add_spacer(height=6)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=12)

                # Haptic All
                hb = dpg.add_button(
                    label="  ⚡ Haptic All  ",
                    callback=lambda: self._ble.send_haptic_all(),
                    width=160
                )
                self._apply_btn_colour(hb, C_AMBER)

                dpg.add_spacer(width=10)

                # Sync All
                sb = dpg.add_button(
                    label="  ⟳ Sync All  ",
                    callback=lambda: self._ble.send_sync_all(),
                    width=140
                )
                self._apply_btn_colour(sb, C_ACCENT)

                dpg.add_spacer(width=10)

                # Calibrate shortcut (same as the button in metrics panel)
                cb = dpg.add_button(
                    label="  ✦ Calibrate  ",
                    callback=lambda: self._cal.capture(),
                    width=140
                )
                self._apply_btn_colour(cb, C_GREEN)

                # Push Quit to the right
                dpg.add_spacer(width=WIN_W - 660)

                qb = dpg.add_button(
                    label="  Quit  ",
                    callback=self._quit,
                    width=90
                )
                self._apply_btn_colour(qb, C_RED)

    def _quit(self):
        """Stops the BLE manager and closes the window."""
        self._ble.stop()
        dpg.stop_dearpygui()

    # ── Private: theming helpers ─────────────────────────────────────────────

    def _apply_theme(self):
        """Sets the global dark colour theme."""
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg,       C_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg,        C_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg,        C_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_Border,         C_BORDER)
                dpg.add_theme_color(dpg.mvThemeCol_Text,           C_TEXT)
                dpg.add_theme_color(dpg.mvThemeCol_Button,         (40, 100, 200, 200))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (60, 130, 240, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (30,  80, 180, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg,    C_BG)
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,  C_PANEL)
        dpg.bind_theme(t)

    def _apply_btn_colour(self, btn_tag, colour):
        """Applies a solid colour to a single button."""
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, colour)
        dpg.bind_item_theme(btn_tag, t)
