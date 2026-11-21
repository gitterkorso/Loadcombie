"""Linjär superposition av oberoende Abaqus-steg, med spårbara lastlägen."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import islice, product
from math import prod
import re

import numpy as np


STRESS = ("S11", "S22", "S12")
MODES = ("STR", "TRAFFIC", "FATIGUE")


def default_config():
    return {
        "groups": {
            "G": dict(gamma_u=1.35, gamma_f=1.0, psi=1.0, xi=0.89, gd=1.0),
            "PED": dict(gamma_u=1.50, gamma_f=0.0, psi=0.4, xi=1.00, gd=1.0),
            "VEH": dict(gamma_u=1.50, gamma_f=0.0, psi=0.0, xi=1.00, gd=1.0),
        },
        "permanent_groups": {},
        "exclusive": True,
        "ped_strategy": "adverse",
        "veh_strategy": "alternative",
        "patterns": {
            "P1": [f"{n:02d}" for n in range(2, 10)],
            "P2": [f"{n:02d}" for n in range(2, 6)],
            "custom": [],
        },
        "mapping": {},
        "mode": "STR",
    }


def infer_step(step):
    name = step.strip().upper()
    if name.startswith("G_"):
        return {"group": "G", "slot": "", "role": "V"}
    match = re.fullmatch(r"([QH])_(PED|VEH)_(V?\d+)", name)
    if match:
        component, group, suffix = match.groups()
        number = int(suffix.lstrip("V"))
        slot = f"{number:02d}" if group == "PED" else f"V{number}"
        return {"group": group, "slot": slot, "role": "H" if component == "H" else "V"}
    return {"group": "IGNORE", "slot": "", "role": "V"}


def _components(quantities, target):
    required = STRESS if target == "Mises" else (target,)
    missing = [name for name in required if name not in quantities]
    if missing:
        raise ValueError("Resultatstorhet saknas: " + ", ".join(missing))
    return [quantities.index(name) for name in required]


def _mises(sx, sy, tau):
    # Equivalent deviatoric form avoids subtracting nearly equal squared terms.
    return np.hypot(np.hypot(0.5 * (sx + sy), 0.5 * np.sqrt(3) * (sx - sy)), np.sqrt(3) * tau)


def _compression(sx, sy, tau):
    sigma_min = 0.5 * (sx + sy) - np.hypot(0.5 * (sx - sy), tau)
    scale = np.maximum(1.0, np.maximum(np.maximum(np.abs(sx), np.abs(sy)), np.abs(tau)))
    return sigma_min < -32 * np.finfo(float).eps * scale


def evaluate(values, factors, quantities, target):
    """Apply a shared or point-specific factor vector. Return one value per point."""
    data = np.asarray(values, dtype=float)
    factors = np.asarray(factors, dtype=float)
    if data.ndim != 3 or len(quantities) != data.shape[2]:
        raise ValueError("Ogiltiga dimensioner för resultatmatrisen.")
    if factors.shape not in ((data.shape[1],), data.shape[:2]) or not np.isfinite(factors).all():
        raise ValueError("Faktorvektorn stämmer inte överens med laststegen.")
    selected = data[:, :, _components(list(quantities), target)]
    used = np.broadcast_to(factors, data.shape[:2])
    if not (np.isfinite(selected) | (used[:, :, None] == 0.0)).all():
        raise ValueError("En vald kombination saknar resultatvärden i en eller flera punkter.")
    total = np.einsum("psq,ps->pq", np.where(used[:, :, None] != 0.0, selected, 0.0), used)
    return _mises(*total.T) if target == "Mises" else total[:, 0]


@dataclass
class _Choices:
    """The rows are signed, factored contributions of individual physical slots."""

    vectors: np.ndarray
    strategy: str

    @property
    def count(self):
        if self.strategy == "adverse":
            return 2 ** len(self.vectors) - 1
        return len(self.vectors) if self.strategy == "alternative" else 1

    def extreme(self, column, maximum):
        """Find an admissible nonempty set of slots independently at each point."""
        if self.strategy == "fixed":
            return self.vectors.sum(axis=0)
        effects = column @ self.vectors.T
        best = np.argmax(effects, axis=1) if maximum else np.argmin(effects, axis=1)
        if self.strategy == "alternative":
            return self.vectors[best]
        selected = effects > 0 if maximum else effects < 0
        empty = ~np.any(selected, axis=1)
        selected[np.flatnonzero(empty), best[empty]] = True
        return selected.astype(float) @ self.vectors

    def at(self, ids):
        if self.strategy == "fixed":
            return self.vectors.sum(axis=0)
        if self.strategy == "alternative":
            return self.vectors[ids]
        mask = ((ids[:, None] + 1) >> np.arange(len(self.vectors))) & 1
        return mask.astype(float) @ self.vectors


@dataclass
class _PermanentGroup:
    name: str
    indices: list[int]
    unfavorable: float
    favorable: float


@dataclass
class _Family:
    name: str
    choices: tuple[_Choices, ...]

    @property
    def count(self):
        return prod(choice.count for choice in self.choices)

    def batches(self, size, step_count):
        # Mixed-radix enumeration bounds working memory even for coexisting groups.
        count = self.count
        if count > np.iinfo(np.int64).max:
            raise ValueError("För många fält för fullständig genomräkning. Välj ett fast mönster.")
        for start in range(0, count, size):
            ids = np.arange(start, min(start + size, count), dtype=np.int64)
            factors = np.zeros((len(ids), step_count))
            for choice in self.choices:
                factors += choice.at(ids % choice.count)
                ids = ids // choice.count
            yield factors


def _coefficients(name, group, keys):
    try:
        values = {k: float(group[k]) for k in keys}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Ofullständig koefficienttabell för grupp {name}.") from exc
    if not all(np.isfinite(v) and v >= 0 for v in values.values()):
        raise ValueError(f"Koefficienterna för {name} måste vara ändliga och minst noll.")
    return values


def _configuration(config):
    cfg = default_config()
    cfg.update(config)
    if cfg["mode"] not in MODES:
        raise ValueError("Okänt beräkningsläge.")
    if not isinstance(cfg["exclusive"], bool):
        raise ValueError("Gruppernas uteslutningsregel måste anges som sant eller falskt.")
    groups = cfg.get("groups")
    if not isinstance(groups, dict):
        raise ValueError("Lastgruppernas koefficienttabell saknas.")
    cfg["groups"] = {
        group: _coefficients(group, groups.get(group), ("gamma_u", "gamma_f", "gd", "psi", "xi"))
        for group in ("G", "PED", "VEH")
    }
    custom = cfg["permanent_groups"]
    if not isinstance(custom, dict):
        raise ValueError("De egna G-gruppernas koefficienttabell är ogiltig.")
    cfg["permanent_groups"] = {}
    for name, group in custom.items():
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError(
                "Varje egen G-grupp måste ha ett namn utan inledande eller avslutande blanksteg."
            )
        cfg["permanent_groups"][name] = _coefficients(name, group, ("gamma_u", "gamma_f", "gd", "xi"))
    return cfg


def _classify(steps, cfg):
    members = {"G": {}, "PED": {}, "VEH": {}}
    records = []
    for i, name in enumerate(steps):
        inferred = infer_step(name)
        rule = cfg["mapping"].get(name, inferred)
        if name.strip().upper().startswith("IL_"):
            rule = {"group": "IGNORE", "slot": "", "role": "V"}
        group, role = rule.get("group"), rule.get("role", "V")
        if group not in (*members, "IGNORE") or role not in ("V", "H"):
            raise ValueError(f"Ogiltig grupp eller riktning för {name}.")
        slot = str(rule.get("slot", "")).strip()
        records.append((group, slot, role))
        if group == "G":
            subgroup = rule.get("permanent_group", "")
            if not isinstance(subgroup, str) or (subgroup and subgroup not in cfg["permanent_groups"]):
                raise ValueError(f"Laststeget {name} är kopplat till en okänd G-grupp: {subgroup}.")
            if cfg["mode"] == "STR":
                members["G"].setdefault(subgroup, []).append(i)
        elif group in ("VEH", "PED") and not (group == "PED" and cfg["mode"] == "FATIGUE"):
            if not slot:
                raise ValueError(f"Laststeget {name} saknar fält eller fordonsläge.")
            part = members[group].setdefault(slot, {"V": [], "H": []})
            part[role].append(i)
    for group in ("PED", "VEH"):
        for slot, parts in members[group].items():
            if not parts["V"]:
                raise ValueError(f"{group} {slot}: horisontalsteget saknar aktivt vertikalsteg.")
    return members, records


def _traffic_choices(group, rows, step_count, cfg, coefficient):
    strategy = cfg["ped_strategy" if group == "PED" else "veh_strategy"]
    if strategy not in ("adverse", "alternative", "all"):
        if group != "PED" or strategy not in cfg["patterns"]:
            raise ValueError(f"Okänd regel för grupp {group}: {strategy}.")
        specified = set(map(str, cfg["patterns"][strategy]))
        missing = sorted(specified.difference(rows))
        if missing:
            raise ValueError(f"Fält saknas för {strategy}: " + ", ".join(missing))
        rows = {slot: part for slot, part in rows.items() if slot in specified}
        if not rows:
            raise ValueError(f"Fältmönstret {strategy} är tomt.")
        strategy = "fixed"
    if strategy == "all":
        strategy = "fixed"
    vertical = np.zeros((len(rows), step_count))
    horizontal = np.zeros_like(vertical)
    for i, part in enumerate(rows.values()):
        vertical[i, part["V"]] = coefficient
        horizontal[i, part["H"]] = coefficient
    variants = [_Choices(vertical + horizontal, strategy)]
    if horizontal.any():
        variants.append(_Choices(vertical - horizontal, strategy))
    return variants


def _permanents(members, cfg, equation):
    result = []
    for name, indices in members.items():
        rule = cfg["permanent_groups"][name] if name else cfg["groups"]["G"]
        upper = rule["gamma_u"] * rule["gd"] * (rule["xi"] if equation == "6.10b" else 1.0)
        result.append(_PermanentGroup(name or "G", indices, upper, rule["gamma_f"]))
    return result


def _families(step_count, members, cfg, equation):
    groups = cfg["groups"]
    traffic = [key for key in ("PED", "VEH") if members[key]]
    selections = [(), *((g,) for g in traffic)]
    if not cfg["exclusive"] and len(traffic) == 2:
        selections.append(tuple(traffic))
    for selected in selections:
        leaders = selected if equation == "6.10b" and selected else (None,)
        for lead in leaders:
            label = equation
            if len(selected) > 1 and lead:
                display = cfg.get("group_names", {}).get(lead) or (
                    "gångtrafik" if lead == "PED" else "fordon"
                )
                label += " · huvudlast " + display
            variants = []
            for group in selected:
                g = groups[group]
                scale = 1 if group == lead else g["psi"]
                coefficients = [1.0]
                if cfg["mode"] == "STR":
                    coefficients = [scale * g["gamma_u"] * g["gd"]]
                    if g["gamma_f"] > 0:
                        coefficients.append(scale * g["gamma_f"])
                choices = []
                for k in dict.fromkeys(coefficients):
                    if k != 0:
                        choices.extend(_traffic_choices(group, members[group], step_count, cfg, k))
                if choices:
                    variants.append(choices)
            # A zero-factor leading group can still affect the companion's coefficient.
            for options in product(*variants):
                yield _Family(label, options)


def _new_result(point_count, step_count):
    return {
        "max": np.full(point_count, -np.inf),
        "min": np.full(point_count, np.inf),
        "fmax": np.zeros((point_count, step_count)),
        "fmin": np.zeros((point_count, step_count)),
        "labelmax": np.empty(point_count, dtype=object),
        "labelmin": np.empty(point_count, dtype=object),
        "compression": np.zeros(point_count, dtype=bool),
    }


def _record(result, side, candidate, vectors, label):
    keep = candidate > result[side] if side == "max" else candidate < result[side]
    if np.any(keep):
        result[side][keep] = candidate[keep]
        result["f" + side][keep] = vectors if vectors.ndim == 1 else vectors[keep]
        result["label" + side][keep] = label


def _linear(result, column, families, permanents):
    for side in ("max", "min"):
        maximum = side == "max"
        base = np.zeros_like(column)
        for group in permanents:
            # A single factor applies to all steps from the same source. Use the
            # sign of that source's total effect even when ξγG,sup is below γG,inf.
            effect = column[:, group.indices].sum(axis=1)
            unfavorable = effect >= 0 if maximum else effect <= 0
            base[:, group.indices] = np.where(unfavorable, group.unfavorable, group.favorable)[:, None]
        for family in families:
            factors = base.copy()
            for choice in family.choices:
                factors += choice.extreme(column, maximum)
            _record(result, side, np.einsum("ps,ps->p", column, factors), factors, family.name)


def _permanent_batches(groups, size, step_count):
    base = np.zeros(step_count)
    varying = []
    for group in groups:
        base[group.indices] = group.unfavorable
        if group.unfavorable != group.favorable:
            varying.append(group)
    if not varying:
        yield base[None, :]
        return
    combinations = product(*((g.unfavorable, g.favorable) for g in varying))
    while batch := list(islice(combinations, size)):
        matrix = np.broadcast_to(base, (len(batch), step_count)).copy()
        choices = np.asarray(batch)
        for i, group in enumerate(varying):
            matrix[:, group.indices] = choices[:, i, None]
        yield matrix


def _tension_only(data, quantities, used, records):
    # A nonnegative sum of positive semidefinite stress tensors cannot cause
    # compression. Diagonal dominance is sufficient. Reversible H needs both signs.
    sx, sy, tau = (data[:, used, quantities.index(q)] for q in STRESS)
    safe = np.minimum(sx, sy) >= np.abs(tau)
    horizontal = [j for j, i in enumerate(used) if records[i][0] != "G" and records[i][2] == "H"]
    if horizontal:
        safe[:, horizontal] = (
            (sx[:, horizontal] == 0) & (sy[:, horizontal] == 0) & (tau[:, horizontal] == 0)
        )
    return np.all(safe, axis=1)


def _plate(result, data, quantities, target, families, permanents, tension_only, cancelled):
    # A nonlinear response has no scalar sign to classify independent G sources.
    # Visit their joint factor choices for Mises and for the principal stress check.
    columns = [np.ascontiguousarray(data[:, :, quantities.index(q)]) for q in STRESS]
    points = np.arange(data.shape[0])
    batch_size = max(1, min(128, 120_000 // max(1, len(points))))
    step_count = data.shape[1]
    for family in families:
        traffic_batch_size = min(batch_size, family.count)
        for base in _permanent_batches(permanents, batch_size // traffic_batch_size, step_count):
            for traffic in family.batches(traffic_batch_size, step_count):
                if cancelled is not None and cancelled():
                    raise ValueError("Beräkningen avbröts när urvalet ändrades.")
                vectors = (base[:, None, :] + traffic[None, :, :]).reshape(-1, step_count)
                sx, sy, tau = (c @ vectors.T for c in columns)
                result["compression"] |= np.any(_compression(sx, sy, tau), axis=1)
                if target == "Mises":
                    values = _mises(sx, sy, tau)
                    for side in ("max", "min"):
                        winner = np.argmax(values, axis=1) if side == "max" else np.argmin(values, axis=1)
                        _record(result, side, values[points, winner], vectors[winner], family.name)
                elif np.all(tension_only | result["compression"]):
                    # The signed extrema are already known from _linear.
                    return


def _describe(result, records, steps, permanents, group_names):
    for side in ("max", "min"):
        labels = result["label" + side]
        descriptions = {}
        for i, f in enumerate(result["f" + side]):
            key = (labels[i], f.tobytes())
            if key not in descriptions:
                parts = labels[i].split(" · ", 1)
                for group in reversed(permanents[parts[0]]):
                    factor = f[group.indices[0]]
                    if group.unfavorable == group.favorable:
                        wording = group.name
                    else:
                        status = "ogynnsam" if factor == group.unfavorable else "gynnsam"
                        wording = f"{group.name} {status}"
                    parts.insert(1, wording)
                for group, title in (("PED", "gångfält"), ("VEH", "fordon")):
                    active = [
                        (slot, role, val)
                        for (g, slot, role), val in zip(records, f)
                        if g == group and val != 0
                    ]
                    if active:
                        slots = dict.fromkeys(slot for slot, role, val in active if role == "V")
                        h = next((val for _, role, val in active if role == "H"), 0)
                        sign = " (+H)" if h > 0 else " (−H)" if h < 0 else ""
                        if group_names.get(group):
                            names = [
                                s
                                for s, (g, _, role), val in zip(steps, records, f)
                                if g == group and role == "V" and val != 0
                            ]
                            parts.append(f"{group_names[group]} ({', '.join(names)})" + sign)
                        else:
                            parts.append(title + " " + ", ".join(slots) + sign)
                if not np.any(f):
                    parts.append("avlastad")
                descriptions[key] = " · ".join(parts)
            labels[i] = descriptions[key]


def envelope(values, steps, quantities, target, config):
    """Return extremes, the underlying step factors and a per-point plate warning.

    values[p, s, q] must represent independent characteristic load effects in a
    common local basis. Step factors are shared across all simultaneous components.
    Pass just the active steps. Fixed field patterns require every specified field.
    """
    data = np.asarray(values, dtype=float)
    steps, quantities = list(steps), list(quantities)
    if data.ndim != 3 or data.shape[1:] != (len(steps), len(quantities)):
        raise ValueError("Indatans dimensioner stämmer inte med antal steg och storheter.")
    if len(set(steps)) != len(steps) or len(set(quantities)) != len(quantities):
        raise ValueError("Upprepade namn på steg eller storheter.")
    required = _components(quantities, target)
    cfg = _configuration(config)
    members, records = _classify(steps, cfg)
    plate = all(q in quantities for q in STRESS)
    if plate:
        required = sorted(set(required) | {quantities.index(q) for q in STRESS})
    used = [i for indices in members["G"].values() for i in indices]
    for group in ("PED", "VEH"):
        used.extend(i for pair in members[group].values() for indices in pair.values() for i in indices)
    if not used:
        raise ValueError("Inga aktiva laststeg för valt beräkningsläge.")
    if not np.isfinite(data[:, used, :][:, :, required]).all():
        raise ValueError("Resultatvärde saknas eller är ogiltigt i ett aktivt steg.")
    if not np.isfinite(data).all():
        data = np.where(np.isfinite(data), data, 0)
    result = _new_result(data.shape[0], len(steps))
    tension_only = _tension_only(data, quantities, used, records) if plate and target != "Mises" else None
    equations = {
        "STR": ("6.10a", "6.10b"),
        "TRAFFIC": ("Karakteristisk trafik",),
        "FATIGUE": ("Utmattning",),
    }[cfg["mode"]]
    permanents = {}
    for eq in equations:
        permanents[eq] = _permanents(members["G"], cfg, eq)
        families = list(_families(len(steps), members, cfg, eq))
        if target != "Mises":
            column = np.ascontiguousarray(data[:, :, quantities.index(target)])
            _linear(result, column, families, permanents[eq])
        if plate and (target == "Mises" or not np.all(tension_only | result["compression"])):
            _plate(
                result,
                data,
                quantities,
                target,
                families,
                permanents[eq],
                tension_only,
                cfg.get("_cancelled"),
            )
    if not (np.isfinite(result["min"]).all() and np.isfinite(result["max"]).all()):
        raise ValueError(
            "Resultatet överskrider talområdet. Kontrollera enheter, indata och koefficienter."
        )
    scale = np.maximum(1.0, np.maximum(np.abs(result["min"]), np.abs(result["max"])))
    crossed = result["max"] < result["min"] - 32 * np.finfo(float).eps * scale
    if np.any(crossed):
        point = np.flatnonzero(crossed)[0]
        raise ValueError(
            f"Omvänt omhölje vid resultatpunkt {point + 1} för {target}: "
            f"max ({result['max'][point]:.8g}) ligger under min ({result['min'][point]:.8g}). "
            "Kontrollera de aktiva G-gruppernas ogynnsamma och gynnsamma faktorer, inklusive γd och ξ."
        )
    _describe(result, records, steps, permanents, cfg.get("group_names", {}))
    return result
