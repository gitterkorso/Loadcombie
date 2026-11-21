"""Build the self-contained Windows x64 distribution from pinned binary wheels.

Build tools: Python 3.12+, pip and Zig 0.13 (ziglang==0.13.0 or zig on PATH).
Run: python packaging/build_windows.py --cache path/to/build-cache --out path/to/Lastkomb-Windows-x64.zip
Use --prepare-only to cache the runtime before the application sources are ready.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
LOCK = HERE / "windows-requirements.txt"
LAUNCHER = HERE / "launcher.c"
ICON = HERE / "Lastkomb.ico"
PYTHON_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
PREFIX = "Lastkomb/"
RUNTIME = PREFIX + "runtime/"
SITE = RUNTIME + "Lib/site-packages/"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_inputs(cache):
    embed = cache / "embed.zip"
    if not embed.exists():
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as source, embed.open("wb") as dest:
            shutil.copyfileobj(source, dest)
    if sha(embed) != PYTHON_SHA:
        raise ValueError(f"Fel SHA-256 för {embed}. Hämta Python-arkivet på nytt.")
    wheel_dir = cache / "wheels"
    wheel_dir.mkdir(exist_ok=True)
    hashes = {line.split("--hash=sha256:")[1].strip() for line in LOCK.read_text().splitlines() if line.strip()}
    found = {sha(p): p for p in wheel_dir.glob("*.whl")}
    if hashes.difference(found):
        subprocess.run([sys.executable, "-m", "pip", "download", "--platform", "win_amd64",
                        "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
                        "--only-binary=:all:", "--no-deps", "--require-hashes", "-r", str(LOCK),
                        "-d", str(wheel_dir)], check=True)
        found = {sha(p): p for p in wheel_dir.glob("*.whl")}
    if hashes.difference(found):
        raise ValueError("Alla låsta Windows-hjul återfanns inte efter hämtning.")
    return embed, sorted(found[h] for h in hashes)


def icon_resource():
    """Encode ICO frames and group as a standard Win32 .res (DWORD alignment)."""
    def record(kind, resource_id, data, flags=0x1030):
        header = struct.pack("<IIHHHHIHHII", len(data), 32, 0xffff, kind,
                             0xffff, resource_id, 0, flags, 0, 0, 0)
        return header + data + bytes(-len(data) % 4)

    ico = ICON.read_bytes()
    reserved, image_type, count = struct.unpack_from("<HHH", ico)
    if reserved or image_type != 1 or not count:
        raise ValueError("Ogiltig Windows-ikon.")
    binary = record(0, 0, b"", 0)
    group = ico[:6]
    for i in range(count):
        entry = 6 + 16 * i
        size, offset = struct.unpack_from("<II", ico, entry + 8)
        binary += record(3, i + 1, ico[offset:offset + size])  # RT_ICON
        group += ico[entry:entry + 12] + struct.pack("<H", i + 1)
    return binary + record(14, 1, group)  # RT_GROUP_ICON


def build_launcher(cache):
    output = cache / "Lastkomb.exe"
    resource = cache / "launcher.res"
    resource.write_bytes(icon_resource())
    if importlib.util.find_spec("ziglang") is not None:
        zig = [sys.executable, "-m", "ziglang"]
    else:
        zig = ["zig"]
    env = {**os.environ, "ZIG_GLOBAL_CACHE_DIR": str(cache / "zig-global"),
           "ZIG_LOCAL_CACHE_DIR": str(cache / "zig-local")}
    subprocess.run([*zig, "cc", "-target", "x86_64-windows-gnu", "-Os", "-s", "-municode",
                    "-Wl,--subsystem,windows", "-Wno-macro-redefined", str(LAUNCHER), str(resource),
                    "-o", str(output), "-luser32"], check=True, env=env)
    return output


def write_bytes(archive, name, data):
    info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compresslevel=5)


def safe_entry(name):
    parts = PurePosixPath(name).parts
    if not parts or parts[0] == "/" or ".." in parts or "\\" in name or ":" in name:
        raise ValueError(f"Otillåten sökväg i ett byggberoende: {name}")
    return parts


def wheel_target(path):
    components = safe_entry(path)
    if not components[0].endswith(".data"):
        return SITE + path
    category = components[1]
    tail = "/".join(components[2:])
    if category == "data":
        return RUNTIME + tail
    if category in ("purelib", "platlib"):
        return SITE + tail
    raise ValueError(f"Oväntad .data-katalog: {path}")


def prepare(cache):
    embed, wheels = load_inputs(cache)
    fingerprint = hashlib.sha256("".join(map(sha, (Path(__file__), LOCK, LAUNCHER, ICON))).encode()).hexdigest()[:20]
    base = cache / f"runtime-{fingerprint}.zip"
    if base.exists():
        return base
    launcher = build_launcher(cache)
    temporary = base.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w") as out:
        write_bytes(out, PREFIX + "Lastkomb.exe", launcher.read_bytes())
        with zipfile.ZipFile(embed) as lib:
            for entry in lib.infolist():
                safe_entry(entry.filename)
                # PySide6 supplies newer compatible VC runtimes app-locally below.
                if entry.filename not in ("python312._pth", "vcruntime140.dll", "vcruntime140_1.dll"):
                    write_bytes(out, RUNTIME + entry.filename, lib.read(entry))
        write_bytes(out, RUNTIME + "python312._pth",
                    b"python312.zip\r\n.\r\nLib/site-packages\r\n../app\r\n")
        write_bytes(out, RUNTIME + "qt.conf", b"[Paths]\nPrefix=Lib/site-packages/PySide6\nPlugins=plugins\n")
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as lib:
                for entry in lib.infolist():
                    if entry.is_dir():
                        continue
                    target = wheel_target(entry.filename)
                    contents = lib.read(entry)
                    write_bytes(out, target, contents)
                    if entry.filename.startswith("PySide6/") and "/" not in entry.filename[len("PySide6/"):]:
                        name = PurePosixPath(entry.filename).name
                        if name.startswith(("msvcp140", "vcruntime140")) and name.endswith(".dll"):
                            write_bytes(out, RUNTIME + name, contents)
    os.replace(temporary, base)
    return base


def verify(artifact, app):
    with zipfile.ZipFile(artifact) as bundle:
        broken = bundle.testzip()
        if broken:
            raise ValueError(f"CRC-fel i {broken}.")
        names = set(bundle.namelist())
        must_have = {PREFIX + "Lastkomb.exe", PREFIX + "app/lastkomb/__main__.py",
                     *[RUNTIME + n for n in ("pythonw.exe", "python.exe", "python312.dll", "python3.dll",
                                              "python312.zip", "python312._pth", "qt.conf",
                                              "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")],
                     *[SITE + n for n in ("PySide6/QtWidgets.pyd", "PySide6/QtGui.pyd", "PySide6/QtCore.pyd",
                                          "PySide6/plugins/platforms/qwindows.dll", "shiboken6/Shiboken.pyd",
                                          "matplotlib/backends/backend_qtagg.py", "openpyxl/__init__.py",
                                          "numpy/_core/_multiarray_umath.cp312-win_amd64.pyd")]}
        if must_have - names:
            raise ValueError("Filer saknas i distributionen: " + str(sorted(must_have - names)))
        if len(names) != len(bundle.infolist()):
            raise ValueError("Dubbla sökvägar i distributionen.")
        for name in (PREFIX + "Lastkomb.exe", RUNTIME + "pythonw.exe"):
            exe = bundle.read(name)
            offset, = struct.unpack_from("<I", exe, 0x3c)
            if (exe[offset:offset+6] != b"PE\0\0\x64\x86" or
                    struct.unpack_from("<H", exe, offset + 24 + 68)[0] != 2):
                raise ValueError(f"{name} är inte ett x64-program med Windows-gränssnitt.")
        for f in app.rglob("*.py"):
            name = PREFIX + "app/lastkomb/" + f.relative_to(app).as_posix()
            if bundle.read(name) != f.read_bytes():
                raise ValueError("Källkoden i ZIP-filen avviker från " + str(f))
            compile(bundle.read(name), name, "exec")
    if sys.platform == "win32":
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(artifact) as archive:
                archive.extractall(tmp)
            console = Path(tmp) / "Lastkomb" / "runtime" / "python.exe"
            test = """
import time
import openpyxl
from PySide6.QtWidgets import QApplication
from lastkomb.window import MainWindow
from lastkomb.engine import default_config, envelope

app = QApplication([])
window = MainWindow()
window.show()
deadline = time.monotonic() + 40
while window.result is None and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
assert window.result is not None, "Demoprojektet saknar resultat."
window.chart.draw()
assert abs(envelope([[[2.0]]], ["G_SELF"], ["SF1"], "SF1", default_config())["max"][0] - 2.7) < 1e-12
openpyxl.Workbook()
window.close()
"""
            subprocess.run([str(console), "-B", "-c", test], check=True, timeout=90,
                           env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    base = prepare(cache)
    if args.prepare_only:
        print("Körmiljön är klar:", base)
        return
    if args.out is None:
        parser.error("--out krävs för att skriva distributionen")
    app = PROJECT / "lastkomb"
    if not (app / "__main__.py").is_file():
        raise ValueError("Programmets startmodul lastkomb/__main__.py saknas.")
    target = args.out.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".tmp") as handle:
        tmp = Path(handle.name)
    try:
        shutil.copyfile(base, tmp)
        with zipfile.ZipFile(tmp, "a") as bundle:
            for f in sorted(app.rglob("*")):
                if f.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in f.parts[len(app.parts):]):
                    write_bytes(bundle, PREFIX + "app/lastkomb/" + f.relative_to(app).as_posix(), f.read_bytes())
            if (PROJECT / "README.md").exists():
                write_bytes(bundle, PREFIX + "README.md", (PROJECT / "README.md").read_bytes())
        verify(tmp, app)
        os.replace(tmp, target)
        print(f"Verifierat Windows-arkiv ({target.stat().st_size:,} byte): {target}")
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
