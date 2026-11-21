"""Check the rendered Qt notation, its accessible text and unchanged data keys."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from lastkomb import qt_notation as notation


def test_rich_text_escapes_input_and_uses_latin_subscripts():
    assert notation.engineering_html("Max My (SM1) & Mz (SM2) < 1") == (
        "Max M<sub>y</sub> (SM1) &amp; M<sub>z</sub> (SM2) &lt; 1"
    )
    assert notation.engineering_html("N (SF1)") == "N (SF1)"


def test_painter_lowers_and_reduces_only_the_index():
    app = QApplication.instance() or QApplication([])
    image = QImage(280, 52, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    calls = []

    class RecordingPainter(QPainter):
        def drawText(self, point, text):
            calls.append((text, point.y(), self.font().pixelSize()))
            super().drawText(point, text)

    font = QFont(app.font())
    font.setPixelSize(22)
    painter = RecordingPainter(image)
    try:
        notation._draw_text(painter, QRect(0, 0, 280, 52), "My (SM1)", font, QColor("black"))
    finally:
        painter.end()
    main, index, suffix = calls
    assert [main[0], index[0], suffix[0]] == ["M", "y", " (SM1)"]
    assert index[1] > main[1] == suffix[1]
    assert index[2] < main[2] == suffix[2]


def test_open_and_collapsed_quantity_picker_typeset_but_keep_original_ids(monkeypatch):
    app = QApplication.instance() or QApplication([])
    box = notation.NotationComboBox()
    seen = []
    paint = notation._draw_text

    def recorded(painter, rect, text, *args):
        seen.append(text)
        paint(painter, rect, text, *args)

    monkeypatch.setattr(notation, "_draw_text", recorded)
    box.addItem("My (SM1)", "SM1")
    box.addItem("Mz (SM2)", "SM2")
    box.addItem("Last My (SM1)", "user step")
    box.setItemData(2, False, notation.NOTATION_ROLE)
    try:
        box.show()
        app.processEvents()
        box.grab()
        assert "My (SM1)" in seen
        seen.clear()
        box.showPopup()
        app.processEvents()
        box.view().viewport().grab()
        assert "My (SM1)" in seen and "Mz (SM2)" in seen
        assert "Last My (SM1)" not in seen
        box.setCurrentIndex(1)
        assert box.currentData() == "SM2" and box.currentText() == "Mz (SM2)"
    finally:
        box.hidePopup()
        box.close()
