"""Upload one golden-reference disc folder to the golden-reference-disks R2 bucket.

    python r2_disc.py gate   "<disc folder>" [--dat <Redump DAT>]
    python r2_disc.py upload "<disc folder>" [--dat <Redump DAT>] [--dry-run]
    python r2_disc.py verify "<disc folder>" [--with-bincue]

`upload` always sends rip-manifest.json, PERSONAL-ARCHIVE-ONLY.txt and archival/ (without
redumper's split .bin/.cue copies). bincue-split/ goes up only when `disc-archiver check --dat`
gives `matched` for it, which means every track file and the cue sheet equal one Redump entry
byte for byte. Anything else (partial, notListed, mismatched) skips bincue-split/ and exits 4 so
the caller asks for manual validation. `--bincue-validated-by-hand` uploads it anyway, and is for
when the user has validated the image and said so.

Files up to 300 MB go through `cf r2 buckets objects upload`. Bigger ones go through R2's S3 API with
`aws s3 cp`, using an account token minted for the run with a 4 hour expiry and deleted when the
run ends. The token value is never printed. Objects whose remote etag already equals the local
file's are skipped, so a failed run can be resumed by running it again.

Settings come from the environment: R2_ACCOUNT_ID (default: the only account `cf accounts list`
shows), GOLDEN_DISCS_BUCKET (default golden-reference-disks), DISC_ARCHIVER (default:
disc-archiver on PATH) and REDUMP_DAT (the default for --dat).

Exit codes: 0 done, 1 failed, 4 done but bincue-split/ held back for manual validation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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


def find_cue(disc: Path) -> Path:
    cues = sorted((disc / "bincue-split").glob("*.cue"))
    if len(cues) != 1:
        raise SystemExit(f"expected one .cue in {disc / 'bincue-split'}, found {len(cues)}")
    return cues[0]


def gate(disc: Path, dat: Path | None) -> dict:
    """Compare bincue-split/ with the Redump DAT now, from the files on disk."""
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
    exact = (
        report["status"] == "matched"
        and all(f["matched"] for f in report["files"])
        and on_disk == compared
    )
    return {"exact": exact, "status": report["status"], "game": report.get("game"),
            "differences": report.get("differences", []), "candidates": report.get("candidates", []),
            "unlisted": sorted(on_disk - compared)}


def print_gate(result: dict) -> None:
    log(f"redump: {result['status']}" + (f" ({result['game']})" if result["game"] else ""))
    for d in result["differences"]:
        log(f"  {d}")
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
