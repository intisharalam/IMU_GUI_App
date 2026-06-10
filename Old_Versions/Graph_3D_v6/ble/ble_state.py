"""
ble_state.py
------------
Holds all the data that the BLE thread writes and the GUI thread reads.

Because two threads access this at the same time (BLE + GUI), every read
and write goes through a threading.Lock() to avoid race conditions.

Nothing in here does any maths — it just stores values safely.
"""

import threading
import collections
import numpy as np


# How many data points to keep in the scrolling plot buffers.
# At 50 Hz, 500 points = 10 seconds of history.
PLOT_BUFFER_SIZE = 500

# The three IMU slot names used everywhere in the app.
SLOT_NAMES = ["wrist", "arm", "chest"]


class IMUSlot:
    """
    Stores all data for one IMU sensor.

    One of these is created for each of the three sensors.
    The BLE thread writes into it; the GUI thread reads from it.
    """

    def __init__(self, name: str):
        self.name = name  # "wrist", "arm", or "chest"

        # --- Connection info ---
        self.connected = False
        self.address = None          # MAC address string once found
        self.packet_count = 0
        self.sync_offset_ms = None   # time offset after a sync request

        # --- Latest raw quaternion from the sensor ---
        # w=1, x=y=z=0 means no rotation (identity)
        self.quat_w = 1.0
        self.quat_x = 0.0
        self.quat_y = 0.0
        self.quat_z = 0.0

        # --- Plot history: raw Euler angles for the left-panel plots ---
        # deque automatically drops old values when full
        self.times  = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.rolls  = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.pitches = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.yaws   = collections.deque(maxlen=PLOT_BUFFER_SIZE)

        # --- BleakClient reference so we can send commands back ---
        self.client = None

        # --- Flag to flash the haptic button amber ---
        self.haptic_active = False

    def update_quaternion(self, w, x, y, z, timestamp):
        """
        Called by the BLE thread every time a new 16-byte packet arrives.
        Stores the quaternion and also computes Euler angles for the raw plot.
        """
        self.quat_w = w
        self.quat_x = x
        self.quat_y = y
        self.quat_z = z
        self.packet_count += 1

        # Convert quaternion to Euler angles (degrees) for display only.
        # These are NOT used for joint angle computation.
        roll, pitch, yaw = _quat_to_euler(w, x, y, z)
        self.times.append(timestamp)
        self.rolls.append(roll)
        self.pitches.append(pitch)
        self.yaws.append(yaw)

    def get_quaternion(self):
        """Returns the latest quaternion as a tuple (w, x, y, z)."""
        return (self.quat_w, self.quat_x, self.quat_y, self.quat_z)

    def get_plot_data(self):
        """
        Returns copies of the plot buffers as plain lists.

        Roll, pitch and yaw are phase-unwrapped before returning so the
        scrolling plots show smooth continuous signals instead of ±180°
        discontinuities. np.unwrap works in radians, so we convert in/out.
        """
        def unwrap_deg(deg_deque):
            lst = list(deg_deque)
            if len(lst) < 2:
                return lst
            return np.degrees(np.unwrap(np.radians(lst))).tolist()

        return (
            list(self.times),
            unwrap_deg(self.rolls),
            unwrap_deg(self.pitches),
            unwrap_deg(self.yaws),
        )


class AppState:
    """
    The single shared state object for the whole application.

    Create one instance in main.py and pass it to every component.
    Use the lock whenever reading or writing from different threads.
    """

    def __init__(self):
        # One lock shared across everything
        self.lock = threading.Lock()

        # One slot per IMU
        self.slots = {name: IMUSlot(name) for name in SLOT_NAMES}

        # --- Calibration ---
        # Set to True once the user clicks Calibrate and all 3 are connected
        self.calibrated = False

        # The neutral-pose quaternions captured at calibration time.
        # Dict of {"wrist": (w,x,y,z), "arm": (w,x,y,z), "chest": (w,x,y,z)}
        self.calibration_quats = {}

        # --- Computed joint angles (updated every frame after calibration) ---
        self.shoulder_flexion    = 0.0   # degrees
        self.shoulder_abduction  = 0.0   # degrees
        self.external_rotation   = 0.0   # degrees
        self.elbow_flexion       = 0.0   # degrees

        # Session maximums — largest angle seen this session
        self.max_flexion    = 0.0
        self.max_abduction  = 0.0
        self.max_ext_rot    = 0.0
        self.max_elbow      = 0.0

        # Plot history for joint angles (right panel)
        self.angle_times      = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.flexion_hist     = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.abduction_hist   = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.ext_rot_hist     = collections.deque(maxlen=PLOT_BUFFER_SIZE)
        self.elbow_hist       = collections.deque(maxlen=PLOT_BUFFER_SIZE)


        # --- User-configurable settings (written by SettingsPanel) ---
        self.affected_side       = "right"   # "right" | "left"
        self.haptic_rep          = True      # fire haptic on rep complete
        self.haptic_rom          = True      # fire haptic at ROM boundary
        self.haptic_deviation    = True      # fire haptic on plane deviation
        self.haptic_trunk        = True      # fire haptic on trunk lean
        self.haptic_hold         = True      # fire haptic on hold complete
        self.rom_goal_fraction   = 0.90      # goal sphere at X% of ROM limit
        self.trunk_lean_limit    = 10.0      # degrees before trunk haptic fires
        self.default_sets        = 3         # pre-filled in exercise panel
        self.default_reps        = 10        # pre-filled in exercise panel

        # --- Session / exercise state (written by GUI, read by panels) ---
        self.session_active      = False
        self.session_reps        = 0
        self.current_exercise    = ""
        self.rom_flex_limit      = 90.0   # goal sphere target degrees
        self.rom_abd_limit       = 90.0
        self.rom_rot_limit       = 45.0
        self.rom_int_rot_limit   = 45.0   # internal rotation ROM
        self.rom_elbow_limit     = 90.0
        self.rom_measured        = False
        self.haptic_log          = []     # list of (timestamp, reason) tuples
        self.plane_of_elevation  = 0.0    # degrees — 0=frontal(abd), 90=sagittal(flex)
        self.trunk_lean_deg      = 0.0    # chest IMU lateral roll during exercise

    def all_connected(self):
        """Returns True only if all three sensors are currently connected."""
        return all(self.slots[n].connected for n in SLOT_NAMES)

    def update_joint_angles(self, flexion, abduction, ext_rot, elbow, timestamp,
                             plane=0.0, trunk_lean=0.0):
        """
        Stores the latest computed joint angles and updates session maxima.
        Called by the calculation layer every frame after calibration.
        """
        self.shoulder_flexion   = flexion
        self.shoulder_abduction = abduction
        self.external_rotation  = ext_rot
        self.elbow_flexion      = elbow

        # Update session maximums (we only track positive/increase in ROM)
        self.max_flexion   = max(self.max_flexion,   abs(flexion))
        self.max_abduction = max(self.max_abduction, abs(abduction))
        self.max_ext_rot   = max(self.max_ext_rot,   abs(ext_rot))
        self.max_elbow     = max(self.max_elbow,     abs(elbow))
        self.plane_of_elevation = plane
        self.trunk_lean_deg     = trunk_lean

        # Append to history for the scrolling plot
        self.angle_times.append(timestamp)
        self.flexion_hist.append(flexion)
        self.abduction_hist.append(abduction)
        self.ext_rot_hist.append(ext_rot)
        self.elbow_hist.append(elbow)


# ── Helper: quaternion → Euler angles ────────────────────────────────────────
# This is only used here for the raw display plots, not for clinical angles.

import math

def _quat_to_euler(w, x, y, z):
    """
    Converts a quaternion (w, x, y, z) to Euler angles in degrees.
    Returns (roll, pitch, yaw).
    Uses the standard ZYX / aerospace convention.
    """
    # Roll (rotation around x-axis)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))

    # Pitch (rotation around y-axis)
    sinp = 2.0 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))   # clamp to avoid atan2 domain errors
    pitch = math.degrees(math.asin(sinp))

    # Yaw (rotation around z-axis)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.degrees(math.atan2(siny_cosp, cosy_cosp))

    return roll, pitch, yaw