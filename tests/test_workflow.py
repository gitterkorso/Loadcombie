"""Exercise the GUI-to-engine path and persisted selection, including coexisting panels."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from openpyxl import load_workbook
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
import pytest

from lastkomb.analysis import calculate, make_report
from lastkomb.data import Source, parse_sources
from lastkomb.engine import default_config
from lastkomb.window import Grid, MainWindow, fresh_view


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def ready(app, window):
    end = time.monotonic() + 8
    while time.monotonic() < end:
        app.processEvents()
        if window.result is not None and not window.timer.isActive():
            return
        QTest.qWait(10)
    raise AssertionError(window.statusBar().currentMessage())


def test_real_window_save_restore_and_export(app, tmp_path, monkeypatch):
    w = MainWindow()
    restored = None
    try:
        ready(app, w)
        w.interval(10, 20)
        ready(app, w)
        w.pin()
        w.overlay("G_SELF", True)
        w.store_panel("checks", {"HUVUDBALK": {"mode": "symmetry"}})
        w.store_panel("fatigue", {"IL_huvudbalk": {"area": "2048"}})
        w.settings.factor_inputs["G", "gamma_u"].setValue(1.32)
        w.settings.axes.setCurrentIndex(w.settings.axes.findData("SM2"))
        w.settings.apply_changes()
        assert set(w.config) >= {"checks", "fatigue", "part_axes"}
        assert w.config["part_axes"]["HUVUDBALK"]["My"] == "SM2"
        ready(app, w)
        w.pin()
        w.pick(w.selected, "curve:G_SELF")
        assert "Lastfall" in w.inspector.toPlainText()
        file = tmp_path / "Kontroll.lastkomb"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(file), ""))
        assert w.save()
        assert not w.dirty
        restored = MainWindow(file)
        ready(app, restored)
        assert restored.view == w.view
        assert restored.config == w.config
        assert restored.result.ids == w.result.ids
        for k in ("min", "max", "fmin", "fmax", "compression", "labelmin", "labelmax"):
            np.testing.assert_equal(restored.result.bounds[k], w.result.bounds[k])
        sheet = tmp_path / "Kombinationer.xlsx"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(sheet), "Excel"))
        restored.export_table()
        wb = load_workbook(sheet, read_only=True, data_only=True)
        report = list(wb["Redovisning 5.3"].values)
        assert len(report) == 7
        assert "Samtidig My (SM2) [kNm]" in report[0]
        assert "Samtidig N (SF1) [kN]" in report[0]
        assert "Valda kurvor" in wb.sheetnames
        wb.close()
        for suffix in ("svg", "pdf", "png"):
            dest = tmp_path / ("figur." + suffix)
            restored.chart.export(dest)
            assert dest.stat().st_size > 1000
    finally:
        for window in (w, restored):
            if window is not None:
                window.dirty = False
                window.close()


def test_partial_stress_tensor_does_not_clear_buckling_flag():
    src = Source("PLAT.csv", b"Steg;Sida;Lage;x;y;S11 [MPa]\nG_SELF;SPOS;1;1;0;-3")
    parts, warnings = parse_sources([src])
    r = calculate(parts["PLAT"], "S11", default_config(), {})
    assert r.pressure(0) == "Okänt"
    report = make_report(r, default_config(), {}, warnings=warnings)
    assert all(row[-1] == "Okänt" for row in report["Redovisning 5.3"][1])


def test_apply_custom_g_factor_reopen_and_export(app, tmp_path, monkeypatch):
    w = MainWindow()
    restored = None
    try:
        source = Source("BRO_SM1.csv", b"Element;Nod;x;y;z;G_SELF;G_SHRINK\n1;1;0;0;0;100;-20")
        path = tmp_path / "Prov.lastkomb"
        w.reset([source], default_config(), fresh_view(), path, False)
        ready(app, w)
        assert w.view["target"] == "SM1"
        assert "My (SM1)" in w.inspector.toPlainText()
        assert w.result.bounds["max"][0] == pytest.approx(108)
        panel = w.settings
        panel.step_table.setCurrentCell(panel.steps.index("G_SHRINK"), 0)
        panel.single_step_group.click()
        name, factors = next(iter(panel.permanent_rows.values()))
        name.setText("Krympning")
        for key, value in {"gamma_u": 1, "gamma_f": 0, "xi": 1}.items():
            factors[key].setValue(value)
        panel.apply_changes()
        ready(app, w)
        assert w.result.bounds["max"][0] == pytest.approx(135)
        assert w.result.bounds["min"][0] == pytest.approx(80)
        assert w.save()
        restored = MainWindow(path)
        ready(app, restored)
        assert restored.config == w.config
        assert "G_SHRINK · G-grupp Krympning" in restored.inspector.toPlainText()
        np.testing.assert_allclose(restored.result.bounds["fmax"][0], [1.35, 0])
        dest = tmp_path / "Prov.xlsx"
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(dest), "Excel"))
        restored.export_table()
        wb = load_workbook(dest, read_only=True, data_only=True)
        try:
            rows = dict(list(wb["Beräkningsvillkor"].values)[1:])
            assert rows["G-steg · G_SHRINK"] == "Krympning"
            assert "gamma_f=0" in rows["G-grupp · Krympning"]
        finally:
            wb.close()
    finally:
        for window in (w, restored):
            if window is not None:
                window.dirty = False
                window.close()


def test_numeric_sort_handles_missing_and_integer_identifiers(app):
    grid = Grid()
    grid.replace(["x"], [[1.0], [""], [20.0], [3.0]])
    grid.sort(0, Qt.SortOrder.AscendingOrder)
    assert grid.order == [0, 3, 2, 1]
    grid.replace(["Nod"], [["101"], ["8"], ["15"]])
    assert grid.order == [1, 2, 0]
