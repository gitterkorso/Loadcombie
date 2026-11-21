"""Write the displayed engineering results with Swedish CSV conventions."""

from __future__ import annotations

import csv
import math
import os
import re
import tempfile
from numbers import Integral, Real
from pathlib import Path


def _cell(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Exporten innehåller ett oändligt eller odefinierat tal.")
        return number
    raise ValueError(f"Värdet {value!r} kan inte skrivas i en tabellcell.")


def _csv_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Ja" if value else "Nej"
    if isinstance(value, float):
        return str(value).replace(".", ",")
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        # Prevent spreadsheet programs from interpreting a step name as a formula.
        return "'" + value
    return value


def _rows(headers, rows):
    headers = list(headers)
    if not headers:
        raise ValueError("En exporttabell saknar rubriker.")
    yield [str(header) for header in headers]
    for i, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            raise ValueError(f"Rad {i} har {len(row)} värden men tabellen har {len(headers)} kolumner.")
        yield [_cell(value) for value in row]


def _unique(name, used, limit):
    base = name[:limit]
    result = base
    i = 1
    while result.casefold() in used:
        i += 1
        suffix = f"_{i}"
        result = base[: limit - len(suffix)] + suffix
    used.add(result.casefold())
    return result


def _write_xlsx(tmp, sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    used = set()
    for name, (headers, rows) in sheets.items():
        tab = re.sub(r"[\\/*?:\[\]\x00-\x1f]", "_", name).strip(" '") or "Tabell"
        sheet = book.create_sheet(_unique(tab, used, 31))
        widths = [0] * len(headers)
        for r, row in enumerate(_rows(headers, rows), start=1):
            for c, value in enumerate(row, start=1):
                cell = sheet.cell(r, c, value)
                if isinstance(value, str):
                    cell.data_type = "s"
                if r == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="244D63")
                    cell.alignment = Alignment(vertical="center")
                elif isinstance(value, float):
                    cell.number_format = "0.##########"
                widths[c - 1] = max(widths[c - 1], len(str(value)) if value is not None else 0)
        sheet.row_dimensions[1].height = 23
        for i, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(i)].width = min(65, max(12, width + 2))
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    book.save(tmp)


def write_tables(path, sheets):
    """Return the saved paths. CSV writes the first table to path, extras to path_<name>.csv."""
    destination = Path(path)
    suffix = destination.suffix.lower()
    if suffix not in {".csv", ".xlsx"}:
        raise ValueError("Välj en fil med ändelsen .csv eller .xlsx.")
    if not sheets:
        raise ValueError("Det finns inga tabeller att exportera.")
    destinations = [destination]
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        staged = [Path(tmp) / "table_0"]
        if suffix == ".xlsx":
            _write_xlsx(staged[0], sheets)
        else:
            labels = set()
            for i, (name, (headers, rows)) in enumerate(sheets.items()):
                if i:
                    slug = re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_") or "Tabell"
                    slug = _unique(slug, labels, 70)
                    destinations.append(destination.with_name(f"{destination.stem}_{slug}.csv"))
                    staged.append(Path(tmp) / f"table_{i}")
                with staged[i].open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.writer(handle, delimiter=";")
                    for row in _rows(headers, rows):
                        writer.writerow([_csv_text(value) for value in row])
        backups = {}
        committed = []
        try:
            for i, (source, target) in enumerate(zip(staged, destinations)):
                if target.exists():
                    if not target.is_file():
                        raise ValueError(f"En mapp har samma namn som exportfilen {target.name}.")
                    backup = Path(tmp) / f"old_{i}"
                    os.replace(target, backup)
                    backups[target] = backup
                os.replace(source, target)
                committed.append(target)
        except BaseException:
            for target in reversed(committed):
                if target not in backups:
                    target.unlink(missing_ok=True)
            for target, backup in backups.items():
                os.replace(backup, target)
            raise
    return destinations
