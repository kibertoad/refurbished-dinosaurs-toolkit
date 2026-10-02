"""The personal-use notice every entry point shows and every output folder carries."""

from __future__ import annotations

from pathlib import Path

NOTICE_FILENAME = "PERSONAL-ARCHIVE-ONLY.txt"

NOTICE = """\
PERSONAL ARCHIVAL COPY - DO NOT SHARE

This tool makes copies of game discs you own, for two purposes only:

  1. a personal archival copy of your own physical disc, and
  2. preparing your own disc for use with a refurbished dinosaurs runtime
     (a clean-room restoration that imports assets from the original you own).

By using it you confirm that:

  * you own the physical disc you are copying, and you obtained it legally;
  * the copy is for you alone. You will never give, upload, sell, lend, seed,
    stream or otherwise share it, or anything extracted from it, with anyone;
  * you will not use it to get around copy protection where the law where you
    live does not allow that.

Copies made with this tool are not for distribution. The restoration projects
never accept disc images, extracted files or reports made from them, and will
never ask you for them. Keep the copy on your own storage, next to the disc it
came from.
"""


def write_notice(directory: Path) -> Path:
    """Write the personal-use notice into ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / NOTICE_FILENAME
    path.write_text(NOTICE, encoding="utf-8")
    return path
