"""Build the standalone Disc Archiver download for the platform this runs on.

The download needs no Python: PyInstaller freezes the interpreter, Tk and the package into a
window (``Disc Archiver``) and a console command (``disc-archiver``). redumper is downloaded on
first use; ``--with-redumper`` carries the pinned release in ``tools/`` instead. Run from the
package directory, with the package and ``packaging/requirements.txt`` installed:

    python packaging/build_bundle.py --out dist
    python packaging/build_bundle.py --out dist --with-redumper --no-gui-smoke

It writes ``dist/disc-archiver-<version>-<platform>.zip`` and checks that both executables start.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib import metadata
from pathlib import Path

from dinorefurb_disc_archiver import redumper
from dinorefurb_disc_archiver.notice import NOTICE, NOTICE_FILENAME

HERE = Path(__file__).resolve().parent
GUI_NAME = "Disc Archiver"
CLI_NAME = "disc-archiver"


def pyinstaller(entry: str, name: str, work: Path, dist: Path, windowed: bool, onefile: bool) -> None:
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--name", name, "--distpath", str(dist), "--workpath", str(work / "build"), "--specpath", str(work),
        "--copy-metadata", "dinorefurb-disc-archiver", "--collect-submodules", "dinorefurb_disc_archiver",
        "--collect-data", "dinorefurb_disc_archiver",
        "--onefile" if onefile else "--onedir", "--windowed" if windowed else "--console",
    ]
    if sys.platform == "darwin" and windowed:
        command += ["--osx-bundle-identifier", "io.github.kibertoad.disc-archiver"]
    subprocess.run([*command, str(HERE / entry)], check=True)


def add_redumper(stage: Path) -> dict[str, str]:
    """Download the pinned redumper, checked by SHA-256, into ``tools/redumper`` with its licence."""
    pin = redumper.load_pin()
    target = stage / "tools" / "redumper"
    redumper.download(print, target=target)
    (target / "LICENSE.txt").write_bytes(redumper.fetch(pin.url(pin.license_url)))
    marker = json.loads((target / redumper.MARKER).read_text())
    return {"tag": pin.tag, "asset": marker["asset"], "sha256": marker["sha256"], "source": marker["source"]}


def readme(version: str, key: str, bundled: dict[str, str] | None) -> str:
    gui = {"windows": f"{GUI_NAME}.exe", "macos": f"{GUI_NAME}.app"}.get(key.split("-")[0], "disc-archiver-gui")
    cli = f"{CLI_NAME}.exe" if key.startswith("windows") else f"./{CLI_NAME}"
    lines = [
        NOTICE,
        "-" * 72,
        f"Disc Archiver {version} for {key}",
        "",
        "No Python or other installation is needed. Unpack this folder anywhere.",
        "",
        f"The window:   double-click {gui}.",
        f"The command:  open a terminal in this folder and run {cli} --help",
        f"              e.g. {cli} rip --drive E: --output \"My Game\" --accept-personal-use",
        "",
        "Restorations publish a disc profile (docs/disc-profile.json in their repository). Load",
        "it in the window, or pass --profile to the command, to get the format it reads ticked.",
        "",
        "These builds are not code-signed yet. Windows SmartScreen may say it protected your PC:",
        "choose More info, then Run anyway. On macOS, right-click the app and choose Open, or",
        "allow it under System Settings > Privacy & Security.",
        "",
    ]
    if bundled:
        lines += [
            f"tools/redumper is redumper {bundled['tag']}, unmodified, from {bundled['asset']}",
            f"(SHA-256 {bundled['sha256']}). It is free software under the GNU GPL version 3,",
            f"in tools/redumper/LICENSE.txt; its source is at {bundled['source']}.",
        ]
    else:
        pin = redumper.load_pin()
        lines += [
            "redumper, which makes the archival copy, is downloaded from GitHub the first time you",
            f"copy a disc (release {pin.tag}, checked by SHA-256). The window asks first; the command",
            f"downloads unless given --no-download. To download it now, run {cli} install-redumper.",
            "If it cannot be downloaded, cdrdao is used when installed;",
            "otherwise only the data track is copied, without audio. You can also put redumper in",
            "tools/ beside this file yourself.",
        ]
    lines += ["", "Full documentation: https://github.com/kibertoad/refurbished-dinosaurs-toolkit/tree/main/packages/disc-archiver", ""]
    return "\n".join(lines)


def smoke(stage: Path, key: str, gui_smoke: bool, expect_redumper: bool) -> None:
    cli = stage / (f"{CLI_NAME}.exe" if key.startswith("windows") else CLI_NAME)
    subprocess.run([str(cli), "--version"], check=True)
    report = subprocess.run([str(cli), "tools"], check=True, capture_output=True, text=True).stdout
    print(report)
    if expect_redumper and "redumper: ready" not in report:
        raise SystemExit("the bundled command does not find the bundled redumper")
    if gui_smoke:
        gui = {
            "windows": stage / f"{GUI_NAME}.exe",
            "macos": stage / f"{GUI_NAME}.app" / "Contents" / "MacOS" / GUI_NAME,
        }.get(key.split("-")[0], stage / "disc-archiver-gui")
        subprocess.run([str(gui), "--smoke-test"], check=True, timeout=120)


def package(stage: Path, out: Path, name: str) -> Path:
    target = out / f"{name}.zip"
    target.unlink(missing_ok=True)
    if sys.platform == "darwin":
        # ditto keeps the app bundle's symlinks and signatures, which zipfile does not.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(stage), str(target)], check=True)
        return target
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(name) / path.relative_to(stage))
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--version", help="the version to name the download after; defaults to the installed package's")
    parser.add_argument("--with-redumper", action="store_true", help="carry redumper in the zip instead of downloading it on first use")
    parser.add_argument("--no-gui-smoke", action="store_true", help="skip starting the window (no display)")
    arguments = parser.parse_args()
    version = arguments.version or metadata.version("dinorefurb-disc-archiver")
    key = redumper.platform_key()
    name = f"disc-archiver-{version}-{key}"
    out = arguments.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        stage = work / name
        stage.mkdir()
        pyinstaller("cli_entry.py", CLI_NAME, work / "cli", stage, windowed=False, onefile=True)
        # A macOS app is a folder; PyInstaller builds it from --onedir.
        gui_name = "disc-archiver-gui" if key.startswith("linux") else GUI_NAME
        pyinstaller("gui_entry.py", gui_name, work / "gui", work / "gui-dist", windowed=True, onefile=sys.platform != "darwin")
        for built in (work / "gui-dist").iterdir():
            if built.suffix == ".app" or built.is_file():
                shutil.move(str(built), stage / built.name)
        bundled = add_redumper(stage) if arguments.with_redumper else None
        (stage / "README.txt").write_text(readme(version, key, bundled), encoding="utf-8")
        (stage / NOTICE_FILENAME).write_text(NOTICE, encoding="utf-8")
        smoke(stage, key, not arguments.no_gui_smoke, bundled is not None)
        print(package(stage, out, name))


if __name__ == "__main__":
    main()
