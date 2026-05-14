"""
main.py
-------
Entry point for the Frozen Shoulder Rehab IMU Monitor.

Just wires up the components and starts the app.
All the real logic lives in the other files.

Run with:
    python main.py
"""

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from gui.app import App


def main():
    # 1. Create the shared state object (used by all components)
    state = AppState()

    # 2. Create the BLE manager and start scanning in the background
    ble = BLEManager(state)
    ble.start()

    # 3. Create the calibration handler
    calibration = Calibration(state)

    # 4. Create the angle processor (reads state, computes joints, writes back)
    angle_processor = AngleProcessor(state, calibration)

    # 5. Create and run the GUI (this blocks until the window is closed)
    app = App(state, ble, calibration, angle_processor)
    app.run()

    print("App closed. Goodbye.")


if __name__ == "__main__":
    main()
