"""Transitive reachability from established starts to target sites over the entry-path CFG."""
from collections import deque
from .image import integer
from .trace import walk, cfg_step, base_mnemonic, unsupported_transfer, holding_instruction, INTERRUPTS, RETURNS
from .pcode_backend import interrupt_vector

# The virtual root of the dominator computation; no file offset is negative.
ROOT = -1
CALLS = ("call", "lcall")
UNSUPPORTED = "unsupported control-transfer frame encoding"
LEAF_OVERLAP = "leaf start inside a reached instruction; the leaf is not decoded, so its boundary is unchecked"


def _sites(value, label, image, limit=256):
    if not isinstance(value, list) or not 1 <= len(value) <= limit or len(set(map(repr, value))) != len(value):
        raise ValueError(f"{label} must be a list of 1..{limit} distinct file offsets")
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


def _kinds(at, ins, step):
    """Each successor of ``step`` with how the CFG reaches it: call, return, table, interrupt, jump or fall."""
    m, following = base_mnemonic(ins), at + ins.size
    targets = {e["target"] for e in step.edges if e["target"] is not None}
    rows, called = [], False
    for successor in step.successors:
        if step.supplied:
            kind = "table"
        elif m in CALLS:
            # A call names its target first; a call to the next instruction lists that site twice.
            kind = "call" if successor in targets and not called else "return"
            called = called or kind == "call"
        elif m in INTERRUPTS:
            kind = "interrupt"
        elif successor == following and m not in ("jmp", "ljmp") and (successor not in targets or len(rows) > 0):
            kind = "fall"
        else:
            kind = "jump"
        rows.append((successor, kind))
    return rows


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
    declared indirect jump table, and every interrupt to the next instruction. A ``leaves`` routine
    is reached but never decoded. Each reached target gets one chain with the fewest calls, the
    assumptions its route rests on, and the routines on every route to it within the read graph.
    """
    from .reports import entries
    starts = _sites(config.get("starts"), "starts", image)
    established = set(entries(image))
    for at in starts:
        if at not in established:
            raise ValueError(f"Reach start {at} must be an established region entry")
    targets = _sites(config.get("targets"), "targets", image)
    leaves = _leaves(config.get("leaves", []), image, set(starts))
    controls = config.get("controls", [])
    if (not isinstance(controls, list) or len(controls) > 256 or any(type(at) is not int for at in controls)
            or len(set(controls)) != len(controls)):
        raise ValueError("Positive controls must be at most 256 distinct file offsets")
    limit = integer(config.get("limit", 1000), 1, 10000, "result limit")
    instruction_limit = config.get("instructionLimit", 10000)
    seen, walk_gaps, _, _, contested = walk(image, starts, instruction_limit, follow_interrupts=True, stops=frozenset(leaves))

    graph, unresolved, interrupts, resolved_calls = {}, [], [], {}
    for at, ins in sorted(seen.items()):
        step = cfg_step(image, at, ins, follow_interrupts=True)
        graph[at] = [(s, kind) for s, kind in _kinds(at, ins, step) if s in seen or s in leaves]
        m = base_mnemonic(ins)
        text = (ins.mnemonic + " " + ins.op_str).strip()
        kind = "call" if m in CALLS else "return" if m in RETURNS else "jump"
        if unsupported_transfer(image, ins):
            unresolved.append({"site": at, "instruction": text, "kind": kind, "reason": UNSUPPORTED})
        for edge in step.edges:
            if edge["target"] is None:
                unresolved.append({"site": at, "instruction": text, "kind": kind,
                                   "reason": edge["provenance"].get("reason", "target outside declared regions")})
            elif m in CALLS:
                resolved_calls[at] = edge["target"]
        if m in INTERRUPTS:
            vector, conditional = interrupt_vector(image.flat, ins, at)
            interrupts.append({"site": at, "instruction": text, "vector": vector, "conditional": conditional})
    for at in leaves:
        graph.setdefault(at, [])

    # Fewest calls first: entering a callee costs one, every other edge nothing (0-1 breadth-first search).
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
    routine_starts = set(starts) | {target for at, target in resolved_calls.items() if target in distance}

    def chain(target):
        hops, assumed_returns, tables, continued = [], [], [], []
        at = target
        while parent[at] is not None:
            previous, kind = parent[at]
            if kind == "call":
                hops.append({"callSite": previous, "routine": at})
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
                          "interruptsContinued": continued[::-1]}}

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
            holder = holding_instruction(seen, target)
            row |= {"reached": False, "status": "inside a reached instruction" if holder is not None
                    else "start of a contested instruction" if target in contested else "not reached",
                    **({"insideInstruction": holder} if holder is not None else {})}
        rows.append(row)

    for row in unresolved + interrupts:
        row["routine"] = routine.get(row["site"])
    unresolved_sites = {row["site"] for row in unresolved}
    gaps = [g for g in walk_gaps if g["site"] not in unresolved_sites and g["site"] not in contested]
    # The walk never decodes a leaf, so its overlap check cannot see a leaf start inside a decoded instruction.
    gaps += [{"site": at, "reason": LEAF_OVERLAP, "insideInstruction": holding_instruction(seen, at)}
             for at in sorted(leaves) if at in distance and holding_instruction(seen, at) is not None]
    for at in controls:
        if at not in resolved_calls:
            raise ValueError(f"Positive control {at} missed or not resolved")
    call_sites = {}
    for site, target in sorted(resolved_calls.items()):
        call_sites.setdefault(target, []).append(site)
    leaf_rows = [{"routine": at, "reason": reason, "reached": at in distance, "callSites": call_sites.get(at, [])}
                 for at, reason in leaves.items()]
    # Kept apart from the gap rows, which the result limit can cut and the filters above can drop.
    stopped = any(g["reason"] == "instruction limit" for g in walk_gaps)
    counts = {"routines": len(routine_starts), "instructions": len(seen), "unresolved": len(unresolved),
              "interrupts": len(interrupts), "gaps": len(gaps), "contested": len(contested)}
    return {"starts": starts, "targets": rows, "leaves": leaf_rows,
            "reachedRoutines": sorted(routine_starts), "counts": counts,
            "unresolved": unresolved[:limit], "interrupts": interrupts[:limit], "gaps": gaps[:limit],
            "contested": sorted(contested)[:limit],
            "truncated": any(len(x) > limit for x in (unresolved, interrupts, gaps, contested)),
            "instructionLimitReached": stopped,
            "controls": [{"site": at, "target": resolved_calls[at]} for at in controls],
            "negativeUsable": bool(controls) and not (stopped or unresolved or gaps or contested),
            "assumptions": ["each reached call returns to its next instruction",
                            "each reached interrupt returns to its next instruction; interrupt handlers are not read",
                            "each leaf calls nothing, for the reason it gives",
                            "each declared indirect jump table holds the routes its declaration gives"],
            "exclusions": ["computed call and jump targets (listed in unresolved)", "unrelocated far calls",
                           "code reached only from outside the starts", "runtime reachability"],
            "interpretation": "Routes over the decoded entry-path CFG from the starts. A chain has the fewest calls; "
                              "throughEveryRoute lists the routine starts every read route to the target passes, "
                              "and an unresolved transfer may add a route that passes none of them. "
                              "negativeUsable needs controls and no unresolved transfer, gap or contested instruction, "
                              "and still rests on the listed assumptions and leaves. "
                              "When instructionLimitReached holds, the walk stopped before reading all it reaches: "
                              "every list and count covers only the part read, which part depends on the walk order, "
                              "and an unreached target may lie past the stop."}
