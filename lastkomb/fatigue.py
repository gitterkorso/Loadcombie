"""Repeated characteristic vehicle passages for nominal normal-stress fatigue.

The IL must give the response at the same section to a downward 1 kN axle
distributed over both wheel tracks on the specified path. No horizontal-load
history is represented. Normal-stress S–N curve: EN 1993-1-9:2005, §7.1.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from copy import deepcopy

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .analysis import quantity_label
from .qt_notation import engineering_html


def cyclic_rainflow(history):
    """Return full cycles per repeat of a closed block using E1049 range pairing."""
    h = np.asarray(history, dtype=float)
    if h.ndim != 1 or len(h) < 2 or not np.isfinite(h).all() or h[0] != h[-1]:
        raise ValueError("Lastförloppet måste vara ändligt och slutet.")
    # Start at a global extreme, including when the unloaded point lies between
    # the tensile and compressive peaks. Keep the closing reversal only once.
    h = h[:-1]
    h = h[np.r_[True, h[1:] != h[:-1]]]
    if len(h) > 1 and h[0] == h[-1]:
        h = h[:-1]
    if len(h) < 2:
        return {}
    turning = h[np.sign(h - np.roll(h, 1)) != np.sign(np.roll(h, -1) - h)]
    pivot = int(np.argmax(turning))
    cycle = np.r_[turning[pivot:], turning[: pivot + 1]]
    counts = Counter()
    stack = []
    for stress in cycle:
        stack.append(float(stress))
        while len(stack) >= 3:
            previous = abs(stack[-2] - stack[-3])
            latest = abs(stack[-1] - stack[-2])
            if latest < previous:
                break
            if len(stack) == 3:
                counts[previous] += 0.5
                del stack[0]
            else:
                counts[previous] += 1
                del stack[-3:-1]
    for left, right in zip(stack, stack[1:]):
        counts[abs(right - left)] += 0.5
    return dict(sorted(counts.items(), reverse=True))


@dataclass
class Passage:
    positions: np.ndarray
    stress: np.ndarray
    spectrum: list  # rows: range [MPa], cycles per passage, S–N life, damage
    damage: float
    passages: int | None  # None when there is no modelled damage


def _number(params, key, name, positive=True):
    try:
        x = float(str(params[key]).strip().replace(",", "."))
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Ange {name}.") from None
    if not math.isfinite(x) or (positive and x <= 0):
        raise ValueError(f"Ogiltigt värde för {name}.")
    return x


def miner_spectrum(counts, category, gamma_mf):
    if not all(math.isfinite(v) and v > 0 for v in (category, gamma_mf)):
        raise ValueError("Detaljkategori och γMf måste vara positiva.")
    reference = category / gamma_mf
    knee = reference * (2 / 5) ** (1 / 3)
    cutoff = knee * (5 / 100) ** (1 / 5)
    crosses_knee = max(counts, default=0) >= knee
    rows = []
    for stress, n in counts.items():
        if stress >= knee:
            life = 2e6 * (reference / stress) ** 3
        elif crosses_knee and stress >= cutoff:
            life = 5e6 * (knee / stress) ** 5
        else:
            life = math.inf
        rows.append((stress, n, life, n / life))
    return rows


def passage(part, params, config=None):
    if part is None or part.kind != "il":
        raise ValueError("Välj en influenslinje för det undersökta snittet.")
    if not params.get("axle_path"):
        raise ValueError("Bekräfta hur enhetslasten är fördelad över hjulspåren.")
    front = _number(params, "front", "främre axellast")
    rear = _number(params, "rear", "bakre axellast")
    spacing = _number(params, "spacing", "axelavstånd")
    area = _number(params, "area", "tvärsnittsarea")
    category = _number(params, "category", "vald detaljkategori")
    gamma = _number(params, "gamma", "γMf")
    factors = {"SF1": 1000 / area}
    if not params.get("axial_only"):
        for i in (1, 2):
            name = quantity_label(f"SM{i}", config or {}, part.name)
            k = _number(params, f"k{i}", f"fiberkoefficient för {name}", positive=False)
            if k:
                factors[f"SM{i}"] = k
    x = np.array([p["pos"] for p in part.points], dtype=float)
    order = np.argsort(x)
    x = x[order]
    if len(x) < 2 or not np.isfinite(x).all() or np.any(np.diff(x) <= 0):
        raise ValueError("Influenslinjen behöver minst två entydiga lastlägen med olika koordinater.")
    influence = np.zeros_like(x)
    for quantity, factor in factors.items():
        name = quantity_label(quantity, config or {}, part.name)
        if quantity not in part.quantities:
            raise ValueError(f"{name} saknas i influenslinjen.")
        expected = "kN/kN" if quantity == "SF1" else "kNm/kN"
        if part.units.get(quantity) != expected:
            raise ValueError(f"{name} behöver enheten {expected}.")
        y = part.values[order, 0, part.quantities.index(quantity)].copy()
        if not np.isfinite(y).all():
            raise ValueError(f"{name} har saknade värden i influenslinjen.")
        # Permit output rounding only. Extrapolating an incomplete IL to zero
        # would fabricate entry/exit peaks and an incorrect damage spectrum.
        tolerance = np.max(np.abs(y)) * 1e-8
        if max(abs(y[0]), abs(y[-1])) > tolerance:
            raise ValueError(
                f"{name} har ett skilt-från-noll ändvärde. "
                "Använd en fullständig influenslinje med noll i båda ändarna."
            )
        y[[0, -1]] = 0
        influence += factor * y
    # Every extremum of this piecewise-linear two-axle convolution is at a
    # breakpoint of one of the axles. Include both entry and exit.
    positions = np.unique(np.r_[x, x + spacing])
    stress = front * np.interp(positions, x, influence, left=0, right=0) + rear * np.interp(
        positions - spacing, x, influence, left=0, right=0
    )
    stress[[0, -1]] = 0
    rows = miner_spectrum(cyclic_rainflow(stress), category, gamma)
    damage = math.fsum(row[3] for row in rows)
    return Passage(positions, stress, rows, damage, math.floor(1 / damage) if damage > 0 else None)


def _sv(value, significant=7):
    return f"{value:.{significant}g}".replace(".", ",")


class FatiguePanel(QWidget):
    """Save per-IL setup via on_save(config['fatigue']); load(part, config) restores it."""

    _fields = (
        ("front", "Axel vid x [kN]", "80"),
        ("rear", "Axel vid x − a [kN]", "40"),
        ("spacing", "Axelavstånd [m]", "3"),
        ("area", "Area A [mm²]", ""),
        ("k1", "k₁", ""),
        ("k2", "k₂", ""),
        ("category", "Detaljkategori ΔσC [MPa]", ""),
        ("gamma", "γMf", "1"),
    )

    def __init__(self, on_save, parent=None):
        super().__init__(parent)
        self.on_save = on_save
        self.part, self.stored = None, {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(widgetResizable=True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        self.heading = QLabel("Utmattning vid en fordonspassage")
        self.heading.setObjectName("heading")
        layout.addWidget(self.heading)
        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.axle_path = QCheckBox("IL avser 1 kN nedåt, fördelat över båda hjulspåren på vald körlinje")
        self.axle_path.toggled.connect(self._invalidate)
        layout.addWidget(self.axle_path)
        grid = QGridLayout()
        forms = [QFormLayout(), QFormLayout()]
        grid.addLayout(forms[0], 0, 0)
        grid.addLayout(forms[1], 0, 1)
        self.fields = {}
        self.bending_labels = {}
        for i, (key, label, default) in enumerate(self._fields):
            editor = QLineEdit(default)
            editor.setMaximumWidth(125)
            editor.textEdited.connect(self._invalidate)
            field_label = QLabel(label)
            forms[i // 4].addRow(field_label, editor)
            if key in ("k1", "k2"):
                self.bending_labels[key] = field_label
            self.fields[key] = editor
        layout.addLayout(grid)
        self.axial_only = QCheckBox("Beräkna endast axiell normalspänning N/A")
        self.axial_only.toggled.connect(self._axial_changed)
        layout.addWidget(self.axial_only)
        self.formula = QLabel()
        self.formula.setWordWrap(True)
        self.formula.setObjectName("quiet")
        layout.addWidget(self.formula)
        self.run = QPushButton("Beräkna spektrum och skadeandel")
        self.run.setObjectName("primary")
        self.run.clicked.connect(self._calculate)
        layout.addWidget(self.run, alignment=Qt.AlignmentFlag.AlignLeft)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Δσ [MPa]", "Cykler/passage", "Livslängd Ni [cykler]", "Skadeandel ni/Ni"]
        )
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(130)
        layout.addWidget(self.table)
        foot = QLabel(
            "Underlaget är en upprepad passage i positiv x-riktning med vertikala axellaster. "
            "Bromskraft fordrar ett separat lastförlopp. Rainflow-räkningen omfattar en sluten passage. "
            "Skadan summeras med Palmgren–Miner och normalkurvan i EN 1993-1-9:2005, 7.1."
        )
        foot.setObjectName("quiet")
        foot.setWordWrap(True)
        layout.addWidget(foot)
        layout.addStretch()
        self.load(None, {})

    def _invalidate(self, *_args):
        if hasattr(self, "table"):
            self.table.setRowCount(0)
            self.summary.setText("")

    def _axial_changed(self, checked):
        for key in ("k1", "k2"):
            self.fields[key].setEnabled(not checked)
        self._invalidate()

    def load(self, part, config):
        if part is not None and part.kind != "il":
            part = None
        self.config = config
        name = part.name if part is not None else ""
        m1, m2 = (quantity_label(q, config, name) for q in ("SM1", "SM2"))
        self.bending_labels["k1"].setText(engineering_html(f"k₁ för {m1} [MPa/kNm]"))
        self.bending_labels["k2"].setText(engineering_html(f"k₂ för {m2} [MPa/kNm]"))
        axis1, axis2 = (m.split()[0][-1] for m in (m1, m2))
        self.formula.setText(
            f"Nominell fiberspänning σ = 1 000·N/A + k₁·M<sub>{axis1}</sub> + k₂·M<sub>{axis2}</sub>. "
            "Ange k med tecken för en bestämd fiber i Abaqus lokala axlar. "
            "Med avstånd r i mm och I i mm⁴ är k = ±10⁶·r/I. "
            "Välj detaljkategori för den verkliga svetsen och aktuell godstjocklek."
        )
        saved = config.get("fatigue", {})
        if self.part is part and self.stored == saved and self.info.text():
            return
        self.part, self.stored = part, deepcopy(saved)
        params = self.stored.get(part.name, {}) if part is not None else {}
        for key, _, initial in self._fields:
            self.fields[key].setText(str(params.get(key, initial)).replace(".", ","))
        self.axial_only.setChecked(bool(params.get("axial_only")))
        self.axle_path.setChecked(bool(params.get("axle_path")))
        self._axial_changed(self.axial_only.isChecked())
        self.run.setEnabled(part is not None)
        self.info.setText(
            f"{part.name} · Linjär interpolation mellan lastlägen. Axellasterna gäller hela axeln."
            if part is not None
            else "Välj hängarens influenslinje i projektträdet för att beräkna spänningsvidder."
        )
        if params and part is not None:
            self._calculate(save=False)

    def _calculate(self, _checked=False, *, save=True):
        params = {key: field.text() for key, field in self.fields.items()}
        params.update(axle_path=self.axle_path.isChecked(), axial_only=self.axial_only.isChecked())
        self._invalidate()
        try:
            result = passage(self.part, params, self.config)
        except (ValueError, ArithmeticError) as exc:
            self.summary.setText(engineering_html(str(exc)))
            return
        life = (
            f"{result.passages:,} hela passager till D = 1".replace(",", " ")
            if result.passages is not None
            else "D = 0 per passage enligt vald S–N-kurva"
        )
        self.summary.setText(
            f"{life}  ·  D/passage = {_sv(result.damage)}  ·  "
            f"σmin = {_sv(result.stress.min())} MPa, σmax = {_sv(result.stress.max())} MPa"
        )
        self.table.setRowCount(len(result.spectrum))
        for r, row in enumerate(result.spectrum):
            for c, value in enumerate(row):
                cell = QTableWidgetItem("∞" if math.isinf(value) else _sv(value))
                cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, cell)
        if save:
            self.stored[self.part.name] = params
            self.on_save(deepcopy(self.stored))
