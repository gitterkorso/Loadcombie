"""Build Lastkomb for the current macOS architecture and optionally create a disk image."""

import argparse
from copy import copy
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--dmg", type=Path)
    parser.add_argument("--sign-identity", help="Developer ID Application identity or certificate fingerprint")
    parser.add_argument("--minimum-macos", help="Verified minimum supported macOS version for this runtime")
    args = parser.parse_args()
    if args.minimum_macos and not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", args.minimum_macos):
        parser.error("--minimum-macos expects a version such as 14.0")
    root = Path(__file__).resolve().parents[1]
    build = args.build.resolve()
    build.mkdir(exist_ok=True, parents=True)
    # Index the fonts shipped in the bundle so first launch needs no system font scan.
    from matplotlib import font_manager, get_data_path

    manager = copy(font_manager.fontManager)
    fonts = Path(get_data_path())
    for key in ("ttflist", "afmlist"):
        setattr(
            manager,
            key,
            [entry for entry in getattr(manager, key) if Path(entry.fname).is_relative_to(fonts)],
        )
    font_cache = build / f"fontlist-v{manager.__version__}.json"
    font_manager.json_dump(manager, font_cache)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--workpath",
            str(build / "build"),
            "--distpath",
            str(build / "dist"),
            str(root / "packaging/Lastkomb.spec"),
            "--",
            "--font-cache",
            str(font_cache),
            *(["--sign-identity", args.sign_identity] if args.sign_identity else []),
            *(["--minimum-macos", args.minimum_macos] if args.minimum_macos else []),
        ],
        check=True,
    )
    if args.dmg:
        # A staging directory outside iCloud avoids Finder metadata on inner frameworks.
        with tempfile.TemporaryDirectory(prefix="lastkomb-image-") as tmp:
            stage = Path(tmp) / "image"
            stage.mkdir()
            app = stage / "Lastkomb.app"
            subprocess.run(
                ["ditto", "--norsrc", "--noextattr", "--noqtn", build / "dist/Lastkomb.app", app],
                check=True,
            )
            if args.sign_identity:
                # PyInstaller has signed each collected binary with the same identity.
                subprocess.run(
                    ["codesign", "--force", "--options", "runtime", "--timestamp",
                     "--sign", args.sign_identity, app], check=True,
                )
            else:
                subprocess.run(["codesign", "--force", "--deep", "--sign", "-", app], check=True)
            subprocess.run(["codesign", "--verify", "--deep", "--strict", app], check=True)
            (stage / "Applications").symlink_to("/Applications")
            subprocess.run(
                [
                    "hdiutil",
                    "create",
                    "-ov",
                    "-format",
                    "UDZO",
                    "-fs",
                    "HFS+",
                    "-volname",
                    "Lastkomb",
                    "-srcfolder",
                    stage,
                    args.dmg.resolve(),
                ],
                check=True,
            )
            if args.sign_identity:
                subprocess.run(
                    ["codesign", "--force", "--timestamp", "--sign", args.sign_identity, args.dmg],
                    check=True,
                )
                subprocess.run(["codesign", "--verify", "--strict", args.dmg], check=True)


if __name__ == "__main__":
    main()
