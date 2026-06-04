"""
metrics_panel.py
----------------
Builds and updates the RIGHT half of the GUI.

Shows:
  - Calibration button and status
  - Four live joint angle readouts with session maximums
  - Scrolling plot of all four joint angles — Y axis fixed 0 to 180 deg
  - ADL milestone indicators (green/red based on clinical thresholds)

This panel reads from AppState and fires calibration through the
Calibration object. It never touches BLE directly.
"""

import time
import dearpygui.dearpygui as dpg

from ble.ble_state import AppState

# ── AMOLED Colours ────────────────────────────────────────────────────────────
C_TEXT   = (240, 240, 240, 255)
C_DIM    = (110, 110, 130, 255)
C_GREEN  = (0,   255, 160, 255)
C_RED    = (255,  60,  60, 255)
C_AMBER  = (255, 200,   0, 255)
C_ACCENT = (0,   180, 255, 255)
C_PURPLE = (180,  80, 255, 255)
C_PANEL  = (10,   10,  10, 255)

# Joint angle plot line colours
C_FLEX   = (0,   180, 255, 255)   # cyan-blue  — flexion
C_ABD    = (0,   255, 160, 255)   # mint green — abduction
C_ROT    = (255, 200,   0, 255)   # amber      — external rotation
C_ELBOW  = (180,  80, 255, 255)   # purple     — elbow

PLOT_WINDOW_S = 10.0

# ── ADL milestone thresholds (degrees) ───────────────────────────────────────
ADL_THRESHOLDS = {
    "Touch head":     ("max_flexion",   130.0),
    "Reach overhead": ("max_abduction", 150.0),
    "Put on coat":    ("max_ext_rot",    60.0),
}


class MetricsPanel:
    """
    The right half of the main window.
    Shows calibrated joint angles and clinical progress indicators.

    build(x, y, width, height) — call once to create all widgets
    update()                   — call every frame to refresh values
    """

    def __init__(self, state: AppState, calibration):
        self._state       = state
        self._calibration = calibration
        self._tags        = {}

    def build(self, x: int, y: int, width: int, height: int):
        self._x = x
        self._y = y
        self._w = width
        self._h = height

        pad     = 6
        GAP     = 4
        TITLE_H = 22
        CAL_H   = 40
        CARD_H  = 74
        ADL_H   = 60
        # Plot gets whatever height is left after fixed sections + gaps
        plot_h  = height - TITLE_H - CAL_H - CARD_H - ADL_H - GAP * 6

        # Cursor-based layout — each section advances cur_y by its height + gap
        cur_y = y + GAP

        # --- Section title ---
        with dpg.child_window(pos=(x + pad, cur_y),
                              width=width - pad * 2, height=TITLE_H,
                              border=False, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            dpg.add_text("CLINICAL METRICS", color=C_ACCENT)
        cur_y += TITLE_H + GAP

        # --- Calibration row ---
        with dpg.child_window(pos=(x + pad, cur_y),
                              width=width - pad * 2, height=CAL_H,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="  Calibrate (I-Pose)  ",
                    callback=self._on_calibrate,
                    width=185, height=CAL_H - 8
                )
                dpg.add_spacer(width=8)
                self._tags["cal_status"] = dpg.add_text(
                    "Not calibrated: connect all sensors first",
                    color=C_AMBER
                )
        cur_y += CAL_H + GAP

        # --- Four angle readout cards ---
        card_gap = 4
        card_w   = (width - pad * 2 - card_gap * 3) // 4

        angle_defs = [
            ("flexion", "Shldr Flex",  C_FLEX),
            ("abd",     "Abduction",   C_ABD),
            ("rot",     "Ext. Rot",    C_ROT),
            ("elbow",   "Elbow Flex",  C_ELBOW),
        ]

        for i, (key, label, colour) in enumerate(angle_defs):
            cx = x + pad + i * (card_w + card_gap)
            self._build_angle_card(key, label, colour, cx, cur_y, card_w, CARD_H)
        cur_y += CARD_H + GAP

        # --- Joint angles plot ---
        with dpg.child_window(pos=(x + pad, cur_y),
                              width=width - pad * 2, height=plot_h,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            dpg.add_text("  Joint Angles over Time", color=C_DIM)

            with dpg.plot(height=plot_h - 48, width=width - pad * 2 - 12,
                          no_title=True, no_mouse_pos=True):
                dpg.add_plot_legend()

                self._tags["angle_x_axis"] = dpg.add_plot_axis(
                    dpg.mvXAxis, label="", no_tick_labels=True)

                with dpg.plot_axis(dpg.mvYAxis, label="deg") as y_ax:
                    self._tags["angle_y_axis"] = y_ax
                    self._tags["flex_series"]  = dpg.add_line_series(
                        [], [], label="Flexion")
                    dpg.bind_item_theme(self._tags["flex_series"],
                                        self._make_series_theme(C_FLEX))

                    self._tags["abd_series"]   = dpg.add_line_series(
                        [], [], label="Abduction")
                    dpg.bind_item_theme(self._tags["abd_series"],
                                        self._make_series_theme(C_ABD))

                    self._tags["rot_series"]   = dpg.add_line_series(
                        [], [], label="Ext. Rotation")
                    dpg.bind_item_theme(self._tags["rot_series"],
                                        self._make_series_theme(C_ROT))

                    self._tags["elbow_series"] = dpg.add_line_series(
                        [], [], label="Elbow")
                    dpg.bind_item_theme(self._tags["elbow_series"],
                                        self._make_series_theme(C_ELBOW))

                dpg.set_axis_limits(self._tags["angle_y_axis"], -180.0, 180.0)

            with dpg.group(horizontal=True):
                for colour, lbl in [
                    (C_FLEX,  "Flexion  "),
                    (C_ABD,   "Abduction  "),
                    (C_ROT,   "Ext. Rot  "),
                    (C_ELBOW, "Elbow"),
                ]:
                    dpg.add_text("[F]" if colour == C_FLEX else
                                 "[A]" if colour == C_ABD  else
                                 "[R]" if colour == C_ROT  else "[E]",
                                 color=colour)
                    dpg.add_text(lbl, color=C_DIM)
        cur_y += plot_h + GAP

        # --- ADL milestone indicators ---
        self._build_adl_indicators(x + pad, cur_y, width - pad * 2, ADL_H)

    def update(self):
        now = time.monotonic()

        with self._state.lock:
            calibrated = self._state.calibrated
            flexion    = self._state.shoulder_flexion
            abduction  = self._state.shoulder_abduction
            ext_rot    = self._state.external_rotation
            elbow      = self._state.elbow_flexion
            max_flex   = self._state.max_flexion
            max_abd    = self._state.max_abduction
            max_rot    = self._state.max_ext_rot
            max_elbow  = self._state.max_elbow
            t_list     = list(self._state.angle_times)
            flex_hist  = list(self._state.flexion_hist)
            abd_hist   = list(self._state.abduction_hist)
            rot_hist   = list(self._state.ext_rot_hist)
            elbow_hist = list(self._state.elbow_hist)

        # --- Calibration status ---
        capturing = self._calibration.is_capturing()
        if capturing:
            dpg.configure_item(self._tags["cal_status"],
                               default_value="Capturing 3-sec average — hold I-pose...",
                               color=C_ACCENT)
        elif calibrated:
            dpg.configure_item(self._tags["cal_status"],
                               default_value="✓ Calibrated", color=C_GREEN)
        else:
            dpg.configure_item(
                self._tags["cal_status"],
                default_value="Not calibrated — connect all sensors first",
                color=C_AMBER
            )

        # --- Angle cards ---
        self._update_angle_card("flexion", flexion,   max_flex)
        self._update_angle_card("abd",     abduction, max_abd)
        self._update_angle_card("rot",     ext_rot,   max_rot)
        self._update_angle_card("elbow",   elbow,     max_elbow)

        # --- Scrolling plot ---
        if t_list:
            cutoff  = now - PLOT_WINDOW_S
            start_i = 0
            for i, t in enumerate(t_list):
                if t >= cutoff:
                    start_i = i
                    break

            xs = [t - now for t in t_list[start_i:]]
            dpg.set_value(self._tags["flex_series"],  [xs, flex_hist[start_i:]])
            dpg.set_value(self._tags["abd_series"],   [xs, abd_hist[start_i:]])
            dpg.set_value(self._tags["rot_series"],   [xs, rot_hist[start_i:]])
            dpg.set_value(self._tags["elbow_series"], [xs, elbow_hist[start_i:]])
            dpg.set_axis_limits(self._tags["angle_x_axis"], -PLOT_WINDOW_S, 0)
        else:
            for series in ["flex_series", "abd_series", "rot_series", "elbow_series"]:
                dpg.set_value(self._tags[series], [[], []])
            dpg.set_axis_limits(self._tags["angle_x_axis"], -PLOT_WINDOW_S, 0)

        # --- ADL indicators ---
        adl_vals = {
            "max_flexion":   max_flex,
            "max_abduction": max_abd,
            "max_ext_rot":   max_rot,
        }
        for adl_name, (state_key, threshold) in ADL_THRESHOLDS.items():
            achieved  = adl_vals[state_key] >= threshold
            tag_key   = f"adl_{adl_name}"
            if tag_key in self._tags:
                dpg.configure_item(self._tags[tag_key],
                                   color=C_GREEN if achieved else C_RED)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_angle_card(self, key, label, colour, x, y, width, height):
        with dpg.child_window(pos=(x, y), width=width, height=height,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            dpg.add_text(label, color=colour)
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                dpg.add_text("Now:", color=C_DIM)
                self._tags[f"{key}_now"] = dpg.add_text("—°", color=C_TEXT)
            with dpg.group(horizontal=True):
                dpg.add_text("Max:", color=C_DIM)
                self._tags[f"{key}_max"] = dpg.add_text("—°", color=colour)

    def _update_angle_card(self, key, current_val, max_val):
        dpg.configure_item(self._tags[f"{key}_now"],
                           default_value=f"{current_val:+.1f}°")
        dpg.configure_item(self._tags[f"{key}_max"],
                           default_value=f"{max_val:.1f}°")

    def _build_adl_indicators(self, x, y, width, height):
        with dpg.child_window(pos=(x, y), width=width, height=height,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            dpg.add_text("  ADL Milestones (session best)", color=C_DIM)
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                for adl_name, (state_key, threshold) in ADL_THRESHOLDS.items():
                    tag_key = f"adl_{adl_name}"
                    self._tags[tag_key] = dpg.add_text(
                        f"[!] {adl_name} (>={threshold:.0f}deg)   ",
                        color=C_RED
                    )

    def _make_series_theme(self, colour):
        """
        Creates and returns a DearPyGui theme that sets a plot line series
        to the given RGBA colour tuple. Both the line and the legend swatch
        are set so they always match.
        """
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, colour,
                                    category=dpg.mvThemeCat_Plots)
        return t

    def _on_calibrate(self):
        success = self._calibration.capture()
        if not success:
            print("[GUI] Calibration failed — are all sensors connected?")