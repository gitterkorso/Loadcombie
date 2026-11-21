import pytest
from openpyxl import load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock

from lastkomb.analysis import (
    calculate,
    comparison_curves,
    make_report,
    notation_runs,
    pin_label,
    quantity_label,
)
from lastkomb.data import Source, parse_sources
from lastkomb.engine import default_config
from lastkomb.report import write_tables


def test_engineering_labels_follow_current_part_or_global_axes():
    cfg = {
        "axes": {"My": "SM2", "Mz": "SM1"},
        "part_axes": {"A": {"My": "SM1", "Mz": "SM2"}, "B": {}},
    }
    for part, my, mz in [("A", "SM1", "SM2"), ("B", "SM1", "SM2"), ("C", "SM2", "SM1")]:
        assert quantity_label(my, cfg, part) == f"My ({my})"
        assert quantity_label(mz, cfg, part) == f"Mz ({mz})"
        assert quantity_label("SF1", cfg, part) == "N (SF1)"
        assert quantity_label("S11", cfg, part) == "S11"


def test_influence_report_keeps_engineering_and_abaqus_labels():
    source = Source("IL_Ram.csv", b"Lastens x [m];SF1 [kN/kN];SM1 [kNm/kN]\n0;1;2")
    part = next(iter(parse_sources([source])[0].values()))
    cfg = default_config()
    r = calculate(part, "SM1", cfg, {})
    headings, rows = make_report(r, cfg, {})["Influenslinje"]
    assert headings[-2:] == ["N (SF1) [kN/kN]", "My (SM1) [kNm/kN]"]
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
    assert curves["curve:" + name].name == "Mz (SM1) max vid 0 m"
    assert "Mz (SM1) max vid 0 m [kNm]" in make_report(result, cfg, view)["Valda kurvor"][0]


@pytest.mark.parametrize(
    "old", ["SM1 min vid -2,5 m", "M_y (SM1) min vid -2,5 m", "My (SM1) min vid -2,5 m"]
)
def test_saved_pin_names_use_actual_axis_after_remapping(old):
    cfg = {"part_axes": {"Ram": {"My": "SM2"}}}
    assert pin_label({"name": old, "part": "Ram"}, cfg) == "Mz (SM1) min vid -2,5 m"
    assert list(notation_runs("Max Mz (SM1) [kNm]")) == [
        ("Max M", False),
        ("z", True),
        (" (SM1) [kNm]", False),
    ]


def test_export_subscripts_only_generated_symbols_and_preserves_csv_text(tmp_path):
    user_text = "Min egen My (SM1)"
    source = Source("RAM_SM1.csv", f"Element;Nod;x;y;z;{user_text} [kNm]\n1;1;0;0;0;10".encode())
    part = next(iter(parse_sources([source])[0].values()))
    cfg = default_config()
    cfg["mapping"] = {user_text: {"group": "G", "role": "V", "slot": ""}}
    old = "M_y (SM1) max vid 0 m"
    view = {"overlays": [user_text], "pins": [{"name": old, "part": part.name, "terms": {user_text: 1}}]}
    sheets = make_report(calculate(part, "SM1", cfg, view), cfg, view, project_name=user_text)
    target = tmp_path / "rapport.xlsx"
    write_tables(target, sheets)
    wb = load_workbook(target, rich_text=True)
    try:
        cells = [c.value for sheet in wb for row in sheet for c in row]

        def subscript_letters(cell):
            return "".join(
                run.text for run in cell if isinstance(run, TextBlock) and run.font.vertAlign == "subscript"
            )

        rich = {str(c): subscript_letters(c) for c in cells if isinstance(c, CellRichText)}
        assert rich["My (SM1)"] == "y"
        assert rich["Samtidig My (SM1) [kNm]"] == "y"
        assert rich["My (SM1) max vid 0 m [kNm]"] == "y"
        assert rich["Förvald axelkoppling · My (SM1), Mz (SM2)"] == "yz"
        assert user_text in cells and user_text + " [kNm]" in cells
        assert not any(user_text in rendered for rendered in rich)
    finally:
        wb.close()
    output = tmp_path / "rapport.csv"
    write_tables(output, sheets)
    assert "Samtidig My (SM1) [kNm]" in output.read_text(encoding="utf-8-sig")
