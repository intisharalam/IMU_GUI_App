"""
render_main.py
--------------
Standalone entry point for the 3D arm renderer.

Runs ONLY the BLE connection + 3D VPython visualisation.
The main GUI (main.py) does NOT need to be running.

Use this to develop and test the 3D render independently.

Run with:
    python render_main.py

Folder structure expected:
    project/
    ├── ble/
    │   ├── ble_state.py
    │   └── ble_manager.py
    ├── calc/
    │   ├── calibration.py
    │   └── joint_angles.py
    ├── render/
    │   ├── __init__.py
    │   └── arm_render.py
    ├── main.py             (GUI entry point — separate)
    └── render_main.py      (this file)
"""

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from render.arm_render import ArmRender


def main():
    # 1. Shared state
    state = AppState()

    # 2. BLE — connects to all 3 IMUs in the background
    ble = BLEManager(state)
    ble.start()

    # 3. Calibration handler
    calibration = Calibration(state)

    # 4. Angle processor — keeps joint angles in AppState up to date
    angle_processor = AngleProcessor(state, calibration)

    # 5. Launch the 3D render — blocks on the main thread until closed
    print("[render_main] Starting 3D arm renderer.")
    print("[render_main] Once sensors connect, press Calibrate in the VPython window.")
    print("[render_main] Close the VPython window to quit.\n")

    renderer = ArmRender(state, angle_processor)
    renderer.run()          # blocks until the window is closed

    # 6. Clean up
    ble.stop()
    print("Renderer closed. Goodbye.")


if __name__ == "__main__":
    main()