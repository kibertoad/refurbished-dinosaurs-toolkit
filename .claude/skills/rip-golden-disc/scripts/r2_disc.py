"""Upload one golden-reference disc folder to the golden-reference-disks R2 bucket.

    python r2_disc.py gate   "<disc folder>" [--dat <Redump DAT>]
    python r2_disc.py upload "<disc folder>" [--dat <Redump DAT>] [--dry-run]
    python r2_disc.py verify "<disc folder>" [--with-bincue]

`upload` always sends rip-manifest.json, PERSONAL-ARCHIVE-ONLY.txt and archival/ (without
redumper's split .bin/.cue copies). bincue-split/ goes up only when `disc-archiver check --dat`
gives `matched` for it, which means every track file and the cue sheet equal one Redump entry
byte for byte, or `partial` with every track file matching and a cue sheet that equals the
entry's once its FILE names, line endings and an all-zero CATALOG line are Redump's. Anything else
(another partial, notListed, mismatched) skips bincue-split/ and exits 4 so the caller asks for
manual validation. `--bincue-validated-by-hand` uploads it anyway, and is for
when the user has validated the image and said so.

A rip of a recordable disc (CD-R/RW) is never uploaded: `gate` and `upload` refuse it with exit 5,
and nothing overrides that. The disc counts as recordable when redumper saved an ATIP for it
(archival/*.atip; pressed discs have none) or the manifest records it under
source.handFilled.recordableMedia.

Files up to 300 MB go through `cf r2 buckets objects upload`. Bigger ones go through R2's S3 API with
`aws s3 cp`, using an account token minted for the run with a 4 hour expiry and deleted when the
run ends. The token value is never printed. Objects whose remote etag already equals the local
file's are skipped, so a failed run can be resumed by running it again.

Settings come from the environment: R2_ACCOUNT_ID (default: the only account `cf accounts list`
shows), GOLDEN_DISCS_BUCKET (default golden-reference-disks), DISC_ARCHIVER (default:
disc-archiver on PATH) and REDUMP_DAT (the default for --dat).

Exit codes: 0 done, 1 failed, 4 done but bincue-split/ held back for manual validation, 5 the
source disc is a CD-R/RW and nothing was uploaded.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

BUCKET = os.environ.get("GOLDEN_DISCS_BUCKET", "golden-reference-disks")
DISC_ARCHIVER = os.environ.get("DISC_ARCHIVER") or shutil.which("disc-archiver") or "disc-archiver"
DEFAULT_DAT = os.environ.get("REDUMP_DAT")
PUT_CAP = 300 * 1000 * 1000  # cf's objects upload answers 413 above about 300 MB
PART = 8 * 1024 * 1024  # aws s3 cp's default multipart chunk
# Permission group ids (cf accounts tokens permission-groups list).
R2_STORAGE_WRITE = "bf7481a1826f439697cb59a20b22293e"  # account scoped
R2_BUCKET_ITEM_READ = "6a018a9f2fc74eb6b293b0c548f38b39"  # bucket scoped
R2_BUCKET_ITEM_WRITE = "2efd5506f9c8494dacb1fa10a3e7d5b6"  # bucket scoped
EXIT_MANUAL = 4
EXIT_RECORDABLE = 5


def log(line: str) -> None:
    print(line, flush=True)


def cf(*args: str, capture: bool = True) -> str:
    # cf drops a .cloudflare/cache folder in its working directory; keep it out of disc folders.
    with tempfile.TemporaryDirectory() as cwd:
        # cf is a .cmd shim on Windows, which CreateProcess does not find by bare name.
        done = subprocess.run([shutil.which("cf") or "cf", *args], cwd=cwd, capture_output=capture, text=True, encoding="utf-8")
    if done.returncode != 0:
        raise SystemExit(f"cf {args[0]} {args[1] if len(args) > 1 else ''} failed: {(done.stderr or '').strip()[:2000]}")
    return done.stdout or ""


def account_id() -> str:
    """R2_ACCOUNT_ID, or the only account the cf login can see."""
    if found := os.environ.get("R2_ACCOUNT_ID"):
        return found
    accounts = json.loads(cf("accounts", "list"))
    if len(accounts) != 1:
        raise SystemExit("set R2_ACCOUNT_ID; the cf login sees accounts: " + ", ".join(a["name"] for a in accounts))
    return accounts[0]["id"]


def content_type(path: Path) -> str:
    if path.name == "rip-manifest.json":
        return "application/json"
    if path.suffix.lower() == ".txt":
        return "text/plain"
    return "application/octet-stream"


def local_etag(path: Path, multipart: bool) -> str:
    """The etag R2 gives the file: plain MD5 for a single put, or aws's multipart form (MD5 of
    the 8 MiB parts' MD5s, then -<parts>)."""
    with path.open("rb") as f:
        if not multipart:
            h = hashlib.md5()
            while chunk := f.read(1 << 20):
                h.update(chunk)
            return h.hexdigest()
        parts = []
        while chunk := f.read(PART):
            parts.append(hashlib.md5(chunk).digest())
    return f"{hashlib.md5(b''.join(parts)).hexdigest()}-{len(parts)}"


def same(path: Path, remote: dict) -> tuple[bool, str]:
    """Whether the remote object holds the file's bytes, and the local etag compared."""
    want = local_etag(path, "-" in remote["etag"])
    return remote["size"] == path.stat().st_size and remote["etag"] == want, want


def disc_files(disc: Path, with_bincue: bool) -> list[Path]:
    files = [disc / "rip-manifest.json", disc / "PERSONAL-ARCHIVE-ONLY.txt"]
    missing = [f for f in files if not f.is_file()]
    archival = disc / "archival"
    if not archival.is_dir():
        missing.append(archival)
    if missing:
        raise SystemExit("missing: " + ", ".join(str(m) for m in missing))
    # redumper split writes the track .bin files and a .cue next to the raw dump; they duplicate
    # bincue-split/ and are never uploaded.
    files += sorted(p for p in archival.rglob("*") if p.is_file() and p.suffix.lower() not in (".bin", ".cue"))
    if with_bincue:
        files += sorted(p for p in (disc / "bincue-split").rglob("*") if p.is_file())
    return files


def key_of(disc: Path, path: Path) -> str:
    return f"{disc.name}/{path.relative_to(disc).as_posix()}"


def remote_objects(prefix: str) -> dict[str, dict]:
    out = json.loads(cf("r2", "buckets", "objects", "list", "--bucket-name", BUCKET, "--prefix", prefix, "--per-page", "1000") or "[]")
    if isinstance(out, dict):
        out = out.get("result", out.get("objects", []))
    return {o["key"]: o for o in out}


def recordable(disc: Path) -> str | None:
    """Why the rip is of a CD-R/RW, or None for a pressed disc.

    redumper saves <name>.atip only when the drive returns an ATIP, which only recordable discs
    carry. The descriptor has the top bit of its bytes 0 and 2 set (MMC READ TOC format 0100b).
    """
    for atip in sorted((disc / "archival").glob("*.atip")):
        data = atip.read_bytes()
        length = int.from_bytes(data[:2], "big") if len(data) >= 2 else 0
        d = data[4:2 + length]
        if len(d) >= 11 and d[0] & 0x80 and d[2] & 0x80:
            kind = "CD-RW" if d[2] & 0x40 else "CD-R"
            return f"archival/{atip.name} holds the disc's ATIP ({kind}, lead-in {d[4]:02d}:{d[5]:02d}:{d[6]:02d})"
    manifest = json.loads((disc / "rip-manifest.json").read_text(encoding="utf-8"))
    marked = manifest.get("source", {}).get("handFilled", {}).get("recordableMedia", {})
    if marked.get("recordable"):
        return "rip-manifest.json records it under source.handFilled.recordableMedia"
    return None


def find_cue(disc: Path) -> Path:
    cues = sorted((disc / "bincue-split").glob("*.cue"))
    if len(cues) != 1:
        raise SystemExit(f"expected one .cue in {disc / 'bincue-split'}, found {len(cues)}")
    return cues[0]


def dat_cue(dat: Path, game: str) -> dict | None:
    """The cue sheet rom of ``game`` in the DAT (.dat or a zip of one): its size and hashes."""
    if dat.suffix.lower() == ".zip":
        with zipfile.ZipFile(dat) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise SystemExit(f"{dat} holds {len(names)} files, expected one DAT")
            data = z.read(names[0])
    else:
        data = dat.read_bytes()
    for entry in ElementTree.fromstring(data).iter("game"):
        if entry.get("name") != game:
            continue
        for rom in entry.iter("rom"):
            if rom.get("name", "").lower().endswith(".cue"):
                return {"size": int(rom.get("size", -1)), "crc": rom.get("crc", "").lower(),
                        "md5": rom.get("md5", "").lower(), "sha1": rom.get("sha1", "").lower()}
    return None


def cue_variants(sheet: bytes, renames: dict[str, str]) -> list[tuple[str, bytes]]:
    """The cue sheet as Redump would write it, with what was changed to get there.

    The archiver names track files after --name, Redump after its entry, so the FILE lines get the
    DAT's names. Redump writes CRLF. An all-zero CATALOG means the disc has no media catalog number;
    some drives report it as zeros, others not at all, so the line is tried both present and absent.
    """
    changed = []
    if renames:
        theirs = {mine.encode(): new.encode() for mine, new in renames.items()}
        pattern = re.compile(b'FILE "(' + b"|".join(re.escape(mine) for mine in theirs) + b')"')
        sheet = pattern.sub(lambda m: b'FILE "' + theirs[m.group(1)] + b'"', sheet)
        changed.append("FILE names")
    crlf = sheet.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if crlf != sheet:
        changed.append("line endings")
    zero = b"CATALOG 0000000000000\r\n"
    out = [(", ".join(changed), crlf)]
    if crlf.startswith(zero):
        out.append((", ".join(changed + ["no all-zero CATALOG line"]), crlf[len(zero):]))
    elif not re.match(rb"CATALOG ", crlf):
        out.append((", ".join(changed + ["an all-zero CATALOG line"]), zero + crlf))
    return out


def cue_equal_after(cue: Path, report: dict, dat: Path) -> str | None:
    """When every track matched and only the cue sheet did not: what the cue differs from the
    DAT's in, if it is only FILE names, line endings and an all-zero CATALOG; otherwise None."""
    tracks = [f for f in report["files"] if f["kind"] == "track"]
    if not report.get("game") or not tracks or not all(f["matched"] for f in tracks):
        return None
    wanted = dat_cue(dat, report["game"])
    if wanted is None:
        return None
    renames = {Path(d["path"]).name: d["datName"] for d in report.get("nameDifferences", [])
               if d.get("datName") and Path(d["path"]).suffix.lower() != ".cue"}
    for changed, variant in cue_variants(cue.read_bytes(), renames):
        if (len(variant) == wanted["size"] and f"{zlib.crc32(variant):08x}" == wanted["crc"]
                and hashlib.md5(variant).hexdigest() == wanted["md5"]
                and hashlib.sha1(variant).hexdigest() == wanted["sha1"]):
            return changed or "nothing"
    return None


def gate(disc: Path, dat: Path | None) -> dict:
    """Compare bincue-split/ with the Redump DAT now, from the files on disk.

    ``matched`` passes. So does ``partial`` when every track file matched and the cue sheet equals
    the DAT's once its FILE names, line endings and an all-zero CATALOG line are made Redump's: those
    say nothing about the disc's bytes.
    """
    if dat is None:
        raise SystemExit("no Redump DAT: pass --dat or set REDUMP_DAT")
    if not dat.is_file():
        raise SystemExit(f"Redump DAT not found: {dat}")
    cue = find_cue(disc)
    done = subprocess.run(
        [DISC_ARCHIVER, "check", "--input", str(cue), "--dat", str(dat)],
        capture_output=True, text=True, encoding="utf-8",
    )
    start = done.stdout.find("{")
    if start < 0:
        raise SystemExit(f"disc-archiver check printed no report (exit {done.returncode}):\n{done.stderr[-3000:]}")
    report = json.loads(done.stdout[start:])["redump"]
    on_disk = {p.name for p in cue.parent.iterdir() if p.suffix.lower() in (".bin", ".cue")}
    compared = {Path(f["path"]).name for f in report["files"]}
    cue_only = cue_equal_after(cue, report, dat) if report["status"] == "partial" else None
    exact = (
        (report["status"] == "matched" and all(f["matched"] for f in report["files"]) or cue_only is not None)
        and on_disk == compared
    )
    return {"exact": exact, "status": report["status"], "game": report.get("game"),
            "differences": report.get("differences", []), "candidates": report.get("candidates", []),
            "unlisted": sorted(on_disk - compared), "cueEqualAfter": cue_only}


def print_gate(result: dict) -> None:
    log(f"redump: {result['status']}" + (f" ({result['game']})" if result["game"] else ""))
    for d in result["differences"]:
        log(f"  {d}")
    if result["cueEqualAfter"]:
        log(f"  every track matches; the cue sheet equals the DAT's after Redump's {result['cueEqualAfter']}")
    for c in result["candidates"][:4]:
        log(f"  nearest: {c.get('name', c)}")
    if result["unlisted"]:
        log(f"  bincue-split files the check did not compare: {', '.join(result['unlisted'])}")


class S3Token:
    """A short-lived account token for R2's S3 API, deleted on exit."""

    def __enter__(self) -> dict[str, str]:
        account = account_id()
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        policies = [
            {"effect": "allow", "permission_groups": [{"id": R2_STORAGE_WRITE}],
             "resources": {f"com.cloudflare.api.account.{account}": "*"}},
            {"effect": "allow", "permission_groups": [{"id": R2_BUCKET_ITEM_READ}, {"id": R2_BUCKET_ITEM_WRITE}],
             "resources": {f"com.cloudflare.edge.r2.bucket.{account}_default_{BUCKET}": "*"}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            policy_file = Path(tmp) / "policies.json"
            policy_file.write_text(json.dumps(policies), encoding="utf-8")
            name = f"golden-disc-upload-{dt.datetime.now():%Y%m%d-%H%M%S}"
            out = cf("accounts", "tokens", "create", "-q", "--name", name, "--expires-on", expires,
                     "--policies", f"@{policy_file}")
        token = json.loads(out)
        token = token.get("result", token)
        self.id = token["id"]
        log(f"minted S3 upload token {name} ({self.id}), expires {expires}")
        self.endpoint = f"https://{account}.r2.cloudflarestorage.com"
        env = dict(os.environ)
        env.update(
            AWS_ACCESS_KEY_ID=self.id,
            AWS_SECRET_ACCESS_KEY=hashlib.sha256(token["value"].encode()).hexdigest(),
            AWS_DEFAULT_REGION="auto",
            AWS_REQUEST_CHECKSUM_CALCULATION="WHEN_REQUIRED",
            AWS_RESPONSE_CHECKSUM_VALIDATION="WHEN_REQUIRED",
        )
        for k in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
            env.pop(k, None)
        # A new token answers Unauthorized for its first few seconds (5 to 15 s when this was written).
        deadline = time.monotonic() + 120
        while True:
            probe = subprocess.run([shutil.which("aws") or "aws", "s3", "ls", f"s3://{BUCKET}/", "--endpoint-url", self.endpoint],
                                   env=env, capture_output=True, text=True)
            if probe.returncode == 0:
                return env
            if time.monotonic() > deadline:
                self.__exit__()
                raise SystemExit(f"the minted token is still refused after 2 minutes: {probe.stderr.strip()[:500]}")
            time.sleep(5)

    def __exit__(self, *exc: object) -> None:
        cf("accounts", "tokens", "delete", self.id, "-f")
        log(f"deleted S3 upload token {self.id}")


def upload(disc: Path, files: list[Path], dry_run: bool) -> None:
    remote = remote_objects(f"{disc.name}/")
    todo = []
    for path in files:
        key = key_of(disc, path)
        have = remote.get(key)
        if have and same(path, have)[0]:
            log(f"already there: {key}")
            continue
        todo.append(path)
    small = [p for p in todo if p.stat().st_size <= PUT_CAP]
    big = [p for p in todo if p.stat().st_size > PUT_CAP]
    for path in small:
        key = key_of(disc, path)
        log(f"put {key} ({path.stat().st_size:,} bytes)")
        if not dry_run:
            cf("r2", "buckets", "objects", "upload", key, "--bucket-name", BUCKET, "--file", str(path),
               "--cf-r2-storage-class", "InfrequentAccess", "--content-type", content_type(path), "-q")
    if not big:
        return
    if dry_run:
        for path in big:
            log(f"s3 cp {key_of(disc, path)} ({path.stat().st_size:,} bytes, needs a minted token)")
        return
    aws = shutil.which("aws")
    if aws is None:
        raise SystemExit("aws CLI not found; it is needed for files over 300 MB")
    token = S3Token()
    with token as env:
        for path in big:
            key = key_of(disc, path)
            log(f"s3 cp {key} ({path.stat().st_size:,} bytes)")
            done = subprocess.run(
                [aws, "s3", "cp", str(path), f"s3://{BUCKET}/{key}", "--endpoint-url", token.endpoint,
                 "--storage-class", "STANDARD_IA", "--content-type", content_type(path), "--only-show-errors"],
                env=env,
            )
            if done.returncode != 0:
                raise SystemExit(f"aws s3 cp failed for {key}")


def verify(disc: Path, files: list[Path]) -> bool:
    remote = remote_objects(f"{disc.name}/")
    ok = True
    for path in files:
        key = key_of(disc, path)
        have = remote.get(key)
        if have is None:
            log(f"MISSING   {key}")
            ok = False
            continue
        matches, want = same(path, have)
        good = matches and have.get("storage_class") == "InfrequentAccess"
        ok &= good
        log(f"{'ok' if good else 'DIFFERS':9} {key}" + ("" if good else f"  remote {have['size']} {have['etag']} {have.get('storage_class')}, local {path.stat().st_size} {want}"))
    expected = {key_of(disc, p) for p in files}
    for key in sorted(set(remote) - expected):
        log(f"extra     {key}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("gate", "upload", "verify"))
    parser.add_argument("disc", type=Path, help="the disc folder, named as its bucket folder")
    parser.add_argument("--dat", type=Path, default=Path(DEFAULT_DAT) if DEFAULT_DAT else None,
                        help="the Redump DAT (.dat or .zip); default REDUMP_DAT")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--with-bincue", action="store_true", help="verify: include bincue-split/")
    parser.add_argument("--bincue-validated-by-hand", action="store_true",
                        help="upload bincue-split/ although the DAT gives no exact match; only on the user's say-so")
    args = parser.parse_args()
    disc = args.disc.resolve()

    if args.command in ("gate", "upload") and (why := recordable(disc)):
        log(f"the source disc is recordable media, not a pressed retail disc: {why}")
        log("CD-R/RW rips are kept locally and never go to the golden reference bucket; nothing uploaded")
        return EXIT_RECORDABLE

    if args.command == "gate":
        result = gate(disc, args.dat)
        print_gate(result)
        log("exact Redump match" if result["exact"] else "NOT an exact Redump match: needs manual validation")
        return 0 if result["exact"] else EXIT_MANUAL

    if args.command == "verify":
        return 0 if verify(disc, disc_files(disc, args.with_bincue)) else 1

    result = gate(disc, args.dat)
    print_gate(result)
    with_bincue = result["exact"] or args.bincue_validated_by_hand
    if not result["exact"]:
        log("bincue-split/ is NOT an exact Redump match" + (
            ": uploading it anyway (validated by hand)" if with_bincue else ": not uploading it, needs manual validation"))
    files = disc_files(disc, with_bincue)
    upload(disc, files, args.dry_run)
    if args.dry_run:
        return 0
    log("verifying against the bucket listing")
    if not verify(disc, files):
        return 1
    return 0 if with_bincue else EXIT_MANUAL


if __name__ == "__main__":
    sys.exit(main())
