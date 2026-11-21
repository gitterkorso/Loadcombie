import json
import zipfile

import numpy as np
import pytest

from lastkomb.data import Source, load_project, parse_sources, save_project


HEADER = "Element;Nod;x [m];y [m];z [m];G_SELF;Q_PED_01\n"


def src(name, text, encoding="utf-8"):
    return Source(name, text.encode(encoding))


def test_beams_match_labels_not_file_order_and_preserve_end_values():
    first = src("BAGE_SF1.csv", HEADER + "12;2;15,5;0;1;1,2;-3\n13;2;15,5;0;1;2,5;4")
    moment = src(
        "BAGE_SM1.csv",
        "G_SELF [Nm];Element;Nod;z [mm];x [mm];y [mm];Q_PED_01 [Nm]\n"
        "2500;13;2;1000;15500;0;4000\n1200;12;2;1000;15500;0;-3000",
    )
    parts, warnings = parse_sources([first, moment])
    p = parts["BAGE"]
    assert len(p.points) == 2
    assert p.points[0]["pos"] == p.points[1]["pos"] == 15.5
    assert p.units == {"SF1": "kN", "SM1": "kNm"}
    np.testing.assert_array_equal(p.values[:, :, 0], p.values[:, :, 1])
    assert "steg saknas" in warnings[0]


def test_plate_different_faces_and_cut_coordinate_cp1252():
    raw = "S22 [MPa];S12 [MPa];Steg;S11 [kPa];x [m];y [m];Sida;Läge längs snittet [m]\n"
    p = parse_sources(
        [src("PLAT_Y0.csv", raw + "0;0;G_SELF;1000;15;2;SPOS;2\n3;4;G_SELF;-2000;15;2;SNEG;2", "cp1252")]
    )[0]["PLAT_Y0"]
    assert p.kind == "plate"
    assert [r["side"] for r in p.points] == ["SNEG", "SPOS"]
    assert all(r["pos"] == 2 and r["x"] == 15 for r in p.points)
    assert p.values[:, 0, p.quantities.index("S11")].tolist() == [-2, 1]


def test_il_and_hanger_variants():
    il = src("IL_hangare.csv", "Steg;Lastens x [m];SF1 [kN/kN];SM1 [Nm/kN]\nIL_05;2,1;0,25;300")
    hanger = src("HANG_SM2.csv", "Hängare x [m];Element;Nod;x_m;y_m;z_m;G_SELF\n2,15;7;6;2;0;-1;200")
    parts, _ = parse_sources([il, hanger])
    line = parts["IL_hangare"]
    assert line.steps == ["IL"] and line.units["SM1"] == "kNm/kN"
    assert line.points[0]["pos"] == 2.1
    assert line.values[0, 0, :].tolist() == [0.25, 0.3]
    assert parts["HANG"].points[0]["hanger"] == 2.15


def test_optionally_import_displacement_mm_and_nodal_reaction():
    disp = src("NODER_U3.csv", "Nod;x [m];G_SELF [mm]\n5;2;2,5")
    force = src("NODER_RF3.csv", "Nod;x [m];G_SELF [N]\n5;2;2000")
    p = parse_sources([disp, force])[0]["NODER"]
    assert p.values[0, 0].tolist() == [0.0025, 2]
    assert p.units == {"U3": "m", "RF3": "kN"}


def test_missing_or_conflicting_values_never_become_zero():
    a = src("BAGE_SF1.csv", HEADER + "1;5;2;0;1;1;\n2;7;2;0;1;7;2")
    b = src("BAGE_SM1.csv", HEADER + "1;5;2;0;1;1;1")
    c = src("BAGE_SF1.csv", HEADER + "1;5;2;0;1;3;1")
    parts, warnings = parse_sources([a, b, c])
    v = parts["BAGE"].values
    assert np.isnan(v[0, 0, 0]) and np.isnan(v[0, 1, 0]) and np.isnan(v[1, :, 1]).all()
    assert any("överlappande" in w for w in warnings)
    assert any("punkt- eller steg" in w for w in warnings)


@pytest.mark.parametrize(
    "header, row, word",
    [
        ("Nod,x [m],G_SELF", "1,5,12", "semikolon"),
        (HEADER.rstrip(), "11;2;4;0;0;10;1,000.5", "Rad 2"),
        (HEADER.replace("G_SELF;", "G_SELF [Pa];").rstrip(), "11;2;4;0;0;10;1", "enhet"),
        (HEADER.replace("Nod;", "Element;").rstrip(), "11;2;4;0;0;10;1", "Dubbla"),
        (HEADER.replace("Nod;", "").rstrip(), "11;4;0;0;10;1", "saknas"),
    ],
)
def test_reject_invalid_file_keep_other_source(header, row, word):
    good = src("GIRDER_SM1.csv", HEADER + "9;5;2;0;0;1;2")
    bad = src("BAGE_SF1.csv", header + "\n" + row)
    parts, warnings = parse_sources([bad, good])
    assert set(parts) == {"GIRDER"} and any(word in w and "BAGE_SF1" in w for w in warnings)


def test_archive_roundtrip_move_source_change_and_checksum_failure(tmp_path):
    orig = tmp_path / "BAGE_SF1.csv"
    orig.write_bytes((HEADER + "1;5;2;0;1;3;8").encode("utf-8"))
    source = Source(orig.name, orig.read_bytes(), str(orig))
    project = tmp_path / "prov.lastkomb"
    settings = {"config": {"xi": 0.89}, "chart": {"mode": "dark", "range": [2, 15]}}
    save_project(project, [source], settings, cache={"version": 1})
    assert load_project(project) == ([source], settings, [])
    orig.write_bytes(b"ny")
    records, restored, warnings = load_project(project)
    assert records == [source] and restored == settings and "ändrats" in warnings[0]
    orig.unlink()
    assert load_project(project)[0] == [source]
    damaged = tmp_path / "skadat.lastkomb"
    with zipfile.ZipFile(project) as zin, zipfile.ZipFile(damaged, "w") as zout:
        for name in zin.namelist():
            zout.writestr(name, b"andra data" if name.startswith("data/") else zin.read(name))
    with pytest.raises(ValueError, match="kontrollsumma"):
        load_project(damaged)


def test_atomic_save_failure_preserves_old_project(tmp_path):
    project = tmp_path / "p.lastkomb"
    save_project(project, [], {"a": 1})
    with pytest.raises(ValueError):
        save_project(project, [], {"a": 2}, cache={"bad": float("nan")})
    assert load_project(project) == ([], {"a": 1}, [])
    assert list(tmp_path.iterdir()) == [project]


def test_version_guards_independent_groups_and_reads_older_projects(tmp_path):
    project = tmp_path / "bro.lastkomb"
    old = {"config": {"permanent_groups": {}}}
    new = {"config": {"permanent_groups": {"Krympning": {"gamma_u": 1, "gamma_f": 0, "xi": 1, "gd": 1}}}}
    for settings, version in ((old, 1), (new, 2)):
        save_project(project, [], settings)
        with zipfile.ZipFile(project) as z:
            assert json.loads(z.read("project.json"))["schema_version"] == version
        assert load_project(project) == ([], settings, [])


def test_project_expected_steps_warn_for_its_own_missing_loads():
    raw = src("BRO_SM1.csv", HEADER + "1;1;0;0;0;4;0")
    assert not parse_sources([raw], ["G_SELF", "Q_PED_01"])[1]
    warnings = parse_sources([raw], ["G_SELF", "Q_PED_01", "Ballast"])[1]
    assert warnings == ["BRO: 1 av projektets 3 steg saknas: Ballast."]


def test_quantity_units_disambiguate_unlabelled_influence_line_and_si_case():
    il = src("mittsnitt.csv", "x [m];SM2 [kN·m/kN]\n3;2")
    p = parse_sources([il])[0]["mittsnitt"]
    assert p.kind == "il" and p.values[0, 0, 0] == 2
    force = src("TEST_RF3.csv", "Nod;x [m];G_SELF [mN];G_SURF [MN]\n1;0;1;1")
    f = parse_sources([force])[0]["TEST"]
    np.testing.assert_allclose(f.values[0, :, 0], [1e-6, 1000], atol=0)


def test_unknown_name_with_valid_prefix_is_flagged_while_canonical_aliases_are_recognised():
    raw = "Element;Nod;x;y;z;Q_PED_01_L;Q_PED_1;H_VEH_V01;IL_2\n1;1;0;0;0;2;3;4;5"
    parts, warnings = parse_sources([src("BAGE_SM1.csv", raw)])
    assert parts["BAGE"].steps == ["Q_PED_01_L", "Q_PED_1", "H_VEH_V01", "IL_2"]
    unknown = next(w for w in warnings if "okända steg" in w)
    assert unknown == "BAGE: kontrollera kopplingen för okända steg: Q_PED_01_L."
    missing = next(w for w in warnings if "steg saknas" in w).split(":")[-1]
    assert "Q_PED_01" not in missing and "H_VEH_V1" not in missing
