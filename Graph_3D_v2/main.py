"""
main.py
-------
Entry point. Wires all components, starts BLE, launches Qt window.

Run with:
    python main.py
"""

import sys
from PyQt5.QtWidgets import QApplication

from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from gui.app import App


def main():
    state          = AppState()
    ble            = BLEManager(state)
    calibration    = Calibration(state)
    angle_processor = AngleProcessor(state, calibration)

    ble.start()

    qt_app = QApplication(sys.argv)
    window = App(state, ble, calibration, angle_processor)
    window.show()
    sys.exit(qt_app.exec_())


if __name__ == "__main__":
    main()
