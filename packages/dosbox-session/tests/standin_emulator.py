"""A stand-in for the DOSBox-X executable, run as `python standin_emulator.py --mode MODE <args>`.

It reads the generated dosbox.conf as DOSBox-X would read its [autoexec]: it finds the folder
mounted as C: and the readiness marker line, and then acts out MODE:

- ready: writes the marker as the guest would after its drives are set up, then runs until killed.
- exit-early: exits with code 3 before writing the marker.
- never-ready: runs until killed without writing the marker.
"""

import argparse
import re
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["ready", "exit-early", "never-ready"])
    parser.add_argument("-conf", dest="conf", required=True)
    parser.add_argument("--agent-config", dest="agent_config", required=True)
    arguments = parser.parse_args()
    if not Path(arguments.agent_config).is_file():
        return 4
    autoexec = Path(arguments.conf).read_text(encoding="utf-8").split("[autoexec]", 1)[1]
    drive_c = re.search(r'^mount c "(.+)"$', autoexec, re.MULTILINE)
    marker = re.search(r"^echo (\S+)> C:\\(\S+)$", autoexec, re.MULTILINE)
    if drive_c is None or marker is None:
        return 5
    time.sleep(0.2)
    if arguments.mode == "exit-early":
        return 3
    if arguments.mode == "ready":
        (Path(drive_c.group(1)) / marker.group(2)).write_text(marker.group(1) + "\r\n", encoding="ascii")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
