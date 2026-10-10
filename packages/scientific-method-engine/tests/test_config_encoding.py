"""Synthetic machine code and synthetic configs only. No original binaries or analysis artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
import xxhash

ENGINE = [sys.executable, "-B", "-m", "scientific_method_engine"]
ENGINE_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}

# One resident segment at 1000h: 0000 calls 0004, 0003 returns, 0004 returns.
CODE = bytes.fromhex("e80100" "c3" "c3")


class ConfigEncodingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "fixture.bin").write_bytes(CODE)
        # The evidence text holds a non-ASCII letter, so the config's Latin-1 bytes are not UTF-8.
        self.text = json.dumps({
            "source": "fixture.bin", "sourceKind": "synthetic-raw", "xxh3": xxhash.xxh3_128_hexdigest(CODE),
            "regions": [{"name": "resident", "start": 0, "end": len(CODE), "ip": 0, "segment": 0x1000,
                         "resident": True, "entries": [0], "evidence": "synthetic code extent, côte"}],
            "target": 4}, ensure_ascii=False)

    def run_config(self, data):
        path = self.root / "config.json"
        path.write_bytes(data)
        return subprocess.run([*ENGINE, "incoming", str(path)], capture_output=True, text=True, encoding="utf-8",
                              env=ENGINE_ENV)

    def test_a_config_reads_the_same_with_and_without_a_utf8_byte_order_mark(self):
        plain = self.run_config(self.text.encode("utf-8"))
        self.assertEqual(plain.returncode, 0, plain.stderr)
        marked = self.run_config(b"\xef\xbb\xbf" + self.text.encode("utf-8"))
        self.assertEqual(marked.returncode, 0, marked.stderr)
        self.assertEqual(json.loads(marked.stdout), json.loads(plain.stdout))
        self.assertTrue(json.loads(plain.stdout)["confirmed"])

    def test_a_second_byte_order_mark_is_refused_by_name(self):
        result = self.run_config(b"\xef\xbb\xbf\xef\xbb\xbf" + self.text.encode("utf-8"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("starts with more than one byte order mark", result.stderr)

    def test_a_prepared_config_on_stdin_is_utf8_whatever_the_locale(self):
        # PYTHONIOENCODING stands in for a Windows locale: sys.stdin would decode the reader's UTF-8 as Latin-1.
        folder = self.root / "côte"
        folder.mkdir()
        (folder / "fixture.bin").write_bytes(CODE)
        prepared = {**json.loads(self.text), "source": str(folder / "fixture.bin"), "preparedProtocol": 3}
        result = subprocess.run([*ENGINE, "incoming", "-"], input=json.dumps(prepared, ensure_ascii=False).encode("utf-8"),
                                capture_output=True, env={**ENGINE_ENV, "PYTHONIOENCODING": "latin-1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["confirmed"])
        result = subprocess.run([*ENGINE, "incoming", "-"], input=self.text.encode("latin-1"), capture_output=True,
                                env=ENGINE_ENV)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Prepared config is not valid UTF-8", result.stderr)

    def test_an_error_naming_a_non_ascii_path_is_written_as_utf8_whatever_the_locale(self):
        # The reader decodes the engine's stderr as UTF-8; PYTHONIOENCODING stands in for a Windows locale.
        folder = self.root / "côte"
        folder.mkdir()
        (folder / "config.json").write_bytes(b"\xff\xfe" + self.text.encode("utf-16-le"))
        result = subprocess.run([*ENGINE, "incoming", str(folder / "config.json")], capture_output=True,
                                env={**ENGINE_ENV, "PYTHONIOENCODING": "latin-1"})
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"Config {folder / 'config.json'} is UTF-16 text", result.stderr.decode("utf-8"))

    def test_utf16_utf32_and_invalid_utf8_configs_are_refused_with_the_encoding_named(self):
        for data in (b"\xff\xfe" + self.text.encode("utf-16-le"), b"\xfe\xff" + self.text.encode("utf-16-be")):
            result = self.run_config(data)
            self.assertEqual(result.returncode, 1)
            self.assertIn("is UTF-16 text; save it as UTF-8", result.stderr)
        for data in (b"\xff\xfe\x00\x00" + self.text.encode("utf-32-le"), b"\x00\x00\xfe\xff" + self.text.encode("utf-32-be")):
            result = self.run_config(data)
            self.assertEqual(result.returncode, 1)
            self.assertIn("is UTF-32 text; save it as UTF-8", result.stderr)
        result = self.run_config(self.text.encode("latin-1"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not valid UTF-8; save it as UTF-8", result.stderr)


if __name__ == "__main__":
    unittest.main()
