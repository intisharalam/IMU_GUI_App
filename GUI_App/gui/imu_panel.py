"""
imu_panel.py
------------
Builds and updates the LEFT half of the GUI.

Shows three columns, one per IMU sensor (WRIST, ARM, CHEST).
Each column has:
  - A status card: connected indicator, packet count, address, sync offset
  - Haptic and Sync buttons
  - A scrolling plot: raw Roll, Pitch, Yaw over the last 10 seconds

This panel only DISPLAYS data — it never writes to AppState.
Button callbacks fire commands back through the BLE manager.
"""

import time
import dearpygui.dearpygui as dpg

from ble.ble_state import AppState, SLOT_NAMES


# ── Colours ──────────────────────────────────────────────────────────────────
C_TEXT    = (210, 220, 230, 255)
C_DIM     = (90,  105, 120, 255)
C_GREEN   = (50,  220, 120, 255)
C_RED     = (220,  70,  70, 255)
C_AMBER   = (240, 180,  40, 255)
C_ACCENT  = (70,  160, 255, 255)
C_PANEL   = (22,  28,  36, 255)
C_ROLL    = (70,  160, 255, 255)
C_PITCH   = (50,  220, 120, 255)
C_YAW     = (240, 180,  40, 255)

PLOT_WINDOW_S = 10.0   # seconds of history shown in plot


class IMUPanel:
    """
    The left half of the main window.
    Three side-by-side columns showing raw IMU status and Euler angle plots.

    build(x, y, width, height) — call once to create all widgets
    update()                   — call every frame to refresh displayed values
    """

    def __init__(self, state: AppState, ble_manager):
        self._state = state
        self._ble   = ble_manager

        # Stores DearPyGui tag references for each sensor column
        # so update() knows which widgets to change
        self._tags = {}   # {"wrist": {...}, "arm": {...}, "chest": {...}}

    def build(self, x: int, y: int, width: int, height: int):
        """
        Creates all widgets for the IMU status panel.
        x, y: top-left corner position
        width, height: total size of this panel
        """
        col_width = width // 3
        col_labels = {"wrist": "WRIST", "arm": "ARM", "chest": "CHEST"}

        for i, name in enumerate(SLOT_NAMES):
            col_x = x + i * col_width
            self._tags[name] = self._build_column(
                name, col_labels[name], col_x, y, col_width, height
            )

    def update(self):
        """
        Refreshes all displayed values from AppState.
        Called every frame by the render loop.
        """
        now = time.monotonic()

        for name in SLOT_NAMES:
            tags = self._tags[name]

            with self._state.lock:
                connected  = self._state.slots[name].connected
                address    = self._state.slots[name].address
                packets    = self._state.slots[name].packet_count
                sync_off   = self._state.slots[name].sync_offset_ms
                haptic_act = self._state.slots[name].haptic_active
                t_list, r_list, p_list, y_list = \
                    self._state.slots[name].get_plot_data()

            # --- Status card ---
            if connected:
                dpg.configure_item(tags["dot"],    color=C_GREEN)
                dpg.configure_item(tags["status"], default_value="Connected",
                                   color=C_GREEN)
                dpg.configure_item(tags["addr"],
                                   default_value=address or "", color=C_DIM)
            else:
                dpg.configure_item(tags["dot"],    color=C_RED)
                dpg.configure_item(tags["status"], default_value="Searching...",
                                   color=C_RED)
                dpg.configure_item(tags["addr"],   default_value="")

            dpg.configure_item(tags["packets"],
                               default_value=str(packets) if packets else "—")

            sync_str = f"{sync_off:+.1f} ms" if sync_off is not None else "—"
            dpg.configure_item(tags["sync"], default_value=sync_str,
                               color=C_TEXT if sync_off else C_DIM)

            # Haptic button flashes amber while the motor is running
            # Swap between two pre-built themes (DearPyGui can't edit theme
            # colour items directly at runtime, so we swap the whole theme)
            if haptic_act:
                dpg.bind_item_theme(tags["haptic_btn"], tags["haptic_theme_amber"])
            else:
                dpg.bind_item_theme(tags["haptic_btn"], tags["haptic_theme_normal"])

            # --- Scrolling plot ---
            if t_list:
                # Only show the last PLOT_WINDOW_S seconds
                cutoff = now - PLOT_WINDOW_S
                # Find where the data starts being within our window
                start_i = 0
                for i, t in enumerate(t_list):
                    if t >= cutoff:
                        start_i = i
                        break

                xs = [t - now for t in t_list[start_i:]]  # seconds ago (negative)
                rs = r_list[start_i:]
                ps = p_list[start_i:]
                ys = y_list[start_i:]

                dpg.set_value(tags["roll_series"],  [xs, rs])
                dpg.set_value(tags["pitch_series"], [xs, ps])
                dpg.set_value(tags["yaw_series"],   [xs, ys])
                dpg.set_axis_limits(tags["x_axis"], -PLOT_WINDOW_S, 0)
            else:
                dpg.set_value(tags["roll_series"],  [[], []])
                dpg.set_value(tags["pitch_series"], [[], []])
                dpg.set_value(tags["yaw_series"],   [[], []])
                dpg.set_axis_limits(tags["x_axis"], -PLOT_WINDOW_S, 0)

    # ── Private: build one sensor column ────────────────────────────────────

    def _build_column(self, name, label, x, y, width, height):
        """
        Builds one sensor column and returns a dict of widget tags.
        """
        tags = {}
        CARD_H = 130
        BTN_H  = 40
        PLOT_H = height - CARD_H - BTN_H - 30

        # --- Status card ---
        with dpg.child_window(pos=(x + 4, y + 4),
                              width=width - 8, height=CARD_H, border=True):
            with dpg.group(horizontal=True):
                # Coloured dot indicator
                tags["dot"] = dpg.add_text("●", color=C_RED)
                dpg.add_text(f" {label}", color=C_ACCENT)

            tags["status"] = dpg.add_text("Searching...", color=C_RED)
            tags["addr"]   = dpg.add_text("", color=C_DIM)

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Packets:", color=C_DIM)
                tags["packets"] = dpg.add_text("—", color=C_TEXT)

            with dpg.group(horizontal=True):
                dpg.add_text("Sync offset:", color=C_DIM)
                tags["sync"] = dpg.add_text("—", color=C_DIM)

        # --- Buttons ---
        btn_y = y + CARD_H + 8
        btn_w = (width - 16) // 2

        with dpg.child_window(pos=(x + 4, btn_y),
                              width=width - 8, height=BTN_H, border=False):
            with dpg.group(horizontal=True):
                # Haptic button — two pre-built themes, swapped at runtime
                haptic_theme_normal = dpg.add_theme()
                with dpg.theme_component(dpg.mvButton, parent=haptic_theme_normal):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (60, 70, 85, 200))
                tags["haptic_theme_normal"] = haptic_theme_normal

                haptic_theme_amber = dpg.add_theme()
                with dpg.theme_component(dpg.mvButton, parent=haptic_theme_amber):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, C_AMBER)
                tags["haptic_theme_amber"] = haptic_theme_amber

                hb = dpg.add_button(
                    label="Haptic",
                    width=btn_w,
                    callback=lambda s, a, u: self._ble.send_haptic(u),
                    user_data=name
                )
                dpg.bind_item_theme(hb, haptic_theme_normal)
                tags["haptic_btn"] = hb

                dpg.add_spacer(width=4)

                # Sync button
                sb = dpg.add_button(
                    label="Sync",
                    width=btn_w,
                    callback=lambda s, a, u: self._ble.send_sync(u),
                    user_data=name
                )
                tags["sync_btn"] = sb

        # --- Scrolling plot ---
        plot_y = btn_y + BTN_H + 4
        with dpg.child_window(pos=(x + 4, plot_y),
                              width=width - 8, height=PLOT_H, border=True):

            dpg.add_text(f"  {label} — Roll / Pitch / Yaw", color=C_DIM)

            with dpg.plot(height=PLOT_H - 50, width=width - 24,
                          no_title=True, no_mouse_pos=True):

                dpg.add_plot_legend()

                tags["x_axis"] = dpg.add_plot_axis(
                    dpg.mvXAxis, label="", no_tick_labels=True)

                with dpg.plot_axis(dpg.mvYAxis, label="deg"):
                    tags["roll_series"]  = dpg.add_line_series(
                        [], [], label="Roll")
                    tags["pitch_series"] = dpg.add_line_series(
                        [], [], label="Pitch")
                    tags["yaw_series"]   = dpg.add_line_series(
                        [], [], label="Yaw")

            # Colour legend
            with dpg.group(horizontal=True):
                dpg.add_text("●", color=C_ROLL)
                dpg.add_text(" Roll  ", color=C_DIM)
                dpg.add_text("●", color=C_PITCH)
                dpg.add_text(" Pitch  ", color=C_DIM)
                dpg.add_text("●", color=C_YAW)
                dpg.add_text(" Yaw", color=C_DIM)

        return tags