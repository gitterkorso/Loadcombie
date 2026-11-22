import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

from lastkomb.analysis import calculate
from lastkomb.chart import Chart
from lastkomb.data import parse_sources
from lastkomb.demo import example_sources
from lastkomb.engine import default_config


def test_positive_moments_plot_down_without_changing_values():
    app = QApplication.instance() or QApplication([])
    chart = Chart(lambda *_: None, lambda *_: None)
    parts, _ = parse_sources(example_sources())
    cfg = default_config()
    for part in (parts["HUVUDBALK"], next(p for p in parts.values() if p.kind == "il")):
        for target in ("SM1", "SM2", "SF1"):
            result = calculate(part, target, cfg, {})
            chart.render(result, {}, cfg)
            chart.draw()
            expected_down = target in ("SM1", "SM2")
            low, high = chart.ax.transData.transform([(0, -1), (0, 1)])
            assert bool(high[1] < low[1]) == expected_down
            assert ("positivt nedåt" in chart.ax.get_ylabel()) == expected_down
            for ids, plotted, direction in chart.picklines:
                np.testing.assert_equal(plotted, result.bounds[direction][ids])
    chart.close()


def test_export_omits_section_cursor_and_restores_it_even_on_error(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    chart = Chart(lambda *_: None, lambda *_: None)
    chart.mark_section(15)
    cursor = chart.section_line

    def save_failure(*args, **kwargs):
        assert not cursor.get_visible()
        raise OSError("No space left")

    monkeypatch.setattr(chart.figure, "savefig", save_failure)
    with pytest.raises(OSError, match="No space left"):
        chart.export(tmp_path / "figure.png")
    assert cursor.get_visible()
    chart.close()
