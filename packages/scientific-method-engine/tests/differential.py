"""Run each synthetic case on every semantics backend and diff the reports (ADR 0003).

Test modules import ``run_report`` from here instead of from the engine. It returns the default
backend's report, or raises its error, after checking that every other registered backend
produced the same report or raised the same error.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scientific_method_engine.x86 import semantics  # noqa: E402
from scientific_method_engine.x86.reports import run_report as engine_report  # noqa: E402


class BackendDifference(AssertionError):
    """Two backends produced different reports for one case."""


def differences(left, right, path="$", limit=20):
    """The paths where two JSON-like values differ, with both values, at most ``limit`` of them."""
    found = []

    def walk(a, b, at):
        if len(found) >= limit:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b), key=str):
                if key not in a or key not in b:
                    found.append((f"{at}.{key}", a.get(key, "<absent>"), b.get(key, "<absent>")))
                else:
                    walk(a[key], b[key], f"{at}.{key}")
        elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and type(a) is type(b):
            if len(a) != len(b):
                found.append((f"{at}.length", len(a), len(b)))
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{at}[{index}]")
        elif a != b or type(a) is not type(b):
            found.append((at, a, b))

    walk(left, right, path)
    return found


def outcome(name, data, config, command):
    """One backend's report, or the error it raised, for one case."""
    with semantics.selected(name):
        try:
            return engine_report(data, copy.deepcopy(config), command), None
        except Exception as error:  # noqa: BLE001 - the error itself is compared
            return None, error


def compare(data, config, command):
    """Each registered backend's outcome, keyed by name, and the differences from the default."""
    outcomes = {name: outcome(name, data, config, command) for name in semantics.names()}
    reference_name = semantics.names()[0]
    reference_report, reference_error = outcomes[reference_name]
    found = {}
    for name, (report, error) in outcomes.items():
        if name == reference_name:
            continue
        if (reference_error is None) != (error is None):
            found[name] = [("$error", repr(reference_error), repr(error))]
        elif error is not None:
            if (type(error), str(error)) != (type(reference_error), str(reference_error)):
                found[name] = [("$error", repr(reference_error), repr(error))]
        else:
            rows = differences(reference_report, report)
            if rows:
                found[name] = rows
    return outcomes, found


def run_report(data, config, command):
    """The default backend's report for one case, after every backend agreed on it."""
    outcomes, found = compare(data, config, command)
    if found:
        lines = [f"{name}: {path}: {left!r} != {right!r}" for name, rows in found.items() for path, left, right in rows]
        raise BackendDifference("Semantics backends disagree:\n" + "\n".join(lines))
    report, error = outcomes[semantics.names()[0]]
    if error is not None:
        raise error
    return report
