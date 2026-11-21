from copy import deepcopy

import numpy as np
import pytest

from lastkomb.checks import check_balance, check_deflection, check_identity, check_symmetry
from lastkomb.data import Part
from lastkomb.engine import default_config


def dataset(q, steps, values):
    points = [dict(x=x, pos=x, y=0, z=0, node=str(i)) for i, x in enumerate([0, 1, 2])]
    unit = "m" if q == "U3" else "kN"
    return Part("Prov", "beam", points, [q], steps, np.asarray(values, dtype=float)[:, :, None], {q: unit})


def test_identity_and_reflection():
    part = dataset("SF1", ["G_SELF", "Q_PED_01", "Q_PED_02"], [[7, 1, -4], [-3, 2, -2], [0, 4, -1]])
    assert "3 importerade" in check_identity(part, "G_SELF")
    assert "Inom tolerans" in check_symmetry(part, "SF1", "Q_PED_01", "Q_PED_02", 1, 1e-6, 0.001, -1)
    assert "Utanför tolerans" in check_symmetry(part, "SF1", "Q_PED_01", "Q_PED_02", 1, 1e-6, 0.001, 1)
    part.points[2]["y"] = 2
    with pytest.raises(ValueError, match="saknar motpunkt"):
        check_symmetry(part, "SF1", "Q_PED_01", "Q_PED_02", 1, 1e-6, 0.001, -1)
    part.points[2] = deepcopy(part.points[0])
    with pytest.raises(ValueError, match="flera kandidater"):
        check_symmetry(part, "SF1", "Q_PED_01", "Q_PED_02", 0.5, 1e-6, 0.001, -1)


def test_reactions_require_unique_nodes_and_external_resultant():
    part = dataset("RF3", ["G_SELF"], [[10], [-5], [20]])
    assert "Inom tolerans" in check_balance(part, "RF3", "G_SELF", -25, 0, True)
    assert "Utanför tolerans" in check_balance(part, "RF3", "G_SELF", 25, 0, True)
    with pytest.raises(ValueError, match="Bekräfta"):
        check_balance(part, "RF3", "G_SELF", -25, 0, False)
    part.points[1]["node"] = part.points[0]["node"]
    with pytest.raises(ValueError, match="flera gånger"):
        check_balance(part, "RF3", "G_SELF", -25, 0, True)


def test_deflection_uses_traffic_on_entire_part():
    part = dataset("U3", ["G_SELF", "Q_PED_01"], [[-20, 0], [-30, -0.080], [-20, 0]])
    cfg = default_config()
    assert "Över L/400" in check_deflection(part, "U3", cfg, {"lo": 2}, 30, True)
    assert "Inom L/400" in check_deflection(part, "U3", cfg, {}, 40, True)
    with pytest.raises(ValueError, match="vertikal"):
        check_deflection(part, "U3", cfg, {}, 40, False)
    part.values[0, 1, 0] = np.nan
    with pytest.raises(ValueError, match="Ofullständiga"):
        check_deflection(part, "U3", cfg, {}, 40, True)
