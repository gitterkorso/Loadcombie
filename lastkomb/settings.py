"""Editor for combination rules and model-dependent display settings."""

import math
import re
from copy import deepcopy

from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .engine import default_config, infer_step

GROUPS = {"G": "Permanent", "PED": "Gångtrafik", "VEH": "Fordon"}
COLORS = {"G": "#788391", "PED": "#3979a6", "VEH": "#c77936"}
FACTORS = {
    "gamma_u": ("γ og.", "Partialkoefficient för ogynnsam last"),
    "gamma_f": ("γ gyn.", "Partialkoefficient för gynnsam last"),
    "psi": ("ψ₀", "Kombinationsfaktor för variabel last"),
    "xi": ("ξ", "Reduktion av ogynnsam permanent last i 6.10b"),
    "gd": ("γd", "Multiplikator på ogynnsam partialkoefficient i STR"),
}
PERMANENT_FACTORS = ("gamma_u", "gamma_f", "xi", "gd")
SV = QLocale("sv_SE")


class FactorBox(QDoubleSpinBox):
    def textFromValue(self, value):
        return (
            self.locale().toString(value, "f", self.decimals()).rstrip("0").rstrip(",")
        )


def factor_input(factor, name):
    editor = FactorBox()
    editor.setLocale(SV)
    editor.setRange(0, 1000)
    editor.setDecimals(7)
    editor.setSingleStep(0.05)
    editor.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    editor.setAccessibleName(f"{name}, {FACTORS[factor][1]}")
    editor.setToolTip(FACTORS[factor][1])
    editor.setMinimumWidth(42)
    return editor


def choices(entries):
    box = QComboBox()
    for value, label in entries:
        box.addItem(label, value)
    return box


def readonly_item(value):
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def ped_slots(text):
    fields = []
    for field in re.split(r"[,;\s]+", text.strip()):
        if not field:
            continue
        if not field.isdigit() or len(field) > 2:
            raise ValueError(
                "Ange gångfält som tvåsiffriga nummer, exempelvis 02, 03, 04."
            )
        field = field.zfill(2)
        if field in fields:
            raise ValueError(f"Fält {field} förekommer flera gånger i samma mönster.")
        fields.append(field)
    return fields


def marker_positions(text):
    if not text.strip():
        return []
    try:
        values = [float(t.strip().replace(",", ".")) for t in text.split(";")]
        if not all(math.isfinite(value) for value in values):
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "Ange ändliga lägen i meter med semikolon mellan, exempelvis 7,5; 15."
        ) from exc
    return sorted(set(values))


class SettingsPanel(QWidget):
    """A draft is committed only when the user presses Tillämpa."""

    def __init__(self, on_apply, parent=None):
        super().__init__(parent)
        self.on_apply = on_apply
        self.steps = []
        self.config = {}
        self.factor_inputs = {}
        self.pattern_inputs = {}
        self.marker_inputs = {}
        self.step_inputs = {}
        self.permanent_rows = {}
        self.original_groups = {}
        self.next_group_key = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 2)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._build_rules()
        self._build_permanent_page()
        self._build_step_page()
        self._build_display()

        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        controls = QHBoxLayout()
        reset = QPushButton("Återställ förval")
        reset.setToolTip(
            "Läs in kursens standardvärden i formuläret. Tillämpa gör ändringen aktiv."
        )
        reset.clicked.connect(self._reset)
        apply = QPushButton("Tillämpa")
        apply.setDefault(True)
        apply.clicked.connect(self.apply_changes)
        controls.addWidget(reset)
        controls.addStretch(1)
        controls.addWidget(apply)
        layout.addLayout(controls)
        self.load(default_config(), [])

    def _scroll_tab(self, title):
        area = QScrollArea()
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidgetResizable(True)
        widget = QWidget()
        area.setWidget(widget)
        self.tabs.addTab(area, title)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(6, 8, 6, 6)
        return layout

    def _build_rules(self):
        layout = self._scroll_tab("Regler")
        self.factors = QTableWidget(len(GROUPS), len(FACTORS))
        self.factors.setAccessibleName("Koefficienter per lastgrupp")
        self.factors.setVerticalHeaderLabels(list(GROUPS.values()))
        for i, (name, (label, tip)) in enumerate(FACTORS.items()):
            header = readonly_item(label)
            header.setToolTip(tip)
            self.factors.setHorizontalHeaderItem(i, header)
        for row, group in enumerate(GROUPS):
            self.factors.verticalHeaderItem(row).setForeground(QColor(COLORS[group]))
            for col, factor in enumerate(FACTORS):
                if (factor == "xi" and group != "G") or (
                    factor == "psi" and group == "G"
                ):
                    self.factors.setItem(row, col, readonly_item("–"))
                    continue
                inp = factor_input(factor, GROUPS[group])
                self.factor_inputs[group, factor] = inp
                self.factors.setCellWidget(row, col, inp)
        self.factors.horizontalHeader().setMinimumSectionSize(42)
        self.factors.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.factors.verticalHeader().setDefaultSectionSize(32)
        self.factors.setFixedHeight(150)
        layout.addWidget(self.factors)
        self.exclusive = QCheckBox("Gångtrafik och fordon utesluter varandra")
        layout.addWidget(self.exclusive)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.ped = choices(
            [
                ("adverse", "Ogynnsamma fält"),
                ("P1", "Fast mönster P1"),
                ("P2", "Fast mönster P2"),
                ("custom", "Eget fast mönster"),
                ("all", "Alla aktiva fält"),
                ("alternative", "Ett fält i taget"),
            ]
        )
        self.veh = choices(
            [
                ("alternative", "Ett läge i taget"),
                ("adverse", "Ogynnsamma lägen summeras"),
            ]
        )
        self.veh.setToolTip("Ett läge i taget gäller för ett enda servicefordon.")
        self.ped_label = QLabel()
        self.veh_label = QLabel()
        form.addRow(self.ped_label, self.ped)
        form.addRow(self.veh_label, self.veh)
        for key, title in [("P1", "P1"), ("P2", "P2"), ("custom", "Eget mönster")]:
            inp = QLineEdit()
            inp.setPlaceholderText("02, 03, 04")
            inp.setAccessibleName(f"Gångfält för {title}")
            self.pattern_inputs[key] = inp
            form.addRow(title, inp)
        layout.addLayout(form)
        layout.addStretch(1)

    def _build_permanent_page(self):
        self.permanent_page = QWidget()
        layout = QVBoxLayout(self.permanent_page)
        explanation = QLabel(
            "För varje G-grupp väljs gynnsam eller ogynnsam faktor utifrån summan av gruppens bidrag. "
            "Lägg steg som ska bedömas oberoende i olika grupper. Koppla stegen under Steg."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.permanent_table = QTableWidget(0, 1 + len(PERMANENT_FACTORS))
        self.permanent_table.setAccessibleName("Egna permanenta lastgrupper")
        self.permanent_table.setHorizontalHeaderLabels(
            ["G-grupp", *(FACTORS[f][0] for f in PERMANENT_FACTORS)]
        )
        self.permanent_table.verticalHeader().hide()
        self.permanent_table.verticalHeader().setDefaultSectionSize(34)
        self.permanent_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.permanent_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        header = self.permanent_table.horizontalHeader()
        header.setMinimumSectionSize(46)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col, factor in enumerate(PERMANENT_FACTORS, 1):
            self.permanent_table.horizontalHeaderItem(col).setToolTip(
                FACTORS[factor][1]
            )
        layout.addWidget(self.permanent_table)
        controls = QHBoxLayout()
        self.add_permanent = QPushButton("Lägg till G-grupp")
        self.add_permanent.clicked.connect(self._new_group)
        self.remove_permanent = QPushButton("Ta bort vald G-grupp")
        self.remove_permanent.setToolTip(
            "Steg i den borttagna gruppen återgår till den permanenta grundgruppen."
        )
        self.remove_permanent.clicked.connect(self._remove_group)
        self.permanent_table.currentCellChanged.connect(
            lambda row, *_: self.remove_permanent.setEnabled(row >= 0)
        )
        controls.addWidget(self.add_permanent)
        controls.addWidget(self.remove_permanent)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.tabs.addTab(self.permanent_page, "G-grupper")

    def _insert_group(self, name, factors):
        # The key stays fixed as the user renames groups, including during a name swap.
        key = self.next_group_key
        self.next_group_key += 1
        row = self.permanent_table.rowCount()
        self.permanent_table.insertRow(row)
        name_edit = QLineEdit(name)
        name_edit.setAccessibleName("Namn på G-grupp")
        name_edit.setPlaceholderText("Gruppnamn")
        self.permanent_table.setCellWidget(row, 0, name_edit)
        editors = {}
        for col, f in enumerate(PERMANENT_FACTORS, 1):
            editor = factor_input(f, name)
            editor.setValue(float(factors[f]))
            editors[f] = editor
            self.permanent_table.setCellWidget(row, col, editor)
        self.permanent_rows[key] = (name_edit, editors)
        for group, *_ in self.step_inputs.values():
            group.addItem(f"G · {name}", key)
        name_edit.textChanged.connect(lambda text: self._rename_group(key, text))
        return key

    def _rename_group(self, key, name):
        label = name.strip() or "Namnlös grupp"
        for group, *_ in self.step_inputs.values():
            i = group.findData(key)
            if i >= 0:
                group.setItemText(i, f"G · {label}")
        for f, editor in self.permanent_rows[key][1].items():
            editor.setAccessibleName(f"{label}, {FACTORS[f][1]}")

    def _new_group(self, _checked=False, *, from_step=None):
        existing = {
            widget.text().strip().casefold()
            for widget, editors in self.permanent_rows.values()
        }
        base = from_step or "G-grupp"
        name = base if from_step else "G-grupp 1"
        index = 1
        while name.casefold() in existing:
            index += 1
            name = f"{base} {index}"
        source = {f: self.factor_inputs["G", f] for f in PERMANENT_FACTORS}
        if from_step is not None:
            box = self.step_inputs[from_step][0]
            previous = box.currentData()
            if previous in self.permanent_rows:
                source = self.permanent_rows[previous][1]
        key = self._insert_group(
            name, {f: editor.value() for f, editor in source.items()}
        )
        if from_step is not None:
            box.setCurrentIndex(box.findData(key))
        self.tabs.setCurrentWidget(self.permanent_page)
        row = self.permanent_table.rowCount() - 1
        self.permanent_table.setCurrentCell(row, 0)
        edit = self.permanent_rows[key][0]
        edit.setFocus()
        edit.selectAll()

    def _remove_group(self):
        row = self.permanent_table.currentRow()
        if row < 0:
            return
        key = list(self.permanent_rows)[row]
        name = self.permanent_rows[key][0].text()
        for group, *_ in self.step_inputs.values():
            if group.currentData() == key:
                group.setCurrentIndex(group.findData("G"))
            group.removeItem(group.findData(key))
        del self.permanent_rows[key]
        self.permanent_table.removeRow(row)
        self.message.setText(
            f"{name} tas bort när du tillämpar. Kopplade steg återgår till grundgruppen."
        )

    def _new_group_for_step(self):
        row = self.step_table.currentRow()
        if row >= 0:
            step = self.step_table.item(row, 0).text()
            if not step.upper().startswith("IL_"):
                self._new_group(from_step=step)

    def _build_step_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 2)
        self.step_table = QTableWidget(0, 4)
        self.step_table.setHorizontalHeaderLabels(
            ["Abaqus-steg", "Grupp", "Fält/läge", "Riktning"]
        )
        self.step_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.step_table.verticalHeader().hide()
        self.step_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.step_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.step_table)
        self.single_step_group = QPushButton("Egen G-grupp för markerat steg")
        self.single_step_group.setToolTip(
            "Markera stegnamnet för att ge lasten egna permanenta faktorer."
        )
        self.single_step_group.clicked.connect(self._new_group_for_step)
        self.step_table.currentCellChanged.connect(self._select_step)
        layout.addWidget(self.single_step_group, alignment=Qt.AlignmentFlag.AlignLeft)
        self.tabs.addTab(page, "Steg")

    def _select_step(self, row, *_):
        step = self.step_table.item(row, 0) if row >= 0 else None
        self.single_step_group.setEnabled(
            step is not None and not step.text().upper().startswith("IL_")
        )

    def _build_display(self):
        layout = self._scroll_tab("Modell")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.axes = choices(
            [
                ("", "Förval · M_y (SM1), M_z (SM2)"),
                ("SM1", "Bekräftad · M_y (SM1), M_z (SM2)"),
                ("SM2", "Bekräftad · M_y (SM2), M_z (SM1)"),
            ]
        )
        form.addRow("Momentaxlar", self.axes)
        axes_info = QLabel(
            "Välj bekräftade momentaxlar om du har kontrollerat delens lokala axlar och tecken i Abaqus. "
            "Kopplingen sparas för den valda delen."
        )
        axes_info.setWordWrap(True)
        form.addRow(axes_info)
        for key, label in [
            ("supports", "Lager [m]"),
            ("hangers", "Hängare [m]"),
            ("vehicles", "Tung fordonsaxel [m]"),
        ]:
            inp = QLineEdit()
            inp.setPlaceholderText("t.ex. 7,5; 15")
            self.marker_inputs[key] = inp
            form.addRow(label, inp)
        defaults = QPushButton("Fyll i V1–V5 enligt lastplaceringen")
        defaults.clicked.connect(
            lambda: self.marker_inputs["vehicles"].setText("7,5; 13; 15")
        )
        form.addRow(defaults)
        info = QLabel(
            "V1/V3 delar tung axel vid 7,5 m, V5 har 13 m och V2/V4 delar 15 m. Tom ruta döljer markeringarna."
        )
        info.setWordWrap(True)
        form.addRow(info)
        layout.addLayout(form)
        layout.addStretch(1)

    def load(self, config, steps, part_name=""):
        self.part_name = part_name
        self.config = deepcopy(config)
        self.steps = sorted(set(steps))
        self._show_config(self.config)
        self.message.clear()

    def _show_config(self, config):
        self.group_names = {
            **GROUPS,
            **{k: v for k, v in config.get("group_names", {}).items() if k in GROUPS},
        }
        self.factors.setVerticalHeaderLabels(list(self.group_names.values()))
        self.ped_label.setText(self.group_names["PED"])
        self.veh_label.setText(self.group_names["VEH"])
        self.exclusive.setText(
            f"{self.group_names['PED']} och {self.group_names['VEH'].lower()} utesluter varandra"
        )
        for (group, factor), inp in self.factor_inputs.items():
            inp.setAccessibleName(f"{self.group_names[group]}, {FACTORS[factor][1]}")
            inp.setValue(float(config["groups"][group][factor]))
        self.exclusive.setChecked(config["exclusive"])
        for box, key in [(self.ped, "ped_strategy"), (self.veh, "veh_strategy")]:
            box.setCurrentIndex(box.findData(config[key]))
        for key, inp in self.pattern_inputs.items():
            inp.setText(
                ", ".join(str(field) for field in config["patterns"].get(key, []))
            )
        axes = config.get("part_axes", {}).get(self.part_name, config.get("axes", {}))
        self.axes.setCurrentIndex(max(0, self.axes.findData(axes.get("My", ""))))
        for key, inp in self.marker_inputs.items():
            inp.setText(
                "; ".join(
                    format(value, ".15g").replace(".", ",")
                    for value in config.get("markers", {}).get(key, [])
                )
            )
        self._mapping_draft = deepcopy(config.get("mapping", {}))
        self.step_table.clearContents()
        self.step_inputs.clear()
        self.permanent_table.setRowCount(0)
        self.permanent_rows.clear()
        self.original_groups.clear()
        for name, factors in config.get("permanent_groups", {}).items():
            self.original_groups[name] = self._insert_group(name, factors)
        self.remove_permanent.setEnabled(False)
        self.single_step_group.setEnabled(False)
        self.step_table.setRowCount(len(self.steps))
        for row, step in enumerate(self.steps):
            inferred = infer_step(step)
            details = self._mapping_draft.get(step, inferred)
            options = [
                ("IGNORE", "Uteslut"),
                ("G", self.group_names["G"] + " (grundgrupp)"),
            ]
            options.extend(
                (key, f"G · {entry[0].text()}")
                for key, entry in self.permanent_rows.items()
            )
            options.extend((key, self.group_names[key]) for key in ("PED", "VEH"))
            group = choices(options)
            choice = details["group"]
            if choice == "G" and details.get("permanent_group"):
                name = details["permanent_group"]
                choice = self.original_groups.get(name, f"missing:{name}")
                if isinstance(choice, str):
                    group.addItem(f"Saknad G-grupp · {name}", choice)
            group.setCurrentIndex(group.findData(choice))
            slot = QLineEdit(details.get("slot", ""))
            slot.setMaximumWidth(80)
            role = choices([("V", "Vertikal"), ("H", "Horisontell")])
            role.setCurrentIndex(role.findData(details.get("role", "V")))
            widgets = (group, slot, role)
            self.step_inputs[step] = widgets
            self.step_table.setItem(row, 0, readonly_item(step))
            for col, editor in enumerate(widgets, start=1):
                self.step_table.setCellWidget(row, col, editor)
            permanent_il = step.upper().startswith("IL_")
            group.setEnabled(not permanent_il)
            if permanent_il:
                group.setCurrentIndex(group.findData("IGNORE"))

            def toggle(_=0, g=group, s=slot, r=role):
                enabled = g.currentData() in ("PED", "VEH")
                s.setEnabled(enabled)
                r.setEnabled(enabled)

            group.currentIndexChanged.connect(toggle)
            toggle()
        self.step_table.resizeColumnsToContents()

    def _reset(self):
        cfg = default_config()
        cfg["group_names"] = self.config.get("group_names", {})
        self._show_config(cfg)
        self.message.setText(
            "Förvalen är inlästa i formuläret. Klicka på Tillämpa för att använda dem."
        )

    def _read_permanent_groups(self):
        names, groups = {}, {}
        seen = set()
        for key, (name_edit, factors) in self.permanent_rows.items():
            name = name_edit.text().strip()
            if not name:
                raise ValueError("Ange ett namn för varje G-grupp.")
            if name.casefold() in seen:
                raise ValueError(
                    f"G-gruppen {name} förekommer flera gånger. Välj unika namn."
                )
            seen.add(name.casefold())
            names[key] = name
            for editor in factors.values():
                editor.interpretText()
            groups[name] = {f: editor.value() for f, editor in factors.items()}
        return names, groups

    def _read_mappings(self, names):
        mapping = deepcopy(self._mapping_draft)
        # Steps in disabled source files do not have an editor. Their mappings must
        # still follow renames and return to the base group if their group is deleted.
        for step, data in mapping.items():
            previous = data.get("permanent_group")
            if step in self.step_inputs or data.get("group") != "G" or not previous:
                continue
            if previous not in self.original_groups:
                raise ValueError(f"{step} hänvisar till en saknad G-grupp: {previous}.")
            key = self.original_groups[previous]
            if key in names:
                data["permanent_group"] = names[key]
            else:
                data.pop("permanent_group")
        for step, (group_box, slot_input, role_box) in self.step_inputs.items():
            choice = group_box.currentData()
            if choice not in GROUPS and choice != "IGNORE" and choice not in names:
                raise ValueError(f"Välj en befintlig G-grupp för {step}.")
            group = "G" if choice in names else choice
            slot = slot_input.text().strip().upper()
            role = role_box.currentData()
            if group in ("G", "IGNORE"):
                slot, role = "", "V"
            elif group == "PED":
                if not re.fullmatch(r"\d{1,2}", slot):
                    raise ValueError(f"{step} behöver ett gångfält, exempelvis 03.")
                slot = slot.zfill(2)
            elif group == "VEH":
                match = re.fullmatch(r"V?(\d{1,2})", slot)
                if not match:
                    raise ValueError(f"{step} behöver ett fordonsläge, exempelvis V3.")
                slot = f"V{int(match[1])}"
            data = {"group": group, "slot": slot, "role": role}
            if choice in names:
                data["permanent_group"] = names[choice]
            if data == infer_step(step):
                mapping.pop(step, None)
            else:
                mapping[step] = data
        return mapping

    def apply_changes(self):
        updated = deepcopy(self.config)
        for (group, factor), inp in self.factor_inputs.items():
            inp.interpretText()
            updated["groups"][group][factor] = inp.value()
        updated["exclusive"] = self.exclusive.isChecked()
        updated["ped_strategy"] = self.ped.currentData()
        updated["veh_strategy"] = self.veh.currentData()
        try:
            for key, inp in self.pattern_inputs.items():
                updated["patterns"][key] = ped_slots(inp.text())
            chosen = updated["ped_strategy"]
            if chosen in updated["patterns"] and not updated["patterns"][chosen]:
                raise ValueError("Ange minst ett fält för det valda gångmönstret.")
            names, updated["permanent_groups"] = self._read_permanent_groups()
            updated["mapping"] = self._read_mappings(names)
            updated["markers"] = {
                key: marker_positions(inp.text())
                for key, inp in self.marker_inputs.items()
            }
        except ValueError as exc:
            self.message.setText(str(exc))
            return
        first = self.axes.currentData()
        if self.part_name:
            updated.setdefault("part_axes", {})[self.part_name] = (
                {"My": first, "Mz": "SM2" if first == "SM1" else "SM1"} if first else {}
            )
        self.on_apply(updated)
        self.message.setText("Inställningarna är tillämpade.")
