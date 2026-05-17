"""
imu_panel.py
------------
Builds and updates the LEFT half of the GUI.

Shows three columns, one per IMU sensor (WRIST, ARM, CHEST).
Each column has:
  - A status card: connected indicator, packet count, address, sync offset
  - Haptic and Sync buttons
  - A scrolling plot: raw Roll, Pitch, Yaw — Y axis fixed -180 to +180 deg

This panel only DISPLAYS data — it never writes to AppState.
Button callbacks fire commands back through the BLE manager.
"""

import time
import dearpygui.dearpygui as dpg

from ble.ble_state import AppState, SLOT_NAMES


# ── AMOLED Colours ────────────────────────────────────────────────────────────
C_TEXT   = (240, 240, 240, 255)
C_DIM    = (110, 110, 130, 255)
C_GREEN  = (0,   255, 160, 255)
C_RED    = (255,  60,  60, 255)
C_AMBER  = (255, 200,   0, 255)
C_ACCENT = (0,   180, 255, 255)
C_PANEL  = (10,   10,  10, 255)

# Plot line colours — vivid, distinct on black
C_ROLL   = (0,   180, 255, 255)   # cyan-blue
C_PITCH  = (0,   255, 160, 255)   # mint green
C_YAW    = (255, 200,   0, 255)   # amber

PLOT_WINDOW_S = 10.0   # seconds of history shown


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
        self._tags  = {}

    def build(self, x: int, y: int, width: int, height: int):
        col_width  = width // 3
        col_labels = {"wrist": "WRIST", "arm": "ARM", "chest": "CHEST"}

        for i, name in enumerate(SLOT_NAMES):
            col_x = x + i * col_width
            self._tags[name] = self._build_column(
                name, col_labels[name], col_x, y, col_width, height
            )

    def update(self):
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

            # Haptic button flashes amber while motor is running
            if haptic_act:
                dpg.bind_item_theme(tags["haptic_btn"], tags["haptic_theme_amber"])
            else:
                dpg.bind_item_theme(tags["haptic_btn"], tags["haptic_theme_normal"])

            # --- Scrolling plot ---
            if t_list:
                cutoff  = now - PLOT_WINDOW_S
                start_i = 0
                for i, t in enumerate(t_list):
                    if t >= cutoff:
                        start_i = i
                        break

                xs = [t - now for t in t_list[start_i:]]
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

    # ── Private ───────────────────────────────────────────────────────────────

    def _build_column(self, name, label, x, y, width, height):
        tags   = {}

        # Heights sized to fit actual rendered content.
        # DearPyGui adds ~16px internal padding per child_window regardless
        # of theme settings — these values account for that.
        CARD_H  = 130   # dot + label + status + addr + packets + sync + padding
        BTN_H   = 34    # one row of buttons
        GAP     = 4
        PLOT_H  = height - CARD_H - BTN_H - GAP * 4

        # --- Status card ---
        with dpg.child_window(pos=(x + 4, y + GAP),
                              width=width - 8, height=CARD_H,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            with dpg.group(horizontal=True):
                tags["dot"] = dpg.add_text("*", color=C_RED)
                dpg.add_text(f"  {label}", color=C_ACCENT)

            tags["status"]  = dpg.add_text("Searching...", color=C_RED)
            tags["addr"]    = dpg.add_text("", color=C_DIM)

            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                dpg.add_text("Packets:", color=C_DIM)
                tags["packets"] = dpg.add_text("—", color=C_TEXT)

            with dpg.group(horizontal=True):
                dpg.add_text("Sync:", color=C_DIM)
                tags["sync"] = dpg.add_text("—", color=C_DIM)

        # --- Buttons ---
        btn_y = y + GAP + CARD_H + GAP
        btn_w = (width - 16) // 2

        with dpg.child_window(pos=(x + 4, btn_y),
                              width=width - 8, height=BTN_H,
                              border=False, no_scrollbar=True,
                              no_scroll_with_mouse=True):
            with dpg.group(horizontal=True):

                haptic_theme_normal = dpg.add_theme()
                with dpg.theme_component(dpg.mvButton,
                                         parent=haptic_theme_normal):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (35, 35, 45, 220))
                tags["haptic_theme_normal"] = haptic_theme_normal

                haptic_theme_amber = dpg.add_theme()
                with dpg.theme_component(dpg.mvButton,
                                         parent=haptic_theme_amber):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, C_AMBER)
                tags["haptic_theme_amber"] = haptic_theme_amber

                hb = dpg.add_button(
                    label="Haptic",
                    width=btn_w, height=BTN_H - 4,
                    callback=lambda s, a, u: self._ble.send_haptic(u),
                    user_data=name
                )
                dpg.bind_item_theme(hb, haptic_theme_normal)
                tags["haptic_btn"] = hb

                dpg.add_spacer(width=4)

                sb = dpg.add_button(
                    label="Sync",
                    width=btn_w, height=BTN_H - 4,
                    callback=lambda s, a, u: self._ble.send_sync(u),
                    user_data=name
                )
                tags["sync_btn"] = sb

        # --- Scrolling plot ---
        plot_y = btn_y + BTN_H + GAP
        with dpg.child_window(pos=(x + 4, plot_y),
                              width=width - 8, height=PLOT_H,
                              border=True, no_scrollbar=True,
                              no_scroll_with_mouse=True):

            dpg.add_text(f"  {label} — Roll / Pitch / Yaw", color=C_DIM)

            # Plot height = container minus title (~18px) minus legend (~18px)
            # minus child_window internal padding (~16px)
            with dpg.plot(height=PLOT_H - 52, width=width - 16,
                          no_title=True, no_mouse_pos=True):

                dpg.add_plot_legend()

                tags["x_axis"] = dpg.add_plot_axis(
                    dpg.mvXAxis, label="", no_tick_labels=True)

                with dpg.plot_axis(dpg.mvYAxis, label="deg") as y_ax:
                    tags["y_axis"] = y_ax
                    tags["roll_series"]  = dpg.add_line_series(
                        [], [], label="Roll")
                    tags["pitch_series"] = dpg.add_line_series(
                        [], [], label="Pitch")
                    tags["yaw_series"]   = dpg.add_line_series(
                        [], [], label="Yaw")

                dpg.set_axis_limits(tags["y_axis"], -180.0, 180.0)

            with dpg.group(horizontal=True):
                dpg.add_text("R", color=C_ROLL);  dpg.add_text(" Roll   ", color=C_DIM)
                dpg.add_text("P", color=C_PITCH); dpg.add_text(" Pitch  ", color=C_DIM)
                dpg.add_text("Y", color=C_YAW);   dpg.add_text(" Yaw",     color=C_DIM)

        return tags