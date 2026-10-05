"""The ``disc-archiver`` command."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import backends, drives, redumper, tools
from .disc import DiscError
from .formats import FORMAT_IDS, FORMATS
from .notice import NOTICE
from .pipeline import archive, fingerprint, manifest_ok, open_source, package_version, profile_paths
from .profile import BUILTIN_PROFILES, check_profile, load_profile

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_MISMATCH = 3

EPILOG = f"""\
exit codes: {EXIT_OK} done and verified; {EXIT_FAILED} failed; {EXIT_USAGE} usage error or notice not
accepted; {EXIT_MISMATCH} a profile check failed or a written format differs from its source.

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

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--output", required=True, type=Path, help="directory to write the copy into")
        sub.add_argument("--name", help="base file name of the images")
        sub.add_argument(
            "--format",
            action="append",
            dest="formats",
            metavar="FORMAT",
            help=f"a format to write; repeat for several. One of {', '.join(FORMAT_IDS)}, "
            "'recommended' (the profile's choice, the default) or 'all'",
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
        help="never download redumper; auto falls back to cdrdao, then to the data track copy",
    )
    common(rip)

    convert = commands.add_parser("convert", help="write the chosen formats from a copy you already have")
    convert.add_argument("--input", required=True, type=Path, help="a .cue, .iso, .ccd or .chd file")
    common(convert)

    check = commands.add_parser("check", help="print a copy's fingerprint and check it against a profile")
    check.add_argument("--input", required=True, type=Path, help="a .cue, .iso, .ccd or .chd file")
    check.add_argument("--profile", default="any", help="a disc profile file or a built-in profile")
    return parser


def _formats(requested: list[str] | None) -> list[str] | None:
    """The formats asked for, or None for the profile's recommendation."""
    if not requested or requested == ["recommended"]:
        return None
    if "recommended" in requested:
        raise DiscError("'recommended' depends on the disc; give it alone or list the formats")
    chosen: list[str] = []
    for item in requested:
        if item == "all":
            chosen += FORMAT_IDS
        elif item in FORMAT_IDS:
            chosen.append(item)
        else:
            raise DiscError(f"unknown format {item!r}; choose from {', '.join(FORMAT_IDS)}, recommended or all")
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
    return EXIT_OK if manifest_ok(manifest) else EXIT_MISMATCH


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
            with tempfile.TemporaryDirectory() as work, open_source(arguments.input, Path(work), _log) as disc:
                found = fingerprint(disc)
                checks = check_profile(profile, found, profile_paths(profile, disc))
            print(json.dumps({"disc": found, "checks": [c.as_json() for c in checks]}, indent=2))
            return EXIT_OK if all(c.matched for c in checks) else EXIT_MISMATCH
        if not _require_acceptance(arguments):
            return EXIT_USAGE
        manifest = archive(
            output=arguments.output,
            profile=profile,
            log=_log,
            formats=_formats(arguments.formats),
            name=arguments.name,
            drive=getattr(arguments, "drive", None),
            backend=getattr(arguments, "backend", "auto"),
            backend_args=getattr(arguments, "backend_arg", ()),
            image=getattr(arguments, "input", None),
            allow_download=not getattr(arguments, "no_download", False),
        )
        return _report(manifest)
    except DiscError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_FAILED


def run() -> None:
    """Console entry point."""
    sys.exit(main())
