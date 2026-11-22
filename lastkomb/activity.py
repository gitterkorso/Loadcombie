"""Calculation feedback for worker threads and synchronous GUI actions."""

from contextlib import contextmanager

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QProgressBar, QWidget


@contextmanager
def wait_cursor():
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


class CalculationIndicator(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(text))
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setAccessibleName(text)
        bar.setFixedSize(72, 10)
        layout.addWidget(bar)
        self.delay = QTimer(self, singleShot=True, interval=350)
        self.delay.timeout.connect(self.show)
        self.hide()

    def set_busy(self, busy):
        if busy:
            if self.isHidden() and not self.delay.isActive():
                self.delay.start()
        else:
            self.delay.stop()
            self.hide()
