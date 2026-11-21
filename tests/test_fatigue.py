import math
from copy import deepcopy

import numpy as np
import pytest

from lastkomb.data import Part
from lastkomb.fatigue import cyclic_rainflow, miner_spectrum, passage


def line():
    return Part(
        "IL_hangare",
        "il",
        [{"pos": x, "x": x} for x in (0, 2, 4)],
        ["SF1", "SM1"],
        ["IL"],
        np.array([[[0.0, 0.0]], [[1.0, 2.0]], [[0.0, 0.0]]]),
        {"SF1": "kN/kN", "SM1": "kNm/kN"},
    )


def settings():
    return dict(
        front="40",
        rear="80",
        spacing=2,
        area=1000,
        category=80,
        gamma="1,0",
        axle_path=True,
        axial_only=True,
    )


def test_hand_calculated_two_axle_pass_and_miner():
    run = passage(line(), settings())
    np.testing.assert_array_equal(run.positions, [0, 2, 4, 6])
    np.testing.assert_array_equal(run.stress, [0, 40, 80, 0])
    assert run.spectrum == [(80.0, 1.0, 2e6, 1 / 2e6)]
    assert run.passages == 2_000_000


def test_signed_bending_and_rear_axle_breakpoint():
    p = line()
    p.points = [{"pos": x} for x in (0, 1, 5)]
    params = dict(settings(), spacing=0.3, axial_only=False, k1="-0,25", k2=0)
    run = passage(p, params)
    assert run.stress.max() == pytest.approx((40 * 0.925 + 80) / 2)
    assert run.positions[run.stress.argmax()] == 1.3


def test_periodic_rainflow_well_known_reversals_and_rotation():
    inner = [-2, 1, -3, 5, -1, 3, -4, 4]
    for i in range(len(inner)):
        rotated = inner[i:] + inner[:i]
        assert cyclic_rainflow(rotated + [rotated[0]]) == {9.0: 1, 7.0: 1, 4.0: 1, 3.0: 1}
    assert cyclic_rainflow([0, 10, -10, 0]) == {20.0: 1}
    assert cyclic_rainflow([0, 10, 10, 0, 0, 10, 0]) == {10.0: 2}
    assert cyclic_rainflow([0, 0]) == {}


def test_damage_on_both_branches_and_cutoff():
    fat = 90
    knee = fat * (2 / 5) ** (1 / 3)
    cutoff = knee * 0.05**0.2
    rows = miner_spectrum({fat: 1, knee: 1, cutoff: 2, cutoff * 0.99: 10}, fat, 1)
    assert rows[0][2] == 2e6
    assert rows[1][2] == pytest.approx(5e6)
    assert rows[2][2] == pytest.approx(1e8)
    assert rows[2][3] == pytest.approx(2e-8)
    assert math.isinf(rows[3][2])
    assert miner_spectrum({knee * 0.95: 1}, fat, 1)[0][3] == 0
    assert miner_spectrum({fat: 1}, fat, 1.2)[0][2] == pytest.approx(2e6 / 1.2**3)


def test_reject_mismatched_axle_support_missing_data_or_category():
    p = line()
    params = settings()
    for key, value in (("axle_path", False), ("area", "-15"), ("category", "")):
        with pytest.raises(ValueError):
            passage(p, dict(params, **{key: value}))
    for value in (np.nan, 0.1):
        broken = deepcopy(p)
        broken.values[0, 0, 0] = value
        with pytest.raises(ValueError):
            passage(broken, params)
    with pytest.raises(ValueError):
        passage(p, dict(params, axial_only=False, k1=0, k2=1))


def test_gui_save_restore_per_il(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from lastkomb.fatigue import FatiguePanel

    app = QApplication.instance() or QApplication([])
    saved = []
    panel = FatiguePanel(saved.append)
    p = line()
    panel.load(p, {})
    assert "<sub>y</sub>" in panel.bending_labels["k1"].text()
    assert "M<sub>y</sub>" in panel.formula.text()
    panel.load(p, {"part_axes": {p.name: {"My": "SM2", "Mz": "SM1"}}})
    assert "<sub>z</sub>" in panel.bending_labels["k1"].text()
    assert "k₁·M<sub>z</sub>" in panel.formula.text()
    panel.axle_path.setChecked(True)
    panel.axial_only.setChecked(True)
    for name, value in settings().items():
        if name in panel.fields:
            panel.fields[name].setText(str(value))
    panel.run.click()
    assert len(saved) == 1 and "2 000 000" in panel.summary.text()
    panel.load(None, {"fatigue": saved[0]})
    assert not panel.run.isEnabled()
    panel.load(p, {"fatigue": saved[0]})
    assert panel.table.rowCount() == 1 and panel.table.item(0, 0).text() == "80"
    assert len(saved) == 1
    panel.close()
