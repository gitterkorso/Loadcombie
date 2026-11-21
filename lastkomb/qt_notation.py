"""Typeset engineering symbols while preserving plain text for Qt accessibility."""

from html import escape

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QMenu,
    QStatusBar,
    QStyle,
    QStyledItemDelegate,
    QStyleOption,
    QStyleOptionComboBox,
    QStyleOptionHeader,
    QStyleOptionMenuItem,
    QStyleOptionViewItem,
)

from .analysis import notation_runs

# The default is to typeset a generated engineering label. Imported load names
# set this role to False so strings supplied by the user are displayed verbatim.
NOTATION_ROLE = Qt.ItemDataRole.UserRole + 1


def engineering_html(text):
    """Escape a plain label and lower only its mathematical index letters."""
    return "".join(f"<sub>{escape(run)}</sub>" if sub else escape(run) for run, sub in notation_runs(text))


def _has_subscript(text):
    return any(sub for _, sub in notation_runs(text))


def _draw_text(painter, rect, text, font, color, alignment=Qt.AlignmentFlag.AlignLeft):
    """Draw a single clipped line using baseline shifts and the original Latin glyphs."""
    small = QFont(font)
    if font.pixelSize() > 0:
        small.setPixelSize(max(1, round(font.pixelSize() * 0.78)))
    else:
        small.setPointSizeF(font.pointSizeF() * 0.78)
    metrics = QFontMetricsF(font, painter.device())
    small_metrics = QFontMetricsF(small, painter.device())
    text = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    runs = [
        (run, sub, (small_metrics if sub else metrics).horizontalAdvance(run))
        for run, sub in notation_runs(text)
    ]
    width = sum(advance for _, _, advance in runs)
    x = float(rect.left())
    if alignment & Qt.AlignmentFlag.AlignHCenter:
        x += max(0, (rect.width() - width) / 2)
    elif alignment & Qt.AlignmentFlag.AlignRight:
        x += max(0, rect.width() - width)
    baseline = rect.top() + (rect.height() + metrics.ascent() - metrics.descent()) / 2
    painter.save()
    painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
    painter.setPen(color)
    for run, sub, advance in runs:
        painter.setFont(small if sub else font)
        y = baseline + (metrics.height() * 0.2 if sub else 0)
        painter.drawText(QPointF(x, y), run)
        x += advance
    painter.restore()


class _NotationDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        opt = QStyleOptionViewItem(option)
        # The app uses a pale green selection in light mode and a dark green
        # selection in dark mode. The normal text colour contrasts with both.
        color = opt.palette.color(QPalette.ColorRole.Text)
        opt.palette.setColor(QPalette.ColorRole.HighlightedText, color)
        if index.data(NOTATION_ROLE) is False or not _has_subscript(text):
            return super().paint(painter, opt, index)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style()
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, widget).adjusted(
            2, 0, -2, 0
        )
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        _draw_text(painter, rect, text, opt.font, color, opt.displayAlignment)


class NotationComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_NotationDelegate(self))

    def paintEvent(self, event):
        text = self.currentText()
        if self.currentData(NOTATION_ROLE) is False or not _has_subscript(text):
            return super().paintEvent(event)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter = QPainter(self)
        opt.currentText = ""
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt, painter, self)
        self.style().drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt, painter, self)
        rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, self
        )
        _draw_text(painter, rect, text, self.font(), opt.palette.color(QPalette.ColorRole.ButtonText))


class NotationHeader(QHeaderView):
    def __init__(self, parent):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.notation_columns = set()

    def paintSection(self, painter, rect, index):
        text = self.model().headerData(index, self.orientation(), Qt.ItemDataRole.DisplayRole) or ""
        if index not in self.notation_columns or not _has_subscript(text):
            return super().paintSection(painter, rect, index)
        opt = QStyleOptionHeader()
        self.initStyleOption(opt)
        opt.rect = rect
        self.initStyleOptionForIndex(opt, index)
        label_rect = self.style().subElementRect(QStyle.SubElement.SE_HeaderLabel, opt, self)
        opt.text = ""
        painter.save()
        self.style().drawControl(QStyle.ControlElement.CE_Header, opt, painter, self)
        _draw_text(
            painter,
            label_rect,
            text,
            self.font(),
            opt.palette.color(QPalette.ColorRole.ButtonText),
            opt.textAlignment,
        )
        painter.restore()


class NotationStatusBar(QStatusBar):
    def paintEvent(self, event):
        text = self.currentMessage()
        if not _has_subscript(text):
            return super().paintEvent(event)
        painter = QPainter(self)
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelStatusBar, opt, painter, self)
        grip = (
            self.style().pixelMetric(QStyle.PixelMetric.PM_SizeGripSize, opt, self)
            if self.isSizeGripEnabled()
            else 0
        )
        rect = self.contentsRect().adjusted(6, 0, -grip - 2, 0)
        _draw_text(painter, rect, text, self.font(), self.palette().color(QPalette.ColorRole.WindowText))


class NotationMenu(QMenu):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        for action in self.actions():
            if not action.property("engineeringLabel") or not _has_subscript(action.text()):
                continue
            opt = QStyleOptionMenuItem()
            self.initStyleOption(opt, action)
            opt.rect = self.actionGeometry(action)
            if not opt.rect.intersects(event.rect()):
                continue
            text, opt.text = opt.text, ""
            painter.fillRect(opt.rect, opt.palette.brush(QPalette.ColorRole.Window))
            self.style().drawControl(QStyle.ControlElement.CE_MenuItem, opt, painter, self)
            margin = 10
            if opt.menuHasCheckableItems:
                margin += self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, opt, self) + 4
            rect = opt.rect.adjusted(margin, 0, -8, 0)
            _draw_text(painter, rect, text, opt.font, opt.palette.color(QPalette.ColorRole.Text))
