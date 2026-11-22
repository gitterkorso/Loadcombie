"""Inspect the independent extrema at one sampled station in the active result."""

from dataclasses import dataclass
from html import escape
from threading import Event

import numpy as np
from PySide6.QtCore import QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTextBrowser, QToolButton, QVBoxLayout

from .activity import CalculationIndicator
from .analysis import fmt, quantity_label
from .engine import envelope, evaluate
from .qt_notation import engineering_html


@dataclass(frozen=True)
class SectionRow:
    quantity: str
    unit: str
    maximum: float
    minimum: float
    max_index: int
    min_index: int


def section_rows(result, config, first, stop, curve=None):
    """Indices address result.ids. Points sharing a station remain independent."""
    part = result.part
    quantities = part.quantities
    targets = list(quantities)
    if part.kind == "plate" and {"S11", "S22", "S12"}.issubset(quantities):
        targets.append("Mises")
    values = result.values[first:stop]
    rows = []
    for target in targets:
        if config.get("_cancelled") and config["_cancelled"]():
            return []
        if curve is not None:
            hi = lo = (curve.values[first:stop] if target == result.target else
                       evaluate(values, curve.factors, quantities, target))
        elif target == result.target:
            hi = result.bounds["max"][first:stop]
            lo = result.bounds["min"][first:stop]
        elif part.kind == "il":
            hi = lo = values[:, 0, quantities.index(target)]
        else:
            bounds = envelope(values, result.steps, quantities, target, config)
            hi, lo = bounds["max"], bounds["min"]
        i, j = int(np.argmax(hi)), int(np.argmin(lo))
        rows.append(SectionRow(target, part.units.get(target, "MPa"), hi[i], lo[j], first + i, first + j))
    return rows


class _SectionJob(QRunnable):
    def __init__(self, generation, signal, result, config, first, stop, curve):
        super().__init__()
        self.generation, self.signal = generation, signal
        self.args = result, config, first, stop, curve

    def run(self):
        try:
            rows = section_rows(*self.args)
            self.signal.emit(self.generation, rows, "")
        except Exception as exc:
            self.signal.emit(self.generation, [], str(exc))


class SectionInspector(QFrame):
    finished = Signal(int, object, str)
    closed = Signal()
    selected = Signal(str, int, str)

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 3, 6, 5)
        box.setSpacing(3)
        head = QHBoxLayout()
        title = QLabel("Snittgranskare")
        title.setObjectName("heading")
        head.addWidget(title)
        head.addStretch()
        close = QToolButton()
        close.setText("×")
        close.setToolTip("Stäng snittgranskaren")
        close.setAccessibleName(close.toolTip())
        close.clicked.connect(self.dismiss)
        head.addWidget(close)
        box.addLayout(head)
        self.caption = QLabel()
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(self.caption)
        self.curve_title = QLabel()
        self.curve_title.setTextFormat(Qt.TextFormat.RichText)
        self.curve_title.setWordWrap(True)
        box.addWidget(self.curve_title)
        self.activity = CalculationIndicator("Beräknar snittet…", self)
        box.addWidget(self.activity)
        self.contents = QTextBrowser()
        self.contents.setFrameShape(QFrame.Shape.NoFrame)
        self.contents.setOpenLinks(False)
        self.contents.setAccessibleName("Snittvärden")
        self.contents.anchorClicked.connect(self.follow)
        box.addWidget(self.contents)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self, singleShot=True, interval=60)
        self.timer.timeout.connect(self.compute)
        self.finished.connect(self.present)
        self.cancelled = Event()
        self.generation = 0
        self.active = False
        self.result = None
        self.position = None
        self.curve = None
        self.curve_key = None
        self.rows = []
        self.hide()

    def suspend(self):
        self.timer.stop()
        self.cancelled.set()
        self.pool.clear()
        self.activity.set_busy(False)
        self.generation += 1
        self.position = None
        self.curve = None
        self.curve_key = None
        self.result = None
        self.rows = []
        self.hide()

    def dismiss(self):
        self.suspend()
        self.active = False
        self.closed.emit()

    def shutdown(self):
        self.suspend()
        self.pool.waitForDone()

    def inspect(self, result, positions, idx, config, mode, direction, curve):
        position = positions[idx]
        if result is self.result and self.position == position and curve is self.curve:
            return
        self.suspend()
        self.active = True
        self.result, self.position = result, position
        self.curve, self.curve_key = curve, direction if curve is not None else None
        self.first = int(positions.searchsorted(position, side="left"))
        self.stop = int(positions.searchsorted(position, side="right"))
        self.cancelled = Event()
        self.config = {**config, "_cancelled": self.cancelled.is_set}
        part = result.part
        coord = {"plate": "Läge", "il": "Lastens x"}.get(part.kind, "x")
        count = self.stop - self.first
        point = part.points[result.ids[idx]]
        context = [part.name]
        if point.get("side"):
            context.append(point["side"])
        context += ["Enhetslast" if part.kind == "il" else mode,
                    f"{count} {'punkt' if count == 1 else 'punkter'}"]
        self.caption.setText(f"{coord} = {fmt(position, 8)} m\n" + " · ".join(context))
        self.curve_title.setVisible(curve is not None)
        if curve is not None:
            if curve.group == "COMB":
                self.curve_title.setText("Fast kombination · " + engineering_html(curve.name))
            else:
                self.curve_title.setText("Lastfall · " + escape(curve.name))
        self.contents.hide()
        self.show()
        self.activity.set_busy(True)
        self.timer.start()

    def compute(self):
        self.pool.start(_SectionJob(self.generation, self.finished, self.result, self.config,
                                    self.first, self.stop, self.curve))

    def present(self, generation, rows, error):
        if generation != self.generation:
            return
        self.activity.set_busy(False)
        self.rows = rows
        if error:
            self.contents.setPlainText(error)
        else:
            il = self.result.part.kind == "il"
            single_value = (il or self.curve is not None) and self.stop - self.first == 1
            headings = ["Ordinat" if il else "Värde"] if single_value else ["Max", "Min"]
            table = ["<tr><th align='left'>Storhet</th>"
                     + "".join(f"<th align='right'>{h}</th>" for h in headings) + "</tr>"]
            for i, row in enumerate(rows):
                name = quantity_label(row.quantity, self.config, self.result.part.name)
                cells = [f"<td>{engineering_html(name)} [{escape(row.unit)}]</td>"]
                for direction, value in (("max", row.maximum), ("min", row.minimum)):
                    if direction == "min" and single_value:
                        continue
                    cells.append(f'<td align="right"><a href="#{i}-{direction}">{fmt(value, 6)}</a></td>')
                table.append("<tr>" + "".join(cells) + "</tr>")
            if il:
                note = "Klicka på en ordinat för att granska punkten."
            elif self.curve is not None:
                note = ("Värdena gäller samma lastplacering." if single_value else
                        "Max och min gäller snittets punkter med samma lastplacering. "
                        "Olika punkter kan styra.")
            else:
                note = ("Styrande punkt och lastkombination kan skilja sig mellan extremvärden. "
                        "Klicka på ett värde för att se samtidiga värden.")
            self.contents.setHtml("<table width='100%' cellspacing='3'>" + "".join(table)
                                  + "</table><p>" + note + "</p>")
        self.contents.document().setTextWidth(max(150, self.width() - 26))
        height = int(self.contents.document().size().height()) + 8
        self.contents.setFixedHeight(max(45, min(178, height)))
        self.contents.show()

    def follow(self, url):
        index, _, direction = url.fragment().partition("-")
        if direction not in ("max", "min") or not index.isdigit() or int(index) >= len(self.rows):
            return
        row = self.rows[int(index)]
        idx = row.max_index if direction == "max" else row.min_index
        self.selected.emit(row.quantity, idx, self.curve_key or direction)
