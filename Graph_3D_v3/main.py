"""
main.py — ShoulderSense v3
--------------------------
Entry point. Run with:
    python main.py
"""
import sys
from PyQt5.QtWidgets import QApplication
from ble.ble_state import AppState
from ble.ble_manager import BLEManager
from calc.calibration import Calibration
from calc.joint_angles import AngleProcessor
from gui.main_window import MainWindow


def main():
    state = AppState()
    ble   = BLEManager(state); ble.start()
    cal   = Calibration(state)
    proc  = AngleProcessor(state, cal)
    app   = QApplication(sys.argv)
    win   = MainWindow(state, ble, cal, proc)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
