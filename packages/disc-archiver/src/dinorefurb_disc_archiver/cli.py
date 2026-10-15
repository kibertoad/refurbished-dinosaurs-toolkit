"""The ``disc-archiver`` command."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import backends, drives, redump, redumper, tools
from .disc import DiscError
from .formats import FORMAT_IDS, FORMATS
from .notice import NOTICE
from .pipeline import (
    archive,
    check_against_manifest,
    fingerprint,
    load_manifest,
    manifest_ok,
    open_source,
    package_version,
    profile_paths,
    redump_check,
    reference_backend,
)
from .profile import ALL, BUILTIN_PROFILES, RECOMMENDED, REFERENCE, check_profile, load_profile

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_MISMATCH = 3

EPILOG = f"""\
exit codes: {EXIT_OK} done and verified; {EXIT_FAILED} failed; {EXIT_USAGE} usage error or notice not
accepted; {EXIT_MISMATCH} a profile check failed, a written format differs from its source, a
checked copy is not identified as its manifest's disc, --dat found the copy mismatched with
the DAT's game, or a rip's data errors remain in a file, in too many sectors to read again, or
where the file system could not say.

Copies are for your own archive and your own restoration runtime only. Never share them.
Run "disc-archiver notice" to read the full notice."""


def _log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disc-archiver",
        description="Make personal archival copies of game discs you own, in several image formats.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("notice", help="print the personal-use notice")
    commands.add_parser("formats", help="list the output formats, most complete first")
    commands.add_parser("tools", help="list the backends, the programs they need and the drives found")
    install = commands.add_parser("install-redumper", help="download the pinned redumper release now")
    install.add_argument("--force", action="store_true", help="download it again even if it is already there")

    def dat_option(sub: argparse.ArgumentParser, compared: str) -> None:
        sub.add_argument(
            "--dat",
            type=Path,
            help=f"a Redump DAT you downloaded from redump.org (.dat or its .zip) to compare {compared} with; "
            "never downloaded by the archiver",
        )

    def common(sub: argparse.ArgumentParser) -> None:
        dat_option(sub, "the bincue-split copy, which is then written too,")
        sub.add_argument("--output", required=True, type=Path, help="directory to write the copy into")
        sub.add_argument("--name", help="base file name of the images")
        sub.add_argument(
            "--format",
            action="append",
            dest="formats",
            metavar="FORMAT",
            help=f"a format to write; repeat for several. One of {', '.join(FORMAT_IDS)}, "
            "'recommended' (the profile's choice, the default), 'reference' (bincue-split and files from "
            "redumper's dump, for a golden-reference copy) or 'all'",
        )
        sub.add_argument(
            "--profile",
            default="any",
            help=f"a restoration's disc profile file, or one of {', '.join(BUILTIN_PROFILES)}",
        )
        sub.add_argument(
            "--accept-personal-use",
            action="store_true",
            help="confirm you own the disc and will never share the copy (required)",
        )

    rip = commands.add_parser("rip", help="copy a disc from a drive, then write the chosen formats")
    rip.add_argument("--drive", required=True, help="the drive: E: on Windows, /dev/sr0 on Linux")
    rip.add_argument(
        "--backend",
        default="auto",
        help="auto (the best installed), " + ", ".join(b.id for b in backends.BACKENDS),
    )
    rip.add_argument("--backend-arg", action="append", default=[], help="an extra argument for the backend program")
    rip.add_argument(
        "--no-download",
        action="store_true",
        help="never download redumper; auto falls back to cdrdao, then to the data track copy, "
        "and a missing redumper stops a 'reference' copy",
    )
    rip.add_argument(
        "--read-offset",
        type=int,
        metavar="SAMPLES",
        help="the drive's read offset, as AccurateRip lists it; corrects the audio of a cdrdao dump "
        "(--backend cdrdao only: redumper corrects the offset itself)",
    )
    common(rip)

    convert = commands.add_parser("convert", help="write the chosen formats from a copy you already have")
    convert.add_argument("--input", required=True, type=Path, help="a .cue, .iso, .ccd, .chd, .mds or .nrg file")
    common(convert)

    check = commands.add_parser(
        "check", help="print a copy's fingerprint and check it against a profile, and against a manifest if given"
    )
    check.add_argument("--input", required=True, type=Path, help="a .cue, .iso, .ccd, .chd, .mds or .nrg file")
    check.add_argument("--profile", default="any", help="a disc profile file or a built-in profile")
    check.add_argument(
        "--manifest",
        type=Path,
        help="a rip-manifest.json: the copy's data track must match it exactly, and each audio track "
        "exactly or at a shift within the fingerprint's tolerance",
    )
    dat_option(check, "the cue sheet and its track files")
    return parser


def _formats(requested: list[str] | None) -> list[str] | None:
    """The formats asked for, or None for the profile's recommendation.

    The presets ``all``, ``recommended`` and ``reference`` stay in the list: the recommendation
    depends on the disc, so the pipeline resolves every preset once the disc is read (see
    ``profile.resolve_formats``).
    """
    if not requested:
        return None
    chosen: list[str] = []
    for item in requested:
        if item in FORMAT_IDS or item in (ALL, RECOMMENDED, REFERENCE):
            chosen.append(item)
        else:
            raise DiscError(f"unknown format {item!r}; choose from {', '.join(FORMAT_IDS)}, recommended, reference or all")
    return list(dict.fromkeys(chosen))


def _require_acceptance(arguments: argparse.Namespace) -> bool:
    print(NOTICE, file=sys.stderr)
    if not arguments.accept_personal_use:
        print(
            "Refusing to copy: pass --accept-personal-use to confirm you own this disc and will "
            "never share the copy or anything taken from it.",
            file=sys.stderr,
        )
        return False
    return True


def _report(manifest: dict) -> int:  # type: ignore[type-arg]
    for output in manifest["outputs"]:
        verification = output["verification"]
        print(f"{output['format']}: {verification['status']} -> {output['entry']}")
        for note in output["notes"]:
            print(f"  note: {note}")
        for difference in verification["differences"]:
            print(f"  DIFFERENCE: {difference}")
        for item in verification["notCompared"]:
            print(f"  not compared: {item}")
    for item in manifest["unavailable"]:
        print(f"{item['format']}: not written: {item['reason']}")
    for check in manifest["profile"]["checks"]:
        if not check["matched"]:
            print(f"profile: {check['check']} expected {check['expected']!r}, found {check['found']!r}")
    _report_redump(manifest["redump"])
    _report_data_errors(manifest["source"].get("dataErrors"))
    return EXIT_OK if manifest_ok(manifest) else EXIT_MISMATCH


def _report_data_errors(record: dict | None) -> None:  # type: ignore[type-arg]
    if record is None:
        return
    print(
        f"data errors: {record['outcome']}, {record['sectors']} of {record['sectorsBefore']} bad sectors left"
        f"{' after reading them again' if record['refined'] else ''}"
    )
    for item in record["files"] or []:
        print(f"  in {item['path']}: {item['sectors']} sectors")


def _report_redump(report: dict | None) -> None:  # type: ignore[type-arg]
    if report is None:
        return
    game = f" {report['game']!r}" if report["game"] else ""
    print(f"redump: {report['status']}{game} in {report['dat']}")
    for item in report["nameDifferences"]:
        print(f"  name: {item['path']} is {item['datName']!r} in the DAT")
    for difference in report["differences"]:
        print(f"  {difference}")
    if nearest := redump.candidates_summary(report):
        print(f"  {nearest}")


def _tools() -> int:
    for backend in backends.BACKENDS:
        missing = backend.missing()
        print(f"{backend.id}: {'ready' if not missing else 'needs ' + ', '.join(missing)} - {backend.description}")
    pin = redumper.load_pin()
    if redumper.installed_bin(pin):
        print(f"  redumper {pin.tag} downloaded to {redumper.install_dir(pin)}")
    elif redumper.can_download(pin):
        print(f"  redumper {pin.tag} for {redumper.platform_key()} is downloaded on first use (or: disc-archiver install-redumper)")
    else:
        print(f"  redumper publishes no build for {redumper.platform_key()}")
    for name, path in tools.available_tools().items():
        tool = tools.TOOLS[name]
        print(f"  {name}: {path or 'not found (' + tool.homepage + ', or set ' + tool.variable + ')'}")
    found = drives.list_drives()
    print("drives: " + (", ".join(d.label for d in found) if found else "none listed; pass --drive by hand"))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command and return its exit code."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "notice":
            print(NOTICE)
            return EXIT_OK
        if arguments.command == "formats":
            for fmt in FORMATS:
                needs = f" (needs {', '.join(fmt.needs)})" if fmt.needs else ""
                print(f"{fmt.id}: {fmt.title}{needs} - {fmt.description}")
            return EXIT_OK
        if arguments.command == "tools":
            return _tools()
        if arguments.command == "install-redumper":
            found = tools.find_tool("redumper")
            if found and not arguments.force:
                print(f"redumper is already at {found}")
                return EXIT_OK
            print(redumper.download(_log) / "redumper")
            return EXIT_OK
        profile = load_profile(arguments.profile)
        if arguments.command == "check":
            manifest = load_manifest(arguments.manifest) if arguments.manifest else None
            dat = None
            if arguments.dat is not None:
                if arguments.input.suffix.lower() != ".cue":
                    raise DiscError("--dat compares a cue sheet and its track files; convert the copy to bincue-split first")
                dat = redump.load_dat(arguments.dat)
            with tempfile.TemporaryDirectory() as work, open_source(arguments.input, Path(work), _log) as disc:
                found = fingerprint(disc)
                checks = check_profile(profile, found, profile_paths(profile, disc))
                redump_report = redump_check(dat, arguments.input, disc) if dat is not None else None
                result: dict[str, object] = {"disc": found, "checks": [c.as_json() for c in checks], "redump": redump_report}
                if manifest is not None:
                    result["manifest"] = check_against_manifest(disc, found, manifest)
            print(json.dumps(result, indent=2))
            if redump_report is not None:
                _log(f"redump: {redump.summary(redump_report)}")
                if nearest := redump.candidates_summary(redump_report):
                    _log(f"redump: {nearest}")
            failed = not all(c.matched for c in checks) or (redump_report is not None and redump_report["status"] == "mismatched")
            identified = manifest is None or result["manifest"]["identified"]  # type: ignore[index]
            return EXIT_MISMATCH if failed or not identified else EXIT_OK
        if arguments.command == "rip" and arguments.read_offset is not None and arguments.backend != "cdrdao":
            print(
                "error: --read-offset corrects a cdrdao dump and needs --backend cdrdao; redumper corrects the drive's "
                "read offset itself",
                file=sys.stderr,
            )
            return EXIT_USAGE
        formats = _formats(arguments.formats)
        if arguments.command == "rip" and formats is not None and REFERENCE in formats:
            try:
                reference_backend(arguments.backend)
            except DiscError as error:
                print(f"error: {error}", file=sys.stderr)
                return EXIT_USAGE
        if not _require_acceptance(arguments):
            return EXIT_USAGE
        manifest = archive(
            output=arguments.output,
            profile=profile,
            log=_log,
            formats=formats,
            name=arguments.name,
            drive=getattr(arguments, "drive", None),
            backend=getattr(arguments, "backend", "auto"),
            backend_args=getattr(arguments, "backend_arg", ()),
            image=getattr(arguments, "input", None),
            allow_download=not getattr(arguments, "no_download", False),
            read_offset=getattr(arguments, "read_offset", None),
            dat=arguments.dat,
        )
        return _report(manifest)
    except DiscError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED


def run() -> None:
    """Console entry point."""
    sys.exit(main())
