"""Downloading the pinned redumper release when redumper is not installed.

The pin is ``redumper.json`` beside this module: a release tag and the SHA-256 of each platform's
zip. A download is used only once its SHA-256 matches the pin. It is unpacked, unmodified, into
the person's own data folder under the tag, so a new pin downloads its own copy and the copies of
earlier tags are removed. This is the archiver's only use of the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .disc import DiscError
from .tools import Log

DOWNLOAD_LIMIT = 200 * 1024 * 1024
MARKER = "installed.json"
Fetch = Callable[[str], bytes]


@dataclass(frozen=True)
class Pin:
    """The redumper release to download."""

    tag: str
    asset: str
    download_url: str
    source: str
    license_url: str
    sha256: dict[str, str]

    def asset_name(self, key: str) -> str:
        """The release asset's file name for a platform."""
        return self.asset.format(tag=self.tag, platform=key)

    def url(self, template: str, key: str = "") -> str:
        """A URL template from the pin, filled in."""
        return template.format(tag=self.tag, asset=self.asset_name(key) if key else "")


def load_pin(text: str | None = None) -> Pin:
    """The pin shipped with the package, or one given as JSON text.

    :raises DiscError: a platform's SHA-256 is not 64 hexadecimal digits.
    """
    if text is None:
        text = resources.files(__package__).joinpath("redumper.json").read_text(encoding="utf-8")
    data = json.loads(text)
    sha256: dict[str, str] = {}
    for key, digest in data["sha256"].items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise DiscError(f"the redumper pin holds no SHA-256 for {key}: {digest!r}")
        sha256[key] = digest.lower()
    return Pin(data["tag"], data["asset"], data["downloadUrl"], data["source"], data["licenseUrl"], sha256)


def platform_key() -> str:
    """``windows-x64``, ``linux-arm64``, ``macos-arm64`` and so on, as redumper names its builds."""
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64", "x86": "x86", "i386": "x86", "i686": "x86"}.get(machine, machine)
    return f"{system}-{arch}"


def data_dir() -> Path:
    """The person's own folder for downloaded programs; ``DISC_ARCHIVER_HOME`` overrides it."""
    configured = os.environ.get("DISC_ARCHIVER_HOME")
    if configured:
        return Path(configured)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "dinorefurb-disc-archiver"


def install_dir(pin: Pin | None = None) -> Path:
    """Where the pinned release is, or will be, unpacked."""
    return data_dir() / "redumper" / (pin or load_pin()).tag


def installed_bin(pin: Pin | None = None) -> Path | None:
    """The ``bin`` folder of a completed download of the pinned release, or None."""
    directory = install_dir(pin)
    return directory / "bin" if (directory / MARKER).is_file() else None


def can_download(pin: Pin | None = None) -> bool:
    """Whether the pin names a build for this computer."""
    return platform_key() in (pin or load_pin()).sha256


def fetch(url: str) -> bytes:
    """GET a URL over HTTPS, at most DOWNLOAD_LIMIT bytes."""
    if not url.startswith("https://"):
        raise DiscError(f"refusing to download from {url}: not HTTPS")
    headers = {"User-Agent": "dinorefurb-disc-archiver", "Accept": "application/octet-stream"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:  # noqa: S310
            data = response.read(DOWNLOAD_LIMIT + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DiscError(f"could not download {url}: {error}") from None
    if len(data) > DOWNLOAD_LIMIT:
        raise DiscError(f"{url} is larger than {DOWNLOAD_LIMIT} bytes")
    return data


def unpack(archive: bytes, target: Path) -> None:
    """Unpack a redumper release zip into ``target``, dropping its one top-level folder."""
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise DiscError("the redumper download is not a zip file") from None
    with bundle:
        for member in bundle.infolist():
            if member.is_dir():
                continue
            parts = Path(member.filename.replace("\\", "/")).parts
            relative = Path(*parts[1:]) if len(parts) > 1 else Path(*parts)
            if not relative.parts or relative.is_absolute() or ".." in relative.parts or relative.drive:
                raise DiscError(f"the redumper download holds an unsafe path {member.filename!r}")
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(bundle.read(member))
            if (member.external_attr >> 16) & 0o111 or relative.parts[0] == "bin":
                out.chmod(0o755)
    program = "redumper.exe" if any(target.glob("bin/redumper.exe")) else "redumper"
    if not (target / "bin" / program).is_file():
        raise DiscError("the redumper download holds no bin/redumper")


def download(log: Log, get: Fetch | None = None, pin: Pin | None = None, target: Path | None = None) -> Path:
    """Download, check and unpack the pinned redumper; return its ``bin`` folder.

    ``target`` defaults to the person's data folder, where the copies of other tags are removed
    once this one is in place; a bundle build passes its own. Nothing is left behind when a step
    fails.
    """
    pin = pin or load_pin()
    get = get or fetch
    key = platform_key()
    if key not in pin.sha256:
        raise DiscError(f"redumper {pin.tag} has no build for {key}")
    expected = pin.sha256[key]
    personal = target is None
    target = target or install_dir(pin)
    name = pin.asset_name(key)
    log(f"Downloading redumper {pin.tag} ({name}) from GitHub")
    archive = get(pin.url(pin.download_url, key))
    actual = hashlib.sha256(archive).hexdigest()
    if actual != expected:
        raise DiscError(f"{name} has SHA-256 {actual}, but {expected} was expected; it was not used")
    log(f"{name}: SHA-256 {actual} checked")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".redumper-", dir=target.parent))
    try:
        unpack(archive, staging)
        marker = {"tag": pin.tag, "asset": name, "sha256": actual, "source": pin.url(pin.source)}
        (staging / MARKER).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    log(f"redumper {pin.tag} is in {target}")
    if personal:
        remove_other_tags(log, pin)
    return target / "bin"


def remove_other_tags(log: Log, pin: Pin) -> None:
    """Remove the finished downloads of tags other than the pin's, which nothing uses any more."""
    for directory in sorted(install_dir(pin).parent.iterdir()):
        if directory.name != pin.tag and not directory.name.startswith(".") and (directory / MARKER).is_file():
            shutil.rmtree(directory, ignore_errors=True)
            log(f"Removed redumper {directory.name} from {directory}")
