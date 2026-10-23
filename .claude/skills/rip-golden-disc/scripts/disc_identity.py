"""Collect evidence of a disc's title, publisher, year and disc number, and
whether it is recordable media (CD-R/RW) rather than a pressed disc.

Reads the mounted file system, the ISO 9660 volume descriptor and, on Windows,
the disc's ATIP, and prints JSON. It decides nothing about the name; the skill
weighs the evidence.

    python -I disc_identity.py D:
    python -I disc_identity.py /dev/sr0 --root /media/cdrom
    python -I disc_identity.py --atip-file "<disc>/archival/<name>.atip"
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
IOCTL_CDROM_READ_TOC_EX = 0x24054
TOC_FULL, TOC_ATIP = 2, 4
ERROR_NOT_READY = 21


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
        # Image tools stamp their name here (UltraISO does), which marks a disc built from a
        # rebuilt image rather than the publisher's master.
        "applicationUse": [s.strip() for s in re.findall(r"[\x20-\x7e]{4,}", b[883:1395].decode("latin-1")) if len(s.strip()) >= 4],
    }


def decode_atip(data):
    """The ATIP descriptor of a READ TOC format 0100b answer (MMC-3), or None when it holds none.

    Only recordable discs have an ATIP: the drive reads it from the pregroove a blank is made
    with. A pressed disc has no pregroove, so the drive refuses the read or answers empty.
    """
    if len(data) < 4:
        return None
    length = int.from_bytes(data[0:2], "big")  # counts the bytes after these two
    d = data[4:2 + length]
    if len(d) < 11 or not d[0] & 0x80 or not d[2] & 0x80:  # MMC sets the top bit of both
        return None

    def msf(b):
        return f"{b[0]:02d}:{b[1]:02d}:{b[2]:02d}"

    return {
        "discType": "CD-RW" if d[2] & 0x40 else "CD-R",
        "discSubType": (d[2] >> 3) & 7,
        "leadInStart": msf(d[4:7]),
        "lastLeadOutStart": msf(d[8:11]),
        "raw": data[:2 + length].hex(),
    }


def read_toc_ex(device, toc_format):
    """IOCTL_CDROM_READ_TOC_EX: the answer's bytes, or None and the Windows error."""
    import ctypes
    from ctypes import wintypes as w

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = w.HANDLE
    k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    handle = k.CreateFileW(device_path(device), 0x80000000, 3, None, 3, 0, None)  # GENERIC_READ, share read/write, OPEN_EXISTING
    if handle in (None, w.HANDLE(-1).value):
        return None, ctypes.get_last_error()
    try:
        request = (ctypes.c_ubyte * 4)(toc_format | 0x80, 0, 0, 0)  # Format in the low bits, Msf set
        out = (ctypes.c_ubyte * 2048)()
        got = w.DWORD()
        if not k.DeviceIoControl(w.HANDLE(handle), IOCTL_CDROM_READ_TOC_EX, request, 4, out, 2048, ctypes.byref(got), None):
            return None, ctypes.get_last_error()
        return bytes(out[:got.value]), 0
    finally:
        k.CloseHandle(w.HANDLE(handle))


def media(device):
    """Whether the disc in the drive is recordable, from its ATIP."""
    if os.name != "nt":
        return {"recordable": None,
                "note": "ATIP is read on Windows only; after the rip, run --atip-file on archival/<name>.atip (redumper writes it only when the disc has one)"}
    data, error = read_toc_ex(device, TOC_ATIP)
    atip = decode_atip(data) if data else None
    if atip:
        return {"recordable": True, "atip": atip}
    # A refused ATIP read means a pressed disc only when the same path can read the disc at all.
    toc, toc_error = read_toc_ex(device, TOC_FULL)
    if not toc:
        return {"recordable": None,
                "note": f"the drive answered neither ATIP (error {error}) nor the full TOC (error {toc_error})"
                        + (": no disc, or not ready" if ERROR_NOT_READY in (error, toc_error) else "")}
    return {"recordable": False, "note": f"the drive reads the full TOC but has no ATIP for the disc (error {error}), as on a pressed disc"}


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
    ap.add_argument("device", nargs="?", help="drive letter (D:) or device path (/dev/sr0)")
    ap.add_argument("--root", help="mount point, when it isn't the drive letter")
    ap.add_argument("--atip-file", help="only decode a saved ATIP (redumper's archival/<name>.atip)")
    args = ap.parse_args()
    if args.atip_file:
        with open(args.atip_file, "rb") as f:
            atip = decode_atip(f.read())
        json.dump({"media": {"recordable": atip is not None, "atip": atip}}, sys.stdout, indent=1)
        print()
        return
    if not args.device:
        ap.error("give the drive, or --atip-file")
    root = root_path(args.device, args.root)

    result = {"media": media(args.device), "pvd": pvd(args.device), "rootListing": [], "ini": {}, "versionInfo": {},
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
