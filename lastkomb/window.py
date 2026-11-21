from __future__ import annotations

from copy import deepcopy
from html import escape
from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableView,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .analysis import calculate, comparison_curves, fmt, group_of, make_report, pin_label, quantity_label
from .chart import Chart, BLUE, ORANGE
from .data import Source, load_project, parse_sources, save_project
from .demo import example_sources
from .engine import default_config, infer_step
from .report import write_tables
from .qt_notation import (
    NOTATION_ROLE,
    NotationComboBox,
    NotationHeader,
    NotationMenu,
    NotationStatusBar,
    engineering_html,
)
from .settings import SettingsPanel
from .style import stylesheet
from .fatigue import FatiguePanel
from .checks import ChecksPanel

Role = Qt.ItemDataRole
CK = Qt.CheckState
HORIZ, VERT = Qt.Orientation.Horizontal, Qt.Orientation.Vertical
MODE_NAMES = {
    "STR": "STR · 6.10a + b",
    "TRAFFIC": "Karakteristisk trafik",
    "FATIGUE": "Utmattning · fordon",
}
GROUP_NAMES = {
    "G": "Permanent last",
    "PED": "Gångtrafik",
    "VEH": "Servicefordon",
    "IGNORE": "Ej kopplade steg",
}


def label(text, name=""):
    item = QLabel(text)
    item.setObjectName(name)
    return item


def button(text, handler, name=""):
    item = QPushButton(text)
    item.setObjectName(name)
    item.clicked.connect(lambda: handler())
    return item


def panel(margin=10):
    widget = QWidget()
    widget.setObjectName("panel")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(7)
    return widget, layout


def row(layout, *items):
    line = QHBoxLayout()
    line.setSpacing(6)
    for item in items:
        if item is None:
            line.addStretch(1)
        else:
            line.addWidget(item)
    layout.addLayout(line)
    return line


def fresh_view():
    return dict(
        part="",
        target="SM1",
        side="",
        disabled=[],
        files_off=[],
        lo=None,
        hi=None,
        overlays=[],
        pins=[],
        dark=False,
        extreme="max",
        point=None,
        tab=0,
    )


class Grid(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.headers, self.rows, self.order = [], [], []
        self.sort_column, self.desc = 0, False

    def sort_key(self, idx):
        value = self.rows[idx][self.sort_column]
        if isinstance(value, (int, float, np.number)):
            return 0, value
        if str(value).isdecimal():
            return 0, int(value)
        return 1, str(value).casefold()

    def replace(self, headers, rows):
        self.beginResetModel()
        self.headers, self.rows = headers, rows
        self.order = list(range(len(rows)))
        if rows:
            self.sort_column = min(self.sort_column, len(headers) - 1)
            self.order.sort(key=self.sort_key, reverse=self.desc)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role):
        if role == Role.DisplayRole:
            return self.headers[section] if orientation == HORIZ else str(section + 1)

    def data(self, index, role):
        if not index.isValid():
            return None
        val = self.rows[self.order[index.row()]][index.column()]
        if role == Role.DisplayRole:
            return fmt(val, 5) if isinstance(val, (float, np.floating)) else str(val)
        if role == Role.TextAlignmentRole and isinstance(val, (int, float, np.number)):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Role.ToolTipRole:
            return str(val)

    def sort(self, col, order):
        if col < 0:
            return
        self.sort_column, self.desc = col, order == Qt.SortOrder.DescendingOrder
        if self.rows and col < len(self.headers):
            self.layoutAboutToBeChanged.emit()
            self.order.sort(key=self.sort_key, reverse=self.desc)
            self.layoutChanged.emit()


class Responses(QObject):
    finished = Signal(int, object, str)


class Job(QRunnable):
    def __init__(self, revision, response, args):
        super().__init__()
        self.revision, self.response, self.args = revision, response, args

    def run(self):
        try:
            result = calculate(*self.args)
            self.response.finished.emit(self.revision, result, "")
        except Exception as exc:
            self.response.finished.emit(self.revision, None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.setWindowTitle("Lastkomb")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))
        self.resize(1440, 880)
        self.setMinimumSize(930, 590)
        self.setAcceptDrops(True)
        self.sources, self.parts, self.issues = [], {}, []
        self.config, self.view = default_config(), fresh_view()
        self.project_path = None
        self.demo = False
        self.dirty = False
        self.locked = False
        self.result = None
        self.past, self.future = [], []
        self.revision = 0
        self.cancel_compute = Event()
        self.responses = Responses()
        self.responses.finished.connect(self.present)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self, singleShot=True, interval=45)
        self.timer.timeout.connect(self.compute)
        self.build_ui()
        self.shortcuts()
        self.set_theme()
        if project:
            self.load_path(project)
        else:
            self.load_demo()

    def build_ui(self):
        self.setStatusBar(NotationStatusBar(self))
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("bar")
        head = QHBoxLayout(bar)
        head.setContentsMargins(15, 9, 15, 9)
        logo = QLabel()
        logo.setPixmap(
            QPixmap(str(Path(__file__).with_name("icon.png"))).scaled(
                30, 30, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        head.addWidget(logo)
        head.addWidget(label("Lastkomb", "brand"))
        self.projlabel = label("", "subbrand")
        head.addSpacing(8)
        head.addWidget(self.projlabel)
        head.addStretch()
        for text, func in (
            ("Nytt", self.new),
            ("Öppna…", self.open_file),
            ("Spara", self.save),
            ("Importera CSV…", self.import_file),
        ):
            head.addWidget(button(text, func, "primary" if text.startswith("Importera") else ""))
        menu_button = QPushButton("Mer ▾")
        self.more = QMenu(menu_button)
        menu_button.setMenu(self.more)
        head.addWidget(menu_button)
        outer.addWidget(bar)

        self.main_split = QSplitter(HORIZ)
        outer.addWidget(self.main_split, 1)
        left, box = panel(9)
        left.setMinimumWidth(153)
        row(box, label("Projekt", "heading"), None, button("+ Mapp", self.import_folder))
        self.stats = label("", "quiet")
        self.stats.setWordWrap(True)
        box.addWidget(self.stats)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(12)
        self.tree.itemChanged.connect(self.tree_changed)
        self.tree.itemClicked.connect(self.tree_clicked)
        box.addWidget(self.tree, 1)
        self.main_split.addWidget(left)

        middle, box = panel(11)
        top = QHBoxLayout()
        self.heading = label("Resultat", "title")
        self.mode = QComboBox()
        for code, name in MODE_NAMES.items():
            self.mode.addItem(name, code)
        self.mode.currentIndexChanged.connect(self.change_mode)
        top.addWidget(self.heading)
        top.addStretch()
        top.addWidget(self.mode)
        box.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.tab_changed)
        box.addWidget(self.tabs, 1)
        chartpage, chartbox = panel(7)
        self.tabs.addTab(chartpage, "Diagram")
        self.qty = NotationComboBox()
        self.qty.currentIndexChanged.connect(self.change_quantity)
        self.face = QComboBox()
        self.face.currentIndexChanged.connect(self.change_side)
        self.lower, self.upper = QLineEdit(), QLineEdit()
        for item, text in ((self.lower, "Från"), (self.upper, "Till")):
            item.setPlaceholderText(text)
            item.setMaximumWidth(72)
            item.setToolTip("Intervallgräns i meter. Tryck Retur för att tillämpa.")
            item.returnPressed.connect(self.apply_interval)
        self.series = QPushButton("Kurvor ▾")
        self.series_menu = NotationMenu(self.series)
        self.series_menu.aboutToShow.connect(self.build_curves)
        self.series.setMenu(self.series_menu)
        self.reset_range = button("Hela", lambda: self.interval(None, None))
        row(
            chartbox,
            self.qty,
            self.face,
            None,
            label("Intervall [m]", "quiet"),
            self.lower,
            self.upper,
            self.reset_range,
            self.series,
        )
        self.chart = Chart(self.pick, self.interval)
        self.chart.setMinimumSize(180, 100)
        self.chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.vert_split = QSplitter(VERT)
        self.vert_split.addWidget(self.chart)
        bottom, bottom_box = panel(0)
        self.table_title = label("Punktvärden", "heading")
        row(
            bottom_box,
            self.table_title,
            None,
            button("Exportera tabell…", self.export_table),
            button("Spara figur…", self.export_figure),
        )
        self.table = QTableView()
        self.table.setHorizontalHeader(NotationHeader(self.table))
        self.model = Grid()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setMinimumSectionSize(55)
        self.table.clicked.connect(self.table_clicked)
        bottom_box.addWidget(self.table, 1)
        self.vert_split.addWidget(bottom)
        self.vert_split.setSizes([375, 220])
        chartbox.addWidget(self.vert_split, 1)
        self.settings = SettingsPanel(self.apply_settings)
        self.tabs.addTab(self.settings, "Lastregler")
        self.checks = ChecksPanel(lambda data: self.store_panel("checks", data))
        self.tabs.addTab(self.checks, "Kontroller")
        self.fatigue = FatiguePanel(lambda data: self.store_panel("fatigue", data))
        self.tabs.addTab(self.fatigue, "Fordonsöverfart")
        self.main_split.addWidget(middle)

        right, details = panel(10)
        right.setMinimumWidth(185)
        row(details, label("Granskare", "heading"), None)
        self.notice = label("")
        self.notice.setObjectName("warning")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        details.addWidget(self.notice)
        self.more_issues = button("Visa importkontroll", self.show_issues)
        details.addWidget(self.more_issues)
        self.update_sources = button("Läs uppdaterade källfiler", self.refresh_sources)
        details.addWidget(self.update_sources)
        self.extreme_combo = NotationComboBox()
        self.extreme_combo.addItem("Styrande maximum", "max")
        self.extreme_combo.addItem("Styrande minimum", "min")
        self.extreme_combo.currentIndexChanged.connect(self.change_extreme)
        details.addWidget(self.extreme_combo)
        self.inspector = QTextBrowser()
        self.inspector.setFrameShape(QTextBrowser.Shape.NoFrame)
        details.addWidget(self.inspector, 1)
        self.pin_button = button("Visa kombination som kurva", self.pin)
        details.addWidget(self.pin_button)
        self.main_split.addWidget(right)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([203, 912, 325])
        self.import_notes = QTextBrowser()
        self.tabs.addTab(self.import_notes, "Importkontroll")

    def shortcuts(self):
        def action(text, key, callback, menu=True):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda: callback())
            self.addAction(a)
            if menu:
                self.more.addAction(a)
            return a

        self.undo_action = action("Ångra", QKeySequence.StandardKey.Undo, self.undo)
        self.redo_action = action("Gör om", QKeySequence.StandardKey.Redo, self.redo)
        self.more.addSeparator()
        action("Spara som…", QKeySequence.StandardKey.SaveAs, lambda: self.save(True))
        action("Importera mapp…", None, self.import_folder)
        action("Öppna räkneexempel", None, self.example_requested)
        action("Växla ljust / mörkt tema", "Ctrl+Shift+L", self.toggle_theme)
        action("Nästa storhet", "Alt+Down", lambda: self.next_item(self.qty))
        action("Nästa del", "Alt+Right", self.next_part)
        action("Nästa beräkningsläge", "Alt+M", lambda: self.next_item(self.mode))
        action("Öppna", QKeySequence.StandardKey.Open, self.open_file, False)
        action("Spara", QKeySequence.StandardKey.Save, self.save, False)
        action("Nytt", QKeySequence.StandardKey.New, self.new, False)

    def current_part(self):
        return self.parts.get(self.view["part"])

    def snapshot(self):
        return (
            list(self.sources),
            deepcopy(self.config),
            deepcopy(self.view),
            self.demo,
            self.project_path,
            list(self.source_notes),
        )

    def before(self):
        self.past.append(self.snapshot())
        del self.past[:-35]
        self.future.clear()
        self.dirty = True

    def restore(self, source, dest):
        if source:
            dest.append(self.snapshot())
            self.sources, self.config, self.view, self.demo, self.project_path, self.source_notes = (
                source.pop()
            )
            self.dirty = True
            self.reparse()
            self.set_theme()
            self.sync()

    def undo(self):
        self.restore(self.past, self.future)

    def redo(self):
        self.restore(self.future, self.past)

    def set_theme(self):
        scheme = Qt.ColorScheme.Dark if self.view["dark"] else Qt.ColorScheme.Light
        QApplication.styleHints().setColorScheme(scheme)
        self.setStyleSheet(stylesheet(self.view["dark"]))
        self.chart.dark = self.view["dark"]

    def toggle_theme(self):
        self.before()
        self.view["dark"] = not self.view["dark"]
        self.set_theme()
        self.sync()

    def reparse(self):
        active = [s for i, s in enumerate(self.sources) if i not in self.view.get("files_off", [])]
        self.parts, self.issues = parse_sources(active, self.config.get("expected_steps"))
        self.steps = sorted({s for p in self.parts.values() if p.kind != "il" for s in p.steps})

    def chart_config(self):
        if "markers" in self.config:
            return self.config
        supports, hangers = set(), set()
        for part in self.parts.values():
            hangers.update(p["hanger"] for p in part.points if "hanger" in p)
            if any(q in ("RF1", "RF2", "RF3") for q in part.quantities):
                supports.update(p["x"] for p in part.points)
        return {
            **self.config,
            "markers": {"supports": sorted(supports), "hangers": sorted(hangers), "vehicles": []},
        }

    def load_demo(self):
        config = default_config()
        config["markers"] = {
            "supports": [0.0, 30.0],
            "hangers": [3.0, 7.0, 11.0, 15.0, 19.0, 23.0, 27.0],
            "vehicles": [7.5, 13.0, 15.0],
        }
        self.reset(example_sources(), config, fresh_view(), None, True)

    def reset(self, sources, config, view, path, demo, notes=None):
        self.sources, self.config, self.view, self.project_path, self.demo = (
            sources,
            config,
            view,
            path,
            demo,
        )
        self.past.clear()
        self.future.clear()
        self.dirty = False
        self.source_notes = notes or []
        self.reparse()
        self.set_theme()
        self.sync()

    def example_requested(self):
        if self.allow_replace():
            self.load_demo()

    def new(self):
        if self.allow_replace():
            self.reset([], default_config(), fresh_view(), None, False)

    def allow_replace(self):
        if not self.dirty:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("Osparade ändringar")
        msg.setText("Vill du spara ändringarna i projektet?")
        save = msg.addButton("Spara", QMessageBox.ButtonRole.AcceptRole)
        discard = msg.addButton("Fortsätt utan att spara", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Avbryt", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        return msg.clickedButton() == discard or (msg.clickedButton() == save and self.save())

    def closeEvent(self, event):
        if not self.allow_replace():
            event.ignore()
            return
        self.timer.stop()
        self.cancel_compute.set()
        self.pool.clear()
        self.pool.waitForDone()
        event.accept()

    def load_path(self, path):
        try:
            sources, settings, notes = load_project(path)
            config, view = default_config(), fresh_view()
            config.update(settings["config"])
            view.update(settings.get("view", {}))
            self.reset(sources, config, view, Path(path), bool(settings.get("example")), notes)
        except Exception as exc:
            if not hasattr(self, "steps"):
                self.reset([], default_config(), fresh_view(), None, False)
            self.message(f"Projektet kunde inte öppnas. {exc}")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Öppna projekt", "", "Lastkomb (*.lastkomb)")
        if path and self.allow_replace():
            self.load_path(path)

    def save(self, as_new=False):
        path = self.project_path
        if path is None or as_new:
            s, _ = QFileDialog.getSaveFileName(
                self, "Spara projekt", str(path or "Broprojekt.lastkomb"), "Lastkomb (*.lastkomb)"
            )
            if not s:
                return False
            path = Path(s)
            if path.suffix.lower() != ".lastkomb":
                path = path.with_name(path.name + ".lastkomb")
        try:
            settings = {"config": self.config, "view": self.view, "example": self.demo}
            cache = None
            if self.result is not None:
                r = self.result
                cache = {
                    "part": r.part.name,
                    "target": r.target,
                    "ids": r.ids,
                    "steps": r.steps,
                    "bounds": {k: np.asarray(v).tolist() for k, v in r.bounds.items()},
                }
            save_project(path, self.sources, settings, cache)
            self.project_path, self.dirty = path, False
            self.project_caption()
            self.statusBar().showMessage(f"Sparat · {path}", 5000)
            return True
        except Exception as exc:
            self.message(f"Projektet kunde inte sparas. {exc}")
            return False

    def refresh_sources(self):
        new = []
        try:
            for s in self.sources:
                path = Path(s.path) if s.path else None
                new.append(Source(s.name, path.read_bytes(), s.path) if path and path.is_file() else s)
        except OSError as exc:
            self.message(str(exc))
            return
        self.before()
        self.sources = new
        self.source_notes = []
        self.reparse()
        self.sync()

    def import_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Importera Abaqus-resultat", "", "CSV (*.csv *.CSV)")
        self.import_paths(paths)

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Importera alla CSV-filer i en mapp")
        if path:
            self.import_paths([path])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if len(paths) == 1 and Path(paths[0]).suffix.lower() == ".lastkomb":
            if self.allow_replace():
                self.load_path(paths[0])
        else:
            self.import_paths(paths)

    def import_paths(self, paths):
        if not paths:
            return
        added, errors = [], []
        try:
            files = []
            for item in map(Path, paths):
                files.extend(
                    sorted(p for p in item.rglob("*") if p.is_file() and p.suffix.lower() == ".csv")
                    if item.is_dir()
                    else [item]
                )
            existing = {s.path: s.content for s in self.sources if s.path and not self.demo}
            for p in dict.fromkeys(files):
                try:
                    if p.suffix.lower() != ".csv":
                        errors.append(f"{p.name} · Förväntade en CSV-fil.")
                        continue
                    raw = p.read_bytes()
                    if existing.get(str(p.resolve())) != raw:
                        added.append(Source(p.name, raw, str(p.resolve())))
                except OSError as exc:
                    errors.append(str(exc))
        except OSError as exc:
            errors.append(str(exc))
        if not added:
            self.message("\n".join(errors) if errors else "Inga nya CSV-filer hittades.")
            return
        self.before()
        if self.demo:
            self.sources = []
            self.view.update(fresh_view() | {"dark": self.view["dark"]})
            self.config = default_config()
            self.demo = False
            self.project_path = None
        by_path = {s.path: i for i, s in enumerate(self.sources) if s.path}
        for s in added:
            if s.path in by_path:
                self.sources[by_path[s.path]] = s
            else:
                self.sources.append(s)
        self.source_notes = errors
        self.reparse()
        self.sync()

    def project_caption(self):
        name = (
            self.project_path.stem
            if self.project_path
            else ("Räkneexempel" if self.demo else "Nytt projekt")
        )
        self.projlabel.setText(
            name + ("   ·   Syntetiska data" if self.demo else "") + ("   •" if self.dirty else "")
        )
        self.setWindowTitle(f"{name} – Lastkomb")
        self.undo_action.setEnabled(bool(self.past))
        self.redo_action.setEnabled(bool(self.future))

    def sync(self):
        self.locked = True
        if self.view["part"] not in self.parts:
            self.view["part"] = next(iter(self.parts), "")
        part = self.current_part()
        quantities = list(part.quantities) if part else []
        if part and part.kind == "plate" and {"S11", "S22", "S12"} <= set(quantities):
            quantities.append("Mises")
        self.mode.setToolTip(
            "Karakteristisk trafik och utmattningsläget använder trafikens karakteristiska värden utan egentyngd."
        )
        if self.view["target"] not in quantities:
            self.view["target"] = quantities[0] if quantities else ""
        self.qty.clear()
        for q in quantities:
            self.qty.addItem(quantity_label(q, self.config, part.name), q)
        self.qty.setCurrentIndex(self.qty.findData(self.view["target"]))
        self.face.clear()
        sides = sorted({p.get("side", "") for p in part.points} - {""}) if part else []
        for side in sides:
            self.face.addItem(side, side)
        if self.view["side"] not in sides:
            self.view["side"] = "SPOS" if "SPOS" in sides else (sides[0] if sides else "")
        self.face.setCurrentIndex(self.face.findData(self.view["side"]))
        self.face.setVisible(bool(sides))
        self.mode.setCurrentIndex(self.mode.findData(self.config["mode"]))
        self.mode.setEnabled(not part or part.kind != "il")
        self.lower.setText("" if self.view["lo"] is None else fmt(self.view["lo"], 6))
        self.upper.setText("" if self.view["hi"] is None else fmt(self.view["hi"], 6))
        self.heading.setText(part.name if part else "Resultat")
        self.chart.setToolTip(
            "Klicka på en extrempunkt för att granska den. Dra vågrätt för att välja intervall."
        )
        self.stats.setText(
            f"{len(self.sources)} filer · {len(self.parts)} delar\n{sum(len(p.points) for p in self.parts.values())} punkter · {len(self.steps)} laststeg"
        )
        self.rebuild_tree()
        self.settings.load(self.chart_config(), self.steps, self.view["part"])
        self.tabs.setCurrentIndex(min(int(self.view.get("tab", 0)), self.tabs.count() - 1))
        self.reload_aux()
        self.project_caption()
        self.locked = False
        self.schedule()

    def reload_aux(self):
        tab = self.tabs.currentWidget()
        if tab is self.fatigue:
            self.fatigue.load(self.current_part(), self.config)
        if tab is self.checks:
            self.checks.load(self.current_part(), self.config, self.view)

    def rebuild_tree(self):
        expanded = {
            self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i).isExpanded()
            for i in range(self.tree.topLevelItemCount())
        }
        self.tree.clear()

        def root(name, default):
            it = QTreeWidgetItem(self.tree, [name])
            it.setExpanded(expanded.get(name, default))
            f = it.font(0)
            f.setBold(True)
            it.setFont(0, f)
            return it

        def child(parent, name, kind, key, ticked=None):
            node = QTreeWidgetItem(parent, [name])
            node.setData(0, Role.UserRole, (kind, key))
            if ticked is not None:
                node.setCheckState(0, CK.Checked if ticked else CK.Unchecked)
            node.setToolTip(0, name)
            return node

        parts = root("Resultatdelar", True)
        for name in self.parts:
            item = child(parts, name, "part", name)
            if name == self.view["part"]:
                self.tree.setCurrentItem(item)
        files = root("Källfiler", False)
        for i, s in enumerate(self.sources):
            child(files, s.name, "file", i, i not in self.view["files_off"])
        for group, desc in GROUP_NAMES.items():
            steps = [s for s in self.steps if group_of(self.config, s) == group]
            if not steps:
                continue
            g = root(self.config.get("group_names", {}).get(group, desc), False)
            g.setData(0, Role.UserRole, ("group", group))
            n = sum(s not in self.view["disabled"] for s in steps)
            g.setCheckState(
                0, CK.Checked if n == len(steps) else (CK.Unchecked if not n else CK.PartiallyChecked)
            )
            g.setForeground(
                0, QBrush(QColor({"G": "#768390", "PED": BLUE, "VEH": ORANGE, "IGNORE": "#a7711c"}[group]))
            )
            for s in steps:
                subgroup = (
                    self.config["mapping"].get(s, {}).get("permanent_group") if group == "G" else None
                )
                name = f"{s} · {subgroup}" if subgroup and subgroup != s else s
                node = child(g, name, "step", s, s not in self.view["disabled"])
                if group == "G":
                    node.setToolTip(0, f"{s} · G-grupp: {subgroup or 'Grundgrupp'}")

    def tree_clicked(self, node, col):
        if self.locked:
            return
        data = node.data(0, Role.UserRole)
        if data and data[0] == "part" and data[1] != self.view["part"]:
            self.choose_part(data[1])

    def tree_changed(self, node, col):
        if self.locked:
            return
        item = node.data(0, Role.UserRole)
        if not item or item[0] == "part":
            return
        kind, key = item
        on = node.checkState(0) == CK.Checked
        self.before()
        if kind == "file":
            off = set(self.view["files_off"])
            off.discard(key) if on else off.add(key)
            self.view["files_off"] = sorted(off)
            self.reparse()
        else:
            keys = [key] if kind == "step" else [s for s in self.steps if group_of(self.config, s) == key]
            off = set(self.view["disabled"])
            self.view["disabled"] = sorted(off - set(keys) if on else off | set(keys))
        # Rebuild after Qt finishes processing the native checkbox event.
        QTimer.singleShot(0, self.sync)

    def choose_part(self, name):
        self.before()
        self.view.update(part=name, side="", lo=None, hi=None, point=None)
        self.sync()

    def next_part(self):
        if self.parts:
            names = list(self.parts)
            self.choose_part(names[(names.index(self.view["part"]) + 1) % len(names)])

    def next_item(self, combo):
        if combo.count() > 1:
            combo.setCurrentIndex((combo.currentIndex() + 1) % combo.count())

    def change_quantity(self):
        quantity = self.qty.currentData()
        if not self.locked and quantity and quantity != self.view["target"]:
            self.before()
            self.view["target"] = quantity
            self.schedule()

    def change_side(self):
        if not self.locked and self.face.currentData():
            self.before()
            self.view.update(side=self.face.currentData(), point=None)
            self.schedule()

    def change_mode(self):
        if not self.locked:
            self.before()
            self.config["mode"] = self.mode.currentData()
            self.view["pins"] = []
            self.sync()

    def apply_interval(self):
        try:
            nums = [
                float(t.replace(",", ".")) if t.strip() else None
                for t in (self.lower.text(), self.upper.text())
            ]
            if any(n is not None and not np.isfinite(n) for n in nums):
                raise ValueError()
            if all(n is not None for n in nums) and nums[0] > nums[1]:
                raise ValueError()
            self.interval(*nums)
        except ValueError:
            self.message("Ange ett giltigt intervall i meter, med nedre gränsen först.")

    def interval(self, lo, hi):
        self.before()
        self.view.update(lo=lo, hi=hi)
        self.sync()

    def apply_settings(self, cfg):
        self.before()
        for key in ("checks", "fatigue"):
            if key in self.config:
                cfg[key] = deepcopy(self.config[key])
        self.config = cfg
        self.view["pins"] = []
        self.sync()

    def store_panel(self, key, settings):
        if self.config.get(key) != settings:
            self.before()
            self.config[key] = deepcopy(settings)
            self.project_caption()

    def tab_changed(self, i):
        if self.locked or not hasattr(self, "checks"):
            return
        if self.view["tab"] != i:
            self.view["tab"] = i
            self.dirty = True
            self.project_caption()
        self.reload_aux()

    def schedule(self):
        self.revision += 1
        self.cancel_compute.set()
        self.cancel_compute = Event()
        self.pool.clear()
        self.result = None
        self.pin_button.setEnabled(False)
        self.model.replace([], [])
        self.chart.render(None, {}, self.config)
        self.inspector.setPlainText(
            "Beräknar aktuellt urval…" if self.parts else "Importera en mapp eller välj CSV-filer."
        )
        self.set_warnings([])
        self.project_caption()
        self.statusBar().showMessage("Beräknar aktuellt urval…" if self.parts else "Väntar på CSV-filer")
        self.timer.start()

    def compute(self):
        part = self.current_part()
        if part:
            config = deepcopy(self.config)
            config["_cancelled"] = self.cancel_compute.is_set
            self.pool.start(
                Job(self.revision, self.responses, (part, self.view["target"], config, deepcopy(self.view)))
            )
        else:
            self.present(self.revision, None, "")

    def present(self, revision, result, error):
        if revision != self.revision:
            return
        self.result = result
        warnings = ([error] if error else []) + (result.warnings if result else [])
        if result and result.part.kind != "il":
            active = set(result.steps)
            for role in ("PED", "VEH"):
                if not self.config.get("expects_horizontal", {}).get(role, True):
                    continue
                mapping = [
                    self.config["mapping"].get(s, infer_step(s))
                    for s in active
                    if group_of(self.config, s) == role
                ]
                vertical = {m["slot"] for m in mapping if m["role"] == "V"}
                horizontal = {m["slot"] for m in mapping if m["role"] == "H"}
                if vertical - horizontal:
                    name = self.config.get("group_names", {}).get(role, GROUP_NAMES[role])
                    warnings.append(
                        f"{name} · Utan aktiv horisontalkomponent i {', '.join(sorted(vertical - horizontal))}."
                    )
        if (
            result
            and result.part.kind == "beam"
            and set(result.part.quantities).intersection(("SF1", "SM1", "SM2"))
        ):
            expected = self.config.get("expected_beam_quantities", ("SF1", "SM1", "SM2"))
            missing = sorted(set(expected) - set(result.part.quantities))
            if missing:
                labels = [quantity_label(q, self.config, result.part.name) for q in missing]
                warnings.append("Samtidiga snittkrafter saknas för " + ", ".join(labels) + ".")
        self.set_warnings(warnings)
        self.curves = comparison_curves(result, self.view, self.config)
        self.chart.render(result, self.view, self.chart_config(), self.curves)
        if not result:
            self.inspector.setPlainText(error or "Importera en mapp eller välj CSV-filer.")
            self.statusBar().showMessage(error or "Inga aktiva resultatdelar")
            return
        il = result.part.kind == "il"
        self.pin_button.setEnabled(not il)
        self.extreme_combo.blockSignals(True)
        self.extreme_combo.clear()
        self.extreme_combo.addItem("Influensordinat" if il else "Styrande maximum", "max")
        if not il:
            self.extreme_combo.addItem("Styrande minimum", "min")
        for key, curve in self.curves.items():
            self.extreme_combo.addItem(curve.name, key)
            self.extreme_combo.setItemData(
                self.extreme_combo.count() - 1, curve.group == "COMB", NOTATION_ROLE
            )
        self.extreme_combo.blockSignals(False)
        self.extreme_combo.setEnabled(not il)
        pts = [result.part.points[i] for i in result.ids]
        quantity = quantity_label(result.target, self.config, result.part.name)
        self.table_title.setText(f"Punktvärden · {len(pts)}")
        headers = [
            "Läge [m]" if result.part.kind == "plate" else "x [m]",
            f"{'Värde' if il else 'Max'} {quantity} [{result.unit}]",
        ]
        headers += [] if il else [f"Min {quantity} [{result.unit}]"]
        notation_columns = set(range(1, len(headers)))
        notation_columns.update(
            i for i, c in enumerate(self.curves.values(), len(headers)) if c.group == "COMB"
        )
        self.table.horizontalHeader().notation_columns = notation_columns
        headers.extend(f"{curve.name} [{result.unit}]" for curve in self.curves.values())
        keys = [
            key for key in ("element", "node", "side", "hanger", "y", "z") if any(key in p for p in pts)
        ]
        headers += [
            {
                "element": "Element",
                "node": "Nod",
                "side": "Sida",
                "hanger": "Hängare x [m]",
                "y": "y [m]",
                "z": "z [m]",
            }[k]
            for k in keys
        ]
        headers += ["Tryck¹"] if result.part.kind == "plate" else []
        rows = []
        for i, pt in enumerate(pts):
            vals = [pt["pos"], result.bounds["max"][i]]
            vals += [] if il else [result.bounds["min"][i]]
            vals.extend(curve.values[i] for curve in self.curves.values())
            vals += [pt.get(k, "") for k in keys]
            if result.part.kind == "plate":
                vals += [result.pressure(i)]
            rows.append(vals)
        self.model.replace(headers, rows)
        self.table.resizeColumnsToContents()
        sel = self.view.get("point")
        idx = result.ids.index(sel) if sel in result.ids else int(np.argmax(result.bounds["max"]))
        self.pick(idx, self.view.get("extreme", "max"), False)
        on = f"{len(result.steps)} av {len(result.part.steps)} steg" if not il else "Influenslinje"
        self.statusBar().showMessage(
            f"{result.part.name}  ·  {quantity} [{result.unit}]  ·  {fmt(min(p['pos'] for p in pts))}–{fmt(max(p['pos'] for p in pts))} m  ·  {MODE_NAMES[self.config['mode']] if not il else 'Enhetslast'}  ·  {on}"
        )

    def set_warnings(self, extra):
        issues = []
        for issue in self.issues:
            marker = ": kontrollera kopplingen för okända steg: "
            if marker in issue:
                name, _, steps = issue.partition(marker)
                unresolved = [
                    s for s in steps.removesuffix(".").split(", ") if s not in self.config["mapping"]
                ]
                if not unresolved:
                    continue
                issue = name + marker + ", ".join(unresolved) + "."
            issues.append(issue)
        notes = list(dict.fromkeys(extra + list(self.source_notes) + issues))
        self.displayed_issues = notes
        self.notice.setVisible(bool(notes))
        self.more_issues.setVisible(bool(notes))
        self.update_sources.setVisible(any("källfilen har ändrats" in s.lower() for s in self.source_notes))
        if notes:
            first = notes[0]
            self.notice.setTextFormat(Qt.TextFormat.RichText)
            self.notice.setText(engineering_html(first[:170] + ("…" if len(first) > 170 else "")))
            self.more_issues.setText(f"Visa importkontroll ({len(notes)})")
        self.import_notes.setHtml(
            "<h3>Importkontroll</h3><p>Stegen ska innehålla oberoende lastbidrag från en linjär analys i samma koordinatsystem. Horisontallastens storlek ska redan ingå i H-steget.</p>"
            + (
                "<ul>" + "".join(f"<li>{engineering_html(str(s))}</li>" for s in notes) + "</ul>"
                if notes
                else "<p>Inga importavvikelser i de aktiva filerna.</p>"
            )
            + "<h4>Källfiler</h4><p>"
            + "<br>".join(escape(s.name) for s in self.sources)
            + "</p>"
        )

    def show_issues(self):
        self.tabs.setCurrentWidget(self.import_notes)

    def table_clicked(self, index):
        if self.result and index.isValid():
            direction = (
                "min" if index.column() == 2 and self.result.part.kind != "il" else self.view["extreme"]
            )
            if index.column() == 1:
                direction = "max"
            if 3 <= index.column() < 3 + len(self.curves):
                direction = list(self.curves)[index.column() - 3]
            self.pick(self.model.order[index.row()], direction)

    def change_extreme(self):
        if self.result and not self.locked:
            self.pick(self.selected, self.extreme_combo.currentData())

    def pick(self, idx, direction, user=True):
        r = self.result
        if not r:
            return
        if r.part.kind == "il" or direction not in ("max", "min") and direction not in self.curves:
            direction = "max"
        curve = self.curves.get(direction)
        self.pin_button.setEnabled(r.part.kind != "il" and curve is None)
        self.selected = idx
        if user and (self.view.get("point") != r.ids[idx] or self.view.get("extreme") != direction):
            self.dirty = True
            self.project_caption()
        self.view.update(point=r.ids[idx], extreme=direction)
        self.extreme_combo.blockSignals(True)
        self.extreme_combo.setCurrentIndex(self.extreme_combo.findData(direction))
        self.extreme_combo.blockSignals(False)
        self.table.selectRow(self.model.order.index(idx))
        self.chart.select_point(idx, direction)
        pt = r.part.points[r.ids[idx]]
        f = curve.factors if curve else r.bounds["f" + direction][idx]
        q = r.target
        quantity = quantity_label(q, self.config, r.part.name)
        val = fmt(curve.values[idx] if curve else r.bounds[direction][idx], 6)
        unit = escape(r.unit)
        title = f"<h2>{engineering_html(quantity)} &nbsp; {val} <small>{unit}</small></h2>"
        keys = [
            ("x", "x"),
            ("pos", "Läge"),
            ("y", "y"),
            ("z", "z"),
            ("side", "Sida"),
            ("element", "Element"),
            ("node", "Nod"),
        ]
        coords = " · ".join(
            f"{name} {escape(fmt(pt[k]))}" + (" m" if k in ("pos", "x", "y", "z") else "")
            for k, name in keys
            if k in pt and (k != "pos" or r.part.kind == "plate")
        )
        html = title + f"<p>{coords}</p>"
        if r.part.kind == "il":
            html += "<h3>Influensordinater</h3>"
        else:
            title = (
                "Fast kombination"
                if curve and curve.group == "COMB"
                else "Lastfall"
                if curve
                else "Styrande kombination"
            )
            description = curve.name if curve else r.bounds["label" + direction][idx]
            description_html = (
                engineering_html(description)
                if curve and curve.group == "COMB"
                else escape(str(description))
            )
            html += f"<h3>{title}</h3><p>{description_html}</p>"
            if q == "Mises":
                html += "<p>σeq = √(S11² + S22² − S11·S22 + 3·S12²)</p>"
            rows = []
            colors = {"G": "#79858f", "PED": BLUE, "VEH": ORANGE, "IGNORE": "#8c8992"}
            for j, (s, factor) in enumerate(zip(r.steps, f)):
                group = group_of(self.config, s)
                custom_g = group == "G" and self.config.get("permanent_groups")
                if factor == 0 and (curve or not custom_g):
                    continue
                subgroup = self.config["mapping"].get(s, {}).get("permanent_group") or "Grundgrupp"
                note = f" · G-grupp {escape(subgroup)}" if custom_g else ""
                cols = [q] if q != "Mises" else ["S11", "S22", "S12"]
                values = ", ".join(
                    fmt(factor * r.values[idx, j, r.part.quantities.index(c)], 5) for c in cols
                )
                raw = ", ".join(fmt(r.values[idx, j, r.part.quantities.index(c)], 5) for c in cols)
                rows.append(
                    f"<p><b style='color:{colors[group]}'>{escape(s)}</b>{note}<br>{fmt(factor, 8)} × ({raw}) = {values} {unit}</p>"
                )
            html += "<h3>Delbidrag</h3>" + ("".join(rows) if rows else "<p>Obelastat tillstånd · 0</p>")
            html += "<h3>Samtidiga värden</h3>"
        html += (
            "<p>"
            + "<br>".join(
                f"{engineering_html(quantity_label(c, self.config, r.part.name))} &nbsp; <b>{fmt(v, 6)}</b> {escape(r.part.units[c])}"
                for c, v in zip(r.part.quantities, f @ r.values[idx])
            )
            + "</p>"
        )
        if r.part.kind == "plate":
            html += (
                "<p><b>¹ Tryck i någon tillåten kombination: "
                + r.pressure(idx)
                + "</b><br>Minsta huvudspänning i plåtens plan. Bucklingsmotstånd kontrolleras separat.</p>"
            )
        self.inspector.setHtml(html)

    def pin(self):
        r = self.result
        if r is None or r.part.kind == "il":
            return
        idx = self.selected
        d = self.view["extreme"]
        if d not in ("min", "max"):
            return
        terms = r.terms(idx, d)
        pins = list(self.view.get("pins", []))
        if any(p["terms"] == terms and p["part"] == r.part.name for p in pins):
            return
        self.before()
        name = f"{r.target} {d} vid {fmt(r.part.points[r.ids[idx]]['pos'])} m"
        if any(p["name"] == name for p in pins):
            name += f" · punkt {r.ids[idx] + 1}"
        pins.append({"name": name, "part": r.part.name, "terms": terms})
        self.view["pins"] = pins
        self.redraw()

    def build_curves(self):
        menu = self.series_menu
        menu.clear()
        r = self.result
        if not r or r.part.kind == "il":
            a = menu.addAction("Inga jämförelsekurvor")
            a.setEnabled(False)
            return
        for s in r.steps:
            a = menu.addAction(s)
            a.setCheckable(True)
            a.setChecked(s in self.view["overlays"])
            a.triggered.connect(lambda on, step=s: self.overlay(step, on))
        if self.view["pins"]:
            menu.addSeparator()
            for i, p in enumerate(self.view["pins"]):
                a = menu.addAction("Ta bort · " + pin_label(p, self.config))
                a.setProperty("engineeringLabel", True)
                a.triggered.connect(lambda _, idx=i: self.remove_pin(idx))

    def overlay(self, step, on):
        self.before()
        select = set(self.view["overlays"])
        select.discard(step) if not on else select.add(step)
        self.view["overlays"] = sorted(select)
        self.redraw()

    def remove_pin(self, idx):
        self.before()
        self.view["pins"].pop(idx)
        self.redraw()

    def redraw(self):
        self.present(self.revision, self.result, "")
        self.project_caption()

    def message(self, text):
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setText(str(text))
        self.notice.show()
        self.statusBar().showMessage(str(text), 9000)

    def export_table(self):
        r = self.result
        if not r:
            self.message("Välj en del med giltiga resultat före export.")
            return
        quantity = quantity_label(r.target, self.config, r.part.name)
        path, filter_ = QFileDialog.getSaveFileName(
            self, "Exportera valt intervall", f"{r.part.name}_{quantity}", "Excel (*.xlsx);;CSV (*.csv)"
        )
        if not path:
            return
        if Path(path).suffix.lower() not in (".xlsx", ".csv"):
            path += ".csv" if "CSV" in filter_ else ".xlsx"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            paths = write_tables(
                path, make_report(r, self.config, self.view, self.projlabel.text(), self.displayed_issues)
            )
            self.statusBar().showMessage(f"Exporterade {len(paths)} fil(er) till {Path(path).parent}", 8000)
        except Exception as exc:
            self.message(str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def export_figure(self):
        r = self.result
        if not r:
            return
        quantity = quantity_label(r.target, self.config, r.part.name)
        path, filter_ = QFileDialog.getSaveFileName(
            self,
            "Spara diagram",
            f"{r.part.name}_{quantity}",
            "PDF (*.pdf);;PNG (*.png);;SVG (*.svg)",
        )
        if path:
            if not Path(path).suffix:
                path += "." + filter_[:3].lower()
            try:
                self.chart.export(path)
                self.statusBar().showMessage(f"Diagram sparat · {path}", 7000)
            except Exception as exc:
                self.message(str(exc))
