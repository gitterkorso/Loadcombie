"""Regression against the independently recalculated Handkontroll.xlsx example."""

import pytest

from lastkomb.analysis import calculate
from lastkomb.data import parse_sources
from lastkomb.demo import example_sources
from lastkomb.engine import default_config


def test_demo_midspan_sm1_matches_handchecked_envelope():
    parts, _ = parse_sources(example_sources())
    result = calculate(parts["HUVUDBALK"], "SM1", default_config(), {"lo": 15.0, "hi": 15.0})
    assert len(result.ids) == 1
    assert not result.warnings
    # LibreOffice recalculation of workbook rows 7–8 from the original 32 step values.
    a_max, a_min = 106.4202575226, 63.8668221758
    b_max, b_min = 150.0975, 62.1670554395
    assert result.bounds["max"][0] == pytest.approx(max(a_max, b_max), abs=1e-10, rel=0)
    assert result.bounds["min"][0] == pytest.approx(min(a_min, b_min), abs=1e-10, rel=0)
    assert result.terms(0, "max")["Q_VEH_V4"] == 1.5
    assert result.terms(0, "min")["G_SELF"] == 1.0
