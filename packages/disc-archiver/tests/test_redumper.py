"""Downloading redumper when it is missing, and falling back when it cannot be downloaded.

A stand-in release zip is served by a fake fetch; nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dinorefurb_disc_archiver import backends, redumper, tools
from dinorefurb_disc_archiver.cli import EXIT_OK, main
from dinorefurb_disc_archiver.disc import DiscError

KEY = redumper.platform_key()
PIN = redumper.load_pin()
PROGRAM = "redumper.exe" if KEY.startswith("windows") else "redumper"


def release_zip(members: dict[str, bytes] | None = None) -> bytes:
    """A zip shaped like a redumper release: one top-level folder holding bin/ and lib/."""
    top = f"redumper-{PIN.tag}-{KEY}"
    members = members if members is not None else {f"{top}/bin/{PROGRAM}": b"#!/bin/sh\n", f"{top}/lib/libc++.1.dylib": b"lib"}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return out.getvalue()


class FakeGitHub:
    """Serves the release listing and the asset; records what was fetched."""

    def __init__(self, archive: bytes, digest: str | None = "auto") -> None:
        self.archive = archive
        self.digest = hashlib.sha256(archive).hexdigest() if digest == "auto" else digest
        self.fetched: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.fetched.append(url)
        if url == PIN.url(PIN.release_api_url):
            asset = {"name": PIN.asset_name(KEY)}
            if self.digest:
                asset["digest"] = f"sha256:{self.digest}"
            return json.dumps({"assets": [asset]}).encode()
        if url == PIN.url(PIN.download_url, KEY):
            return self.archive
        raise DiscError(f"unexpected URL {url}")


def offline(url: str) -> bytes:
    raise DiscError(f"offline: {url}")


def quiet(_: str) -> None:
    pass


class RedumperDownloadTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.environment = mock.patch.dict(os.environ, {"DISC_ARCHIVER_HOME": str(self.dir / "home"), "PATH": ""})
        self.environment.start()
        for name in tools.TOOLS:
            os.environ.pop(tools.TOOLS[name].variable, None)
        self.lines: list[str] = []

    def tearDown(self) -> None:
        self.environment.stop()
        self._temp.cleanup()

    def log(self, line: str) -> None:
        self.lines.append(line)


@unittest.skipUnless(redumper.can_download(), f"redumper publishes no build for {KEY}")
class DownloadTests(RedumperDownloadTestCase):
    def test_a_download_checked_against_githubs_digest_is_installed_and_found(self) -> None:
        github = FakeGitHub(release_zip())
        bin_dir = redumper.download(self.log, github)
        self.assertEqual(bin_dir, redumper.install_dir() / "bin")
        self.assertTrue((redumper.install_dir() / "lib" / "libc++.1.dylib").is_file())
        marker = json.loads((redumper.install_dir() / redumper.MARKER).read_text())
        self.assertEqual(marker["sha256"], github.digest)
        self.assertEqual(tools.find_tool("redumper"), bin_dir / PROGRAM)
        self.assertIn(f"{PIN.asset_name(KEY)}: SHA-256 {github.digest} checked", self.lines)

    def test_a_pinned_hash_is_used_instead_of_githubs(self) -> None:
        archive = release_zip()
        pin = redumper.Pin(**{**PIN.__dict__, "sha256": {KEY: hashlib.sha256(archive).hexdigest()}})
        github = FakeGitHub(archive, digest=None)
        redumper.download(quiet, github, pin)
        self.assertEqual(github.fetched, [PIN.url(PIN.download_url, KEY)])

    def test_a_download_with_the_wrong_hash_is_not_used(self) -> None:
        github = FakeGitHub(release_zip(), digest="0" * 64)
        with self.assertRaisesRegex(DiscError, "was expected; it was not used"):
            redumper.download(quiet, github)
        self.assertIsNone(tools.find_tool("redumper"))
        self.assertFalse(redumper.install_dir().exists())

    def test_no_hash_anywhere_means_no_download(self) -> None:
        github = FakeGitHub(release_zip(), digest=None)
        with self.assertRaisesRegex(DiscError, "publishes no SHA-256"):
            redumper.download(quiet, github)
        self.assertNotIn(PIN.url(PIN.download_url, KEY), github.fetched)

    def test_unsafe_or_incomplete_zips_are_refused_and_leave_nothing(self) -> None:
        for members, message in (
            ({"top/../../evil": b"x"}, "unsafe path"),
            ({"top/lib/only": b"x"}, "no bin/redumper"),
        ):
            with self.subTest(message), self.assertRaisesRegex(DiscError, message):
                redumper.download(quiet, FakeGitHub(release_zip(members)))
            self.assertFalse(redumper.install_dir().exists())
            self.assertEqual(list((self.dir / "home" / "redumper").glob(".redumper-*")), [])
        with self.assertRaisesRegex(DiscError, "not a zip"):
            redumper.download(quiet, FakeGitHub(b"not a zip"))

    def test_choosing_redumper_downloads_it_first(self) -> None:
        chosen = backends.choose_backend("auto", quiet, get=FakeGitHub(release_zip()))
        self.assertEqual(chosen.id, "redumper")

    def test_the_command_installs_it(self) -> None:
        out = io.StringIO()
        with mock.patch.object(redumper, "fetch", FakeGitHub(release_zip())), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["install-redumper"]), EXIT_OK)
            self.assertEqual(main(["install-redumper"]), EXIT_OK)
        self.assertIn("redumper is already at", out.getvalue())


class FallbackTests(RedumperDownloadTestCase):
    def cdrdao(self) -> None:
        for name in ("cdrdao", "toc2cue"):
            path = self.dir / name
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
            os.environ[tools.TOOLS[name].variable] = str(path)

    def test_a_failed_download_falls_back_to_cdrdao(self) -> None:
        self.cdrdao()
        chosen = backends.choose_backend("auto", self.log, get=offline)
        self.assertEqual(chosen.id, "cdrdao")
        self.assertTrue(any("falling back" in line for line in self.lines), self.lines)

    def test_without_cdrdao_the_data_track_copy_says_audio_is_lost(self) -> None:
        chosen = backends.choose_backend("auto", self.log, get=offline)
        self.assertEqual(chosen.id, "data-copy")
        self.assertTrue(any("Audio tracks will not be copied" in line for line in self.lines))

    def test_a_declined_download_is_not_attempted(self) -> None:
        self.cdrdao()
        chosen = backends.choose_backend("auto", self.log, allow_download=False, get=self.fail_fetch)
        self.assertEqual(chosen.id, "cdrdao")
        with self.assertRaisesRegex(DiscError, "downloading it was declined"):
            backends.choose_backend("redumper", quiet, allow_download=False, get=self.fail_fetch)

    def test_an_explicit_redumper_fails_when_it_cannot_be_downloaded(self) -> None:
        if not redumper.can_download():
            self.skipTest("no build for this platform")
        with self.assertRaisesRegex(DiscError, "could not be downloaded: offline"):
            backends.choose_backend("redumper", quiet, get=offline)

    def test_a_platform_redumper_does_not_build_for(self) -> None:
        with mock.patch.object(redumper, "platform_key", return_value="plan9-mips"):
            self.assertFalse(redumper.can_download())
            with self.assertRaisesRegex(DiscError, "no build for plan9-mips"):
                backends.choose_backend("redumper", quiet, get=self.fail_fetch)

    def test_other_backends_never_download(self) -> None:
        self.assertEqual(backends.choose_backend("data-copy", quiet, get=self.fail_fetch).id, "data-copy")

    def test_only_https_is_fetched(self) -> None:
        with self.assertRaisesRegex(DiscError, "not HTTPS"):
            redumper.fetch("http://example.com/redumper.zip")

    def fail_fetch(self, url: str) -> bytes:
        self.fail(f"fetched {url}")


class PinTests(unittest.TestCase):
    def test_the_shipped_pin_names_every_platform_redumper_builds(self) -> None:
        self.assertEqual(
            set(PIN.sha256),
            {f"{s}-{a}" for s in ("windows", "linux") for a in ("x64", "x86", "arm64")} | {"macos-x64", "macos-arm64"},
        )
        self.assertEqual(PIN.asset_name("windows-x64"), f"redumper-{PIN.tag}-windows-x64.zip")
        self.assertTrue(PIN.url(PIN.download_url, "linux-x64").startswith("https://github.com/superg/redumper/releases/download/"))

    def test_platform_names(self) -> None:
        for system, machine, key in (("win32", "AMD64", "windows-x64"), ("linux", "aarch64", "linux-arm64"), ("darwin", "arm64", "macos-arm64")):
            with mock.patch.object(redumper.sys, "platform", system), mock.patch.object(redumper.platform, "machine", return_value=machine):
                self.assertEqual(redumper.platform_key(), key)


if __name__ == "__main__":
    unittest.main()
