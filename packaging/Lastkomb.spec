import argparse
from pathlib import Path

options = argparse.ArgumentParser()
options.add_argument("--font-cache", required=True)
options.add_argument("--sign-identity")
options.add_argument("--minimum-macos")
args = options.parse_args()

root = Path(SPECPATH).parent
icon = str(root / "packaging/Lastkomb.icns")
analysis = Analysis(
    [str(root / "start.py")],
    datas=[(str(root / "lastkomb/icon.png"), "lastkomb"), (args.font_cache, ".")],
    excludes=["tkinter", "PyQt5", "PyQt6"],
)
exe = EXE(
    PYZ(analysis.pure),
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Lastkomb",
    console=False,
    icon=icon,
    codesign_identity=args.sign_identity,
)
collect = COLLECT(exe, analysis.binaries, analysis.datas, name="Lastkomb")
metadata = {"CFBundleVersion": "1"}
if args.minimum_macos:
    metadata["LSMinimumSystemVersion"] = args.minimum_macos
app = BUNDLE(
    collect,
    name="Lastkomb.app",
    icon=icon,
    bundle_identifier="se.lastkomb.desktop",
    version="0.1.0",
    info_plist=metadata,
)
