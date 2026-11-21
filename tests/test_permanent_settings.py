"""Verify permanent-group editing through the same controls used in the desktop app."""

import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from lastkomb.engine import default_config
from lastkomb.settings import SettingsPanel
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def settings():
    app = QApplication.instance() or QApplication([])
    applied = []
    panel = SettingsPanel(applied.append)
    panel.resize(780, 550)
    panel.show()
    app.processEvents()
    yield panel, applied
    panel.close()
    panel.deleteLater()
    app.processEvents()


def g_mapping(name):
    return {"group": "G", "role": "V", "slot": "", "permanent_group": name}


def test_single_load_or_shared_group_round_trip(settings):
    panel, applied = settings
    old = default_config()
    old.pop("permanent_groups", None)
    old["group_names"] = {"PED": "Trafik", "VEH": "Temperatur"}
    steps = ["Betong", "Krympning", "Ballast", "IL_01"]
    panel.load(old, steps)
    assert panel.factors.verticalHeaderItem(2).text() == "Temperatur"
    assert panel.veh_label.text() == "Temperatur"
    assert panel.exclusive.text() == "Trafik och temperatur utesluter varandra"

    panel.tabs.setCurrentIndex(panel.tabs.indexOf(panel.single_step_group.parent()))
    index = panel.steps.index("Krympning")
    panel.step_table.setCurrentCell(index, 0)
    panel.single_step_group.click()
    assert panel.tabs.currentWidget() is panel.permanent_page
    key = next(iter(panel.permanent_rows))
    name, editors = panel.permanent_rows[key]
    assert name.text() == "Krympning"
    assert panel.step_inputs["Krympning"][0].currentData() == key
    for f, value in {"gamma_u": 1.0, "gamma_f": 0.0, "xi": 1.0, "gd": 0.95}.items():
        editors[f].setValue(value)
    panel.add_permanent.click()
    second = panel.permanent_table.cellWidget(1, 0)
    second.setText("Egenvikt")
    for step in ("Betong", "Ballast"):
        group = panel.step_inputs[step][0]
        group.setCurrentIndex(group.findText("G · Egenvikt"))
    assert applied == []
    assert "permanent_groups" not in old
    panel.apply_changes()
    saved = applied[-1]
    assert saved["permanent_groups"]["Krympning"] == {
        "gamma_u": 1.0,
        "gamma_f": 0.0,
        "xi": 1.0,
        "gd": 0.95,
    }
    assert saved["mapping"]["Krympning"] == g_mapping("Krympning")
    assert (
        saved["mapping"]["Betong"]
        == saved["mapping"]["Ballast"]
        == g_mapping("Egenvikt")
    )
    assert "IL_01" not in saved["mapping"]
    panel.load(saved, steps)
    assert panel.step_inputs["Krympning"][0].currentText() == "G · Krympning"
    assert panel.step_inputs["Betong"][0].currentText() == "G · Egenvikt"
    assert not panel.step_inputs["IL_01"][0].isEnabled()
    assert panel.permanent_table.cellWidget(0, 2).value() == 0.0


def test_renaming_and_deleting_keep_inactive_step_links(settings):
    panel, applied = settings
    original = default_config()
    original["permanent_groups"] = {
        "Betong": {"gamma_u": 1.25, "gamma_f": 0.9, "xi": 0.85, "gd": 1.0}
    }
    original["mapping"] = {
        "G_ACTIVE": g_mapping("Betong"),
        "G_DISABLED": g_mapping("Betong"),
    }
    snapshot = deepcopy(original)
    panel.load(original, ["G_ACTIVE"])
    name = panel.permanent_table.cellWidget(0, 0)
    name.setText("Egenvikt")
    assert panel.step_inputs["G_ACTIVE"][0].currentText() == "G · Egenvikt"
    panel.apply_changes()
    assert original == snapshot
    renamed = applied[-1]
    assert set(renamed["permanent_groups"]) == {"Egenvikt"}
    assert (
        renamed["mapping"]["G_ACTIVE"]
        == renamed["mapping"]["G_DISABLED"]
        == g_mapping("Egenvikt")
    )
    panel.load(renamed, ["G_ACTIVE"])
    panel.tabs.setCurrentWidget(panel.permanent_page)
    panel.permanent_table.setCurrentCell(0, 0)
    panel.remove_permanent.click()
    panel.apply_changes()
    after = applied[-1]
    assert after["permanent_groups"] == {}
    assert "G_ACTIVE" not in after["mapping"]
    assert after["mapping"]["G_DISABLED"] == {"group": "G", "slot": "", "role": "V"}


def test_invalid_names_and_draft_reset(settings):
    panel, applied = settings
    panel.load(default_config(), ["G_SELF"])
    panel.add_permanent.click()
    name = panel.permanent_table.cellWidget(0, 0)
    name.setText("")
    panel.apply_changes()
    assert not applied
    name.setText("Ballast")
    panel.add_permanent.click()
    panel.permanent_table.cellWidget(1, 0).setText("  ballast  ")
    panel.apply_changes()
    assert not applied
    assert "unika namn" in panel.message.text()
    panel._reset()
    assert not applied
    panel.apply_changes()
    assert applied[-1]["permanent_groups"] == {}
    assert applied[-1]["mapping"] == {}


def test_clicked_editor_selects_its_group_for_deletion(settings):
    panel, applied = settings
    for name in ["Betong", "Ballast", "Jordtryck"]:
        panel.add_permanent.click()
        panel.permanent_table.cellWidget(panel.permanent_table.currentRow(), 0).setText(
            name
        )
    box = panel.permanent_table.cellWidget(0, 0)
    QTest.mouseClick(box, Qt.MouseButton.LeftButton)
    panel.remove_permanent.click()
    panel.apply_changes()
    assert list(applied[-1]["permanent_groups"]) == ["Ballast", "Jordtryck"]
