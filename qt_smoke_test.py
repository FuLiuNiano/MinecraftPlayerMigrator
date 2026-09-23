import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel

app = QApplication(sys.argv)
window = QLabel("Qt packaging test")
window.show()
timer = QTimer()
timer.setSingleShot(True)
timer.timeout.connect(app.quit)
timer.start(1000)
raise SystemExit(app.exec())
