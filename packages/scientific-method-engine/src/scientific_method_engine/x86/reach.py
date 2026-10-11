"""Transitive reachability from established starts to target sites over the entry-path CFG."""
from collections import deque
from .image import integer
from .trace import (walk, cfg_step, base_mnemonic, unsupported_transfer, holding_instruction, INTERRUPTS, RETURNS,
                    LIMIT_REASON, OVERLAP_REASON, UNDECODED_REASON)
from .pcode_backend import interrupt_vector
from .dispatch import indirect_call_declarations, call_ends

# The virtual root of the dominator computation; no file offset is negative.
ROOT = -1
CALLS = ("call", "lcall")
UNSUPPORTED = "unsupported control-transfer frame encoding"
LEAF_OVERLAP = "leaf start inside a reached instruction; the leaf is not decoded, so its boundary is unchecked"


def _text(ins):
    return (ins.mnemonic + " " + ins.op_str).strip()


def _sites(value, label, image, limit=256, least=1):
    if not isinstance(value, list) or not least <= len(value) <= limit or len(set(map(repr, value))) != len(value):
        raise ValueError(f"{label} must be a list of {least}..{limit} distinct file offsets")
    for at in value:
        integer(at, 0, len(image.data) - 1, label)
        if image.region(at) is None:
            raise ValueError(f"{label} {at} is outside declared code")
    return list(value)


def _leaves(value, image, starts):
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("leaves must be a list of at most 256 objects")
    leaves = {}
    for leaf in value:
        if not isinstance(leaf, dict) or set(leaf) != {"routine", "reason"}:
            raise ValueError("Each leaf needs exactly routine and reason")
        at = integer(leaf["routine"], 0, len(image.data) - 1, "leaf routine")
        if image.region(at) is None:
            raise ValueError(f"leaf routine {at} is outside declared code")
        if not isinstance(leaf["reason"], str) or not leaf["reason"].strip():
            raise ValueError("A leaf needs a nonempty reason")
        if at in leaves:
            raise ValueError("Duplicate leaf routine")
        if at in starts:
            raise ValueError("A start cannot be a leaf")
        leaves[at] = leaf["reason"]
    return leaves


def no_return_declarations(value, image):
    """The declared routines and interrupt sites that never return, each with its reason."""
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("noReturn must be a list of at most 256 objects")
    routines, interrupts = {}, {}
    for entry in value:
        if not isinstance(entry, dict) or set(entry) not in ({"routine", "reason"}, {"interrupt", "reason"}):
            raise ValueError("Each noReturn entry needs reason and exactly one of routine and interrupt")
        kind = "routine" if "routine" in entry else "interrupt"
        at = integer(entry[kind], 0, len(image.data) - 1, f"noReturn {kind}")
        if image.region(at) is None:
            raise ValueError(f"noReturn {kind} {at} is outside declared code")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValueError("A noReturn entry needs a nonempty reason")
        found = routines if kind == "routine" else interrupts
        if at in found:
            raise ValueError(f"Duplicate noReturn {kind}")
        if kind == "interrupt":
            ins = image.decode(at)
            if ins is None or base_mnemonic(ins) not in INTERRUPTS:
                raise ValueError(f"noReturn interrupt {at} is not an interrupt instruction")
            vector, conditional = interrupt_vector(image.flat, ins, at)
            if conditional:
                raise ValueError(f"noReturn interrupt {at} is conditional and continues when it does not interrupt")
            found[at] = {"reason": entry["reason"], "vector": vector, "following": at + ins.size}
            continue
        found[at] = entry["reason"]
    return routines, interrupts


def returns_from(graph, seen, returning_leaves, routine):
    """Where ``routine``'s own paths return: each return instruction, and each leaf they enter other than by a call.

    Calls are stepped over at the return sites the graph keeps. The walk assumes that a leaf returns,
    so a jump, branch, table row or fall-through into one in ``returning_leaves`` returns too.
    """
    found, stack, visited = [], [routine], set()
    while stack:
        at = stack.pop()
        if at in visited:
            continue
        visited.add(at)
        if at in returning_leaves:
            found.append(at)
            continue
        if at not in seen:
            continue
        if base_mnemonic(seen[at]) in RETURNS:
            found.append(at)
        stack.extend(s for s, kind in graph.get(at, []) if kind != "call")
    return sorted(found)


def successor_kinds(at, ins, step):
    """Each successor of ``step`` with how the CFG reaches it: call, return, table, interrupt, jump or fall."""
    m, following = base_mnemonic(ins), at + ins.size
    # A call lists its resolved or declared targets first, then its return site; a call to the next
    # instruction lists that site twice.
    targets = {e["target"] for e in step.edges if e["target"] is not None}
    calls = sum(e["target"] is not None for e in step.edges) if m in CALLS else 0
    rows = []
    for successor in step.successors:
        if m in CALLS:
            kind = "call" if len(rows) < calls else "return"
        elif step.supplied:
            kind = "table"
        elif m in INTERRUPTS:
            kind = "interrupt"
        elif successor == following and m not in ("jmp", "ljmp") and (successor not in targets or len(rows) > 0):
            kind = "fall"
        else:
            kind = "jump"
        rows.append((successor, kind))
    return rows


def successor_graph(image, seen, **step):
    """Each site of ``seen`` mapped to its ``successor_kinds`` rows that stay in ``seen``, under ``cfg_step``'s ``step`` options."""
    return {at: [(s, kind) for s, kind in successor_kinds(at, ins, cfg_step(image, at, ins, **step)) if s in seen]
            for at, ins in seen.items()}


def no_return_rows(routines, interrupts, returns, call_sites, seen, reached, read):
    """The ``noReturn`` report rows of the declared ``routines`` and ``interrupts``.

    ``returns`` gives each routine's return sites, ``call_sites`` the reached calls to each routine,
    ``seen`` the instructions the calls and interrupts were read in, ``reached`` the sites a walk from
    the starts reached and ``read`` the sites the return check decoded.
    """
    rows = [{"routine": at, "reason": reason, "reached": at in reached, "read": at in read,
             "returnSites": returns[at], "contradicted": bool(returns[at]),
             "callSites": [{"site": site, "following": site + seen[site].size,
                            "followingRead": site + seen[site].size in seen}
                           for site in call_sites.get(at, [])]}
            for at, reason in routines.items()]
    return rows + [{"interrupt": at, "reason": row["reason"], "vector": row["vector"], "reached": at in seen,
                    "following": row["following"], "followingRead": row["following"] in seen}
                   for at, row in interrupts.items()]


def fewest_calls(graph, starts):
    """``(distance, parent, routine)`` for every site ``graph`` reaches from ``starts`` by the route with the fewest calls.

    ``graph`` maps a site to its ``successor_kinds`` rows. ``distance`` counts the calls on the route,
    ``parent`` gives each site's ``(predecessor, kind)`` on it (None at a start), and ``routine`` the
    target of the route's last call, or the start.
    """
    # Entering a callee costs one, every other edge nothing (0-1 breadth-first search).
    distance, parent, routine = {}, {}, {}
    queue = deque()
    for at in starts:
        distance[at], parent[at], routine[at] = 0, None, at
        queue.append((0, at))
    while queue:
        cost, at = queue.popleft()
        if cost > distance[at]:
            continue
        for successor, kind in graph.get(at, []):
            step_cost = cost + (kind == "call")
            if successor not in distance or step_cost < distance[successor]:
                distance[successor], parent[successor] = step_cost, (at, kind)
                routine[successor] = successor if kind == "call" else routine[at]
                (queue.append if kind == "call" else queue.appendleft)((step_cost, successor))
    return distance, parent, routine


def decoded_ranges(seen, routine):
    """The instructions in ``seen`` as half-open ``[start, end)`` runs, each with the ``routine`` they were read in.

    A run holds instructions that abut, each starting where the one before it ends, and that
    ``routine`` gives the same routine. A site on no route from a start has the routine None. Runs
    overlap only where ``seen`` holds instructions that overlap, which the walk keeps when it proves
    both boundaries.
    """
    # Past an overlap, the run an instruction continues need not be the last one opened, so each open
    # run is found by where it ends and in which routine.
    ranges, ending = [], {}
    for at, ins in sorted(seen.items()):
        owner = routine.get(at)
        run = ending.pop((at, owner), None)
        if run is None:
            run = {"start": at, "end": at, "routine": owner}
            ranges.append(run)
        run["end"] = at + ins.size
        ending[(run["end"], owner)] = run
    return ranges


def _dominators(order, predecessors):
    """Immediate dominators (Cooper, Harvey and Kennedy) of every node in ``order``, a reverse postorder from ROOT."""
    index = {node: i for i, node in enumerate(order)}
    idom = {ROOT: ROOT}

    def intersect(a, b):
        while a != b:
            while index[a] > index[b]:
                a = idom[a]
            while index[b] > index[a]:
                b = idom[b]
        return a
    changed = True
    while changed:
        changed = False
        for node in order[1:]:
            done = [p for p in predecessors[node] if p in idom]
            new = done[0]
            for p in done[1:]:
                new = intersect(p, new)
            if idom.get(node) != new:
                idom[node] = new
                changed = True
    return idom


def reach(image, config):
    """Which target sites the starts reach over resolved calls and jumps, by which chain, and what the walk could not read.

    The walk decodes from ``starts`` (established region entries) and follows every resolved call
    into its callee and on at its return site, every resolved jump and branch, the rows of a
    declared indirect jump table, the declared targets of an ``indirectCalls`` site, and every
    interrupt to the next instruction. A ``leaves`` routine is reached but never decoded. A call to a
    ``noReturn`` routine and a ``noReturn`` interrupt site do not continue at the next instruction,
    nor does a declared indirect call that is exhaustive and whose targets are all ``noReturn``.
    Each reached target gets one chain with the fewest calls, the assumptions its route rests on,
    and the routines on every route to it within the read graph. With ``decodedRanges`` the report
    also gives every instruction the walk decoded, as ``decoded_ranges`` groups them.
    """
    from .reports import entries
    starts = _sites(config.get("starts"), "starts", image)
    established = set(entries(image))
    for at in starts:
        if at not in established:
            raise ValueError(f"Reach start {at} must be an established region entry")
    targets = _sites(config.get("targets"), "targets", image)
    leaves = _leaves(config.get("leaves", []), image, set(starts))
    controls = _sites(config.get("controls", []), "controls", image, least=0)
    instruction_controls = _sites(config.get("instructionControls", []), "instructionControls", image, least=0)
    indirect_calls = indirect_call_declarations(config.get("indirectCalls", []), image)
    # A control shows that the walk resolved a call from its own encoding, which a declaration does not.
    for at in controls:
        if at in indirect_calls:
            raise ValueError(f"control {at} is a declared indirect call, whose targets rest on its declaration; give "
                             "the site as an instructionControls entry, or a call inside a declared target as a control")
    # The walk decodes a site as image.decode does, so a control whose bytes are no call can never pass.
    for at in controls:
        ins = image.decode(at)
        if ins is None or base_mnemonic(ins) not in CALLS:
            decoded = f"decodes as {_text(ins)}" if ins is not None else "does not decode"
            raise ValueError(f"control {at} is not a call site ({decoded}); controls are call sites the walk must "
                             "reach and resolve, and instructionControls are sites the walk must decode")
    # The walk decodes every start it is given, so a start as an instruction control would pass whatever the walk misses.
    for at in instruction_controls:
        if at in starts:
            raise ValueError(f"instruction control {at} is a start; the walk decodes every start, so it shows nothing "
                             "the walk found")
    limit = integer(config.get("limit", 1000), 1, 10000, "result limit")
    with_ranges = config.get("decodedRanges", False)
    if not isinstance(with_ranges, bool):
        raise ValueError("decodedRanges must be a boolean")
    instruction_limit = config.get("instructionLimit", 10000)
    no_return_routines, no_return_interrupts = no_return_declarations(config.get("noReturn", []), image)
    declared = {"no_return_calls": frozenset(no_return_routines), "no_return_interrupts": frozenset(no_return_interrupts),
                "indirect_calls": indirect_calls}
    seen, walk_gaps, _, _, contested = walk(image, starts, instruction_limit, follow_interrupts=True,
                                            stops=frozenset(leaves), **declared)

    # Each reached call site with its resolved or declared targets.
    graph, unresolved, interrupts, call_targets = {}, [], [], {}
    for at, ins in sorted(seen.items()):
        step = cfg_step(image, at, ins, follow_interrupts=True, **declared)
        graph[at] = [(s, kind) for s, kind in successor_kinds(at, ins, step) if s in seen or s in leaves]
        m = base_mnemonic(ins)
        text = _text(ins)
        kind = "call" if m in CALLS else "return" if m in RETURNS else "jump"
        if unsupported_transfer(image, ins):
            unresolved.append({"site": at, "instruction": text, "kind": kind, "reason": UNSUPPORTED})
        for edge in step.edges:
            if edge["target"] is None:
                unresolved.append({"site": at, "instruction": text, "kind": kind,
                                   "reason": edge["provenance"].get("reason", "target outside declared regions")})
            elif m in CALLS:
                call_targets.setdefault(at, []).append(edge["target"])
        if m in INTERRUPTS and at not in no_return_interrupts:
            vector, conditional = interrupt_vector(image.flat, ins, at)
            interrupts.append({"site": at, "instruction": text, "vector": vector, "conditional": conditional})
    for at in leaves:
        graph.setdefault(at, [])

    distance, parent, routine = fewest_calls(graph, starts)

    # Dominators over everything reached, from a virtual root that leads to every start.
    successors = {ROOT: list(starts)} | {at: [s for s, _ in graph.get(at, [])] for at in distance}
    order, visited, stack = [], {ROOT}, [(ROOT, iter(successors[ROOT]))]
    while stack:
        node, children = stack[-1]
        child = next(children, None)
        if child is None:
            order.append(node)
            stack.pop()
        elif child not in visited:
            visited.add(child)
            stack.append((child, iter(successors[child])))
    order.reverse()
    predecessors = {node: [] for node in order}
    for node in order:
        for child in successors[node]:
            predecessors[child].append(node)
    idom = _dominators(order, predecessors)
    routine_starts = set(starts) | {target for found in call_targets.values() for target in found if target in distance}

    def chain(target):
        hops, assumed_returns, tables, declared_calls, continued = [], [], [], [], []
        at = target
        while parent[at] is not None:
            previous, kind = parent[at]
            if kind == "call":
                hops.append({"callSite": previous, "routine": at})
                if previous in indirect_calls:
                    declared_calls.append({"site": previous, "target": at})
            elif kind == "return":
                assumed_returns.append(previous)
            elif kind == "table":
                tables.append({"site": previous, "target": at})
            elif kind == "interrupt":
                continued.append(previous)
            at = previous
        hops.append({"routine": at})
        return {"chain": hops[::-1], "calls": distance[target],
                "route": {"assumedReturns": assumed_returns[::-1], "declaredTableJumps": tables[::-1],
                          "declaredCalls": declared_calls[::-1], "interruptsContinued": continued[::-1]}}

    # The walk decodes both instructions of an unresolved overlap and keeps neither, so their starts are not in seen.
    overlapping = {g["site"] for g in walk_gaps if g["reason"] == OVERLAP_REASON}
    # A site an edge leads to whose bytes do not decode is reached, though no instruction starts there.
    undecodable = {g["site"] for g in walk_gaps if g["reason"] == UNDECODED_REASON}

    def unreached(site):
        holder = holding_instruction(seen, site)
        if holder is not None:
            return {"status": "inside a reached instruction", "insideInstruction": holder}
        return {"status": "start of a contested instruction" if site in contested
                else "start of an unresolved overlapping instruction" if site in overlapping
                else "reached but not decodable" if site in undecodable else "not reached"}

    rows = []
    for target in targets:
        row = {"target": target}
        if target in distance:
            cut, at = [], idom[target]
            while at != ROOT:
                if at in routine_starts:
                    cut.append(at)
                at = idom[at]
            row |= {"reached": True, "routine": routine[target], "leaf": target in leaves, **chain(target),
                    "throughEveryRoute": cut[::-1]}
        else:
            row |= {"reached": False, **unreached(target)}
        rows.append(row)

    for row in unresolved + interrupts:
        row["routine"] = routine.get(row["site"])
    unresolved_sites = {row["site"] for row in unresolved}
    gaps = [g for g in walk_gaps if g["site"] not in unresolved_sites and g["site"] not in contested]
    # The walk never decodes a leaf, so its overlap check cannot see a leaf start inside a decoded instruction.
    gaps += [{"site": at, "reason": LEAF_OVERLAP, "insideInstruction": holding_instruction(seen, at)}
             for at in sorted(leaves) if at in distance and holding_instruction(seen, at) is not None]
    # Kept apart from the gap rows, which the result limit can cut.
    stopped = any(g["reason"] == LIMIT_REASON for g in walk_gaps)
    # A control at a site a noReturn declaration kept the walk from names that declaration, which may be the cause.
    def ends(at, found):
        return call_ends(at, found[0], indirect_calls, no_return_routines)
    cut_by = {at + seen[at].size: f"the call at {at} to noReturn routine{'s' if len(found) > 1 else ''} "
                                  + ", ".join(map(str, found))
              for at, found in sorted(call_targets.items()) if ends(at, found)}
    cut_by |= {row["following"]: f"the noReturn interrupt at {at}"
               for at, row in no_return_interrupts.items() if at in seen}
    # Not reached and reached-but-unresolved need different fixes, so each failure says which it is.
    failures = []
    for label, sites, passed in (("control", controls, call_targets), ("instruction control", instruction_controls, seen)):
        for at in sites:
            if at in passed:
                continue
            # Only a call-site control can be decoded and still fail.
            if at in seen:
                reasons = sorted({row["reason"] for row in unresolved if row["site"] == at})
                failures.append(f"{label} {at} is reached but its call target is unresolved ({'; '.join(reasons)})")
            elif at in leaves and at in distance:
                failures.append(f"{label} {at} is the start of a leaf, which the walk reaches but does not decode")
            else:
                state = unreached(at)
                detail = (f"inside the reached instruction at {state['insideInstruction']}" if "insideInstruction" in state
                          else "the " + state["status"] if state["status"].startswith("start") else state["status"])
                cut = cut_by.get(at)
                failures.append(f"{label} {at} is {detail}"
                                + (f" (it follows {cut})" if cut else "")
                                + (" (the walk stopped at its instruction limit)" if stopped else ""))
    if failures:
        raise ValueError("Positive controls failed: " + "; ".join(failures))
    # Every call to each routine, and the calls to a noReturn routine that the declaration ended.
    call_sites, ended_sites = {}, {}
    for site, found in sorted(call_targets.items()):
        ended = ends(site, found)
        for target in found:
            call_sites.setdefault(target, []).append(site)
            if ended:
                ended_sites.setdefault(target, []).append(site)
    leaf_rows = [{"routine": at, "reason": reason, "reached": at in distance, "callSites": call_sites.get(at, [])}
                 for at, reason in leaves.items()]
    # Each call the declaration kept from its return site, so a reviewer can check what follows it.
    returning_leaves = set(leaves) - set(no_return_routines)
    returns = {at: returns_from(graph, seen, returning_leaves, at) for at in no_return_routines}
    no_return = no_return_rows(no_return_routines, no_return_interrupts, returns, ended_sites, seen, distance, seen)
    contradicted = any(row.get("contradicted") for row in no_return)
    # A declared target the walk entered but established no instruction at shows in gaps or contested too,
    # unless the walk stopped at its instruction limit before reading it.
    indirect_rows = [declaration | {"reached": site in seen, "routine": routine.get(site),
                                    "unreadTargets": [t for t in declaration["targets"]
                                                      if site in seen and t not in seen and t not in leaves]}
                     for site, declaration in indirect_calls.items()]
    counts = {"routines": len(routine_starts), "instructions": len(seen), "unresolved": len(unresolved),
              "interrupts": len(interrupts), "gaps": len(gaps), "contested": len(contested)}
    assumptions = ["each reached call returns to its next instruction"
                   + (", except a call to a noReturn routine" if no_return_routines else ""),
                   "each reached interrupt returns to its next instruction"
                   + (", except at a noReturn interrupt site" if no_return_interrupts else "")
                   + "; interrupt handlers are not read",
                   "each leaf calls nothing, for the reason it gives",
                   "each declared indirect jump table holds the routes its declaration gives"]
    if indirect_calls:
        assumptions.append("each declared indirect call can call the targets its declaration gives, for the evidence "
                           "it gives, and only those when it is declared exhaustive")
    if no_return:
        assumptions.append("each noReturn routine and interrupt never returns, for the reason it gives")
    # Every range is kept whatever the result limit, so a caller can place any site in the walk; the
    # instruction limit bounds their number.
    ranges = {"decodedRanges": decoded_ranges(seen, routine)} if with_ranges else {}
    return {"starts": starts, "targets": rows, "leaves": leaf_rows, "noReturn": no_return, "indirectCalls": indirect_rows,
            "reachedRoutines": sorted(routine_starts), **ranges, "counts": counts,
            "unresolved": unresolved[:limit], "interrupts": interrupts[:limit], "gaps": gaps[:limit],
            "contested": sorted(contested)[:limit],
            "truncated": any(len(x) > limit for x in (unresolved, interrupts, gaps, contested)),
            "instructionLimitReached": stopped,
            "controls": [{"site": at, "target": call_targets[at][0]} for at in controls],
            "instructionControls": [{"site": at, "instruction": _text(seen[at]), "routine": routine.get(at)}
                                    for at in instruction_controls],
            "negativeUsable": bool(controls or instruction_controls)
                              and not (stopped or unresolved or gaps or contested or contradicted),
            "assumptions": assumptions,
            "exclusions": ["computed call and jump targets no declaration gives (listed in unresolved)", "unrelocated far calls",
                           "code reached only from outside the starts", "runtime reachability"],
            "interpretation": "Routes over the decoded entry-path CFG from the starts. A chain has the fewest calls; "
                              "throughEveryRoute lists the routine starts every read route to the target passes, "
                              "and an unresolved transfer may add a route that passes none of them. "
                              "negativeUsable needs a control of either kind, a walk that did not stop at its instruction limit, "
                              "and no unresolved transfer, gap, contested instruction or contradicted noReturn "
                              "routine, and still rests on the listed assumptions, leaves and noReturn declarations. "
                              "A declared indirect call counts as resolved when it is declared exhaustive: its table "
                              "rows are read from the build's bytes and its targets list rests on its evidence alone, "
                              "and neither proves an instruction boundary or that the call runs. "
                              "A noReturn routine is contradicted when a return instruction, or a leaf entered "
                              "other than by a call, lies on its own read paths; an empty returnSites means only "
                              "that the walk read none. "
                              "decodedRanges, when asked for, covers the instructions the walk decoded; no range "
                              "starts at a leaf or at a contested, unresolved overlapping or undecodable start, though "
                              "one can lie inside a decoded instruction. A site inside a range lies in a decoded "
                              "instruction, which may start before it; a target at the site shows whether one starts "
                              "there. "
                              "When instructionLimitReached holds, the walk stopped before reading all it reaches: "
                              "every list and count covers only the part read, which part depends on the walk order, "
                              "and an unreached target may lie past the stop."}
