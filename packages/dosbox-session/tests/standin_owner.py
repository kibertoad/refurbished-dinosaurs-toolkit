"""An owner process that stops between launching its emulator and recording it in the run lock.

Run as `python standin_owner.py LOCK RUN_DIRECTORY PYTHON`. It takes the run lock, launches a
stand-in emulator (PYTHON sleeping) the way a session does, prints the emulator's identity as one
JSON line and then waits to be killed, without ever recording the emulator in the lock.
"""

import json
import sys
import time

from dinorefurb_dosbox_session import processes
from dinorefurb_dosbox_session.lock import RunLock


def main() -> None:
    lock_path, run_directory, python = sys.argv[1:4]
    RunLock.acquire(lock_path, "killed-owner", processes.current())
    emulator = processes.launch_owned([python, "-c", "import time; time.sleep(120)"], run_directory)
    print(json.dumps(emulator.identity.to_json()), flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
