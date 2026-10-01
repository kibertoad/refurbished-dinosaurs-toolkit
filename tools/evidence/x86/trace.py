"""Bounded control flow and interprocedural path reports."""
from copy import deepcopy
from capstone.x86 import X86_OP_IMM, X86_OP_REG, X86_OP_MEM
from .image import integer
from .machine import (State, StopPath, ordinary, predicate, REGISTERS, ALIASES, string_instruction, string_count,
                      string_effect, check_string_form)
from .values import const, unknown, sources, op, Value
from .result_flow import validate_contracts, result_contracts

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
        return image.near_target(site, ins.operands[0].imm), {"encoding": "relative32" if image.flat else "relative16", "loadedTarget": ins.operands[0].imm & image.mask,
                                                             "mapping": "source PE section table" if image.config.get("peMetadata") else "declared region mapping"}
    return None, {"reason": "computed transfer remains unresolved"}


OVERLAP_REASON = "overlapping entry-path instructions; boundary unresolved"
RETURNS = {"ret": "near return", "retf": "far return", "iret": "interrupt return", "iretd": "interrupt return"}
INTERRUPTS = ("int", "int1", "int3", "into")
PORTS = ("in", "out", "insb", "insw", "insd", "outsb", "outsw", "outsd")


def base_mnemonic(ins):
    # Capstone names REP/REPNE/BND prefixes in the mnemonic ("repz ret", "rep insb", "bnd jmp").
    return ins.mnemonic.split()[-1]
CONTESTED_REASON = "reached only through a rejected overlapping start"


def unsupported_transfer(image, ins):
    """Operand-size overrides and flat-model far transfers fall outside the frame model."""
    m = base_mnemonic(ins)
    return ((0x66 in ins.prefix and (m in ("call", "lcall", "ret", "retf", "jmp", "ljmp") or m.startswith(("j", "loop"))))
            or (image.flat and m in ("lcall", "ljmp", "retf")))


def counter_branch(state, ins):
    """JCXZ/JECXZ and the LOOP family: CX (ECX with a 32-bit address size) decides the branch, with ZF for LOOPE/LOOPNE."""
    m = ins.mnemonic
    counter = "ecx" if ins.addr_size == 4 else "cx"
    if m.startswith("loop"):
        # LOOP decrements the counter without changing any flag.
        state.setreg(counter, op("sub", state.reg(counter), const(1, 32 if counter == "ecx" else 16), state.at), state.at)
    count = state.reg(counter)
    info = {"predicate": m, "counter": counter, "count": count.report()}
    if m in ("jcxz", "jecxz"):
        answer = None if count.number is None else count.number == 0
        return answer, info, repr(("counter-zero", count.term))
    nonzero = None if count.number is None else count.number != 0
    zero_flag = None
    if m != "loop":
        zero_flag, flag_info = predicate(state, "je")
        info["zeroFlag"] = flag_info
        if zero_flag is not None and m in ("loopne", "loopnz"):
            zero_flag = not zero_flag
    parts = [nonzero] if m == "loop" else [nonzero, zero_flag]
    answer = False if False in parts else None if None in parts else True
    flags = state.flags if state.flags is not None else ("unresolved", state.flag_epoch)
    return answer, info, repr((m, count.term) if m == "loop" else (m, count.term, flags))


def walk(image, entries, limit=10000):
    integer(limit, 1, 100000, "instruction limit")
    pending, seen, gaps, edges = list(entries), {}, [], []
    # Decoded successors of each instruction, so a proof can be checked for independence below.
    # A call's return site is reached only if the callee returns, so it never proves an overlapping start.
    successors, returns, supplied_edges = {}, set(), set()
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
        successors[at] = following_sites = []
        m, following = base_mnemonic(ins), at + ins.size
        if unsupported_transfer(image, ins):
            gaps.append({"site": at, "reason": "unsupported control-transfer frame encoding"})
            continue
        declaration = image.indirect_jumps.get(at)
        if declaration is not None:
            for row in declaration["rows"]:
                target = row["target"]
                edges.append({"site": at, "target": target, "kind": "jmp",
                              "provenance": {"encoding": "declared indirect jump table", **declaration}})
                pending.append(target)
                following_sites.append(target)
                supplied_edges.add((at, target))
            if not declaration["exhaustive"]:
                gaps.append({"site": at, "reason": "indirect jump table is not declared exhaustive"})
                edges.append({"site": at, "target": None, "kind": "jmp",
                              "provenance": {"reason": "indirect jump table is not declared exhaustive"}})
            continue
        if m in RETURNS:
            continue
        if m in ("call", "lcall", "jmp", "ljmp") or m.startswith("j") or m.startswith("loop"):
            target, provenance = call_target(image, at, ins)
            edges.append({"site": at, "target": target, "kind": m, "provenance": provenance})
            if target is None:
                gaps.append({"site": at, "reason": provenance.get("reason", "target outside declared regions")})
            else:
                pending.append(target)
                following_sites.append(target)
            if m in ("jmp", "ljmp"):
                continue
        if m in INTERRUPTS or m == "hlt" or m in PORTS:
            gaps.append({"site": at, "reason": "hardware or interrupt boundary"})
            continue
        if m in ("call", "lcall") and following not in following_sites:
            returns.add((at, following))
        pending.append(following)
        following_sites.append(following)
    # An entry into another instruction is not a verified boundary. Retain both
    # interpretations as gaps rather than choosing whichever was visited first.
    active, conflicts, pairs = [], set(), []
    for start, end in sorted((at, at + ins.size) for at, ins in seen.items()):
        active = [(a, b) for a, b in active if b > start]
        for a, b in active:
            conflicts.update((a, start))
            pairs.append((a, start))
        active.append((start, end))
    # A start is verified when it overlaps nothing, or when a verified instruction
    # reaches it by a direct edge or by falling through (other than a call's return
    # site). Each proving step must be reachable from the entries without passing
    # through the start it proves, so raw candidates and conflicting declared
    # entries never prove themselves. Rejected starts are excluded and the proof
    # repeated until nothing changes.
    rejected, reach = set(), {}

    def reachable(inner=None):
        # Instructions reachable from the entries without passing through inner or a rejected start.
        if inner not in reach:
            stack, visited = [x for x in entries if x != inner and x not in rejected], set()
            while stack:
                at = stack.pop()
                if at in visited or at == inner or at in rejected or at not in successors:
                    continue
                visited.add(at)
                stack.extend(successors[at])
            reach[inner] = visited
        return reach[inner]

    def independent(site, inner):
        return site in reachable(inner)

    while True:
        reach.clear()
        verified = {at for at in seen if at not in conflicts and at not in rejected}
        frontier = list(verified)
        while frontier:
            at = frontier.pop()
            for target in successors[at]:
                if ((at, target) not in returns and (at, target) not in supplied_edges
                        and target in seen and target not in verified
                        and target not in rejected and independent(at, target)):
                    verified.add(target)
                    frontier.append(target)
        unresolved = {at for pair in pairs if pair[1] not in verified for at in pair}
        if unresolved <= rejected:
            break
        rejected |= unresolved
    # Only what the accepted starts reach is established. An instruction reached only
    # through a rejected start leaves seen with it, so no report confirms what the proof
    # above refused to count; it is returned as contested instead of being lost.
    # The reach cache still holds the final rejected set, as the last pass added nothing.
    established = reachable()
    contested = {}
    for at in sorted(seen):
        if at in established:
            continue
        if at not in unresolved:
            contested[at] = seen[at]
        del seen[at]
    # Mark each direct edge from a surviving site that proves a surviving overlapping start.
    overlapping = {inner for _, inner in pairs} - unresolved
    # Supplied table edges never prove a boundary, even to a start another edge proves.
    for e in edges:
        if (e["target"] in overlapping and e["site"] in seen and e["site"] in verified
                and (e["site"], e["target"]) not in supplied_edges and independent(e["site"], e["target"])):
            e["overlappingTarget"] = True
            e["boundaryEvidence"] = "direct edge from an independently verified instruction"
    for at in sorted(unresolved):
        gaps.append({"site": at, "reason": OVERLAP_REASON})
    intervals = sorted((at, at + ins.size) for at, ins in seen.items())
    undecoded = []
    for r in image.regions:
        inside = [(start, end) for start, end in intervals if r["start"] <= start < r["end"]]
        undecoded.extend({**hole, "region": r["name"]} for hole in uncovered(r["start"], r["end"], inside))
    return seen, gaps, edges, undecoded, contested


def uncovered(start, end, spans):
    """The ranges of start..end that no span covers; spans are clipped to the bounds."""
    missing, cursor = [], start
    for a, b in sorted(spans):
        a, b = max(a, start), min(b, end)
        if a >= b:
            continue
        if a > cursor:
            missing.append({"start": cursor, "end": a})
        cursor = max(cursor, b)
    if cursor < end:
        missing.append({"start": cursor, "end": end})
    return missing


def snapshot(state):
    return {name: state.reg(name).report() for name in ALIASES}


def trace(image, config):
    entry = integer(config.get("entry"), 0, len(image.data) - 1, "entry")
    if not any(entry in r["entries"] for r in image.regions):
        raise ValueError("Trace entry must be an established region entry")
    max_steps = integer(config.get("maxSteps", 512), 1, 10000, "maxSteps")
    max_paths = integer(config.get("maxPaths", 64), 1, 256, "maxPaths")
    max_depth = integer(config.get("maxDepth", 8), 1, 32, "maxDepth")
    integer(config.get("returnBytes", image.bits // 8), 2, 4, "returnBytes")
    if config.get("returnBytes", image.bits // 8) not in ((4,) if image.flat else (2, 4)):
        raise ValueError("returnBytes must agree with the selected near/far frame model")
    contracts = validate_contracts(config, image)
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
        if "returnBytes" in model and (type(model["returnBytes"]) is not int or model["returnBytes"] not in (2, 4)):
            raise ValueError("Modeled returnBytes must be 2 or 4")
        if any(r not in REGISTERS for r in model.get("preserves", [])):
            raise ValueError("Model preserves must name full registers")
        for case in cases:
            for r, n in case.get("registers", {}).items():
                if r not in ALIASES or type(n) is not int or not 0 <= n < 1 << ALIASES[r][2]:
                    raise ValueError("Invalid model register")
    pending, outputs, global_gaps = [State(entry, image, config)], [], []
    created = 1
    total_steps = 0
    total_string_steps = 0
    string_limit = integer(config.get("stringIterations", 4096), 0, 65536, "string iteration budget")
    total_limit = integer(config.get("totalSteps", 20000), 1, 100000, "totalSteps")
    checkpoints = set(config.get("checkpoints", []))
    # How often one path may pass the same instruction; a loop with a known bound needs it raised.
    visit_limit = integer(config.get("visitLimit", 4), 1, 4096, "visitLimit")

    def string_step(s, ins, count):
        # Reserve iterations only when they fit, so a rejected request never drains the shared budget.
        nonlocal total_string_steps
        remaining = string_limit - total_string_steps
        if count.number is not None and count.number <= remaining:
            total_string_steps += count.number
        string_effect(s, ins, count, remaining)

    def finish(s, reason=None, returned=False):
        outputs.append({"returned": returned, "stop": reason, "stopSite": None if returned else s.at, "steps": s.steps,
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
                if state.visits[at] > visit_limit:
                    raise StopPath(f"instruction repeated more than {visit_limit} times; raise visitLimit or read the loop's bound")
                if at in checkpoints:
                    state.event("checkpoint", registers=snapshot(state))
                m, following = ins.mnemonic, at + ins.size
                is_string = string_instruction(ins)
                if (0xf2 in ins.prefix or 0xf3 in ins.prefix) and not is_string:
                    raise StopPath("repeat prefix requires a separate bounded string-operation reading")
                if 0x66 in ins.prefix and (m in ("call", "lcall", "ret", "retf", "jmp", "ljmp") or m.startswith(("j", "loop"))):
                    raise StopPath("Operand-size control transfer override is outside the selected frame model")
                if image.flat and m in ("lcall", "ljmp"):
                    raise StopPath("Far transfer is outside the PE32 flat model")
                if is_string:
                    # Unsupported forms stop once, before any direction split.
                    check_string_form(state, ins)
                    count = string_count(state, ins)
                    direction = state.direction_flag
                    if count.number and direction.number is None and count.number <= string_limit - total_string_steps:
                        key = repr(("direction", direction.term))
                        if key in state.assumptions:
                            state.direction_flag = const(state.assumptions[key], 1, at)
                            state.event("flag-assumption", flag="DF", value=state.direction_flag.report(), producer=direction.report(),
                                        evidence="conditional outcome of one unresolved direction producer")
                        else:
                            # Like a branch, the last case reuses this state, so a path limit never drops it.
                            for choice in (0, 1):
                                if choice == 0:
                                    if created >= max_paths:
                                        global_gaps.append({"site": at, "reason": "path limit at unknown direction flag"})
                                        continue
                                    child = deepcopy(state); created += 1
                                else:
                                    child = state
                                child.assumptions[key] = choice
                                child.direction_flag = const(choice, 1, at)
                                child.event("flag-assumption", flag="DF", value=child.direction_flag.report(), producer=direction.report(),
                                            evidence="conditional outcome of one unresolved direction producer")
                                if child is not state:
                                    try:
                                        string_step(child, ins, count)
                                        child.at = following
                                        pending.append(child)
                                    except StopPath as error:
                                        finish(child, str(error))
                    string_step(state, ins, count)
                    state.at = following
                    continue
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
                    # push cs + near call builds a far frame only in real mode; far transfers stop in the flat model.
                    push_cs = (not image.flat and m == "call" and previous is not None and previous.mnemonic == "push"
                               and previous.size + state.path[-2] == at and previous.operands[0].type == X86_OP_REG
                               and previous.reg_name(previous.operands[0].reg) == "cs" and previous.operands[0].size == 2)
                    model = next((x for x in models if x["site"] == at), None)
                    if model:
                        return_bytes = model.get("returnBytes", 4 if m == "lcall" else image.bits // 8)
                        # The encoding before the call decides validity; a path that reaches the call
                        # without executing that push only stops, it does not invalidate the model.
                        encoded_push_cs = at > 0 and image.region(at - 1) is image.region(at) and image.data[at - 1] == 0x0E
                        if (return_bytes != 4 if image.flat else
                                (m == "lcall" and return_bytes != 4) or (m == "call" and return_bytes == 4 and not encoded_push_cs)):
                            raise ValueError("Modeled return width differs from the encoded call frame")
                        if not image.flat and return_bytes == 4 and m == "call" and not push_cs:
                            raise StopPath("four-byte call model reached without an immediately executed push cs")
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
                            child.clear_memory()
                            child.forget_flags()
                            child.direction_flag = unknown(f"modeled-call:{at}:DF:{child.flag_serial}", 1, at)
                            child.interrupt_flag = unknown(f"modeled-call:{at}:IF:{child.flag_serial}", 1, at)
                            for r, n in case.get("registers", {}).items():
                                child.setreg(r, const(n, ALIASES[r][2], at), at)
                            child.conditional.append({"site": at, "evidence": model["evidence"],
                                                      "assumption": "call returns with balanced stack; memory effects unresolved"})
                            child.event("call-return", callSite=at, callerEntry=state.frames[-1]["entry"],
                                        resultContracts=result_contracts(child, contracts, target), registers=snapshot(child), modeled=True,
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
                    return_ip = (here["ip"] + following - here["start"]) & image.mask
                    if m == "lcall":
                        state.push(state.reg("cs"))
                    state.push(const(return_ip, image.bits, at))
                    flags_frame = False
                    if push_cs:
                        # Inspect the word above CS without reporting a read the program never made.
                        try:
                            flags_word = state.peek(state.segment("ss"), op("add", state.reg(state.sp), const(4, 16), at), 2)
                            flags_frame = (16, flags_word.term) in state.saved_flags
                        except StopPath:
                            flags_frame = False
                    state.frames.append({"entry": target, "sp": state.reg(state.sp), "returnBytes": 4 if m == "lcall" or push_cs else image.bits // 8,
                                         "frameSource": "push-CS/near-call; matching far return required" if push_cs else m,
                                         "continuation": following, "returnIP": return_ip, "callSite": at,
                                         "callerCS": state.reg("cs"), "localFlagsFrame": flags_frame})
                    if m == "lcall":
                        state.setreg("cs", const(target_region["segment"], 16, at), at)
                    state.at = target
                    continue
                if m in ("iret", "iretd"):
                    if image.flat or 0x66 in ins.prefix or m != "iret":
                        raise StopPath("IRET requires an unprefixed segmented16 local frame")
                    frame = state.frames[-1]
                    if len(state.frames) == 1 or not frame.get("localFlagsFrame"):
                        raise StopPath("IRET requires a traced local push-CS call above saved FLAGS")
                    if state.reg(state.sp).term != frame["sp"].term:
                        raise StopPath("IRET stack balance differs from the local call")
                    actual_ip, actual_cs = state.pop(2), state.pop(2)
                    if actual_ip.number != frame["returnIP"] or actual_cs.term != frame["callerCS"].term:
                        raise StopPath("IRET return target or segment was overwritten or unresolved")
                    state.setreg("cs", actual_cs, at)
                    state.restore_flags(16)
                    state.event("local-iret", continuation=frame["continuation"],
                                interpretation="local stack/flags transfer only; no interrupt or hardware simulation")
                    state.frames.pop()
                    state.event("call-return", callSite=frame["callSite"], registers=snapshot(state), modeled=False)
                    state.at = frame["continuation"]
                    continue
                if m in ("ret", "retf"):
                    if image.flat and m == "retf":
                        raise StopPath("Far return is outside the PE32 flat model")
                    frame = state.frames[-1]
                    roles = result_contracts(state, contracts, frame["entry"])
                    state.event("return", registers=snapshot(state), cleanupBytes=ins.operands[0].imm if ins.operands else 0,
                                resultContracts=roles, callSite=frame.get("callSite"),
                                callerEntry=state.frames[-2]["entry"] if len(state.frames) > 1 else None)
                    # The entry frame gets the same width and balance checks as a traced call.
                    expected = 4 if m == "retf" else image.bits // 8
                    if expected != frame["returnBytes"] or state.reg(state.sp).term != frame["sp"].term:
                        raise StopPath("return frame or stack balance differs from the call")
                    if len(state.frames) == 1:
                        finish(state, returned=True)
                        break
                    actual_ip = state.pop(image.bits // 8)
                    if actual_ip.number != frame["returnIP"]:
                        raise StopPath("return target was overwritten or has unknown provenance")
                    if m == "retf":
                        actual_cs = state.pop(2)
                        if actual_cs.term != frame["callerCS"].term:
                            raise StopPath("far return segment changed")
                        state.setreg("cs", actual_cs, at)
                    if ins.operands:
                        state.setreg(state.sp, op("add", state.reg(state.sp), const(ins.operands[0].imm, image.bits), at), at)
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
                if m.startswith("j") or m.startswith("loop"):
                    target, _ = call_target(image, at, ins)
                    if m in ("jcxz", "jecxz") or m.startswith("loop"):
                        answer, info, key = counter_branch(state, ins)
                        negated = False
                    else:
                        answer, info = predicate(state, m)
                        condition, negated = BRANCH_CONDITIONS.get(m, (m, False))
                        if condition == "c":
                            # CF can outlive its producer (INC/DEC, CLC/STC, shifts), so key it by its own value.
                            key = repr((condition, state.carry_value().term))
                        else:
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
            "nativeReachability": "unconfirmed", "stepsUsed": total_steps, "stringIterationsUsed": total_string_steps,
            "limits": {"steps": max_steps, "paths": max_paths, "depth": max_depth}}
