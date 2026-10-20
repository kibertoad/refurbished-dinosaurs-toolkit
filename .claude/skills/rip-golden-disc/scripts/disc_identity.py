"""Collect evidence of a disc's title, publisher, year and disc number.

Reads the mounted file system and the ISO 9660 volume descriptor and prints
JSON. It decides nothing; the skill weighs the evidence.

    python -I disc_identity.py D:
    python -I disc_identity.py /dev/sr0 --root /media/cdrom
"""

import argparse
import collections
import datetime
import json
import os
import re
import sys

MAX_SCAN_BYTES = 8 * 1024 * 1024
TEXT_EXTS = {".txt", ".nfo", ".diz", ".wri", ".1st", ".me", ".doc"}
INI_EXTS = {".ini", ".inf", ".tag", ".id", ".lid"}
EXE_EXTS = {".exe", ".dll"}
YEAR = r"(19[7-9]\d|20[0-4]\d)"
COPYRIGHT_RE = re.compile(r"(?i)(?:copyright|\(c\)|\xa9)[^\r\n]{0,80}?" + YEAR + r"(?:\s*[-,]\s*" + YEAR + r")?[^\r\n]{0,60}")
DISC_NO_RE = re.compile(r"(?i)(?:disc|disk|cd)[ _-]?(\d)\b")
VERSION_KEYS = ("ProductName", "FileDescription", "CompanyName", "LegalCopyright", "ProductVersion")
REDIST_DIRS = {"directx", "directx5", "directx6", "directx7", "dx", "dx5", "dx6", "dx7", "dx8", "dx9", "redist", "msvcrt", "vfw", "wmp", "quicktime", "qt", "indeo", "acrobat"}
REDIST_COMPANY = re.compile(r"(?i)microsoft|installshield|apple|intel|adobe|wise solutions|realnetworks")
PRINTABLE = re.compile(r"[^\x20-\x7e\xa9]+")
INI_KEYS = re.compile(r"(?im)^\s*(AppName|Application|Title|Product|ProductName|Company|Publisher|Version|label|open|icon)\s*=\s*(.+?)\s*$")


def device_path(device):
    if os.name == "nt":
        letter = device.rstrip(":\\/")
        return "\\\\.\\" + letter + ":"
    return device


def root_path(device, root):
    if root:
        return root
    if os.name == "nt":
        return device.rstrip(":\\/") + ":\\"
    sys.exit("give --root: the mount point of " + device)


def pvd(device):
    def text(b):
        return b.decode("ascii", "replace").strip() or None

    def date(b):
        s = b[:14].decode("ascii", "replace")
        if not s.strip("0 ") or not s.isdigit():
            return None
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"

    try:
        with open(device_path(device), "rb", buffering=0) as f:
            f.seek(16 * 2048)
            b = f.read(2048)
    except OSError as e:
        return {"error": str(e)}
    if b[1:6] != b"CD001":
        return {"error": "no ISO 9660 primary volume descriptor at sector 16"}
    return {
        "volumeId": text(b[40:72]),
        "volumeSetId": text(b[190:318]),
        "publisher": text(b[318:446]),
        "preparer": text(b[446:574]),
        "application": text(b[574:702]),
        "created": date(b[813:830]),
        "modified": date(b[830:847]),
    }


def walk(root, max_depth=3):
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in REDIST_DIRS]
        depth = os.path.relpath(dirpath, root).count(os.sep) + (dirpath != root)
        if depth >= max_depth:
            dirnames[:] = []
        for name in filenames:
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, root), depth


def read_head(path, limit=MAX_SCAN_BYTES):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return b""


def version_strings(data):
    """StringFileInfo values from a PE's version resource, found by pattern."""
    out = {}
    for key in VERSION_KEYS:
        pat = re.escape(key.encode("utf-16le")) + rb"(?:\x00\x00)+((?:[^\x00]\x00){2,120}?)\x00\x00"
        m = re.search(pat, data)
        if m:
            out[key] = PRINTABLE.sub(" ", m.group(1).decode("utf-16le", "replace")).strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="drive letter (D:) or device path (/dev/sr0)")
    ap.add_argument("--root", help="mount point, when it isn't the drive letter")
    args = ap.parse_args()
    root = root_path(args.device, args.root)

    result = {"pvd": pvd(args.device), "rootListing": [], "ini": {}, "versionInfo": {},
              "readmes": {}, "copyrights": [], "discNumberHints": [], "fileDates": {}}
    try:
        result["rootListing"] = sorted(os.listdir(root))
    except OSError as e:
        result["rootListing"] = {"error": str(e)}

    years = collections.Counter()
    newest = None
    copyrights = collections.Counter()
    for full, rel, depth in walk(root):
        name = os.path.basename(rel)
        ext = os.path.splitext(name)[1].lower()
        try:
            mtime = datetime.datetime.fromtimestamp(os.stat(full).st_mtime)
            years[mtime.year] += 1
            if newest is None or mtime > newest[0]:
                newest = (mtime, rel)
        except (OSError, ValueError, OverflowError):
            pass
        if DISC_NO_RE.search(name):
            result["discNumberHints"].append(rel)

        if ext in INI_EXTS and depth <= 2:
            data = read_head(full, 256 * 1024).decode("latin-1")
            pairs = INI_KEYS.findall(data)
            if pairs:
                result["ini"][rel] = [f"{k}={v}" for k, v in pairs[:20]]
            elif len(data) < 200 and data.strip():
                result["ini"][rel] = [data.strip()]
        elif ext in EXE_EXTS and depth <= 2:
            data = read_head(full)
            info = version_strings(data)
            if not info or REDIST_COMPANY.search(info.get("CompanyName", "")):
                continue
            result["versionInfo"][rel] = info
            for m in COPYRIGHT_RE.finditer(PRINTABLE.sub(" ", data.decode("latin-1"))):
                copyrights[m.group(0).strip()] += 1
        elif (ext in TEXT_EXTS or name.lower().startswith("read")) and depth <= 2:
            text = read_head(full, 512 * 1024).decode("latin-1").replace("	", " ")
            lines = [PRINTABLE.sub(" ", l).strip() for l in text.splitlines()]
            lines = [l for l in lines if l]
            result["readmes"][rel] = lines[:12]
            for m in COPYRIGHT_RE.finditer(text):
                copyrights[m.group(0).strip()] += 1

    result["copyrights"] = [c for c, _ in copyrights.most_common(25)]
    result["fileDates"] = {
        "filesPerYear": dict(sorted(years.items())),
        "newest": f"{newest[0]:%Y-%m-%d} {newest[1]}" if newest else None,
    }
    vol = (result["pvd"] or {}).get("volumeId") or ""
    if DISC_NO_RE.search(vol) or re.search(r"(?i)_?(\d)$", vol):
        result["discNumberHints"].insert(0, "volumeId " + vol)
    json.dump(result, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
