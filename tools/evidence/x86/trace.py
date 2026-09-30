"""Bounded control flow and interprocedural path reports."""
from copy import deepcopy
from capstone.x86 import X86_OP_IMM, X86_OP_REG, X86_OP_MEM
from .image import integer
from .machine import State, StopPath, ordinary, predicate, REGISTERS, ALIASES
from .values import const, unknown, sources, op, Value

# Synonymous and complementary branches on one flag producer share a single assumption.
BRANCH_CONDITIONS = {}
for names, condition in ((("je", "jz"), "z"), (("jb", "jc", "jnae"), "c"), (("jbe", "jna"), "be"),
                         (("jl", "jnge"), "l"), (("jle", "jng"), "le"), (("js",), "s"),
                         (("jo",), "o"), (("jp", "jpe"), "p")):
    for name in names:
        BRANCH_CONDITIONS[name] = (condition, False)
for names, condition in ((("jne", "jnz"), "z"), (("jae", "jnb", "jnc"), "c"), (("ja", "jnbe"), "be"),
                         (("jge", "jnl"), "l"), (("jg", "jnle"), "le"), (("jns",), "s"),
                         (("jno",), "o"), (("jnp", "jpo"), "p")):
    for name in names:
        BRANCH_CONDITIONS[name] = (condition, True)


def call_target(image, site, ins):
    if ins.mnemonic in ("lcall", "ljmp"):
        return image.far_target(site, ins)
    if ins.operands and ins.operands[0].type == X86_OP_IMM:
        return image.near_target(site, ins.operands[0].imm), {"encoding": "relative16"}
    return None, {"reason": "computed transfer remains unresolved"}


def walk(image, entries, limit=10000):
    integer(limit, 1, 100000, "instruction limit")
    pending, seen, gaps, edges = list(entries), {}, [], []
    while pending:
        at = pending.pop()
        if at in seen:
            continue
        if len(seen) >= limit:
            gaps.append({"site": at, "reason": "instruction limit"})
            break
        ins = image.decode(at)
        if ins is None:
            gaps.append({"site": at, "reason": "undecoded or unmapped edge"})
            continue
        seen[at] = ins
        m, following = ins.mnemonic, at + ins.size
        if m in ("ret", "retf", "iret", "iretd"):
            continue
        if m in ("call", "lcall", "jmp", "ljmp") or m.startswith("j") or m.startswith("loop"):
            target, provenance = call_target(image, at, ins)
            edges.append({"site": at, "target": target, "kind": m, "provenance": provenance})
            if target is None:
                gaps.append({"site": at, "reason": provenance.get("reason", "target outside declared regions")})
            else:
                pending.append(target)
            if m in ("jmp", "ljmp"):
                continue
        if m in ("int", "int3", "into", "hlt", "in", "out", "insb", "insw", "outsb", "outsw"):
            gaps.append({"site": at, "reason": "hardware or interrupt boundary"})
            continue
        pending.append(following)
    intervals = sorted((at, at + ins.size) for at, ins in seen.items())
    undecoded = []
    for r in image.regions:
        cursor = r["start"]
        for start, end in intervals:
            if start < r["start"] or start >= r["end"]:
                continue
            if start > cursor:
                undecoded.append({"start": cursor, "end": start, "region": r["name"]})
            cursor = max(cursor, end)
        if cursor < r["end"]:
            undecoded.append({"start": cursor, "end": r["end"], "region": r["name"]})
    return seen, gaps, edges, undecoded


def snapshot(state):
    return {name: state.reg(name).report() for name in ALIASES}


def trace(image, config):
    entry = integer(config.get("entry"), 0, len(image.data) - 1, "entry")
    if not any(entry in r["entries"] for r in image.regions):
        raise ValueError("Trace entry must be an established region entry")
    max_steps = integer(config.get("maxSteps", 512), 1, 10000, "maxSteps")
    max_paths = integer(config.get("maxPaths", 64), 1, 256, "maxPaths")
    max_depth = integer(config.get("maxDepth", 8), 1, 32, "maxDepth")
    integer(config.get("returnBytes", 2), 2, 4, "returnBytes")
    if config.get("returnBytes", 2) not in (2, 4):
        raise ValueError("returnBytes must be 2 or 4")
    models = config.get("callModels", [])
    if not isinstance(models, list) or len(models) > 64:
        raise ValueError("At most 64 explicit call models")
    sites = set()
    for model in models:
        integer(model.get("site"), 0, len(image.data) - 1, "model site")
        if model["site"] in sites or not model.get("evidence"):
            raise ValueError("Call model requires a unique site and evidence")
        sites.add(model["site"])
        cases = model.get("cases", [])
        if not isinstance(cases, list) or not 1 <= len(cases) <= 16:
            raise ValueError("A model requires 1..16 return cases")
        if any(r not in REGISTERS for r in model.get("preserves", [])):
            raise ValueError("Model preserves must name full registers")
        for case in cases:
            for r, n in case.get("registers", {}).items():
                if r not in ALIASES or type(n) is not int or not 0 <= n < 1 << ALIASES[r][2]:
                    raise ValueError("Invalid model register")
    pending, outputs, global_gaps = [State(entry, image, config)], [], []
    created = 1
    total_steps = 0
    total_limit = integer(config.get("totalSteps", 20000), 1, 100000, "totalSteps")
    checkpoints = set(config.get("checkpoints", []))

    def finish(s, reason=None, returned=False):
        outputs.append({"returned": returned, "stop": reason, "steps": s.steps,
                        "instructionPath": s.path, "guards": s.guards, "events": s.events,
                        "registers": snapshot(s), "conditionalModels": s.conditional})

    while pending:
        state = pending.pop()
        try:
            while True:
                if state.steps >= max_steps:
                    raise StopPath("step limit; loop progress unresolved")
                if total_steps >= total_limit:
                    raise StopPath("total instruction budget exhausted")
                total_steps += 1
                at = state.at
                ins = image.decode(at)
                if ins is None:
                    raise StopPath("undecoded or unmapped instruction")
                state.steps += 1
                state.path.append(at)
                state.visits[at] = state.visits.get(at, 0) + 1
                if state.visits[at] > 4:
                    raise StopPath("repeated instruction; bounded loop reading required")
                if at in checkpoints:
                    state.event("checkpoint", registers=snapshot(state))
                m, following = ins.mnemonic, at + ins.size
                if 0xf2 in ins.prefix or 0xf3 in ins.prefix:
                    raise StopPath("repeat prefix requires a separate bounded string-operation reading")
                if 0x66 in ins.prefix and (m in ("call", "lcall", "ret", "retf", "jmp", "ljmp") or m.startswith("j")):
                    raise StopPath("32-bit control transfer is outside the 16-bit frame model")
                if m in ("call", "lcall"):
                    target, provenance = call_target(image, at, ins)
                    indirect_value = None
                    if target is None and ins.operands and ins.operands[0].type in (X86_OP_REG, X86_OP_MEM):
                        indirect_value = state.get(ins, ins.operands[0], image)
                    guard_checks = []
                    if indirect_value is not None:
                        for g in state.guards:
                            if g.get("right", {}).get("value") == 0:
                                guard_checks.append({"site": g["site"], "predicate": g["predicate"], "taken": g["taken"],
                                                     "sameTargetValue": g.get("left", {}).get("expression") == indirect_value.term})
                    state.event("call", target=target, provenance=provenance, registers=snapshot(state),
                                indirectValue=indirect_value.report() if indirect_value is not None else None, guards=guard_checks)
                    previous = image.decode(state.path[-2]) if len(state.path) > 1 else None
                    push_cs = (m == "call" and previous is not None and previous.mnemonic == "push"
                               and previous.size + state.path[-2] == at and previous.operands[0].type == X86_OP_REG
                               and previous.reg_name(previous.operands[0].reg) == "cs" and previous.operands[0].size == 2)
                    model = next((x for x in models if x["site"] == at), None)
                    if model:
                        return_bytes = model.get("returnBytes", 4 if m == "lcall" else 2)
                        if return_bytes not in (2, 4) or type(return_bytes) is not int:
                            raise ValueError("Modeled returnBytes must be 2 or 4")
                        if (m == "lcall" and return_bytes != 4) or (m == "call" and return_bytes == 4 and not push_cs):
                            raise ValueError("Modeled return width differs from the encoded call frame")
                        if push_cs and return_bytes != 4:
                            raise StopPath("push-CS/near-call model requires an explicit four-byte return contract")
                        if push_cs:
                            actual_cs = state.pop(2)
                            if actual_cs.term != state.reg("cs").term:
                                raise StopPath("modeled far return segment changed")
                        for case in model["cases"]:
                            if created >= max_paths:
                                global_gaps.append({"site": at, "reason": "path limit at modeled call"})
                                break
                            child = deepcopy(state)
                            created += 1
                            for r in REGISTERS:
                                if r not in model.get("preserves", []) and r not in ("esp", "cs"):
                                    child.regs[r] = unknown(f"modeled-call:{at}:{r}", ALIASES[r][2], at)
                            child.memory.clear()
                            child.memory_epoch += 1
                            child.forget_flags()
                            for r, n in case.get("registers", {}).items():
                                child.setreg(r, const(n, ALIASES[r][2], at), at)
                            child.conditional.append({"site": at, "evidence": model["evidence"],
                                                      "assumption": "call returns with balanced stack; memory effects unresolved"})
                            child.event("call-return", callSite=at, registers=snapshot(child), modeled=True,
                                        unknownMemoryEffects=True)
                            child.at = following
                            pending.append(child)
                        break
                    if target is None:
                        raise StopPath("unresolved call: " + provenance.get("reason", "outside mapped code"))
                    if len(state.frames) >= max_depth:
                        raise StopPath("call depth limit; recursion or callee remains unresolved")
                    target_region = image.region(target)
                    if target_region is None:
                        raise StopPath("call target outside declared code regions")
                    here = image.region(at)
                    return_ip = (here["ip"] + following - here["start"]) & 0xFFFF
                    if m == "lcall":
                        state.push(state.reg("cs"))
                    state.push(const(return_ip, 16, at))
                    state.frames.append({"entry": target, "sp": state.reg("sp"), "returnBytes": 4 if m == "lcall" or push_cs else 2,
                                         "frameSource": "push-CS/near-call; matching far return required" if push_cs else m,
                                         "continuation": following, "returnIP": return_ip, "callSite": at,
                                         "callerCS": state.reg("cs")})
                    if m == "lcall":
                        state.setreg("cs", const(target_region["segment"], 16, at), at)
                    state.at = target
                    continue
                if m in ("ret", "retf"):
                    frame = state.frames[-1]
                    roles = []
                    for contract in config.get("returnContracts", []):
                        if contract.get("entry") != frame["entry"]:
                            continue
                        register = contract.get("register")
                        if register not in ALIASES or not contract.get("evidence"):
                            raise ValueError("Return contract requires register and evidence")
                        value = state.reg(register)
                        failures = contract.get("failures", [])
                        if not isinstance(failures, list) or len(failures) > 256 or any(type(n) is not int or not 0 <= n < 1 << value.bits for n in failures):
                            raise ValueError("Failure encodings must fit the consumed return width")
                        roles.append({"register": register, "value": value.report(), "failureEncodings": failures,
                                      "matchesFailureEncoding": None if value.number is None else value.number in failures,
                                      "evidence": contract["evidence"]})
                    state.event("return", registers=snapshot(state), cleanupBytes=ins.operands[0].imm if ins.operands else 0, resultContracts=roles)
                    # The entry frame gets the same width and balance checks as a traced call.
                    expected = 4 if m == "retf" else 2
                    if expected != frame["returnBytes"] or state.reg("sp").term != frame["sp"].term:
                        raise StopPath("return frame or stack balance differs from the call")
                    if len(state.frames) == 1:
                        finish(state, returned=True)
                        break
                    actual_ip = state.pop(2)
                    if actual_ip.number != frame["returnIP"]:
                        raise StopPath("return target was overwritten or has unknown provenance")
                    if m == "retf":
                        actual_cs = state.pop(2)
                        if actual_cs.term != frame["callerCS"].term:
                            raise StopPath("far return segment changed")
                        state.setreg("cs", actual_cs, at)
                    if ins.operands:
                        state.setreg("sp", op("add", state.reg("sp"), const(ins.operands[0].imm, 16), at), at)
                    state.frames.pop()
                    state.event("call-return", callSite=frame["callSite"], registers=snapshot(state), modeled=False)
                    state.at = frame["continuation"]
                    continue
                if m in ("jmp", "ljmp"):
                    target, provenance = call_target(image, at, ins)
                    if target is None:
                        raise StopPath("unresolved jump: " + provenance.get("reason", "outside mapped code"))
                    if m == "ljmp":
                        target_region = image.region(target)
                        if target_region is None:
                            raise StopPath("jump target outside declared code regions")
                        state.setreg("cs", const(target_region["segment"], 16, at), at)
                    state.at = target
                    continue
                if m.startswith("j"):
                    if m in ("jcxz", "jecxz"):
                        raise StopPath("counter branch not supported")
                    target, _ = call_target(image, at, ins)
                    answer, info = predicate(state, m)
                    condition, negated = BRANCH_CONDITIONS.get(m, (m, False))
                    key = repr((condition, state.flags if state.flags is not None else ("unresolved", state.flag_epoch)))
                    if answer is None and key in state.assumptions:
                        answer = state.assumptions[key] != negated
                    choices = [answer] if answer is not None else [False, True]
                    branches = []
                    for index, taken in enumerate(choices):
                        # The last choice reuses this state; earlier ones copy it before it changes.
                        child = state if index == len(choices) - 1 else deepcopy(state)
                        guard = {"site": at, "taken": taken, **info}
                        child.guards.append(guard)
                        child.event("branch", **{k: v for k, v in guard.items() if k != "site"})
                        child.assumptions[key] = taken != negated
                        child.at = target if taken else following
                        if child.at is None:
                            finish(child, "branch target outside mapped code")
                        else:
                            branches.append(child)
                    if not branches:
                        break
                    state = branches.pop()
                    for child in branches:
                        if created >= max_paths:
                            global_gaps.append({"site": at, "reason": "path limit"})
                        else:
                            pending.append(child)
                            created += 1
                    continue
                ordinary(state, ins, image)
                state.at = following
        except StopPath as error:
            finish(state, str(error))
    return {"paths": outputs, "gaps": global_gaps,
            "completeWithinModel": not global_gaps and bool(outputs) and all(p["returned"] for p in outputs),
            "nativeReachability": "unconfirmed", "stepsUsed": total_steps,
            "limits": {"steps": max_steps, "paths": max_paths, "depth": max_depth}}
