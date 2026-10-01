"""Ordered, path-local effect evidence; no transactional or native-execution claims."""
from bisect import bisect_right


KINDS = {"read", "write", "call", "call-return", "return", "branch", "compare", "flag-assumption",
         "arithmetic", "value-transfer", "conversion", "flag-write", "flags-save", "flags-restore", "local-iret", "string-operation"}


def _storage(event):
    # Equal offsets alone cannot identify storage. Retain the segment/base expression
    # and the complete access width; a narrower restore is a separate access.
    if not all(k in event for k in ("segment", "offset", "width")):
        return None
    # Terms are hashable tuples; compare them directly instead of serializing each access.
    return (event["segment"].get("expression"), event["offset"].get("expression"), event["width"],
            event.get("segmentInterpretation"))


def effect_ordering(report):
    """Add bounded path timelines and explicit local restoration witnesses.

    The existing tracer bounds every event. Prefix counts index a path's write
    list instead of copying that list for every call (linear report size). A
    witness restores one read value only; it says nothing about other storage,
    aliases, external effects, resources or transactionality.
    """
    summaries = []
    for index, path in enumerate(report["paths"]):
        timeline, writes, calls, witnesses = [], [], [], []
        snapshots = {}
        pending = {}
        unknown_orders = []
        last_write = {}
        for event in path["events"]:
            kind = event["kind"]
            if kind in KINDS:
                timeline.append(event)
            if kind == "read":
                key = _storage(event)
                if key is not None:
                    snapshots.setdefault(key, {})[event["value"].get("expression")] = event
            elif kind == "write":
                key = _storage(event)
                original = snapshots.get(key, {}).get(event["value"].get("expression"))
                # An equal expression alone is not a restore: an independent constant store or a
                # re-pushed return address can match it. The written value must derive from the read.
                if (original is not None and last_write.get(key, -1) > original["order"]
                        and original["site"] in event["value"].get("producers", ())):
                    witnesses.append({"readOrder": original["order"], "restoreOrder": event["order"],
                                      "entry": event["entry"], "width": event["width"],
                                      "pathReturned": path["returned"],
                                      "unknownEffectsBetweenCount": len(unknown_orders) - bisect_right(unknown_orders, original["order"]),
                                      "meaning": "same complete storage/value expression restored locally; not transactionality"})
                writes.append(event)
                last_write[key] = event["order"]
            elif kind == "call":
                call = {"order": event["order"], "site": event["site"], "entry": event["entry"],
                        "depth": event["depth"], "target": event.get("target"),
                        "writesBeforeCount": len(writes), "status": "unresolved-or-stopped",
                        "unknownEffects": True, "continuation": None, "_unknownStart": len(unknown_orders)}
                calls.append(call)
                pending.setdefault((event["site"], event["depth"]), []).append(call)
            elif kind == "call-return":
                stack = pending.get((event["callSite"], event["depth"]), [])
                if stack:
                    call = stack.pop()
                    modeled = event.get("modeled", False)
                    call.update(status="modeled-return" if modeled else "traced-return",
                                unknownEffects=bool(modeled or event.get("unknownMemoryEffects") or len(unknown_orders) > call.pop("_unknownStart")),
                                returnOrder=event["order"], writesAfterCount=len(writes),
                                continuation="assumes balanced returning service; its memory/flag effects are unknown" if modeled
                                else "local callee return reached within the instruction model")
                if event.get("modeled") or event.get("unknownMemoryEffects"):
                    unknown_orders.append(event["order"])
        for call in calls:
            call.pop("_unknownStart", None)
        boundary = None
        if path.get("stop"):
            boundary = {"site": path.get("stopSite"), "reason": path["stop"],
                        "writesBeforeCount": len(writes), "meaning": "later effects are not read"}
        summaries.append({"path": index, "returned": path["returned"], "stop": boundary,
                          "guards": path["guards"], "timeline": timeline,
                          "writeOrders": [w["order"] for w in writes], "calls": calls,
                          "localRestorationWitnesses": witnesses,
                          "effectCompleteWithinModel": bool(path["returned"] and not unknown_orders),
                          "transactionality": "not established; local writes and result codes cannot prove external rollback"})
    report["effectOrdering"] = {"paths": summaries,
                                "allPathsRead": report["completeWithinModel"],
                                "nativeReachability": "unconfirmed",
                                "meaning": "separate conditional paths; write prefixes index each path's writeOrders"}
    return report
