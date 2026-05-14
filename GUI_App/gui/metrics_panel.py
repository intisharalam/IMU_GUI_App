"""
metrics_panel.py
----------------
Builds and updates the RIGHT half of the GUI.

Shows:
  - Calibration button and status
  - Four live joint angle readouts with session maximums:
      Shoulder Flexion, Shoulder Abduction, External Rotation, Elbow Flexion
  - Scrolling plot of all four joint angles over time
  - ADL milestone indicators (green/red based on clinical thresholds)

This panel reads from AppState and fires calibration through the
Calibration object. It never touches BLE directly.
"""

import time
import dearpygui.dearpygui as dpg

from ble.ble_state import AppState

# ── Colours ──────────────────────────────────────────────────────────────────
C_TEXT    = (210, 220, 230, 255)
C_DIM     = (90,  105, 120, 255)
C_GREEN   = (50,  220, 120, 255)
C_RED     = (220,  70,  70, 255)
C_AMBER   = (240, 180,  40, 255)
C_ACCENT  = (70,  160, 255, 255)
C_PANEL   = (22,  28,  36,  255)

# Joint angle plot line colours
C_FLEX    = (70,  160, 255, 255)   # blue   — flexion
C_ABD     = (50,  220, 120, 255)   # green  — abduction
C_ROT     = (240, 180,  40, 255)   # amber  — external rotation
C_ELBOW   = (200,  80, 200, 255)   # purple — elbow

PLOT_WINDOW_S = 10.0   # seconds of history to show

# ── ADL milestone thresholds (degrees) ───────────────────────────────────────
# Based on clinical literature for frozen shoulder rehabilitation.
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
        """Creates all widgets for the metrics panel."""
        self._x = x
        self._y = y
        self._w = width
        self._h = height

        pad = 8

        # --- Section title ---
        with dpg.child_window(pos=(x + pad, y + pad),
                              width=width - pad * 2, height=30, border=False):
            dpg.add_text("CLINICAL METRICS", color=C_ACCENT)

        # --- Calibration row ---
        cal_y = y + 44
        with dpg.child_window(pos=(x + pad, cal_y),
                              width=width - pad * 2, height=50, border=True):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="  Calibrate (I-Pose)  ",
                    callback=self._on_calibrate,
                    width=200
                )
                dpg.add_spacer(width=10)
                self._tags["cal_status"] = dpg.add_text(
                    "Not calibrated — connect all sensors and press Calibrate",
                    color=C_AMBER
                )

        # --- Four angle readout cards ---
        card_y   = cal_y + 60
        card_h   = 80
        card_gap = 6
        card_w   = (width - pad * 2 - card_gap * 3) // 4

        angle_defs = [
            # (key in tags dict, display label, colour)
            ("flexion",  "Shoulder Flexion",   C_FLEX),
            ("abd",      "Abduction",           C_ABD),
            ("rot",      "External Rotation",   C_ROT),
            ("elbow",    "Elbow Flexion",        C_ELBOW),
        ]

        for i, (key, label, colour) in enumerate(angle_defs):
            cx = x + pad + i * (card_w + card_gap)
            self._build_angle_card(key, label, colour, cx, card_y, card_w, card_h)

        # --- Joint angles plot ---
        plot_y = card_y + card_h + 10
        plot_h = height - (plot_y - y) - 120  # leave room for ADL indicators

        with dpg.child_window(pos=(x + pad, plot_y),
                              width=width - pad * 2, height=plot_h, border=True):
            dpg.add_text("  Joint Angles over Time", color=C_DIM)

            with dpg.plot(height=plot_h - 50, width=width - pad * 2 - 16,
                          no_title=True, no_mouse_pos=True):
                dpg.add_plot_legend()

                self._tags["angle_x_axis"] = dpg.add_plot_axis(
                    dpg.mvXAxis, label="", no_tick_labels=True)

                with dpg.plot_axis(dpg.mvYAxis, label="degrees"):
                    self._tags["flex_series"]  = dpg.add_line_series(
                        [], [], label="Flexion")
                    self._tags["abd_series"]   = dpg.add_line_series(
                        [], [], label="Abduction")
                    self._tags["rot_series"]   = dpg.add_line_series(
                        [], [], label="Ext. Rotation")
                    self._tags["elbow_series"] = dpg.add_line_series(
                        [], [], label="Elbow")

            # Colour legend
            with dpg.group(horizontal=True):
                for colour, lbl in [
                    (C_FLEX,  "Flexion  "),
                    (C_ABD,   "Abduction  "),
                    (C_ROT,   "Ext. Rotation  "),
                    (C_ELBOW, "Elbow"),
                ]:
                    dpg.add_text("●", color=colour)
                    dpg.add_text(lbl, color=C_DIM)

        # --- ADL milestone indicators ---
        adl_y = plot_y + plot_h + 8
        self._build_adl_indicators(x + pad, adl_y, width - pad * 2)

    def update(self):
        """Refreshes all displayed values. Called every frame."""
        now = time.monotonic()

        with self._state.lock:
            calibrated  = self._state.calibrated
            flexion     = self._state.shoulder_flexion
            abduction   = self._state.shoulder_abduction
            ext_rot     = self._state.external_rotation
            elbow       = self._state.elbow_flexion
            max_flex    = self._state.max_flexion
            max_abd     = self._state.max_abduction
            max_rot     = self._state.max_ext_rot
            max_elbow   = self._state.max_elbow
            t_list      = list(self._state.angle_times)
            flex_hist   = list(self._state.flexion_hist)
            abd_hist    = list(self._state.abduction_hist)
            rot_hist    = list(self._state.ext_rot_hist)
            elbow_hist  = list(self._state.elbow_hist)

        # --- Calibration status text ---
        if calibrated:
            dpg.configure_item(
                self._tags["cal_status"],
                default_value="✓ Calibrated",
                color=C_GREEN
            )
        else:
            dpg.configure_item(
                self._tags["cal_status"],
                default_value="Not calibrated — connect all sensors and press Calibrate",
                color=C_AMBER
            )

        # --- Angle readout cards ---
        self._update_angle_card("flexion", flexion, max_flex)
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
            achieved = adl_vals[state_key] >= threshold
            colour = C_GREEN if achieved else C_RED
            tag_key = f"adl_{adl_name}"
            if tag_key in self._tags:
                dpg.configure_item(self._tags[tag_key], color=colour)

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_angle_card(self, key, label, colour, x, y, width, height):
        """Builds one angle readout card (current value + session max)."""
        with dpg.child_window(pos=(x, y), width=width, height=height, border=True):
            dpg.add_text(label, color=colour)
            dpg.add_spacer(height=2)

            with dpg.group(horizontal=True):
                dpg.add_text("Now:", color=C_DIM)
                self._tags[f"{key}_now"] = dpg.add_text("—°", color=C_TEXT)

            with dpg.group(horizontal=True):
                dpg.add_text("Max:", color=C_DIM)
                self._tags[f"{key}_max"] = dpg.add_text("—°", color=colour)

    def _update_angle_card(self, key, current_val, max_val):
        """Updates the text in one angle readout card."""
        dpg.configure_item(self._tags[f"{key}_now"],
                           default_value=f"{current_val:+.1f}°")
        dpg.configure_item(self._tags[f"{key}_max"],
                           default_value=f"{max_val:.1f}°")

    def _build_adl_indicators(self, x, y, width):
        """
        Builds the ADL milestone row.
        Each milestone is a coloured label that turns green when the
        threshold angle has been reached this session.
        """
        with dpg.child_window(pos=(x, y), width=width, height=70, border=True):
            dpg.add_text("  ADL Milestones (session best)", color=C_DIM)
            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                for adl_name, (state_key, threshold) in ADL_THRESHOLDS.items():
                    tag_key = f"adl_{adl_name}"
                    self._tags[tag_key] = dpg.add_text(
                        f"● {adl_name} (≥{threshold:.0f}°)   ",
                        color=C_RED
                    )

    def _on_calibrate(self):
        """Called when the user clicks the Calibrate button."""
        success = self._calibration.capture()
        if not success:
            print("[GUI] Calibration failed — are all sensors connected?")
