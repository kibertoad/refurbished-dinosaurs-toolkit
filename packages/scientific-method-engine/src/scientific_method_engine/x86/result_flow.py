"""Declared result roles and bounded producer dependencies across caller continuations."""
from .image import integer
from .machine import ALIASES


def validate_contracts(config, image):
    contracts = config.get("returnContracts", [])
    if not isinstance(contracts, list) or len(contracts) > 256:
        raise ValueError("At most 256 return contracts")
    keys = set()
    for c in contracts:
        if not isinstance(c, dict):
            raise ValueError("Return contract must be an object")
        entry = integer(c.get("entry"), 0, len(image.data) - 1, "return contract entry")
        register = c.get("register")
        if not isinstance(register, str) or register not in ALIASES or not isinstance(c.get("evidence"), str) or not c["evidence"].strip():
            raise ValueError("Return contract requires register and evidence")
        if (entry, register) in keys:
            raise ValueError("Return contract entry/register must be unique")
        keys.add((entry, register))
        bits = ALIASES[register][2]
        failures = c.get("failures", [])
        if not isinstance(failures, list) or len(failures) > 256 or any(type(n) is not int or not 0 <= n < 1 << bits for n in failures):
            raise ValueError("Failure encodings must fit the consumed return width")
        encodings = c.get("encodings", [])
        if not isinstance(encodings, list) or len(encodings) > 256:
            raise ValueError("At most 256 result encodings")
        for encoding in encodings:
            if not isinstance(encoding, dict) or type(encoding.get("value")) is not int or not 0 <= encoding["value"] < 1 << bits:
                raise ValueError("Result encoding must fit the declared return width")
            if not all(isinstance(encoding.get(k), str) and encoding[k].strip() for k in ("role", "evidence")):
                raise ValueError("Result encoding requires role and evidence")
    return contracts


def result_contracts(state, contracts, entry):
    rows = []
    for c in contracts:
        if c["entry"] != entry:
            continue
        register = c["register"]
        # Tag the declared value at the return boundary. A producer dependency is
        # deliberately weaker than unchanged value identity, especially for loops.
        state.setreg(register, state.reg(register), state.at)
        value = state.reg(register)
        rows.append({"entry": entry, "register": register, "value": value.report(),
                     "failureEncodings": c.get("failures", []), "encodings": c.get("encodings", []),
                     "matchesFailureEncoding": None if value.number is None else value.number in c.get("failures", []),
                     "matchingRoles": None if value.number is None else [e for e in c.get("encodings", []) if e["value"] == value.number],
                     "evidence": c["evidence"], "encodingsExhaustive": False, "successEstablished": False})
    return rows


SIGNED = {"jl", "jnge", "jle", "jng", "jg", "jnle", "jge", "jnl"}
UNSIGNED = {"jb", "jc", "jnae", "jbe", "jna", "ja", "jnbe", "jae", "jnb", "jnc"}


def return_flows(report, config):
    limit = integer(config.get("returnFlowLimit", 128), 1, 1024, "return flow limit")
    consumer_limit = integer(config.get("returnConsumerLimit", 256), 1, 10000, "return consumer limit")
    analysis_limit = integer(config.get("returnFlowAnalysisLimit", 1000000), 1, 10000000, "return flow analysis limit")
    steps, capped = 0, False
    for path in report["paths"]:
        flows, omitted = [], 0
        for index, event in enumerate(path["events"]):
            if event["kind"] != "return" and not (event["kind"] == "call-return" and event.get("modeled")):
                continue
            for contract in event.get("resultContracts", []):
                if len(flows) >= limit or capped:
                    omitted += 1
                    continue
                consumers, dropped = [], 0
                marker = event["site"]
                for later in path["events"][index + 1:]:
                    if steps >= analysis_limit:
                        capped = True
                        break
                    steps += 1
                    if later["kind"] not in ("value-transfer", "conversion", "read", "write", "compare", "branch", "return"):
                        continue
                    values = {k: later[k] for k in ("sourceValue", "resultValue", "destinationContainerValue", "result", "value", "left", "right", "carry", "count")
                              if isinstance(later.get(k), dict)}
                    if later["kind"] == "return":
                        values.update({"return:" + c["register"]: c["value"] for c in later.get("resultContracts", [])})
                    dependent = {k: v for k, v in values.items() if marker in v.get("producers", [])}
                    if not dependent:
                        continue
                    if len(consumers) >= consumer_limit:
                        dropped += 1
                        continue
                    row = {k: later[k] for k in ("kind", "site", "entry", "depth", "order", "operation", "source", "destination",
                                                 "sourceBits", "destinationBits", "destinationContainer", "conversion", "sourceRegister", "destinationRegister", "effectiveOperandBits", "decoderMnemonic", "width", "segment", "offset",
                                                 "effectiveSegmentRegister", "predicate", "taken", "flagProducer") if k in later}
                    widths = {k: "narrower" if v["bits"] < contract["value"]["bits"] else
                              "wider" if v["bits"] > contract["value"]["bits"] else "sameWidth"
                              for k, v in dependent.items()}
                    row.update(values=values, dependentValueFields=list(dependent), returnWidthRelationships=widths,
                               relationship="producer dependency only; not value or storage identity")
                    if later["kind"] == "branch":
                        row["predicateDomain"] = "signed" if later.get("predicate") in SIGNED else "unsigned" if later.get("predicate") in UNSIGNED else "flags/equality"
                    consumers.append(row)
                flows.append({"originOrder": event["order"], "originSite": event["site"], "calleeEntry": contract["entry"],
                              "callSite": event.get("callSite"), "callerEntry": event.get("callerEntry"),
                              "conditionalModel": event.get("modeled", False), "resultContract": contract,
                              "consumers": consumers, "consumersOmitted": dropped,
                              "successEstablished": False})
        path["returnFlows"] = {"results": flows, "resultsOmitted": omitted, "resultLimit": limit,
                               "consumerLimit": consumer_limit,
                               "complete": not omitted and not any(f["consumersOmitted"] for f in flows) and path["returned"] and not report["gaps"],
                               "interpretation": "conditional static dependency paths; encodings are declared evidence, not live occurrence; a branch never establishes initialization, accepted contents or extent"}
    report["returnFlowAnalysis"] = {"limit": analysis_limit, "steps": steps, "capped": capped}
    if capped:
        for path in report["paths"]:
            path["returnFlows"]["complete"] = False
    return report
