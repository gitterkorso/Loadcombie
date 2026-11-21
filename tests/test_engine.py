"""Reference calculations intentionally avoid the production combination generator."""

import itertools

import numpy as np
import pytest

from lastkomb.engine import default_config, envelope, evaluate, infer_step


def linear(raw, steps, config=None):
    data = np.array(raw, dtype=float)
    return envelope(data[:, :, None], steps, ["SF1"], "SF1", config or default_config())


def test_inference_defaults_are_independent_and_serializable():
    import json

    cfg = default_config()
    assert json.loads(json.dumps(cfg)) == cfg
    cfg["patterns"]["P1"].clear()
    assert len(default_config()["patterns"]["P1"]) == 8
    assert infer_step("H_VEH_V4") == dict(group="VEH", slot="V4", role="H")
    assert infer_step("Q_PED_04") == dict(group="PED", slot="04", role="V")
    assert infer_step("IL_1")["group"] == "IGNORE"
    assert infer_step("Q_SNOW")["group"] == "IGNORE"


def test_self_weight_only_and_unit_factors():
    cfg = default_config()
    cfg["groups"]["G"].update(gamma_u=1, gamma_f=1, xi=1)
    expected = np.array([2.5132, -0.677, 0.0])
    r = linear(expected[:, None], ["G_SELF"], cfg)
    np.testing.assert_array_equal(r["max"], expected)
    np.testing.assert_array_equal(r["min"], expected)


def test_both_equations_and_aggregate_permanent_factor():
    # G = (160 - 60) = 100. 6.10a uses 135+0.6Q, 6.10b uses 120.15+1.5Q.
    r = linear([[160, -60, 2], [160, -60, 20], [-160, 60, -20]], ["G_SELF", "G_SURF", "Q_PED_01"])
    np.testing.assert_allclose(r["max"], [136.2, 150.15, -100])
    np.testing.assert_allclose(r["min"], [100, 100, -150.15])
    assert r["labelmax"][0].startswith("6.10a")
    assert r["labelmax"][1].startswith("6.10b")
    assert (r["fmin"][:, 0] == r["fmin"][:, 1]).all()


def test_unfavourable_fields_shared_direction_and_simultaneous_values():
    steps = ["Q_PED_01", "H_PED_01", "Q_PED_02", "H_PED_02", "Q_PED_03", "H_PED_03"]
    m = np.array([[1.0, 0, -1, 0, 5, 0], [1, 10, 1, -10, 0, 0]])
    data = np.stack([m, np.ones_like(m)], axis=2)
    r = envelope(data, steps, ["SM1", "SF1"], "SM1", default_config())
    np.testing.assert_allclose(r["max"], [9, 16.5])
    np.testing.assert_allclose(r["min"], [-1.5, -13.5])
    np.testing.assert_allclose(evaluate(data, r["fmax"], ["SM1", "SF1"], "SM1"), r["max"])
    assert evaluate(data, r["fmax"], ["SM1", "SF1"], "SF1")[0] == 6.0


def test_independent_brute_force_reference_for_beam_plate_and_compression():
    steps = [
        "G_SELF",
        "G_SURF",
        "Q_PED_01",
        "H_PED_01",
        "Q_PED_02",
        "H_PED_02",
        "Q_VEH_V1",
        "H_VEH_V1",
        "Q_VEH_V2",
        "H_VEH_V2",
    ]
    rng = np.random.default_rng(47)
    data = rng.normal(size=(7, len(steps), 3))
    factors = set()
    for a in (True, False):
        for g in [1, 1.35 if a else 0.89 * 1.35]:
            factors.add((g, g) + (0,) * 8)
            for direction in (-1, 1):
                for slots in itertools.product((0, 1), repeat=2):
                    k = 1.5 * (0.4 if a else 1)
                    p1, p2 = (k * present for present in slots)
                    factors.add((g, g, p1, direction * p1, p2, direction * p2, 0, 0, 0, 0))
                for active in [(1, 0), (0, 1)]:
                    v1, v2 = (1.5 * (0 if a else 1) * p for p in active)
                    factors.add((g, g, 0, 0, 0, 0, v1, direction * v1, v2, direction * v2))
    states = np.einsum("psq,ks->pkq", data, np.array(list(factors)))
    x, y, t = states.transpose(2, 0, 1)
    vm = np.sqrt(x * x + y * y - x * y + 3 * t * t)
    compression = np.min((x + y) / 2 - np.sqrt(((x - y) / 2) ** 2 + t * t), axis=1) < 0
    for quantities, target, expected in [
        (["SF1", "SM1", "SM2"], "SF1", x),
        (["S11", "S22", "S12"], "S11", x),
        (["S11", "S22", "S12"], "Mises", vm),
    ]:
        r = envelope(data, steps, quantities, target, default_config())
        for side, function in [("min", np.min), ("max", np.max)]:
            np.testing.assert_allclose(r[side], function(expected, axis=1))
            assert all(
                tuple(np.round(v, 12)) in {tuple(np.round(f, 12)) for f in factors} for v in r["f" + side]
            )
            np.testing.assert_allclose(evaluate(data, r["f" + side], quantities, target), r[side])
        if quantities[0] == "S11":
            np.testing.assert_array_equal(r["compression"], compression)


def test_mises_all_subsets_and_pressure_any_combination():
    cfg = default_config()
    cfg["mode"] = "TRAFFIC"
    data = np.array([[[3, 3, 0], [0, 0, 2]], [[2, 2, 0], [1, 1, 0]]])
    res = envelope(data, ["Q_PED_01", "Q_PED_02"], ["S11", "S22", "S12"], "Mises", cfg)
    np.testing.assert_allclose(res["max"], [np.sqrt(9 + 12), 3])
    np.testing.assert_allclose(res["min"], [0, 0])
    assert list(res["compression"]) == [True, False]


def test_mises_min_can_require_nontrivial_subset_and_favourable_g():
    cfg = default_config()
    cfg["groups"]["G"].update(gamma_u=1.35, xi=0.89)
    data = np.array([[[3, 0, 0], [-2, 0, 0], [0, 100, 0]]])
    r = envelope(data, ["G_SELF", "Q_PED_01", "Q_PED_02"], ["S11", "S22", "S12"], "Mises", cfg)
    assert r["min"][0] == 0
    np.testing.assert_allclose(r["fmin"][0], [1, 1.5, 0])


def test_exclusion_changed_with_accompanying_psi_and_no_simultaneous_positions():
    steps = ["Q_PED_01", "Q_VEH_V1", "Q_VEH_V2"]
    row = [[10, 8, 7]]
    cfg = default_config()
    assert linear(row, steps, cfg)["max"][0] == 15
    cfg["exclusive"] = False
    assert linear(row, steps, cfg)["max"][0] == 18
    cfg["groups"]["VEH"]["psi"] = 0.5
    r = linear(row, steps, cfg)
    np.testing.assert_allclose(r["fmax"][0], [1.5, 0.75, 0])
    assert r["max"][0] == 21


def test_unit_modes_and_selected_field_patterns():
    cfg = default_config()
    steps = ["G_SELF", "Q_PED_02", "Q_PED_03", "Q_VEH_V1", "Q_VEH_V2"]
    cfg.update(mode="TRAFFIC", ped_strategy="P2")
    cfg["patterns"]["P2"] = ["02", "03"]
    r = linear([[9999, 80, -30, 9, 5]], steps, cfg)
    assert r["max"][0] == 50
    cfg["mode"] = "FATIGUE"
    r = linear([[9999, 80, -30, 9, 5]], steps, cfg)
    assert r["max"][0] == 9 and r["min"][0] == 0


def test_incomplete_fixed_pattern_and_horizontal_only_are_errors():
    cfg = default_config()
    cfg["ped_strategy"] = "P2"
    with pytest.raises(ValueError, match="Fält saknas"):
        linear([[1]], ["Q_PED_02"], cfg)
    with pytest.raises(ValueError, match="vertikalsteg"):
        linear([[1]], ["H_VEH_V1"])


def test_unknown_and_influence_steps_are_never_silently_used():
    cfg = default_config()
    cfg["mode"] = "TRAFFIC"
    cfg["mapping"] = {
        "Vertical": dict(group="PED", role="V", slot="03"),
        "IL_01": dict(group="VEH", role="V", slot="V1"),
    }
    r = linear([[1, 200, 500]], ["Vertical", "IL_01", "Unknown"], cfg)
    np.testing.assert_allclose(r["fmax"][0], [1, 0, 0])
    with pytest.raises(ValueError, match="Inga aktiva"):
        linear([[3]], ["IL_01"], cfg)


def test_missing_value_cannot_be_interpreted_as_zero():
    with pytest.raises(ValueError, match="Resultatvärde"):
        linear([[np.nan]], ["G_SELF"])
    r = linear([[4, np.nan]], ["G_SELF", "Unknown"])
    assert r["max"][0] == 5.4
    a = np.array([[[4.0], [np.nan]]])
    with pytest.raises(ValueError, match="saknar"):
        evaluate(a, np.ones(2), ["SF1"], "SF1")
    np.testing.assert_equal(evaluate(a, [1, 0], ["SF1"], "SF1"), [4])


def test_symmetric_synthetic_p1_and_mirrored_vehicle_slots():
    cfg = default_config()
    cfg.update(mode="TRAFFIC", ped_strategy="P1")
    fields = [f"Q_PED_{n:02d}" for n in range(2, 10)]
    x = np.arange(31)
    values = np.array([30 - np.abs(x - c) for c in np.linspace(2, 28, 8)]).T
    r = linear(values, fields, cfg)
    np.testing.assert_allclose(r["max"], r["max"][::-1])
    left = np.maximum(0, 10 - np.abs(x - 8))
    right = left[::-1]
    np.testing.assert_array_equal(
        linear(left[:, None], ["Q_VEH_V2"], cfg)["max"],
        linear(right[:, None], ["Q_VEH_V4"], cfg)["max"][::-1],
    )


def test_reduction_and_gd_only_affect_unfavourable_g():
    cfg = default_config()
    cfg["groups"]["G"].update(gd=0.91, xi=0.89)
    r = linear([[100.0], [-100.0]], ["G_SELF"], cfg)
    np.testing.assert_allclose(r["max"], [122.85, -100.0])
    np.testing.assert_allclose(r["min"], [100.0, -122.85])


def test_scalar_and_full_tensor_agree_for_mixed_groups_both_signs_and_custom_psi():
    cfg = default_config()
    cfg.update(exclusive=False, ped_strategy="alternative", veh_strategy="adverse")
    cfg["groups"]["VEH"].update(psi=0.35)
    steps = [
        "G_SELF",
        "Q_PED_01",
        "H_PED_01",
        "Q_PED_02",
        "H_PED_02",
        "Q_VEH_V1",
        "H_VEH_V1",
        "Q_VEH_V2",
        "H_VEH_V2",
    ]
    data = np.random.default_rng(6).normal(size=(11, len(steps), 3))
    beam = envelope(data, steps, ["SF1", "SM1", "SM2"], "SF1", cfg)
    plate = envelope(data, steps, ["S11", "S22", "S12"], "S11", cfg)
    for side in ("min", "max"):
        np.testing.assert_allclose(beam[side], plate[side])


def test_1024_field_subsets_against_full_mises_reference():
    cfg = default_config()
    cfg["mode"] = "TRAFFIC"
    data = np.random.default_rng(12).normal(size=(3, 10, 3))
    steps = [f"Q_PED_{i:02d}" for i in range(1, 11)]
    result = envelope(data, steps, ["S11", "S22", "S12"], "Mises", cfg)
    masks = np.array(list(itertools.product([0, 1], repeat=10)))
    sx, sy, tau = np.einsum("ks,psq->qpk", masks, data)
    von_mises = np.sqrt(sx**2 + sy**2 - sx * sy + 3 * tau**2)
    np.testing.assert_allclose(result["max"], von_mises.max(axis=1))
    np.testing.assert_allclose(result["min"], von_mises.min(axis=1))


def test_signed_g_factor_follows_effect_even_when_reduced_upper_below_one():
    cfg = default_config()
    cfg["groups"]["G"].update(gd=0.83)
    steps = ["G_SELF", "Q_PED_01"]
    values = np.array([[[100, 1, 2], [20, 0, 0]], [[-100, 1, 2], [-20, 0, 0]]], float)
    for quantities in [("SF1", "SM1", "SM2"), ("S11", "S22", "S12")]:
        r = envelope(values, steps, quantities, quantities[0], cfg)
        assert r["min"][0] == 100 and r["max"][1] == -100
        np.testing.assert_allclose(r["fmax"][0], [1.35 * 0.89 * 0.83, 1.5])
        np.testing.assert_allclose(r["fmin"][1], [1.35 * 0.89 * 0.83, 1.5])


def test_exercise_a0_independent_sources_and_shrinkage_course_reference():
    steps = [
        "Self-weight",
        "Ballast",
        "Earth pressure",
        "Shrinkage",
        "Traffic I",
        "Traffic II",
        "Temperature I",
        "Temperature II",
    ]
    cfg = default_config()
    cfg.update(
        exclusive=False, ped_strategy="alternative", group_names={"PED": "Trafik", "VEH": "Temperatur"}
    )
    for name in steps[:4]:
        cfg["permanent_groups"][name] = {"gamma_u": 1.35, "gamma_f": 1.0, "xi": 0.89, "gd": 1}
        cfg["mapping"][name] = {"group": "G", "permanent_group": name}
    cfg["permanent_groups"]["Shrinkage"].update(gamma_u=1, gamma_f=0, xi=1)
    for n, (group, gamma, psi) in enumerate([("PED", 1.45, 0.8), ("VEH", 1.5, 0.6)]):
        cfg["groups"][group].update(gamma_u=gamma, psi=psi)
        for i, name in enumerate(steps[4 + 2 * n : 6 + 2 * n]):
            cfg["mapping"][name] = {"group": group, "slot": str(i + 1), "role": "V"}
    # Canvas course 65270, Exercise A0_Complete data set, moment columns, rows 6–8.
    end = [-54.1051, -43.2841, -33.8241, 19.4497, -247.682, -32.2696, -17.115736, 31.897508]
    mid = [67.1281, 53.7341, -35.2405, 19.6519, 307.186, -34.0309, -17.293672, 32.229116]
    r = linear([end, mid, end], steps, cfg)
    np.testing.assert_allclose(r["min"], [-532.19584235, 13.10438725, -532.19584235], atol=1e-8, rtol=0)
    np.testing.assert_allclose(r["max"], [-63.917338, 604.0532377, -63.917338], atol=1e-8, rtol=0)
    np.testing.assert_allclose(r["fmax"][1], [1.2015, 1.2015, 1, 1, 1.45, 0, 0, 0.9])
    np.testing.assert_allclose(r["fmin"][1], [1, 1, 1.2015, 0, 0, 1.16, 1.5, 0])
    assert "Earth pressure gynnsam" in r["labelmax"][1]
    assert "Shrinkage gynnsam" in r["labelmin"][1]
    assert "huvudlast Trafik" in r["labelmax"][1]
    assert "Temperature II" in r["labelmax"][1]


def test_named_source_steps_share_the_sign_of_their_sum():
    cfg = default_config()
    cfg["permanent_groups"]["Stomme"] = {"gamma_u": 2, "gamma_f": 0.75, "xi": 1, "gd": 1}
    steps = ["G_A", "G_B", "G_C"]
    cfg["mapping"] = {s: {"group": "G", "permanent_group": "Stomme"} for s in steps[:2]}
    r = linear([[3, -10, 5]], steps, cfg)
    np.testing.assert_allclose(r["fmax"][0], [0.75, 0.75, 1.35])
    np.testing.assert_allclose(r["fmin"][0], [2, 2, 1])
    assert r["max"][0] == pytest.approx(1.5) and r["min"][0] == -9


def test_named_g_sources_tensor_extrema_and_compression_against_joint_enumeration():
    cfg = default_config()
    cfg["groups"]["G"].update(gamma_u=1.35, gamma_f=1, xi=0.89, gd=0.6)
    cfg["permanent_groups"]["B"] = dict(gamma_u=1.6, gamma_f=0.85, xi=0.8, gd=1.0)
    cfg["mapping"]["G_B"] = {"group": "G", "permanent_group": "B"}
    data = np.random.default_rng(607).normal(size=(11, 3, 3))
    # Include a point in biaxial tension for every combination.
    data[0] = [[5, 5, 0], [2, 2, 0], [1, 1, 0]]
    steps, qs = ["G_A", "G_B", "Q_PED_01"], list(("S11", "S22", "S12"))
    vm_states, tensors = [], []
    signed = {"min": [], "max": []}
    for a in [True, False]:
        ua, ub, q = (1.35 * 0.6, 1.6, 1.5 * 0.4) if a else (1.35 * 0.6 * 0.89, 1.6 * 0.8, 1.5)
        for ga, gb, traffic in itertools.product([ua, 1], [ub, 0.85], [q, 0]):
            total = data[:, 0, :] * ga + data[:, 1, :] * gb + data[:, 2, :] * traffic
            xx, yy, tau = total.T
            vm_states.append(np.sqrt(xx**2 + yy**2 - xx * yy + 3 * tau**2))
            tensors.append((xx + yy) / 2 - np.sqrt(((xx - yy) / 2) ** 2 + tau**2))
        for target in signed:
            m = data[:, :, 0]
            adverse = m >= 0 if target == "max" else m <= 0
            subtotal = m[:, 0] * np.where(adverse[:, 0], ua, 1) + m[:, 1] * np.where(
                adverse[:, 1], ub, 0.85
            )
            signed[target].extend([subtotal, subtotal + m[:, 2] * q])
    for target in ["Mises", "S11"]:
        result = envelope(data, steps, qs, target, cfg)
        for side in ("max", "min"):
            expected = (np.max if side == "max" else np.min)(
                vm_states if target == "Mises" else signed[side], axis=0
            )
            np.testing.assert_allclose(result[side], expected)
            np.testing.assert_allclose(result[side], evaluate(data, result["f" + side], qs, target))
        np.testing.assert_array_equal(result["compression"], np.min(tensors, axis=0) < 0)


def test_many_permanent_sources_have_pointwise_signed_extrema():
    cfg = default_config()
    steps = [f"G_{i}" for i in range(80)]
    for step in steps:
        cfg["permanent_groups"][step] = dict(gamma_u=1.5, gamma_f=1, gd=1, xi=1)
        cfg["mapping"][step] = {"group": "G", "permanent_group": step}
    r = linear([[1, -1] * 40], steps, cfg)
    assert r["min"][0] == -20 and r["max"][0] == 20
    # Mixed positive-semidefinite and compressive points should also finish
    # without traversing 2**80 sign combinations just to flag compression.
    data = np.ones((2, len(steps), 3))
    data[1] *= -1
    checks = itertools.count()
    cfg["_cancelled"] = lambda: next(checks) >= 3
    plate = envelope(data, steps, ["S11", "S22", "S12"], "S11", cfg)
    np.testing.assert_array_equal(plate["compression"], [False, True])
    np.testing.assert_allclose(plate["max"], [120, -80])
    np.testing.assert_allclose(plate["min"], [80, -120])


def test_unresolved_g_source_and_invalid_factor_are_reported():
    cfg = default_config()
    cfg["mapping"]["G_BALLAST"] = {"group": "G", "permanent_group": "Ballast"}
    with pytest.raises(ValueError, match="G_BALLAST.*okänd G-grupp.*Ballast"):
        linear([[1]], ["G_BALLAST"], cfg)
    cfg["permanent_groups"]["Ballast"] = dict(gamma_u=1.35, gamma_f=1, xi=0.89, gd=-0.1)
    with pytest.raises(ValueError, match="Ballast.*minst noll"):
        linear([[1]], ["G_BALLAST"], cfg)


def test_only_an_actually_inverted_envelope_is_rejected():
    cfg = default_config()
    cfg["groups"]["G"].update(gamma_u=0.5, gamma_f=1.0, xi=1)
    for quantities in [["SF1"], ["S11", "S22", "S12"]]:
        data = np.ones((1, 1, len(quantities)))
        with pytest.raises(ValueError, match=r"Omvänt omhölje.*max \(0.5\).*min \(1\).*γd och ξ"):
            envelope(data, ["G_SELF"], quantities, quantities[0], cfg)
    cfg["groups"]["G"]["gamma_u"] = np.nextafter(1.0, 0.0)
    res = linear([[1]], ["G_SELF"], cfg)
    assert res["max"][0] == pytest.approx(res["min"][0])
