"""Build the standalone Disc Archiver download for the platform this runs on.

The download needs no Python: PyInstaller freezes the interpreter, Tk and the package into a
window (``Disc Archiver``) and a console command (``disc-archiver``), and the pinned redumper
release is placed beside them in ``tools/``. Run from the package directory, with the package
and ``packaging/requirements.txt`` installed:

    python packaging/build_bundle.py --out dist
    python packaging/build_bundle.py --out dist --no-redumper --no-gui-smoke

It writes ``dist/disc-archiver-<version>-<platform>.zip`` and checks that both executables start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path

from dinorefurb_disc_archiver.notice import NOTICE, NOTICE_FILENAME

HERE = Path(__file__).resolve().parent
GUI_NAME = "Disc Archiver"
CLI_NAME = "disc-archiver"
DOWNLOAD_LIMIT = 200 * 1024 * 1024


def platform_key() -> str:
    """``windows-x64``, ``linux-x64``, ``macos-arm64`` and so on, as redumper names its builds."""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def pyinstaller(entry: str, name: str, work: Path, dist: Path, windowed: bool, onefile: bool) -> None:
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--name", name, "--distpath", str(dist), "--workpath", str(work / "build"), "--specpath", str(work),
        "--copy-metadata", "dinorefurb-disc-archiver", "--collect-submodules", "dinorefurb_disc_archiver",
        "--onefile" if onefile else "--onedir", "--windowed" if windowed else "--console",
    ]
    if sys.platform == "darwin" and windowed:
        command += ["--osx-bundle-identifier", "io.github.kibertoad.disc-archiver"]
    subprocess.run([*command, str(HERE / entry)], check=True)


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "disc-archiver-bundle", "Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - fixed https URLs
        data = response.read(DOWNLOAD_LIMIT + 1)
    if len(data) > DOWNLOAD_LIMIT:
        raise SystemExit(f"{url} is larger than {DOWNLOAD_LIMIT} bytes")
    return data


def add_redumper(stage: Path, key: str) -> dict[str, str]:
    """Download the pinned redumper build, check its SHA-256 and unpack it under ``tools/redumper``."""
    pin = json.loads((HERE / "redumper.json").read_text())
    asset = pin["assets"].get(key)
    if asset is None:
        raise SystemExit(f"redumper.json pins no redumper build for {key}; add one or pass --no-redumper")
    expected = asset["sha256"]
    if expected is None:
        release = json.loads(_fetch(f"https://api.github.com/repos/superg/redumper/releases/tags/{pin['tag']}"))
        digests = {a["name"]: a.get("digest") or "" for a in release["assets"]}
        if not digests.get(asset["name"], "").startswith("sha256:"):
            raise SystemExit(f"GitHub publishes no SHA-256 for {asset['name']}; pin one in redumper.json")
        expected = digests[asset["name"]].removeprefix("sha256:")
    archive = _fetch(f"https://github.com/superg/redumper/releases/download/{pin['tag']}/{asset['name']}")
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise SystemExit(f"{asset['name']} has SHA-256 {actual}, expected {expected}")
    print(f"redumper {pin['tag']} {asset['name']} sha256 {actual}")
    target = stage / "tools" / "redumper"
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / asset["name"]
        path.write_bytes(archive)
        with zipfile.ZipFile(path) as bundle:
            members = [m for m in bundle.infolist() if not m.is_dir()]
            for member in members:
                parts = Path(member.filename).parts
                # The zip holds one top-level folder; keep what is under it (bin/, lib/).
                relative = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
                if ".." in relative.parts or relative.is_absolute():
                    raise SystemExit(f"{asset['name']} holds an unsafe path {member.filename}")
                out = target / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(bundle.read(member))
                mode = member.external_attr >> 16
                if mode & 0o111 or relative.parts[0] == "bin":
                    out.chmod(0o755)
    (target / "LICENSE.txt").write_bytes(_fetch(pin["licenseUrl"]))
    return {"tag": pin["tag"], "asset": asset["name"], "sha256": actual, "source": pin["source"]}


def readme(version: str, key: str, redumper: dict[str, str] | None) -> str:
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
    if redumper:
        lines += [
            f"tools/redumper is redumper {redumper['tag']}, unmodified, from {redumper['asset']}",
            f"(SHA-256 {redumper['sha256']}). It is free software under the GNU GPL version 3,",
            f"in tools/redumper/LICENSE.txt; its source is at {redumper['source']}.",
        ]
    else:
        lines += ["This build carries no redumper. Install it from https://github.com/superg/redumper", "and put it in tools/ beside this file for archival dumps."]
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
    parser.add_argument("--no-redumper", action="store_true", help="leave redumper out")
    parser.add_argument("--no-gui-smoke", action="store_true", help="skip starting the window (no display)")
    arguments = parser.parse_args()
    version = arguments.version or metadata.version("dinorefurb-disc-archiver")
    key = platform_key()
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
        redumper = None if arguments.no_redumper else add_redumper(stage, key)
        (stage / "README.txt").write_text(readme(version, key, redumper), encoding="utf-8")
        (stage / NOTICE_FILENAME).write_text(NOTICE, encoding="utf-8")
        smoke(stage, key, not arguments.no_gui_smoke, redumper is not None)
        print(package(stage, out, name))


if __name__ == "__main__":
    main()
