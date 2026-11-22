import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from lastkomb.analysis import calculate, comparison_curves
from lastkomb.data import Source, parse_sources
from lastkomb.engine import default_config
from lastkomb.section import section_rows
from lastkomb.window import MainWindow, fresh_view


def wait_for(app, predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("Resultatet blev inte klart.")


def test_section_uses_active_plate_face_steps_and_independent_mises():
    source = Source("PLAT.csv", b"Steg;Sida;Lage;x;y;S11;S22;S12\n"
                    b"Q_VEH_V1;SPOS;4;4;0;10;3;2\nQ_VEH_V2;SPOS;4;4;0;0;20;0\n"
                    b"G_SELF;SPOS;4;4;0;1000;1000;0\nQ_VEH_V1;SNEG;4;4;0;300;0;0\n"
                    b"Q_VEH_V2;SNEG;4;4;0;0;400;0\nG_SELF;SNEG;4;4;0;0;0;0\n")
    part = parse_sources([source])[0]["PLAT"]
    config = {**default_config(), "mode": "TRAFFIC"}
    view = {"side": "SPOS", "lo": 4, "hi": 4}
    result = calculate(part, "S11", config, view)
    rows = {r.quantity: r for r in section_rows(result, config, 0, len(result.ids))}
    assert len(result.ids) == 1
    assert {k: r.maximum for k, r in rows.items()} == {"S11": 10, "S22": 20, "S12": 2, "Mises": 20}
    assert result.concurrent(0, "max")[1] == 3
    curve = comparison_curves(result, {"overlays": ["Q_VEH_V1"]}, config)["curve:Q_VEH_V1"]
    assert section_rows(result, config, 0, 1, curve)[-1].maximum == pytest.approx(91 ** 0.5)
    view["disabled"] = ["Q_VEH_V2"]
    reduced = calculate(part, "S11", config, view)
    assert section_rows(reduced, config, 0, 1)[-1].maximum == pytest.approx(91 ** 0.5)


def test_influence_line_inspects_imported_ordinates():
    source = Source("IL_BRO.csv", b"Lastens x;SM1;SF1\n0;0;0\n3;-8;5\n6;0;0\n")
    part = parse_sources([source])[0]["IL_BRO"]
    result = calculate(part, "SF1", default_config(), {})
    moment, force = section_rows(result, default_config(), 1, 2)
    assert (moment.maximum, moment.minimum, moment.unit, moment.min_index) == (-8, -8, "kNm/kN", 1)
    assert (force.maximum, force.minimum, force.unit) == (5, 5, "kN/kN")


def test_chart_click_drag_interval_and_exact_extreme_navigation():
    app = QApplication.instance() or QApplication([])
    header = b"Element;Nod;x;y;z;G_SELF\n"
    forces = Source("BRO_SF1.csv", header + b"1;1;0;0;0;2\n2;2;10;0;0;10\n3;3;10;0;0;-9\n")
    moments = Source("BRO_SM1.csv", header + b"1;1;0;0;0;3\n2;2;10;0;0;-5\n3;3;10;0;0;30\n")
    window = MainWindow()
    try:
        window.reset([forces, moments], default_config(), fresh_view(), None, False)
        window.show()
        wait_for(app, lambda: window.result is not None)
        chart = window.chart
        chart.draw()

        def mouse(event_name, x, y, shift=False):
            coords = chart.ax.transData.transform((x, y))
            return MouseEvent(event_name, chart, *coords, button=1, modifiers={"shift"} if shift else {})

        y = float(window.result.bounds["min"][2])
        chart.press(mouse("button_press_event", 10, y))
        chart.release(mouse("button_release_event", 10, y))
        wait_for(app, lambda: window.section.rows)
        assert window.selected == 2 and window.view["extreme"] == "min"
        assert window.section.position == 10
        assert "2 punkter" in window.section.caption.text()
        force, moment = window.section.rows
        assert (force.max_index, force.min_index) == (1, 2)
        assert (moment.max_index, moment.min_index) == (2, 1)
        assert force.maximum == pytest.approx(13.5)
        assert moment.maximum == pytest.approx(40.5)
        window.section.follow(QUrl("#1-min"))
        wait_for(app, lambda: window.section.rows and window.result.target == "SM1")
        assert window.selected == 1 and window.view["extreme"] == "min"
        assert "Nod 2" in window.inspector.toPlainText()
        assert "Samtidiga värden" in window.inspector.toPlainText()
        window.overlay("G_SELF", True)
        window.pick(1, "curve:G_SELF")
        wait_for(app, lambda: window.section.rows)
        force, moment = window.section.rows
        assert force.maximum == 10 and force.minimum == -9
        assert moment.maximum == 30 and moment.minimum == -5
        assert window.section.curve_title.text() == "Lastfall · G_SELF"
        window.section.follow(QUrl("#0-min"))
        wait_for(app, lambda: window.section.rows and window.result.target == "SF1")
        assert window.selected == 2 and window.view["extreme"] == "curve:G_SELF"
        chart.draw()
        chart.press(mouse("button_press_event", 10, 1))
        chart.motion(mouse("motion_notify_event", 0.2, 1))
        chart.release(mouse("button_release_event", 0.2, 1))
        wait_for(app, lambda: window.section.rows)
        assert window.section.position == chart.section_line.get_xdata()[0] == 0
        assert window.selected == 0 and window.view["extreme"] == "curve:G_SELF"
        assert "Värde" in window.section.contents.toPlainText()
        assert window.section.rows[0].maximum == 2
        assert "Lastfall" in window.inspector.toPlainText() and "Nod 1" in window.inspector.toPlainText()
        assert window.view["lo"] is None

        chart.press(mouse("button_press_event", 6, 1, shift=True))
        chart.release(mouse("button_release_event", 10.1, 1, shift=True))
        wait_for(app, lambda: window.section.rows)
        assert window.view["lo"] == pytest.approx(6)
        assert window.section.position == 10
        window.pick(0, "max")
        window.pin()
        pin = "curve:" + window.view["pins"][-1]["name"]
        window.pick(0, pin)
        wait_for(app, lambda: window.section.rows)
        assert window.section.curve_title.text().startswith("Fast kombination")
        window.section.follow(QUrl("#1-max"))
        wait_for(app, lambda: window.section.rows and window.result.target == "SM1")
        assert window.view["extreme"] == pin and window.section.rows[1].maximum == pytest.approx(40.5)
        window.section.dismiss()
        assert window.section.isHidden() and chart.section_line is None
    finally:
        window.dirty = False
        window.close()
