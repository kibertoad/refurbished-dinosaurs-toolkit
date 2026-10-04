"""Downloading the pinned redumper release when redumper is not installed.

The pin is ``redumper.json`` beside this module: a release tag and the SHA-256 of each platform's
zip. A download is used only once its SHA-256 matches the pin, or, while the pin holds no value
for the platform, the digest GitHub publishes for the asset. It is unpacked, unmodified, into the
person's own data folder under the tag, so a new pin downloads its own copy. This is the
archiver's only use of the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
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
    release_api_url: str
    source: str
    license_url: str
    sha256: dict[str, str | None]

    def asset_name(self, key: str) -> str:
        """The release asset's file name for a platform."""
        return self.asset.format(tag=self.tag, platform=key)

    def url(self, template: str, key: str = "") -> str:
        """A URL template from the pin, filled in."""
        return template.format(tag=self.tag, asset=self.asset_name(key) if key else "")


def load_pin(text: str | None = None) -> Pin:
    """The pin shipped with the package, or one given as JSON text."""
    if text is None:
        text = resources.files(__package__).joinpath("redumper.json").read_text(encoding="utf-8")
    data = json.loads(text)
    return Pin(
        data["tag"], data["asset"], data["downloadUrl"], data["releaseApiUrl"], data["source"],
        data["licenseUrl"], dict(data["sha256"]),
    )


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
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:  # noqa: S310
            data = response.read(DOWNLOAD_LIMIT + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DiscError(f"could not download {url}: {error}") from None
    if len(data) > DOWNLOAD_LIMIT:
        raise DiscError(f"{url} is larger than {DOWNLOAD_LIMIT} bytes")
    return data


def expected_sha256(pin: Pin, key: str, get: Fetch | None = None) -> str:
    """The SHA-256 the download must have: the pin's, or GitHub's published digest when unpinned."""
    if key not in pin.sha256:
        raise DiscError(f"redumper {pin.tag} has no build for {key}")
    pinned = pin.sha256[key]
    if pinned:
        return pinned.lower()
    try:
        release = json.loads((get or fetch)(pin.url(pin.release_api_url)))
    except json.JSONDecodeError:
        raise DiscError("GitHub's release listing for redumper is not JSON") from None
    name = pin.asset_name(key)
    for asset in release.get("assets", []):
        digest = asset.get("digest") or ""
        if asset.get("name") == name and digest.startswith("sha256:"):
            return digest.removeprefix("sha256:").lower()
    raise DiscError(f"GitHub publishes no SHA-256 for {name}, and the pin has none; it was not downloaded")


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

    ``target`` defaults to the person's data folder; a bundle build passes its own. Nothing is
    left behind when a step fails.
    """
    pin = pin or load_pin()
    get = get or fetch
    key = platform_key()
    target = target or install_dir(pin)
    expected = expected_sha256(pin, key, get)
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
    return target / "bin"
