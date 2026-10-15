"""Downloading redumper when it is missing, and falling back when it cannot be downloaded.

A stand-in release zip is served by a fake fetch; nothing here touches the network.
"""

from __future__ import annotations

import contextlib
import dataclasses
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


def pin_json(sha256: object) -> str:
    """The shipped pin as JSON text, with this platform's SHA-256 replaced."""
    data = json.loads(redumper.resources.files(redumper.__package__).joinpath("redumper.json").read_text(encoding="utf-8"))
    data["sha256"][KEY] = sha256
    return json.dumps(data)


class FakeGitHub:
    """Serves the release asset; records what was fetched."""

    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.digest = hashlib.sha256(archive).hexdigest()
        self.fetched: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.fetched.append(url)
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
    """The shipped pin's tag and URLs, with this platform's hash set to the stand-in zip served."""

    def serve(self, archive: bytes) -> FakeGitHub:
        pin = dataclasses.replace(PIN, sha256={**PIN.sha256, KEY: hashlib.sha256(archive).hexdigest()})
        pinned = mock.patch.object(redumper, "load_pin", return_value=pin)
        pinned.start()
        self.addCleanup(pinned.stop)
        return FakeGitHub(archive)

    def test_a_download_matching_the_pin_is_installed_and_found(self) -> None:
        github = self.serve(release_zip())
        bin_dir = redumper.download(self.log, github)
        self.assertEqual(bin_dir, redumper.install_dir() / "bin")
        self.assertTrue((redumper.install_dir() / "lib" / "libc++.1.dylib").is_file())
        marker = json.loads((redumper.install_dir() / redumper.MARKER).read_text())
        self.assertEqual(marker["sha256"], github.digest)
        self.assertEqual(tools.find_tool("redumper"), bin_dir / PROGRAM)
        self.assertIn(f"{PIN.asset_name(KEY)}: SHA-256 {github.digest} checked", self.lines)
        self.assertEqual(github.fetched, [PIN.url(PIN.download_url, KEY)])

    def test_the_shipped_pin_refuses_a_zip_with_another_hash(self) -> None:
        github = FakeGitHub(release_zip())
        with self.assertRaisesRegex(DiscError, f"but {PIN.sha256[KEY]} was expected; it was not used"):
            redumper.download(quiet, github)
        self.assertEqual(github.fetched, [PIN.url(PIN.download_url, KEY)])
        self.assertIsNone(tools.find_tool("redumper"))
        self.assertFalse(redumper.install_dir().exists())

    def test_a_new_tag_removes_the_downloads_of_earlier_tags(self) -> None:
        home = self.dir / "home" / "redumper"
        (home / "b1").mkdir(parents=True)
        (home / "b1" / redumper.MARKER).write_text("{}")
        (home / "kept").mkdir()
        redumper.download(self.log, self.serve(release_zip()))
        self.assertEqual(sorted(p.name for p in home.iterdir()), sorted(["kept", PIN.tag]))
        self.assertIn(f"Removed redumper b1 from {home / 'b1'}", self.lines)

    def test_a_download_to_another_folder_leaves_the_data_folder_alone(self) -> None:
        earlier = self.dir / "home" / "redumper" / "b1"
        earlier.mkdir(parents=True)
        (earlier / redumper.MARKER).write_text("{}")
        redumper.download(quiet, self.serve(release_zip()), target=self.dir / "bundle")
        self.assertTrue((earlier / redumper.MARKER).is_file())

    def test_unsafe_or_incomplete_zips_are_refused_and_leave_nothing(self) -> None:
        for members, message in (
            ({"top/../../evil": b"x"}, "unsafe path"),
            ({"top/lib/only": b"x"}, "no bin/redumper"),
        ):
            with self.subTest(message), self.assertRaisesRegex(DiscError, message):
                redumper.download(quiet, self.serve(release_zip(members)))
            self.assertFalse(redumper.install_dir().exists())
            self.assertEqual(list((self.dir / "home" / "redumper").glob(".redumper-*")), [])
        with self.assertRaisesRegex(DiscError, "not a zip"):
            redumper.download(quiet, self.serve(b"not a zip"))

    def test_choosing_redumper_downloads_it_first(self) -> None:
        chosen = backends.choose_backend("auto", quiet, get=self.serve(release_zip()))
        self.assertEqual(chosen.id, "redumper")

    def test_the_command_installs_it(self) -> None:
        out = io.StringIO()
        with mock.patch.object(redumper, "fetch", self.serve(release_zip())), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
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

    def test_the_shipped_pin_holds_a_sha256_for_every_platform(self) -> None:
        for key, digest in PIN.sha256.items():
            with self.subTest(key):
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_a_pin_without_a_valid_sha256_does_not_load(self) -> None:
        for digest in (None, "", "0" * 63, "g" * 64):
            with self.subTest(digest), self.assertRaisesRegex(DiscError, f"no SHA-256 for {KEY}"):
                redumper.load_pin(pin_json(digest))
        self.assertEqual(redumper.load_pin(pin_json("AB" * 32)).sha256[KEY], "ab" * 32)

    def test_platform_names(self) -> None:
        for system, machine, key in (("win32", "AMD64", "windows-x64"), ("linux", "aarch64", "linux-arm64"), ("darwin", "arm64", "macos-arm64")):
            with mock.patch.object(redumper.sys, "platform", system), mock.patch.object(redumper.platform, "machine", return_value=machine):
                self.assertEqual(redumper.platform_key(), key)


if __name__ == "__main__":
    unittest.main()
