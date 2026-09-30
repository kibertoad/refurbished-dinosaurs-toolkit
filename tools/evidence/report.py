#!/usr/bin/env python3
"""Read-only bounded x86 evidence reports. Keep inputs/outputs local-only."""
import json
import sys
from pathlib import Path


def main():
    from x86.image import read_source
    from x86.reports import run_report
    if len(sys.argv) != 3:
        raise ValueError("Usage: report.py <trace|uses|arguments|effects|returns|memory|incoming|guards|allocation|dispatch> <config.json|->")
    command, config_path = sys.argv[1:]
    if config_path == "-":
        text = sys.stdin.read(1024 * 1024 + 1)
        base = Path.cwd()
    else:
        path = Path(config_path).resolve()
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Config exceeds 1 MiB")
        text, base = path.read_text(encoding="utf-8"), path.parent
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Config exceeds 1 MiB")
    config = json.loads(text)
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    data, identity = read_source(config, base)
    result = run_report(data, config, command)
    print(json.dumps({"schema": "bounded-x86-v1", "decoder": "capstone 5.0.7", "sourceIdentity": identity,
                      "status": "Conditional static report; never promotes an evidence entry", **result}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError, ImportError) as error:
        print("Evidence report: " + str(error), file=sys.stderr)
        sys.exit(1)
