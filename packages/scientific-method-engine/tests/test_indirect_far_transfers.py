"""Indirect far CALL and JMP through a traced m16:16 pointer. Synthetic bytes only."""
import unittest

from test_x86 import events
from scientific_method_engine.x86.reports import run_report
from scientific_method_engine.x86.trace import walk
from scientific_method_engine.x86.image import Image

# The producer stores an offset and a segment at DS:0200, then calls through them.
STORE_OFFSET = "c7 06 00 02 30 00"   # mov word [0200], 0030
STORE_SEGMENT = "c7 06 02 02 00 10"  # mov word [0202], 1000
CALL = "ff 1e 00 02"                  # call far [0200]
JUMP = "ff 2e 00 02"                  # jmp far [0200]
TARGETS = 0x30
# 0030: mov ax, 42; retf   0034: mov ax, 7; retf   0038: mov ax, 42; ret
TARGET_CODE = "b8 2a 00 cb b8 07 00 cb b8 2a 00 c3"
FRAME = {"ds": 0x1000, "ss": 0x9000}


def program(*parts):
    producer = bytes.fromhex(" ".join(parts))
    assert len(producer) <= TARGETS
    return producer + bytes(TARGETS - len(producer)) + bytes.fromhex(TARGET_CODE), len(producer)


def regions(data, producer_end, resident=None):
    rows = [{"name": "producer", "start": 0, "end": producer_end, "ip": 0, "segment": 0x1000, "entries": [0],
             "evidence": "synthetic producer"},
            {"name": "target", "start": TARGETS, "end": len(data), "ip": TARGETS, "segment": 0x1000,
             "entries": [TARGETS], "evidence": "synthetic declared target"}]
    if resident is not None:
        for row in rows:
            row["resident"] = resident
    return rows


def run(*parts, command="trace", resident=None, **extra):
    data, end = program(*parts)
    config = {"regions": regions(data, end, resident), "entry": 0, "registers": {**FRAME, **extra.pop("registers", {})},
              "maxSteps": 32, "maxPaths": 2, "totalSteps": 64, "visitLimit": 4, **extra}
    return run_report(data, config, command)


def site(*parts):
    return len(bytes.fromhex(" ".join(parts)))


def only_path(test, result):
    test.assertEqual(len(result["paths"]), 1)
    return result["paths"][0]


class FollowedPointer(unittest.TestCase):
    def test_produced_pointer_enters_the_declared_target_and_returns(self):
        result = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", command="guards")
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertTrue(result["completeWithinModel"])
        self.assertEqual(path["registers"]["ax"]["value"], 42)
        call = events(result, "call")[0]
        self.assertEqual(call["site"], 12)
        self.assertEqual(call["target"], TARGETS)
        self.assertEqual(call["returnFrameBytes"], 4)
        self.assertEqual(call["indirectValue"]["value"], 0x10000030)
        provenance = call["provenance"]
        self.assertEqual(provenance["encoding"], "m16:16")
        self.assertEqual(provenance["loadedAddress"], "1000:0030")
        self.assertEqual((provenance["offsetWord"]["value"], provenance["offsetWord"]["producers"]), (0x30, [0]))
        self.assertEqual((provenance["segmentWord"]["value"], provenance["segmentWord"]["producers"]), (0x1000, [6]))
        self.assertEqual([row["writeOrder"] for row in provenance["offsetWord"]["bytes"] + provenance["segmentWord"]["bytes"]],
                         [0, 0, 1, 1])
        self.assertEqual(provenance["pointerRead"]["segmentRegister"], "ds")
        self.assertEqual(provenance["pointerRead"]["offset"]["value"], 0x200)
        self.assertEqual(provenance["admission"], {"rule": "exact declared region mapping", "region": "target",
                                                   "regionSegment": 0x1000, "regionIp": TARGETS, "regionStart": TARGETS,
                                                   "regionEvidence": "synthetic declared target"})
        self.assertNotIn("reason", provenance)
        # The read that fetched the pointer is the event the provenance cites.
        read = next(e for e in path["events"] if e["order"] == provenance["pointerRead"]["order"])
        self.assertEqual((read["kind"], read["width"]), ("read", 4))
        returned = [e for e in path["events"] if e["kind"] == "call-return"]
        self.assertEqual([(e["callSite"], e["modeled"]) for e in returned], [(12, False)])

    def test_callee_runs_under_the_segment_word_and_the_far_return_restores_cs(self):
        result = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3")
        path = only_path(self, result)
        inner = next(e for e in path["events"] if e["kind"] == "return" and e["depth"] == 1)
        self.assertEqual(inner["registers"]["cs"]["value"], 0x1000)
        self.assertIn(6, inner["registers"]["cs"]["producers"])
        self.assertEqual(path["registers"]["cs"]["value"], 0x1000)

    def test_overwriting_the_offset_word_changes_the_target(self):
        rewrite = "c7 06 00 02 34 00"
        result = run(STORE_OFFSET, STORE_SEGMENT, rewrite, CALL, "c3")
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertEqual(path["registers"]["ax"]["value"], 7)
        call = events(result, "call")[0]
        self.assertEqual(call["target"], 0x34)
        self.assertEqual(call["provenance"]["offsetWord"]["producers"], [12])
        self.assertEqual(call["provenance"]["segmentWord"]["producers"], [6])

    def test_overwriting_the_segment_word_with_an_undeclared_segment_stops(self):
        rewrite = "c7 06 02 02 00 20"
        result = run(STORE_OFFSET, STORE_SEGMENT, rewrite, CALL, "c3")
        path = only_path(self, result)
        self.assertFalse(path["returned"])
        self.assertEqual(path["stop"], "unresolved call: far pointer names no declared code region")
        call = events(result, "call")[0]
        self.assertIsNone(call["target"])
        self.assertEqual(call["provenance"]["loadedAddress"], "2000:0030")
        self.assertEqual(call["provenance"]["segmentWord"]["producers"], [12])
        self.assertFalse(result["completeWithinModel"])

    def test_effective_segment_override_is_kept(self):
        # mov word es:[0200], 0030 / mov word es:[0202], 1000 / call far es:[0200]
        result = run("26 c7 06 00 02 30 00", "26 c7 06 02 02 00 10", "26 ff 1e 00 02", "c3", registers={"es": 0x1000, "ds": 0x5000})
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertEqual(events(result, "call")[0]["provenance"]["pointerRead"]["segmentRegister"], "es")

    def test_pointer_built_from_registers_by_instructions_is_followed(self):
        # mov ax, 0030 / mov [0200], ax / mov ax, 1000 / mov [0202], ax / call far [0200]
        result = run("b8 30 00", "a3 00 02", "b8 00 10", "a3 02 02", CALL, "c3")
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        provenance = events(result, "call")[0]["provenance"]
        self.assertEqual(provenance["offsetWord"]["producers"], [0, 3])
        self.assertEqual(provenance["segmentWord"]["producers"], [6, 9])


class StoppedPointer(unittest.TestCase):
    def assert_stopped(self, result, reason):
        path = only_path(self, result)
        self.assertFalse(path["returned"])
        self.assertEqual(path["stop"], reason)
        self.assertFalse(result["completeWithinModel"])
        self.assertFalse([e for e in path["events"] if e["depth"] > 0], "the target must not be entered")
        call = [e for e in path["events"] if e["kind"] in ("call", "far-jump")][-1]
        self.assertIsNone(call["target"])
        return call["provenance"]

    def test_unknown_pointer_stays_stopped(self):
        provenance = self.assert_stopped(run(CALL, "c3"), "unresolved call: far pointer offset and segment words unknown")
        self.assertEqual({row["unwritten"]["cause"] for w in ("offsetWord", "segmentWord") for row in provenance[w]["bytes"]},
                         {"no write on this path"})
        self.assertNotIn("loadedAddress", provenance)

    def test_partly_unknown_pointer_stays_stopped(self):
        self.assert_stopped(run(STORE_OFFSET, CALL, "c3"), "unresolved call: far pointer segment word unknown")
        self.assert_stopped(run(STORE_SEGMENT, CALL, "c3"), "unresolved call: far pointer offset word unknown")
        # One byte of the segment word stored: the word is still unknown.
        self.assert_stopped(run(STORE_OFFSET, "c6 06 02 02 00", CALL, "c3"), "unresolved call: far pointer segment word unknown")

    def test_alias_invalidated_pointer_stays_stopped(self):
        # mov word es:[di], 0 with ES and DI unknown may store over the pointer.
        provenance = self.assert_stopped(run(STORE_OFFSET, STORE_SEGMENT, "26 c7 05 00 00", CALL, "c3"),
                                         "unresolved call: far pointer offset and segment words unknown")
        causes = {row["unwritten"]["cause"] for w in ("offsetWord", "segmentWord") for row in provenance[w]["bytes"]}
        self.assertEqual(causes, {"dropped by a possibly aliasing write"})

    def test_pointer_dropped_by_a_modeled_call_stays_stopped(self):
        # A modeled call between the stores and the transfer forgets memory.
        parts = (STORE_OFFSET, STORE_SEGMENT, "e8 00 00", CALL, "c3")
        model = [{"site": 12, "evidence": "synthetic modeled service", "cases": [{}]}]
        result = run(*parts, callModels=model)
        provenance = self.assert_stopped(result, "unresolved call: far pointer offset and segment words unknown")
        self.assertEqual({row["unwritten"]["cause"] for row in provenance["offsetWord"]["bytes"]}, {"dropped by a modeled call"})

    def test_target_outside_declared_regions_stays_stopped(self):
        # 1000:0020 lies between the two declared regions.
        provenance = self.assert_stopped(run("c7 06 00 02 20 00", STORE_SEGMENT, CALL, "c3"),
                                         "unresolved call: far pointer names no declared code region")
        self.assertEqual(provenance["loadedAddress"], "1000:0020")

    def test_canonical_segment_alias_is_not_followed(self):
        # 1003:0000 is linear 10030, the resident target, but not under the target region's mapping.
        provenance = self.assert_stopped(run("c7 06 00 02 00 00", "c7 06 02 02 03 10", CALL, "c3", resident=True),
                                         "unresolved call: far pointer names declared resident code only through a segment alias of its mapping")
        self.assertEqual(provenance["loadedAddress"], "1003:0000")

    def test_overlay_analysis_segment_is_not_a_load_address(self):
        data, end = program(STORE_OFFSET, STORE_SEGMENT, CALL, "c3")
        rows = regions(data, end, resident=True)
        rows[1].update(resident=False, container={"view": "overlay-1", "start": TARGETS, "end": len(data)})
        result = run_report(data, {"regions": rows, "entry": 0, "registers": FRAME}, "trace")
        provenance = self.assert_stopped(
            result, "unresolved call: far pointer names overlay code by its analysis segment, which is not a load address")
        self.assertEqual(provenance["admission"], {"region": "target"})

    def test_operand_size_override_stays_stopped(self):
        # 66 FF 1E is a far call through m16:32, outside the segmented16 frame model.
        result = run(STORE_OFFSET, STORE_SEGMENT, "66 ff 1e 00 02", "c3")
        path = only_path(self, result)
        self.assertEqual(path["stop"], "Operand-size control transfer override is outside the selected frame model")
        self.assertFalse(events(result, "call"))

    def test_unknown_jump_pointer_stays_stopped_with_its_event(self):
        provenance = self.assert_stopped(run(STORE_SEGMENT, JUMP), "unresolved jump: far pointer offset word unknown")
        self.assertEqual(provenance["segmentWord"]["value"], 0x1000)


class ModelsAndLimits(unittest.TestCase):
    def test_call_model_at_the_site_keeps_precedence(self):
        model = [{"site": 12, "evidence": "synthetic far service", "cases": [{"registers": {"ax": 5}}]}]
        result = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", callModels=model)
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertEqual(path["registers"]["ax"]["value"], 5)
        self.assertEqual(path["conditionalModels"][0]["site"], 12)
        self.assertEqual(events(result, "call")[0]["target"], TARGETS)
        self.assertFalse([e for e in path["events"] if e["depth"] > 0])

    def test_depth_limit_stops_before_the_target(self):
        result = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", maxDepth=1)
        path = only_path(self, result)
        self.assertEqual(path["stop"], "call depth limit; recursion or callee remains unresolved")
        self.assertEqual(events(result, "call")[0]["target"], TARGETS)

    def test_step_limit_inside_the_target_stays_a_stop(self):
        result = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", maxSteps=4)
        path = only_path(self, result)
        self.assertEqual(path["stop"], "step limit; loop progress unresolved")
        self.assertEqual(path["stopSite"], TARGETS + 3)

    def test_reach_control_into_the_target_holds_only_when_read(self):
        control = [{"name": "enters", "kind": "reach", "at": {"site": TARGETS}, "expect": "always"}]
        held = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", relationalControls=control)
        self.assertEqual(held["relationalControls"]["controls"][0]["verdict"], "held")
        for limit in ({"maxDepth": 1}, {"maxSteps": 3}):
            with self.subTest(limit=limit):
                capped = run(STORE_OFFSET, STORE_SEGMENT, CALL, "c3", relationalControls=control, **limit)
                self.assertEqual(capped["relationalControls"]["controls"][0]["verdict"], "undecided")
        unknown = run(STORE_OFFSET, CALL, "c3", relationalControls=control)
        self.assertEqual(unknown["relationalControls"]["controls"][0]["verdict"], "undecided")


class FarJump(unittest.TestCase):
    def test_produced_pointer_jump_continues_at_the_target(self):
        result = run("c7 06 00 02 38 00", STORE_SEGMENT, JUMP)
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertEqual(path["registers"]["ax"]["value"], 42)
        jump = next(e for e in path["events"] if e["kind"] == "far-jump")
        self.assertEqual(jump["target"], 0x38)
        self.assertEqual(jump["provenance"]["loadedAddress"], "1000:0038")
        self.assertEqual(jump["provenance"]["offsetWord"]["producers"], [0])
        self.assertEqual(jump["indirectValue"]["value"], 0x10000038)

    def test_far_jump_event_reaches_the_guard_and_effect_projections(self):
        for command in ("guards", "effects"):
            with self.subTest(command=command):
                result = run("c7 06 00 02 38 00", STORE_SEGMENT, JUMP, command=command)
                self.assertTrue(events(result, "far-jump"))


class Trampolines(unittest.TestCase):
    def layout(self, entry_declared=True):
        # Resident producer, a resident stub holding an INT 3Fh trampoline at file 0x20, and an
        # overlay whose entry is file 0x30.
        data, end = program("c7 06 00 02 20 00", STORE_SEGMENT, CALL, "c3")
        data = data[:0x20] + bytes.fromhex("cd 3f 00 00") + data[0x24:]
        rows = [{"name": "producer", "start": 0, "end": end, "ip": 0, "segment": 0x1000, "resident": True,
                 "entries": [0], "evidence": "synthetic producer"},
                {"name": "stub", "start": 0x20, "end": 0x24, "ip": 0x20, "segment": 0x1000, "resident": True,
                 "entries": [0x20], "evidence": "synthetic trampoline stub"}]
        if entry_declared:
            rows.append({"name": "overlay", "start": TARGETS, "end": len(data), "ip": 0, "segment": 0x4000,
                         "resident": False, "container": {"view": "overlay-1", "start": TARGETS, "end": len(data)},
                         "entries": [TARGETS], "evidence": "synthetic overlay analysis view"})
        exports = [{"descriptor": 1, "trampoline": 0x20, "entry": TARGETS, "codeRange": {"start": TARGETS, "end": len(data)},
                    "evidence": "synthetic FBOV descriptor/trampoline"}]
        return run_report(data, {"regions": rows, "entry": 0, "registers": FRAME, "overlayExports": exports}, "trace")

    def test_pointer_at_a_trampoline_continues_at_its_overlay_entry(self):
        result = self.layout()
        path = only_path(self, result)
        self.assertTrue(path["returned"], path["stop"])
        self.assertEqual(path["registers"]["ax"]["value"], 42)
        call = events(result, "call")[0]
        self.assertEqual(call["target"], TARGETS)
        admission = call["provenance"]["admission"]
        self.assertEqual(admission["region"], "stub")
        self.assertEqual(admission["trampoline"], {"trampoline": 0x20, "descriptor": 1, "entry": TARGETS,
                                                   "evidence": "synthetic FBOV descriptor/trampoline"})
        inner = next(e for e in path["events"] if e["kind"] == "return" and e["depth"] == 1)
        self.assertEqual(inner["registers"]["cs"]["value"], 0x4000)

    def test_trampoline_entry_outside_declared_regions_stays_stopped(self):
        result = self.layout(entry_declared=False)
        path = only_path(self, result)
        self.assertEqual(path["stop"], "unresolved call: the overlay entry the FBOV trampoline names lies outside declared code regions")
        self.assertEqual(events(result, "call")[0]["provenance"]["admission"]["trampoline"]["entry"], TARGETS)


class StaticWalk(unittest.TestCase):
    def test_walk_names_an_indirect_far_transfer_as_computed(self):
        for transfer in (CALL, JUMP):
            with self.subTest(transfer=transfer):
                data, end = program(transfer, "c3")
                image = Image(data, {"regions": regions(data, end)})
                _, gaps, edges, _, _ = walk(image, [0])
                self.assertIn({"site": 0, "reason": "computed transfer remains unresolved"}, gaps)
                self.assertIsNone(edges[0]["target"])


if __name__ == "__main__":
    unittest.main()
