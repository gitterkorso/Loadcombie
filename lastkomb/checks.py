"""Independent spot checks on imported characteristic responses."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .analysis import calculate, group_of, quantity_label
from .engine import default_config, envelope
from .qt_notation import NotationComboBox, engineering_html


def number(value):
    return format(float(value), ".8g").replace(".", ",")


def _value(text, *, positive=False, nonnegative=False):
    try:
        value = float(str(text).strip().replace(",", "."))
    except ValueError:
        raise ValueError("Fyll i ett tal i varje synligt talfält.") from None
    if not math.isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError("Kontrollera tecken och storlek på de angivna talen.")
    return value


def _column(part, step, q, config=None):
    if step not in part.steps or q not in part.quantities:
        raise ValueError("Välj ett importerat laststeg och en importerad storhet.")
    col = part.values[:, part.steps.index(step), part.quantities.index(q)]
    if len(col) == 0 or not np.isfinite(col).all():
        raise ValueError(
            f"{step} / {quantity_label(q, config or {}, part.name)} saknar fullständiga punktvärden."
        )
    return col


def check_identity(part, step, config=None):
    """Recompute a single isolated permanent step and compare every imported quantity."""
    cfg = default_config()
    cfg["groups"]["G"].update(gamma_u=1, gamma_f=1, gd=1, xi=1)
    cfg["mapping"][step] = dict(group="G", slot="", role="V")
    for q in part.quantities:
        raw = _column(part, step, q, config)
        result = envelope(raw[:, None, None], [step], [q], q, cfg)
        error = max(np.max(np.abs(raw - result[side])) for side in ("min", "max"))
        if error:
            raise ValueError(
                f"Avvikelse för {quantity_label(q, config or {}, part.name)}: {number(error)} {part.units[q]}."
            )
    count = len(part.points) * len(part.quantities)
    return f"{step} × 1,0 reproducerar samtliga {count} importerade värden exakt."


def check_symmetry(part, q, first, second, centre, coord_tol, value_tol, parity, config=None):
    """Match x ↔ 2c−x on the same line and side; ambiguous stations are inconclusive."""
    if parity not in (1, -1) or coord_tol < 0 or value_tol < 0:
        raise ValueError("Välj symmetritecken och icke-negativa toleranser.")
    a = _column(part, first, q, config)
    b = _column(part, second, q, config)
    clusters = defaultdict(list)
    for i, p in enumerate(part.points):
        if p.get("x") is None:
            raise ValueError("En punkt saknar x-koordinat för speglingen.")
        clusters[(p.get("side"), p.get("instance"))].append((p["x"], i))
    ambiguous = missing = off_centre = 0
    max_error, worst = -1.0, None
    for cluster in clusters.values():
        cluster.sort()
        xs = [x for x, _ in cluster]
        for x, i in cluster:
            target = 2 * centre - x
            lo = bisect_left(xs, target - coord_tol)
            hi = bisect_right(xs, target + coord_tol)
            p = part.points[i]
            match = []
            for _, j in cluster[lo:hi]:
                other = part.points[j]
                if all(
                    (coord not in p and coord not in other)
                    or (coord in p and coord in other and abs(p[coord] - other[coord]) <= coord_tol)
                    for coord in ("y", "z")
                ):
                    match.append(j)
            if not match:
                missing += 1
            elif len(match) > 1:
                ambiguous += 1
            else:
                j = match[0]
                if i != j:
                    off_centre += 1
                error = abs(a[i] - parity * b[j])
                if error > max_error:
                    max_error, worst = float(error), (x, part.points[j]["x"])
    if ambiguous or missing:
        raise ValueError(
            f"Ej entydig spegling. {missing} punkter saknar motpunkt och {ambiguous} har flera kandidater."
        )
    if not off_centre:
        raise ValueError("Underlaget saknar ett spegelpar utanför symmetrisnittet.")
    outcome = "Inom tolerans" if max_error <= value_tol else "Utanför tolerans"
    return (
        f"{outcome}. Största avvikelse {number(max_error)} {part.units[q]} vid x = "
        f"{number(worst[0])} och {number(worst[1])} m. {len(a)} punktjämförelser."
    )


def check_balance(part, q, step, external, tolerance, complete):
    if not complete:
        raise ValueError("Bekräfta att alla stödreaktioner för steget ingår i delen.")
    if q not in ("RF1", "RF2", "RF3") or part.units.get(q) != "kN":
        raise ValueError("Importera RF1, RF2 eller RF3 i kN för de tillhörande stödnoderna.")
    reactions = _column(part, step, q)
    nodes = {(p.get("instance"), p.get("node")) for p in part.points}
    if any(node is None for _, node in nodes) or len(nodes) != len(part.points):
        raise ValueError(
            "En stödnod saknas eller förekommer flera gånger. Reaktionerna kan inte summeras entydigt."
        )
    reaction_sum = math.fsum(reactions)
    residual = reaction_sum + external
    state = "Inom tolerans" if abs(residual) <= tolerance else "Utanför tolerans"
    return (
        f"{state}. Σ{q} = {number(reaction_sum)} kN för {len(nodes)} noder. "
        f"Med yttre last {number(external)} kN blir residualen {number(residual)} kN."
    )


def check_deflection(part, q, config, view, span, confirmed):
    if not confirmed or q not in ("U1", "U2", "U3") or part.units.get(q) != "m":
        raise ValueError("Välj en importerad U-komponent i meter och bekräfta att den är vertikal.")
    if not math.isfinite(span) or span <= 0:
        raise ValueError("Ange en positiv teoretisk spännvidd.")
    result = calculate(part, q, {**config, "mode": "TRAFFIC"}, {"disabled": view.get("disabled", [])})
    if result.warnings or len(result.ids) != len(part.points):
        raise ValueError("Ofullständiga förskjutningsvärden i den valda delen.")
    worst = np.maximum(np.abs(result.bounds["min"]), np.abs(result.bounds["max"]))
    i = int(np.argmax(worst))
    limit = span / 400
    x = part.points[result.ids[i]]["x"]
    state = "Inom L/400" if worst[i] <= limit else "Över L/400"
    return (
        f"{state} för {len(result.steps)} aktiva trafiksteg. Största |{q}| = "
        f"{number(worst[i] * 1000)} mm vid x = {number(x)} m. Gräns {number(limit * 1000)} mm."
    )


class ChecksPanel(QWidget):
    def __init__(self, on_save=None, parent=None):
        super().__init__(parent)
        self.on_save = on_save
        self.part = None
        self.config = {}
        self.view = {}
        outer = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(area)
        page = QWidget()
        area.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 6, 4, 6)
        self.mode = QComboBox()
        for key, label in (
            ("identity", "Egenlast med faktor 1"),
            ("symmetry", "Spegelsymmetri"),
            ("balance", "Reaktionsjämvikt"),
            ("deflection", "Nedböjning L/400"),
        ):
            self.mode.addItem(label, key)
        layout.addWidget(self.mode)
        self.scope = QLabel("Kontrollen använder hela den valda delen.")
        self.scope.setWordWrap(True)
        layout.addWidget(self.scope)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)

        self.g_step = QComboBox()
        self._form([("Permanent steg", self.g_step)])
        self.sym_q, self.step_a, self.step_b = NotationComboBox(), QComboBox(), QComboBox()
        self.parity = QComboBox()
        self.parity.addItem("Samma tecken", 1)
        self.parity.addItem("Motsatt tecken", -1)
        self.centre, self.xtol, self.stol = QLineEdit(), QLineEdit(), QLineEdit()
        self.sym_tolerance_label = QLabel("Värdetolerans")
        self._form(
            [
                ("Storhet", self.sym_q),
                ("Steg A", self.step_a),
                ("Speglat steg B", self.step_b),
                ("Förväntad relation", self.parity),
                ("Spegelplan x [m]", self.centre),
                ("Koordinattolerans [m]", self.xtol),
                (self.sym_tolerance_label, self.stol),
            ]
        )
        self.rf, self.rf_step = QComboBox(), QComboBox()
        self.force, self.ftol = QLineEdit(), QLineEdit()
        self.supports = QCheckBox("Delen omfattar alla stödreaktioner i steget")
        self._form(
            [
                ("Reaktion", self.rf),
                ("Laststeg", self.rf_step),
                ("Yttre last, tecknad [kN]", self.force),
                ("Residualtolerans [kN]", self.ftol),
                (None, self.supports),
            ]
        )
        self.u, self.span = QComboBox(), QLineEdit()
        self.vertical = QCheckBox("Vald komponent är vertikal i modellen")
        self._form(
            [("Förskjutning", self.u), ("Teoretisk spännvidd [m]", self.span), (None, self.vertical)]
        )
        self.run = QPushButton("Kör kontroll")
        self.run.clicked.connect(self._run)
        layout.addWidget(self.run)
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(
            self.result.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.result)
        layout.addStretch(1)
        self.mode.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.mode.currentIndexChanged.connect(self._update)
        self.sym_q.currentIndexChanged.connect(self._update)
        for widget in (self.g_step, self.step_a, self.step_b, self.parity, self.rf, self.rf_step, self.u):
            widget.currentIndexChanged.connect(self.result.clear)
        for widget in (self.centre, self.xtol, self.stol, self.force, self.ftol, self.span):
            widget.textEdited.connect(self.result.clear)
        for widget in (self.supports, self.vertical):
            widget.toggled.connect(self.result.clear)
        self.load(None, {}, {})

    def _form(self, rows):
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 4)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for label, widget in rows:
            if label is None:
                form.addRow(widget)
            else:
                form.addRow(label, widget)
        self.pages.addWidget(page)

    def load(self, part, config, view):
        self.part, self.config, self.view = part, config, view
        self.result.clear()
        saved = config.get("checks", {}).get(part.name, {}) if part else {}
        qs = part.quantities if part else []
        steps = [s for s in part.steps if s not in view.get("disabled", [])] if part else []
        g_steps = [s for s in steps if group_of(config, s) == "G"]
        widgets = [
            (self.g_step, g_steps, "g", "G_SELF"),
            (self.sym_q, qs, "q", view.get("target", "")),
            (self.step_a, steps, "a", "G_SELF"),
            (self.step_b, steps, "b", "G_SELF"),
            (self.rf, [q for q in qs if q.startswith("RF")], "rf", ""),
            (self.rf_step, steps, "rf_step", "G_SELF"),
            (self.u, [q for q in qs if q in ("U1", "U2", "U3")], "u", ""),
        ]
        for widget, opts, key, initial in widgets:
            widget.clear()
            for opt in opts:
                name = quantity_label(opt, config, part.name) if widget is self.sym_q else opt
                widget.addItem(name, opt)
            if opts:
                widget.setCurrentIndex(max(0, widget.findData(saved.get(key, initial))))
        self.mode.setCurrentIndex(max(0, self.mode.findData(saved.get("mode", "identity"))))
        self.parity.setCurrentIndex(max(0, self.parity.findData(saved.get("parity", 1))))
        for key, widget, initial in (
            ("centre", self.centre, "15"),
            ("xtol", self.xtol, "0,000001"),
            ("stol", self.stol, "0,001"),
            ("force", self.force, ""),
            ("ftol", self.ftol, "0,001"),
            ("span", self.span, ""),
        ):
            widget.setText(str(saved.get(key, initial)))
        self.supports.setChecked(bool(saved.get("complete_supports")))
        self.vertical.setChecked(bool(saved.get("vertical_axis")))
        self._update()

    def _update(self):
        mode = self.mode.currentData()
        if self.part:
            unit = self.part.units.get(self.sym_q.currentData(), "")
            self.sym_tolerance_label.setText(f"Värdetolerans [{unit}]")
        available = {
            "identity": self.g_step.count(),
            "symmetry": self.step_a.count(),
            "balance": self.rf.count(),
            "deflection": self.u.count(),
        }
        enabled = self.part is not None and bool(available.get(mode)) and self.part.kind != "il"
        self.run.setEnabled(enabled)
        notes = {
            "identity": "Prövar ett isolerat permanent steg mot importerade värden för hela delen.",
            "symmetry": "Välj ett lastfall och dess verkliga spegelfall. Samma steg kan väljas två gånger.",
            "balance": "ΣRF och den totala yttre lasten i samma riktning ska summera till noll.",
            "deflection": "Hela delen prövas med aktiva trafiksteg i karakteristisk kombination. Egentyngden utelämnas.",
        }
        unavailable = {
            "identity": "Ett permanent laststeg behövs.",
            "symmetry": "Välj en del med laststeg.",
            "balance": "Importera stödnodernas RF1, RF2 eller RF3.",
            "deflection": "Importera U1, U2 eller U3 för brobanan.",
        }
        self.scope.setText(notes[mode] if enabled else unavailable[mode])
        self.result.clear()

    def _run(self):
        mode = self.mode.currentData()
        settings = {
            "mode": mode,
            "g": self.g_step.currentText(),
            "q": self.sym_q.currentData() or "",
            "a": self.step_a.currentText(),
            "b": self.step_b.currentText(),
            "parity": self.parity.currentData(),
            "centre": self.centre.text(),
            "xtol": self.xtol.text(),
            "stol": self.stol.text(),
            "rf": self.rf.currentText(),
            "rf_step": self.rf_step.currentText(),
            "force": self.force.text(),
            "ftol": self.ftol.text(),
            "u": self.u.currentText(),
            "span": self.span.text(),
        }
        settings["complete_supports"] = self.supports.isChecked()
        settings["vertical_axis"] = self.vertical.isChecked()
        try:
            if mode == "identity":
                message = check_identity(self.part, settings["g"], self.config)
            elif mode == "symmetry":
                message = check_symmetry(
                    self.part,
                    settings["q"],
                    settings["a"],
                    settings["b"],
                    _value(settings["centre"]),
                    _value(settings["xtol"], nonnegative=True),
                    _value(settings["stol"], nonnegative=True),
                    settings["parity"],
                    self.config,
                )
            elif mode == "balance":
                message = check_balance(
                    self.part,
                    settings["rf"],
                    settings["rf_step"],
                    _value(settings["force"]),
                    _value(settings["ftol"], nonnegative=True),
                    self.supports.isChecked(),
                )
            else:
                message = check_deflection(
                    self.part,
                    settings["u"],
                    self.config,
                    self.view,
                    _value(settings["span"], positive=True),
                    self.vertical.isChecked(),
                )
        except ValueError as exc:
            self.result.setText(str(exc))
            return
        checked = deepcopy(self.config.get("checks", {}))
        if checked.get(self.part.name) != settings and self.on_save:
            checked[self.part.name] = settings
            self.on_save(checked)
        self.result.setText(engineering_html(message))
