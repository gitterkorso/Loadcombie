"""Load independent Abaqus step responses and portable Lastkomb projects."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field

import numpy as np

from .engine import infer_step


@dataclass
class Source:
    name: str
    content: bytes
    path: str = ""


@dataclass
class Part:
    name: str
    kind: str
    points: list[dict]
    quantities: list[str]
    steps: list[str]
    values: np.ndarray
    units: dict[str, str]


# Result records are keyed on physical identifiers, never on file row order.
_POINT_FIELDS = ("instance", "element", "node", "hanger", "side", "pos", "x", "y", "z")
_COORDS = {"pos", "x", "y", "z", "hanger", "load_x"}
_LABELS = {
    "element": ("element", "elementlabel", "elementnr", "elementid", "elementnummer", "el", "elem"),
    "node": ("nod", "node", "nodelabel", "nodnr", "nodid", "nodeid", "nodnummer"),
    "instance": ("instance", "instans", "partinstance"),
    "hanger": ("hangarex", "hangarx", "hangerx", "xhangare", "xhangar", "xhanger"),
    "step": ("steg", "stegnamn", "step", "stepname", "loadcase", "lastfall", "ilsteg"),
    "side": ("sida", "side", "sectionpoint", "spos/sneg", "platsida", "skalsida"),
    "pos": (
        "lage",
        "s",
        "distance",
        "position",
        "pos",
        "snittlage",
        "snittkoordinat",
        "lagelangssnitt",
        "lagelangssnittet",
        "langs",
        "avstand",
        "avstandlangssnittet",
        "positionlangssnittet",
        "pathdistance",
        "langssnittet",
        "snittlage",
        "langdlangssnittet",
    ),
    "load_x": ("lastensx", "lastx", "xlast", "loadx", "xload", "lastlage", "lastposition"),
}
for _axis in ("x", "y", "z"):
    _LABELS[_axis] = (_axis, f"coord{_axis}", f"{_axis}coord", f"{_axis}koordinat")
_META = {alias: key for key, aliases in _LABELS.items() for alias in aliases}
_EXPECTED = (
    ["G_SELF", "G_SURF"]
    + [f"{p}_PED_{i:02d}" for p in ("Q", "H") for i in range(1, 11)]
    + [f"{p}_VEH_V{i}" for p in ("Q", "H") for i in range(1, 6)]
)
_BEAM = ("SF1", "SM1", "SM2", "U1", "U2", "U3", "RF1", "RF2", "RF3")
_STRESS = ("S11", "S22", "S12")
_DIM = {
    "SF1": "kN",
    "SM1": "kNm",
    "SM2": "kNm",
    **dict.fromkeys(_STRESS, "MPa"),
    **dict.fromkeys(("U1", "U2", "U3"), "m"),
    **dict.fromkeys(("RF1", "RF2", "RF3"), "kN"),
}
_SCALES = {
    "m": {"m": 1, "mm": 0.001, "cm": 0.01},
    "kN": {"kn": 1, "n": 0.001, "mn": 1000, "millin": 1e-6},
    "kNm": {"knm": 1, "nm": 0.001, "knmm": 0.001, "nmm": 0.000001, "mnm": 1000, "millinm": 1e-6},
    "MPa": {"mpa": 1, "pa": 0.000001, "kpa": 0.001, "gpa": 1000, "n/mm2": 1, "millipa": 1e-9},
    "kN/kN": {"kn/kn": 1, "n/n": 1, "n/kn": 0.001, "kn/n": 1000},
    "kNm/kN": {"knm/kn": 1, "nm/n": 1, "nm/kn": 0.001, "nmm/n": 0.001, "knm/n": 1000},
}
_ALL_UNITS = {k for units in _SCALES.values() for k in units}


def _ascii(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _unit(text):
    # SI prefix case changes the magnitude by nine orders (mPa and MPa).
    text = _ascii(text).replace("mPa", "millipa").replace("mN", "millin")
    return text.casefold().replace("·", "").replace("*", "").replace(" ", "").replace("^", "")


def _head(text):
    """Separate display label and optional bracketed, parenthesized or suffix unit."""
    text = text.strip()
    wrapped = re.fullmatch(r"(.+?)\s*[\[(]\s*([^\[\]()]+?)\s*[\])]", text)
    if wrapped:
        return wrapped.group(1).strip(), wrapped.group(2)
    # Common script headers are e.g. x_m, S11_MPa, SF1_kN_per_kN.
    tokens = re.match(r"^(.+?)[ _]+([^ _]+(?:_per_[^ _]+)?)$", text, re.I)
    if tokens and _unit(tokens.group(2).replace("_per_", "/")) in _ALL_UNITS:
        return tokens.group(1), tokens.group(2).replace("_per_", "/")
    return text, ""


def _label(label):
    return re.sub(r"[^a-z0-9/]", "", _ascii(label).casefold())


def _factor(unit, target, column):
    if not unit:
        return 1
    try:
        return _SCALES[target][_unit(unit)]
    except KeyError:
        raise ValueError(f"Kolumn {column!r} har enheten {unit!r}. Förväntad enhet är {target}.") from None


def _number(cell):
    text = cell.strip()
    if text.casefold() in ("", "na", "n/a", "nan", "inf", "-inf", "+inf", "infinity", "-infinity", "null"):
        return math.nan
    if not re.fullmatch(r"[-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][-+]?\d+)?", text):
        raise ValueError(f"Ogiltigt tal {cell!r}. Använd decimalkomma utan tusentalsavgränsare.")
    val = float(text.replace(",", "."))
    return val if math.isfinite(val) else math.nan


def _step(text):
    text = text.strip()
    if not text:
        raise ValueError("Stegnamn saknas.")
    return text


def _step_family(step):
    rule = infer_step(step)
    if rule["group"] in ("PED", "VEH"):
        prefix = "Q" if rule["role"] == "V" else "H"
        return f"{prefix}_{rule['group']}_{rule['slot']}"
    return step.upper()


@dataclass
class _Sheet:
    name: str
    kind: str
    quantities: list
    steps: dict = field(default_factory=dict)
    points: dict = field(default_factory=dict)
    records: dict = field(default_factory=dict)
    missing: int = 0
    first_missing: str = ""
    conflicts: int = 0

    def add(self, key, step, quantity, value, row):
        cell = key, step, quantity
        if cell in self.records:
            self.conflicts += 1
            if self.records[cell] != value:
                value = math.nan
        self.records[cell] = value
        self.steps[step] = None
        if not math.isfinite(value):
            self.missing += 1
            if not self.first_missing:
                self.first_missing = f"rad {row}, {step}, {quantity}"


def _parse_one(source):
    try:
        content = source.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = source.content.decode("cp1252")
    reader = csv.reader(io.StringIO(content, newline=""), delimiter=";", strict=True)
    fields = next(reader, None)
    if not fields or len(fields) < 2:
        raise ValueError("Rubrikrad saknas eller filen saknar semikolon mellan kolumnerna.")
    raw = [s.strip() for s in fields]
    if any(not s for s in raw):
        raise ValueError("Rubrikraden innehåller ett tomt kolumnnamn.")
    parsed = [_head(s) for s in raw]
    meta = {}
    qcols = {}
    for i, (label, unit) in enumerate(parsed):
        role = _META.get(_label(label))
        quantity = label.strip().upper()
        if role:
            if role in meta:
                raise ValueError(f"Dubbla kolumner för {role} ({raw[i]}).")
            meta[role] = i
        elif quantity in _DIM:
            if quantity in qcols:
                raise ValueError(f"Dubbla kolumner för {quantity}.")
            qcols[quantity] = i

    stem = Path(source.name).stem
    file_q = re.fullmatch(r"(.+?)[_ .-]+(" + "|".join(_BEAM) + ")", stem, re.I)
    if "side" in meta or set(qcols).intersection(_STRESS):
        kind, name = "plate", stem
        must = {"step", "side", "pos", "x", "y"}
        if not set(qcols).intersection(_STRESS):
            raise ValueError("Plåtsnittet saknar spänningskolumner S11, S22 och S12.")
        qcols = {k: v for k, v in qcols.items() if k in _STRESS}
    elif (
        "load_x" in meta
        or (stem.upper().startswith("IL_") and qcols)
        or (
            "x" in meta
            and any(_unit(parsed[i][1]) in _SCALES["kN/kN"] | _SCALES["kNm/kN"] for i in qcols.values())
        )
    ):
        kind, name = "il", stem
        if "load_x" not in meta and "x" in meta:
            meta["load_x"] = meta.pop("x")
        must = {"load_x"}
        if not qcols or set(qcols) - {"SF1", "SM1", "SM2"}:
            raise ValueError("Influenslinjen behöver SF1, SM1 eller SM2 per enhetslast.")
    elif file_q:
        kind, name = "beam", file_q[1]
        quantity = file_q[2].upper()
        must = {"x", "node"}
        if quantity.startswith(("SF", "SM")):
            must |= {"element", "y", "z"}
    else:
        raise ValueError(
            "Okänd filtyp. För breda tabeller ska filnamnet ange del och storhet, exempelvis BAGE_SF1.csv."
        )
    missing_columns = must.difference(meta)
    if missing_columns:
        raise ValueError("Obligatoriska rubriker saknas: " + ", ".join(sorted(missing_columns)) + ".")
    coords = {key: _factor(parsed[idx][1], "m", raw[idx]) for key, idx in meta.items() if key in _COORDS}

    if kind == "beam":
        if qcols or "step" in meta:
            raise ValueError("Bred snittkraftstabell ska ha en resultatkolumn per laststeg.")
        stepcols = [
            (i, _step(label), _factor(unit, _DIM[quantity], raw[i]))
            for i, (label, unit) in enumerate(parsed)
            if i not in meta.values()
        ]
        if not stepcols:
            raise ValueError("Tabellen saknar laststeg.")
        if len({s.casefold() for _, s, _ in stepcols}) != len(stepcols):
            raise ValueError("Rubrikraden har dubbla laststeg.")
        result = _Sheet(name, kind, [quantity])
    else:
        scale = {
            q: _factor(parsed[i][1], _DIM[q] + ("/kN" if kind == "il" else ""), raw[i])
            for q, i in qcols.items()
        }
        result = _Sheet(name, kind, list(qcols))

    for row in reader:
        n = reader.line_num
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if len(row) != len(raw):
            raise ValueError(f"Rad {n} har {len(row)} fält. Rubriken anger {len(raw)}.")
        try:
            point = {}
            for key, idx in meta.items():
                if key == "step":
                    continue
                if key in coords:
                    val = _number(row[idx]) * coords[key]
                    if not math.isfinite(val):
                        raise ValueError(f"Tom eller ogiltig koordinat {raw[idx]}.")
                else:
                    val = row[idx].strip()
                    if not val:
                        raise ValueError(f"Tom punktidentitet {raw[idx]}.")
                    if key == "side":
                        val = val.upper()
                        if val not in ("SPOS", "SNEG"):
                            raise ValueError(f"Okänd plåtsida {val!r}. Ange SPOS eller SNEG.")
                point[key] = val
            if kind == "il":
                point["x"] = point["pos"] = point.pop("load_x")
            elif kind == "beam":
                point["pos"] = point["x"]
            key = tuple(point.get(s) for s in _POINT_FIELDS)
            result.points[key] = point
            if kind == "beam":
                for i, step, multiplier in stepcols:
                    result.add(key, step, quantity, _number(row[i]) * multiplier, n)
            else:
                step = _step(row[meta["step"]]) if kind == "plate" else "IL"
                for q, i in qcols.items():
                    result.add(key, step, q, _number(row[i]) * scale[q], n)
        except ValueError as exc:
            raise ValueError(f"Rad {n}: {exc}") from exc
    if not result.points:
        raise ValueError("Filen saknar datarader.")
    return result


def parse_sources(sources, expected_steps=None):
    """Import complete files transactionally. Missing or conflicting cells remain NaN."""
    warnings, by_part = [], {}
    for src in sources:
        try:
            sheet = _parse_one(src)
        except (ValueError, UnicodeError, csv.Error) as exc:
            warnings.append(f"{src.name}: {exc}")
            continue
        if sheet.missing:
            warnings.append(
                f"{src.name}: {sheet.missing} saknade/ogiltiga värden (första på {sheet.first_missing})."
            )
        if sheet.conflicts:
            warnings.append(f"{src.name}: {sheet.conflicts} dubbletter. Motstridiga värden har spärrats.")
        by_part.setdefault((sheet.name.casefold(), sheet.kind), []).append((src.name, sheet))

    parts = {}
    for (_, kind), files in by_part.items():
        name = files[0][1].name
        if name in parts:
            name = f"{name} ({kind})"
        points, steps, quantities = {}, {}, {}
        for _, sheet in files:
            points.update(sheet.points)
            steps.update(sheet.steps)
            quantities.update(dict.fromkeys(sheet.quantities))
        point_keys = sorted(
            points,
            key=lambda k: (
                points[k].get("side", ""),
                points[k].get("hanger", 0),
                points[k]["pos"],
                points[k].get("y", 0),
                points[k].get("z", 0),
                points[k].get("element", ""),
                points[k].get("node", ""),
            ),
        )
        indices = {key: i for i, key in enumerate(point_keys)}
        si = {s: i for i, s in enumerate(steps)}
        qi = {q: i for i, q in enumerate(quantities)}
        shape = (len(points), len(steps), len(quantities))
        values = np.full(shape, np.nan)
        origins = np.full(shape, -1, dtype=np.int32)
        for fi, (filename, sheet) in enumerate(files):
            overlaps = {}
            for (key, step, quantity), value in sheet.records.items():
                idx = indices[key], si[step], qi[quantity]
                previous = origins[idx]
                if previous >= 0:
                    prevfile = files[previous][0]
                    overlaps[prevfile] = overlaps.get(prevfile, 0) + 1
                    if value != values[idx]:
                        values[idx] = np.nan
                else:
                    origins[idx] = fi
                    values[idx] = value
            for prevfile, count in overlaps.items():
                warnings.append(
                    f"{filename} / {prevfile}: {count} överlappande värden. Motstridiga värden har spärrats."
                )
        gaps = np.count_nonzero(origins == -1)
        if gaps:
            warnings.append(
                f"{name}: olika punkt- eller steguppsättningar i {', '.join(f for f, _ in files)}. "
                f"{gaps} resultatceller saknar underlag."
            )
        if kind != "il":
            actual = {_step_family(step) for step in steps}
            expected = _EXPECTED if expected_steps is None else expected_steps
            missing = [step for step in expected if _step_family(step) not in actual]
            if missing:
                profile = "uppgiftens" if expected_steps is None else "projektets"
                warnings.append(
                    f"{name}: {len(missing)} av {profile} {len(expected)} steg saknas: {', '.join(missing)}."
                )
            unknown = [
                s for s in steps if infer_step(s)["group"] == "IGNORE" and not s.upper().startswith("IL_")
            ]
            if unknown:
                warnings.append(f"{name}: kontrollera kopplingen för okända steg: {', '.join(unknown)}.")
        if kind == "plate" and set(_STRESS) - set(quantities):
            warnings.append(f"{name}: von Mises kräver S11, S22 och S12.")
        units = {q: _DIM[q] + ("/kN" if kind == "il" else "") for q in quantities}
        parts[name] = Part(
            name, kind, [points[k] for k in point_keys], list(quantities), list(steps), values, units
        )
    return parts, warnings


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def save_project(path, sources, settings, cache=None):
    """Replace a project atomically only after its archive has been closed and synced."""
    path = Path(path)
    entries = [
        {
            "name": src.name,
            "member": f"data/{i:05d}.csv",
            "path": str(src.path),
            "sha256": _digest(src.content),
        }
        for i, src in enumerate(sources)
    ]
    config = settings.get("config", {})
    custom_g = bool(config.get("permanent_groups")) or any(
        item.get("permanent_group") for item in config.get("mapping", {}).values()
    )
    manifest = _json_bytes({"schema_version": 2 if custom_g else 1, "files": entries, "settings": settings})
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            tmp = Path(handle.name)
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("project.json", manifest)
                for src, entry in zip(sources, entries):
                    z.writestr(entry["member"], src.content)
                if cache is not None:
                    z.writestr("cache/result.json", _json_bytes(cache))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def load_project(path):
    sources, warnings = [], []
    try:
        with zipfile.ZipFile(path) as z:
            manifest = json.loads(z.read("project.json"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") not in (1, 2):
                raise ValueError("Projektfilens schemaversion stöds inte.")
            if not isinstance(manifest["settings"], dict) or not isinstance(manifest["files"], list):
                raise ValueError("Projektets inställningar eller fillista är ogiltiga.")
            for file in manifest["files"]:
                name, member, original = file["name"], file["member"], file.get("path", "")
                if not all(isinstance(s, str) for s in (name, member, original)):
                    raise ValueError("Ogiltigt filnamn i projektet.")
                content = z.read(member)
                if _digest(content) != file["sha256"]:
                    raise ValueError(f"{name}: den inbäddade filens kontrollsumma stämmer inte.")
                sources.append(Source(name, content, original))
                if original:
                    try:
                        p = Path(original)
                        if p.is_absolute() and p.is_file() and _digest(p.read_bytes()) != file["sha256"]:
                            warnings.append(
                                f"{name}: källfilen har ändrats sedan projektet sparades. Inbäddade data används."
                            )
                    except OSError:
                        warnings.append(
                            f"{name}: källfilen kunde inte kontrolleras. Inbäddade data används."
                        )
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise ValueError(f"Skadad eller ofullständig .lastkomb-fil ({exc}).") from exc
    return sources, manifest["settings"], warnings
