"""Small generated datasets for the built-in, explicitly synthetic example."""

import math

from .data import Source


def example_sources():
    steps = ["G_SELF", "G_SURF"]
    steps += [f"{prefix}_PED_{i:02d}" for i in range(1, 11) for prefix in ("Q", "H")]
    steps += [f"{prefix}_VEH_V{i}" for i in range(1, 6) for prefix in ("Q", "H")]
    positions = [j / 2 for j in range(61)]
    sources = []

    def load(step, x, phase=0):
        shape = 4 * x * (30 - x) / 900
        if step == "G_SELF":
            return shape * 52
        if step == "G_SURF":
            return shape * 13
        if "PED" in step:
            middle = (int(step[-2:]) - 0.5) * 3
            if step.startswith("H"):
                return shape * 0.65 * math.sin((x - middle) / 6 + phase)
            return shape * (9 * math.exp(-(((x - middle) / 8) ** 2)) - 1.3)
        middle = (7.5, 15, 7.5, 15, 13)[int(step[-1]) - 1]
        if step.startswith("H"):
            return shape * 4 * math.sin((x - middle) / 7 + phase)
        return shape * (44 + int(step[-1])) * math.exp(-abs(x - middle) / 11)

    def line(items):
        return ";".join((f"{v:.9f}".replace(".", ",") if isinstance(v, float) else str(v)) for v in items)

    def add(name, rows):
        sources.append(Source(name, ("\n".join(map(line, rows)) + "\n").encode("utf-8-sig")))

    for qty in ("SF1", "SM1", "SM2"):
        rows = [["Element", "Nod", "x [m]", "y [m]", "z [m]", *steps]]
        for i, x in enumerate(positions):
            coeff = {"SF1": -3.2, "SM1": 1, "SM2": 0.22 * math.cos(x / 9)}[qty]
            rows.append(
                [
                    100 + i,
                    200 + i,
                    x,
                    0.0,
                    0.0,
                    *(coeff * load(s, x, 0.5 if qty == "SM2" else 0) for s in steps),
                ]
            )
        add(f"HUVUDBALK_{qty}.csv", rows)
    rows = [["Steg", "Sida", "Läge [m]", "x [m]", "y [m]", "S11 [MPa]", "S22 [MPa]", "S12 [MPa]"]]
    for side, sign in (("SPOS", 1), ("SNEG", -1)):
        for x in positions[::4]:
            for s in steps:
                val = load(s, x)
                rows.append(
                    [s, side, x, x, 0.0, sign * 0.6 * val, sign * 0.3 * load(s, x, 0.5), 0.12 * val]
                )
    add("PLAT_Y0_LANGS.csv", rows)
    rows = [["Steg", "Lastens x [m]", "SF1 [kN/kN]", "SM1 [kNm/kN]", "SM2 [kNm/kN]"]]
    for i, x in enumerate(positions):
        h = min(x, 30 - x) / 2
        rows.append([f"IL_{i:02d}", x, -h / 12, h, h * 0.08])
    add("IL_huvudbalk.csv", rows)
    return sources
