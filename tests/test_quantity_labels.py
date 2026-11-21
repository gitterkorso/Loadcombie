from lastkomb.analysis import calculate, comparison_curves, make_report, quantity_label
from lastkomb.data import Source, parse_sources
from lastkomb.engine import default_config


def test_engineering_labels_follow_current_part_or_global_axes():
    cfg = {
        "axes": {"My": "SM2", "Mz": "SM1"},
        "part_axes": {"A": {"My": "SM1", "Mz": "SM2"}, "B": {}},
    }
    for part, my, mz in [("A", "SM1", "SM2"), ("B", "SM1", "SM2"), ("C", "SM2", "SM1")]:
        assert quantity_label(my, cfg, part) == f"M_y ({my})"
        assert quantity_label(mz, cfg, part) == f"M_z ({mz})"
        assert quantity_label("SF1", cfg, part) == "N (SF1)"
        assert quantity_label("S11", cfg, part) == "S11"


def test_influence_report_keeps_engineering_and_abaqus_labels():
    source = Source("IL_Ram.csv", b"Lastens x [m];SF1 [kN/kN];SM1 [kNm/kN]\n0;1;2")
    part = next(iter(parse_sources([source])[0].values()))
    cfg = default_config()
    r = calculate(part, "SM1", cfg, {})
    headings, rows = make_report(r, cfg, {})["Influenslinje"]
    assert headings[-2:] == ["N (SF1) [kN/kN]", "M_y (SM1) [kNm/kN]"]
    assert rows[0][-2:] == [1, 2]


def test_pinned_label_uses_display_axes_while_selection_key_stays_compatible():
    source = Source("RAM_SM1.csv", b"Element;Nod;x;y;z;G_SELF\n1;1;0;0;0;10")
    part = next(iter(parse_sources([source])[0].values()))
    cfg = default_config()
    cfg["part_axes"] = {part.name: {"My": "SM2", "Mz": "SM1"}}
    name = "SM1 max vid 0 m"
    view = {"pins": [{"name": name, "part": part.name, "terms": {"G_SELF": 1.0}}]}
    result = calculate(part, "SM1", cfg, view)
    curves = comparison_curves(result, view, cfg)
    assert curves["curve:" + name].name == "M_z (SM1) max vid 0 m"
    assert "M_z (SM1) max vid 0 m [kNm]" in make_report(result, cfg, view)["Valda kurvor"][0]
