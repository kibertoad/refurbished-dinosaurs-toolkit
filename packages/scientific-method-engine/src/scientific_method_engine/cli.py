"""Read-only bounded x86 evidence reports. Keep configs and reports in GAME_DIR, uncommitted."""
import json
import sys
from pathlib import Path

import capstone
import pypcode

from . import PREPARED_PROTOCOL

CONFIG_LIMIT = 1024 * 1024
PREPARED_CONFIG_LIMIT = 16 * 1024 * 1024
# The header names the decoder and instruction semantics that actually ran, not the pins in pyproject.toml.
DECODER = "capstone " + capstone.__version__
INSTRUCTION_SEMANTICS = f"pypcode {pypcode.__version__} (Ghidra SLEIGH x86)"
USAGE = ("Usage: scientific-method-engine <operand|operand-candidates|target|bounds|owner|callees|trace|uses|arguments|"
         "effects|returns|memory|incoming|call-order|guards|allocation|dispatch> <config.json|->\n"
         "effects includes ordered path writes/calls and local restoration witnesses; transactionality remains unestablished.\n"
         "callees compares its edges with an ExportCallEdges.java export given as ghidraCallEdges;\n"
         "each row it compares fall-through at carries the engine's side as engineReadsOn;\n"
         "agreed is false where Ghidra ends the function at an instruction the engine reads past (ghidraEndsFunction),\n"
         "continues past one the engine stops at (ghidraContinues),\n"
         "or continues at another address than the next instruction (ghidraFallsThroughElsewhere).\n"
         "trace, arguments, effects, returns, guards, memory and allocation check relationalControls:\n"
         "a violated control fails the report; an undecided one is reported and never counts as held.\n"
         "entryFrame {from} starts an entry inside the function at from, in the frame a trace from there observed.\n"
         "Declared table continuations are separate conditional paths that spend continuationBudget\n"
         "(paths, totalSteps, maxSteps, visitLimit, stringIterations); ordinary computed transfers remain stopped.\n"
         "Port accesses and interrupts are hardware-boundary events; portInputs supplies port reads as assumptions.\n"
         "A callModels entry at an INT n site (real mode) returns past the interrupt under its cases (leavesFlags: true\n"
         "keeps the interrupt's FLAGS word on the stack); other interrupts stop.\n"
         "callModels[].preservesMemory keeps explicit, bounded pre-call byte scopes across a modeled call; other memory stays unknown.\n"
         "A scope needs a concrete segment; its base may be symbolic, such as BP in an entryFrame query.\n"
         "Each traced path's loops record lists restart edges and what changed between iterations (loopIterationLimit).\n"
         "       scientific-method-engine ghidra-scripts")


def ghidra_scripts():
    """The directory to pass to Ghidra's analyzeHeadless -scriptPath."""
    return Path(__file__).resolve().parent / "ghidra"


def main(argv):
    """Run one command (``argv`` excludes the program name) and print its JSON report."""
    from .x86.image import read_source
    from .x86.reports import run_report
    if argv == ["ghidra-scripts"]:
        print(ghidra_scripts())
        return
    if len(argv) != 2:
        raise ValueError(USAGE)
    command, config_path = argv
    reject_derived = False
    if config_path == "-":
        # The reader caps its input at 1 MiB, then adds every source relocation.
        limit, label = PREPARED_CONFIG_LIMIT, "Prepared config exceeds 16 MiB"
        text = sys.stdin.read(limit + 1)
        base = Path.cwd()
    else:
        limit, label = CONFIG_LIMIT, "Config exceeds 1 MiB"
        path = Path(config_path).resolve()
        if path.stat().st_size > limit:
            raise ValueError(label)
        text, base = path.read_text(encoding="utf-8"), path.parent
        reject_derived = True
    if len(text.encode("utf-8")) > limit:
        raise ValueError(label)
    config = json.loads(text)
    if not isinstance(config, dict):
        raise ValueError("Config must be an object")
    if reject_derived and "overlayExports" in config:
        # Only the reader's MZ/FBOV loader (stdin mode) derives overlay exports from source tables.
        raise ValueError("overlayExports is source-derived and cannot be supplied")
    if config_path == "-":
        protocol = config.pop("preparedProtocol", None)
        if protocol != PREPARED_PROTOCOL:
            raise ValueError(f"Reader sent prepared config protocol {protocol}; this engine reads protocol "
                             f"{PREPARED_PROTOCOL}. Install matching @scientific-method/executable-reader and "
                             "scientific-method-engine releases.")
    elif "preparedProtocol" in config:
        raise ValueError("preparedProtocol is set by the reader and cannot be supplied")
    data, identity = read_source(config, base)
    result = run_report(data, config, command)
    report = {"schema": "bounded-x86-v1", "decoder": DECODER, "instructionSemantics": INSTRUCTION_SEMANTICS,
              "sourceIdentity": identity, "status": "Conditional static report; never promotes an evidence entry", **result}
    # The reader parses the prepared path's output and prints it indented itself, so the pipe carries
    # compact JSON and stays within the reader's output cap. A config file run prints for people.
    print(json.dumps(report, separators=(",", ":")) if config_path == "-" else json.dumps(report, indent=2))


def run():
    """Entry point of the ``scientific-method-engine`` command; exits with 1 on any error."""
    try:
        main(sys.argv[1:])
    except (ValueError, TypeError, KeyError, OSError, ImportError) as error:
        print("Evidence report: " + str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
