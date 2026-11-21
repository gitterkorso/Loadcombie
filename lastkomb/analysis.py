"""Apply a reproducible selection to imported points and generate engineering tables."""

from dataclasses import dataclass

import numpy as np

from .engine import envelope, evaluate, infer_step


def fmt(value, precision=4):
    if isinstance(value, (float, int, np.number)):
        return (
            f"{value:,.{precision}f}".replace(",", " ").replace(".", ",").rstrip("0").rstrip(",")
            if precision
            else str(round(value))
        )
    return str(value)


def group_of(config, step):
    return config.get("mapping", {}).get(step, infer_step(step))["group"]


def quantity_label(quantity, config, part_name):
    """Display engineering notation alongside the Abaqus result identifier."""
    if quantity == "SF1":
        return "N (SF1)"
    if quantity not in ("SM1", "SM2"):
        return quantity
    axes = config.get("part_axes", {}).get(part_name, config.get("axes")) or {}
    my = axes.get("My")
    if my not in ("SM1", "SM2"):
        my = "SM2" if axes.get("Mz") == "SM1" else "SM1"
    axis = "M_y" if quantity == my else "M_z"
    return f"{axis} ({quantity})"


@dataclass
class Result:
    part: object
    ids: list
    steps: list
    values: np.ndarray
    target: str
    bounds: dict
    warnings: list

    @property
    def unit(self):
        return self.part.units.get(self.target, "MPa" if self.target == "Mises" else "")

    def pressure(self, idx):
        if self.part.kind != "plate":
            return ""
        if not {"S11", "S22", "S12"}.issubset(self.part.quantities):
            return "Okänt"
        return "Ja" if self.bounds["compression"][idx] else "Nej"

    def concurrent(self, idx, extreme):
        return self.bounds["f" + extreme][idx] @ self.values[idx]

    def terms(self, idx, extreme):
        factors = self.bounds["f" + extreme][idx]
        return {s: float(f) for s, f in zip(self.steps, factors) if f}


def calculate(part, target, config, view):
    steps = [
        s
        for s in part.steps
        if s not in view.get("disabled", []) and (part.kind == "il" or group_of(config, s) != "IGNORE")
    ]
    if part.kind != "il" and config["mode"] != "STR":
        allowed = ("PED", "VEH") if config["mode"] == "TRAFFIC" else ("VEH",)
        steps = [s for s in steps if group_of(config, s) in allowed]
    if not steps:
        raise ValueError("Inga aktiva laststeg för det valda beräkningsläget.")
    lo, hi = view.get("lo"), view.get("hi")
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("Nedre intervallgränsen är större än den övre.")
    ids = [
        i
        for i, p in enumerate(part.points)
        if (lo is None or p["pos"] >= lo)
        and (hi is None or p["pos"] <= hi)
        and (not view.get("side") or p.get("side", "") == view["side"])
    ]
    if not ids:
        raise ValueError("Det finns inga resultatpunkter i det valda intervallet.")
    ids.sort(key=lambda i: part.points[i]["pos"])
    values = part.values[np.ix_(ids, [part.steps.index(s) for s in steps], range(len(part.quantities)))]
    complete = np.isfinite(values).all(axis=(1, 2))
    warnings = []
    if not complete.all():
        warnings.append(
            f"{int((~complete).sum())} punkter saknar värden för aktiva steg eller samtidiga storheter. De har uteslutits."
        )
        ids = [idx for idx, ok in zip(ids, complete) if ok]
        values = values[complete]
    if not ids:
        raise ValueError(
            "Alla valda punkter saknar värden för minst ett aktivt steg eller en samtidig storhet."
        )
    if part.kind == "il":
        u = values[:, 0, part.quantities.index(target)]
        bounds = dict(
            max=u,
            min=u,
            fmax=np.ones((len(ids), 1)),
            fmin=np.ones((len(ids), 1)),
            labelmax=["Influenslinje"] * len(ids),
            labelmin=["Influenslinje"] * len(ids),
            compression=np.zeros(len(ids), dtype=bool),
        )
    else:
        bounds = envelope(values, steps, part.quantities, target, config)
    return Result(part, ids, steps, values, target, bounds, warnings)


@dataclass
class Curve:
    name: str
    group: str
    factors: np.ndarray
    values: np.ndarray


def pin_label(pin, config):
    """Keep saved selection keys stable while displaying the current axis mapping."""
    name = pin["name"]
    quantity, _, placement = name.partition(" ")
    if quantity in ("SM1", "SM2", "SF1") and placement.startswith(("max vid ", "min vid ")):
        return f"{quantity_label(quantity, config, pin['part'])} {placement}"
    return name


def comparison_curves(result, view, config):
    curves = {}
    if result is None or result.part.kind == "il":
        return curves

    def add(key, name, group, coeff):
        f = np.array([coeff.get(step, 0.0) for step in result.steps])
        curves["curve:" + key] = Curve(
            name, group, f, evaluate(result.values, f, result.part.quantities, result.target)
        )

    for step in view.get("overlays", []):
        if step in result.steps:
            add(step, step, group_of(config, step), {step: 1.0})
    for pin in view.get("pins", []):
        if pin["part"] == result.part.name and set(pin["terms"]).issubset(result.steps):
            add(pin["name"], pin_label(pin, config), "COMB", pin["terms"])
    return curves


def make_report(result, config, view, project_name="", warnings=()):
    part = result.part
    axes = config.get("part_axes", {}).get(part.name, config.get("axes") or {})

    def names(q):
        return quantity_label(q, config, part.name)

    common = ["Del", "Element", "Nod", "Sida", "Hängare x [m]", "x [m]", "y [m]", "z [m]", "Läge [m]"]

    def coords(i):
        pt = part.points[result.ids[i]]
        return [
            part.name,
            *(pt.get(k, "") for k in ("element", "node", "side", "hanger", "x", "y", "z", "pos")),
        ]

    if part.kind == "il":
        return {
            "Influenslinje": (
                common + [f"{names(q)} [{part.units[q]}]" for q in part.quantities],
                [coords(i) + list(map(float, row[0])) for i, row in enumerate(result.values)],
            )
        }
    qs = part.quantities
    labels = common + ["Styrande storhet", "Extrem", "Värde", "Enhet"]
    labels += [f"Samtidig {names(q)} [{part.units[q]}]" for q in qs]
    labels += ["Kombination", "Faktorer per steg", "Tryck i någon kombination"]

    def row(r, i, direction):
        factors = " + ".join(f"{fmt(f, 10)}·{s}" for s, f in r.terms(i, direction).items()) or "0"
        return coords(i) + [
            names(r.target),
            direction,
            float(r.bounds[direction][i]),
            r.unit,
            *map(float, r.concurrent(i, direction)),
            r.bounds["label" + direction][i],
            factors,
            r.pressure(i),
        ]

    chosen, extremes, all_points = [], [], []
    for q in [*qs, *(["Mises"] if set(("S11", "S22", "S12")).issubset(qs) else [])]:
        r = (
            result
            if q == result.target
            else Result(
                part,
                result.ids,
                result.steps,
                result.values,
                q,
                envelope(result.values, result.steps, qs, q, config),
                [],
            )
        )
        for direction, op in (("max", np.argmax), ("min", np.argmin)):
            extremes.append(row(r, int(op(r.bounds[direction])), direction))
            for i in range(len(result.ids)):
                item = row(r, i, direction)
                all_points.append(item)
                if q == result.target:
                    chosen.append(item)
    axis_source = "Vald axelkoppling" if axes else "Förvald axelkoppling"
    axes_note = f"{axis_source} · {names('SM1')}, {names('SM2')}"
    group_names = config.get("group_names", {})
    ped, veh = group_names.get("PED", "Gångtrafik"), group_names.get("VEH", "Servicefordon")
    info = [
        ["Projekt", project_name],
        ["Del", part.name],
        ["Läge", config["mode"]],
        ["Aktiva steg", ", ".join(result.steps)],
        [f"Placeringsregel · {ped}", config["ped_strategy"]],
        [f"Placeringsregel · {veh}", config["veh_strategy"]],
        [f"{ped} och {veh} får kombineras", "Nej" if config["exclusive"] else "Ja"],
        ["Momentaxlar", axes_note],
        ["Punkter i urvalet", len(result.ids)],
        ["Tryck", "Negativ minsta huvudspänning i minst en tillåten kombination."],
    ]
    for notice in dict.fromkeys([*result.warnings, *warnings]):
        info.append(["Avvikelse i underlaget", notice])

    def factors(group):
        return ", ".join(f"{key}={fmt(v, 10)}" for key, v in group.items() if isinstance(v, (int, float)))

    for name, group in config["groups"].items():
        info.append([group_names.get(name, name), factors(group)])
    for name, group in config.get("permanent_groups", {}).items():
        info.append([f"G-grupp · {name}", factors(group)])
    if config.get("permanent_groups"):
        for step in result.steps:
            if group_of(config, step) == "G":
                name = config["mapping"].get(step, {}).get("permanent_group") or "Grundgrupp"
                info.append([f"G-steg · {step}", name])
    sheets = {
        "Diagramvärden": (labels, chosen),
        "Redovisning 5.3": (labels, extremes),
        "Alla punktextremer": (labels, all_points),
        "Beräkningsvillkor": (["Inställning", "Värde"], info),
    }
    curves = comparison_curves(result, view, config)
    if curves:
        sheets["Valda kurvor"] = (
            common + [f"{c.name} [{result.unit}]" for c in curves.values()],
            [coords(i) + [float(c.values[i]) for c in curves.values()] for i in range(len(result.ids))],
        )
        for c in curves.values():
            info.append(
                [
                    c.name,
                    " + ".join(f"{fmt(f, 10)}·{s}" for s, f in zip(result.steps, c.factors) if f) or "0",
                ]
            )
    return sheets
