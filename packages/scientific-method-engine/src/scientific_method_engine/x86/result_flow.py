"""Declared result roles and bounded producer dependencies across caller continuations."""
from .image import integer
from .machine import ALIASES, BRANCH_CONDITIONS
from .values import result_marker


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
        # Tag the declared value with a marker unique to the return event about to be
        # recorded. The instruction site would also tag the SP the return pops, every
        # register a call model clobbers and every later execution of the same return.
        # A producer dependency is still weaker than unchanged value identity.
        state.setreg(register, state.reg(register), result_marker(len(state.events)))
        value = state.reg(register)
        rows.append({"entry": entry, "register": register, "value": value.report(),
                     "failureEncodings": c.get("failures", []), "encodings": c.get("encodings", []),
                     "matchesFailureEncoding": None if value.number is None else value.number in c.get("failures", []),
                     "matchingRoles": None if value.number is None else [e for e in c.get("encodings", []) if e["value"] == value.number],
                     "evidence": c["evidence"], "encodingsExhaustive": False, "successEstablished": False})
    return rows


# Sign and overflow tests read the operand as a signed value; carry tests as unsigned.
DOMAINS = {"l": "signed", "le": "signed", "s": "signed", "o": "signed", "c": "unsigned", "be": "unsigned"}


def predicate_domain(predicate):
    """How a branch reads what it tests: ``signed``, ``unsigned``, ``counter`` or ``flags/equality``.

    LOOP, LOOPE, LOOPNE, JCXZ and JECXZ test CX or ECX against zero (``counter``); the conditional
    jumps read the flags of their producer.
    """
    if predicate.startswith("loop") or predicate in ("jcxz", "jecxz"):
        return "counter"
    return DOMAINS.get(BRANCH_CONDITIONS.get(predicate, (None,))[0], "flags/equality")


VALUE_FIELDS = ("sourceValue", "resultValue", "destinationContainerValue", "result", "value", "left", "right", "carry", "count")
CONSUMER_KINDS = ("value-transfer", "conversion", "read", "write", "compare", "branch", "return")
ROW_FIELDS = ("kind", "site", "entry", "depth", "order", "operation", "source", "destination", "sourceBits", "destinationBits",
              "destinationContainer", "conversion", "sourceRegister", "destinationRegister", "effectiveOperandBits", "decoderMnemonic",
              "width", "segment", "offset", "effectiveSegmentRegister", "predicate", "taken", "flagProducer")


def _event_values(event):
    values = {k: event[k] for k in VALUE_FIELDS if isinstance(event.get(k), dict)}
    if event["kind"] == "return":
        values.update({"return:" + c["register"]: c["value"] for c in event.get("resultContracts", [])})
    return values


def return_flows(report, config):
    limit = integer(config.get("returnFlowLimit", 128), 1, 1024, "return flow limit")
    consumer_limit = integer(config.get("returnConsumerLimit", 256), 1, 10000, "return consumer limit")
    analysis_limit = integer(config.get("returnFlowAnalysisLimit", 1000000), 1, 10000000, "return flow analysis limit")
    steps, capped = 0, False
    for path in report["paths"]:
        events = path["events"]
        event_values = {}
        flows, omitted = [], 0
        for index, event in enumerate(events):
            if event["kind"] != "return" and not (event["kind"] == "call-return" and event.get("modeled")):
                continue
            for contract in event.get("resultContracts", []):
                if len(flows) >= limit or capped:
                    omitted += 1
                    continue
                consumers, dropped, scanned = [], 0, True
                marker = event["order"]
                for later_index in range(index + 1, len(events)):
                    if steps >= analysis_limit:
                        capped, scanned = True, False
                        break
                    steps += 1
                    later = events[later_index]
                    if later["kind"] not in CONSUMER_KINDS:
                        continue
                    if later_index not in event_values:
                        event_values[later_index] = _event_values(later)
                    values = event_values[later_index]
                    dependent = {k: v for k, v in values.items() if marker in v.get("resultOrigins", ())}
                    if not dependent:
                        continue
                    if len(consumers) >= consumer_limit:
                        dropped += 1
                        continue
                    row = {k: later[k] for k in ROW_FIELDS if k in later}
                    widths = {k: "narrower" if v["bits"] < contract["value"]["bits"] else
                              "wider" if v["bits"] > contract["value"]["bits"] else "sameWidth"
                              for k, v in dependent.items()}
                    row.update(values=values, dependentValueFields=list(dependent), returnWidthRelationships=widths,
                               relationship="producer dependency only; not value or storage identity")
                    if later["kind"] == "branch":
                        domain = predicate_domain(later.get("predicate", ""))
                        # returnFlows has reported LOOP and JCXZ as flags/equality since it shipped, and
                        # consumers read that value, so it stays until a major release changes it.
                        row["predicateDomain"] = "flags/equality" if domain == "counter" else domain
                    consumers.append(row)
                flows.append({"originOrder": event["order"], "originSite": event["site"], "calleeEntry": contract["entry"],
                              "callSite": event.get("callSite"), "callerEntry": event.get("callerEntry"),
                              "conditionalModel": event.get("modeled", False), "resultContract": contract,
                              "consumers": consumers, "consumersOmitted": dropped, "consumerScanComplete": scanned,
                              "successEstablished": False})
        path["returnFlows"] = {"results": flows, "resultsOmitted": omitted, "resultLimit": limit,
                               "consumerLimit": consumer_limit,
                               "complete": not omitted and all(not f["consumersOmitted"] and f["consumerScanComplete"] for f in flows)
                                           and path["returned"] and not report["gaps"],
                               "interpretation": "conditional static dependency paths; encodings are declared evidence, not live occurrence; a branch never establishes initialization, accepted contents or extent"}
    report["returnFlowAnalysis"] = {"limit": analysis_limit, "steps": steps, "capped": capped}
    return report
