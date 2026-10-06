"""PE32 acceptance uses constructed headers and instructions only."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import xxhash

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
# The engine CLI runs from this checkout's source whether or not the package is installed.
ENGINE = [sys.executable, "-B", "-m", "scientific_method_engine"]
ENGINE_ENV = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
# The reader's CLI test runs only inside the monorepo, where the reader sits beside the engine.
READER = SRC.parents[1] / "executable-reader" / "bin" / "scientific-method.ts"
from scientific_method_engine.x86.image import Image
from scientific_method_engine.x86.pe import pe32
from scientific_method_engine.x86.reports import run_report

BASE, CODE_VA, DATA_VA = 0x400000, 0x401000, 0x402000
CODE_RAW, DATA_RAW = 0x200, 0x400


def fixture(code, entries=(0,), **extra):
    data = bytearray(0x600)
    def w(at, n):
        struct.pack_into('<H', data, at, n)
    def d(at, n):
        struct.pack_into('<I', data, at, n)
    data[:2] = b'MZ'; d(60, 0x80); data[0x80:0x84] = b'PE\0\0'
    w(0x84, 0x14c); w(0x86, 2); w(0x94, 0xe0)
    opt = 0x98
    w(opt, 0x10b); d(opt + 28, BASE); d(opt + 32, 0x1000); d(opt + 36, 0x200)
    d(opt + 56, 0x3000); d(opt + 60, 0x200); d(opt + 92, 16)
    for at, name, rva, raw, flags in ((0x178, b'.text', 0x1000, CODE_RAW, 0x60000020),
                                      (0x1a0, b'.data', 0x2000, DATA_RAW, 0xc0000040)):
        data[at:at + len(name)] = name
        for off, n in ((8, 0x200), (12, rva), (16, 0x200), (20, raw), (36, flags)):
            d(at + off, n)
    code = bytes.fromhex(code) if isinstance(code, str) else code
    if not 0 < len(code) <= 0x200:
        raise ValueError('synthetic code extent')
    data[CODE_RAW:CODE_RAW + len(code)] = code
    config = {'sourceKind': 'pe32', 'entry': CODE_RAW,
              'regions': [{'name': 'text', 'start': CODE_RAW, 'end': CODE_RAW + len(code),
                           'entries': [CODE_RAW + x for x in entries], 'evidence': 'synthetic established entries'}], **extra}
    return bytes(data), config


class Code:
    def __init__(self):
        self.data, self.labels, self.fixups = bytearray(), {}, []
    def emit(self, code):
        self.data.extend(bytes.fromhex(code)); return self
    def label(self, name):
        self.labels[name] = len(self.data); return self
    def branch(self, opcode, label):
        self.emit(opcode)
        width = 4 if opcode in ('e8', 'e9', '0f 84', '0f 85') else 1
        self.fixups.append((len(self.data), width, label))
        self.data.extend(bytes(width)); return self
    def bytes(self):
        for at, width, label in self.fixups:
            self.data[at:at + width] = (self.labels[label] - at - width).to_bytes(width, 'little', signed=True)
        return bytes(self.data)


def report(code, command='trace', entries=(0,), **extra):
    data, config = fixture(code.bytes() if isinstance(code, Code) else code, entries, **extra)
    return run_report(data, config, command)


def events(result, kind):
    return [e for p in result['paths'] for e in p['events'] if e['kind'] == kind]


class PEReporterTests(unittest.TestCase):
    def test_cmps_with_one_address_for_both_operands(self):
        # ESI = EDI: both CMPSB loads match both operands, and each operand takes one of them.
        r = report('fc be 00 20 40 00 bf 00 20 40 00 c6 06 61 a6 74 01 c3 c3')
        path, = r['paths']
        self.assertTrue(path['returned'], path['stop'])
        self.assertEqual((path['registers']['esi']['value'], path['registers']['edi']['value']), (DATA_VA + 1,) * 2)
        roles = [e['role'] for e in events(r, 'read') if e['role'] and e['role'].startswith('string')]
        self.assertEqual(sorted(roles), ['string-destination', 'string-source'])

    def test_port_access_reports_its_boundary_and_stops_on_io_privilege(self):
        # mov dx, 0x3c8; out dx, al; ret
        r = report('66 ba c8 03 ee c3')
        path, = r['paths']
        self.assertFalse(path['returned'])
        self.assertIn('I/O privilege', path['stop'])
        row, = events(r, 'hardware-boundary')
        self.assertEqual((row['boundary'], row['port']['value'], row['width']), ('port-output', 0x3c8, 1))
        self.assertEqual(r['hardwareBoundaries'][0]['placement'], 'everyTracedPath')
        with self.assertRaisesRegex(ValueError, 'flat model'):
            report('ec c3', portInputs=[{'site': CODE_RAW, 'value': 1, 'evidence': 'synthetic'}])

    def test_entry_walk_records_a_gap_at_a_port_access(self):
        # mov dx, 0x3c8; out dx, al; mov [DATA_VA], eax; call t; ret; t: ret
        c = Code().emit('66 ba c8 03 ee a3 00 20 40 00').branch('e8', 't').emit('c3').label('t').emit('c3')
        out, store, call = CODE_RAW + 4, CODE_RAW + 5, CODE_RAW + 10
        gap = {'site': out, 'reason': 'port access in the flat model depends on I/O privilege, which is not modeled'}
        r = report(c, 'incoming', target=CODE_RAW + c.labels['t'])
        self.assertEqual(r['confirmed'], [])
        self.assertEqual([(x['site'], x['classification']) for x in r['candidates']], [(call, 'raw byte candidate')])
        self.assertIn(gap, r['gaps'])
        self.assertFalse(r['negativeUsable'])
        with self.assertRaisesRegex(ValueError, 'control'):
            report(c, 'incoming', target=CODE_RAW + c.labels['t'], controls=[call])
        r = report(c, 'operand-candidates', query={'offset': DATA_VA})
        self.assertEqual([(x['site'], x['classification']) for x in r['candidates']], [(store, 'unresolvedBoundary')])
        self.assertIn(gap, r['gaps'])
        # uses still inventories the store past the trace's stop, conditional on the port access continuing.
        r = report(c, 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[store])
        self.assertEqual(r['matches'], [])
        row, = r['conditionalAccesses']
        self.assertEqual((row['site'], row['dependsOn']), (store, [{'site': out, 'reason': gap['reason']}]))
        self.assertEqual(row['classification'], 'operand past a PE32 port access; values and continuation unresolved')
        # The inventoried store is not reported a second time as a raw candidate.
        self.assertEqual(r['rawCandidates'], [])
        self.assertIn(gap, r['gaps'])
        self.assertFalse(r['negativeUsable'])

    def test_entry_walk_records_a_gap_at_each_port_instruction(self):
        # mov dx, 0x3c8; <port access>; mov [DATA_VA], eax; ret
        reason = 'port access in the flat model depends on I/O privilege, which is not modeled'
        for name, port in (('in al, dx', 'ec'), ('rep insb', 'f3 6c'), ('outsd', '6f'), ('rep outsb', 'f3 6e')):
            with self.subTest(name):
                store = CODE_RAW + 4 + len(port.split())
                r = report(f'66 ba c8 03 {port} a3 00 20 40 00 c3', 'operand-candidates', query={'offset': DATA_VA})
                self.assertEqual([(x['site'], x['classification']) for x in r['candidates']],
                                 [(store, 'unresolvedBoundary')])
                self.assertIn({'site': CODE_RAW + 4, 'reason': reason}, r['gaps'])

    def test_uses_names_each_port_access_past_a_stop(self):
        # mov dx, 0x3c8; out dx, al; out dx, al; mov [DATA_VA], eax; ret
        r = report('66 ba c8 03 ee ee a3 00 20 40 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4})
        row, = r['conditionalAccesses']
        self.assertEqual([d['site'] for d in row['dependsOn']], [CODE_RAW + 4, CODE_RAW + 5])
        self.assertEqual(row['dependsOn'][1]['reason'], 'port access past a stop; assumed to continue')

    def test_uses_classifies_an_operand_reached_only_past_a_port_access(self):
        cfg = 'entry-CFG operand past a stop; values and callee effects unresolved'
        port = 'operand past a PE32 port access; values and continuation unresolved'
        def row(code):
            r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4})
            found, = r['conditionalAccesses']
            return found['site'] - CODE_RAW, found['classification'], [d['site'] - CODE_RAW for d in found['dependsOn']]
        # call eax; mov [DATA_VA], eax; ret: past an unread call only.
        self.assertEqual(row('ff d0 a3 00 20 40 00 c3'), (2, cfg, [0]))
        # mov dx, 0x3c8; out dx, al; mov [DATA_VA], eax; ret: past a port access only.
        self.assertEqual(row('66 ba c8 03 ee a3 00 20 40 00 c3'), (5, port, [4]))
        # call eax; mov dx, 0x3c8; out dx, al; mov [DATA_VA], eax; ret: every route from the unread call
        # crosses the port access, so the row is outside the entry CFG and dependsOn names both.
        self.assertEqual(row('ff d0 66 ba c8 03 ee a3 00 20 40 00 c3'), (7, port, [0, 6]))
        # call eax; test eax, eax; jz store; mov dx, 0x3c8; out dx, al; store: mov [DATA_VA], eax; ret
        # One route from the unread call skips the port access, so the row keeps the shared value.
        c = Code().emit('ff d0 85 c0').branch('74', 'store').emit('66 ba c8 03 ee').label('store').emit('a3 00 20 40 00 c3')
        self.assertEqual(row(c), (c.labels['store'], cfg, [0, c.labels['store'] - 1]))

    def test_uses_claims_no_port_access_route_when_the_walk_reaches_its_limit(self):
        # call eax; jz far; mov dx, 0x3c8; out dx, al; store: mov [DATA_VA], eax; ret; far: nop; nop; nop; ret
        c = Code().emit('ff d0').branch('74', 'far').emit('66 ba c8 03 ee').label('store').emit('a3 00 20 40 00 c3')
        c.label('far').emit('90 90 90 c3')
        store = c.labels['store']
        for limit, classification in ((100, 'operand past a PE32 port access; values and continuation unresolved'),
                                      (5, 'entry-CFG operand past a stop; values and callee effects unresolved')):
            with self.subTest(limit=limit):
                r = report(c, 'uses', query={'offset': DATA_VA, 'width': 4}, instructionLimit=limit)
                found, = r['conditionalAccesses']
                self.assertEqual((found['site'] - CODE_RAW, found['classification']), (store, classification))
                self.assertEqual(any(g.get('reason') == 'instruction limit' for g in r['gaps']), limit == 5)
                self.assertFalse(r['negativeUsable'])

    def test_uses_takes_the_access_direction_of_a_string_port_or_x87_operand_from_its_mnemonic(self):
        # Capstone flags no access on an INS or OUTS memory operand and reports FSTP's store as a read.
        cfg = 'entry-CFG operand past a stop; values and callee effects unresolved'
        port = 'operand past a PE32 port access; values and continuation unresolved'
        for name, code, expected in (('call eax; rep insb', 'ff d0 f3 6c c3', [(2, 'write', cfg)]),
                                     ('call eax; outsd', 'ff d0 6f c3', [(2, 'read', cfg)]),
                                     ('call eax; fstp dword [DATA_VA]', 'ff d0 d9 1d 00 20 40 00 c3', [(2, 'write', cfg)]),
                                     ('mov dx, 0x3c8; out dx, al; insb', '66 ba c8 03 ee 6c c3', [(5, 'write', port)])):
            with self.subTest(name):
                r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4})
                self.assertEqual([(e['site'] - CODE_RAW, e['kind'], e['classification']) for e in r['conditionalAccesses']],
                                 expected)
                self.assertFalse(r['negativeUsable'])
        # A read query no longer lists the FSTP store.
        r = report('ff d0 d9 1d 00 20 40 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4, 'access': 'read'})
        self.assertEqual(r['conditionalAccesses'], [])

    def test_uses_continues_at_the_return_site_of_a_call_open_at_a_stop(self):
        cfg = 'entry-CFG operand past a stop; values and callee effects unresolved'
        port = 'operand past a PE32 port access; values and continuation unresolved'
        port_reason = 'port access in the flat model depends on I/O privilege, which is not modeled'
        call_reason = 'unresolved call: computed transfer remains unresolved'
        open_call = 'call open at a stop inside its callee; continued at its return site, assumed to return'
        # call f; mov [DATA_VA], eax; ret; f: <stop>; ret
        for name, code, stop, reason, classification in (
                ('port access', 'e8 06 00 00 00 a3 00 20 40 00 c3 66 ba c8 03 ee c3', 15, port_reason, port),
                ('unread call', 'e8 06 00 00 00 a3 00 20 40 00 c3 ff d0 c3', 11, call_reason, cfg)):
            with self.subTest(name):
                r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 5])
                self.assertEqual(r['matches'], [])
                row, = r['conditionalAccesses']
                self.assertEqual((row['site'] - CODE_RAW, row['classification']), (5, classification))
                self.assertEqual([(d['site'] - CODE_RAW, d['reason']) for d in row['dependsOn']],
                                 [(0, open_call), (stop, reason)])
                self.assertFalse(r['negativeUsable'])

    def test_uses_continues_at_every_return_site_of_a_nested_stop(self):
        # call f; mov [DATA_VA], eax; ret; f: call g; mov [DATA_VA+4], eax; ret; g: push eax; call eax; pop eax; ret
        c = Code().branch('e8', 'f').emit('a3 00 20 40 00 c3')
        c.label('f').branch('e8', 'g').emit('a3 04 20 40 00 c3')
        c.label('g').emit('50 ff d0 58 c3')
        f, g = c.labels['f'], c.labels['g']
        stop = g + 1
        open_call = 'call open at a stop inside its callee; continued at its return site, assumed to return'
        r = report(c, 'uses', query={'offset': DATA_VA, 'width': 8}, controls=[CODE_RAW + 5, CODE_RAW + f + 5])
        rows = [(e['site'] - CODE_RAW, e['classification'], [(d['site'] - CODE_RAW, d['reason']) for d in e['dependsOn']])
                for e in r['conditionalAccesses']]
        unread = (stop, 'unresolved call: computed transfer remains unresolved')
        cfg = 'entry-CFG operand past a stop; values and callee effects unresolved'
        # Each caller's store depends on the stop and on every open call from the stop out to it.
        self.assertEqual(rows, [(5, cfg, [(0, open_call), (f, open_call), unread]),
                                (f + 5, cfg, [(f, open_call), unread])])
        self.assertFalse(r['negativeUsable'])

    def test_uses_names_a_call_stepped_over_on_the_way_to_a_return_site(self):
        # call f; mov [DATA_VA], eax; ret; f: call eax; call h; ret; h: ret
        c = Code().branch('e8', 'f').emit('a3 00 20 40 00 c3')
        c.label('f').emit('ff d0').branch('e8', 'h').emit('c3').label('h').emit('c3')
        f = c.labels['f']
        r = report(c, 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 5])
        row, = r['conditionalAccesses']
        self.assertEqual([(d['site'] - CODE_RAW, d['reason']) for d in row['dependsOn']],
                         [(0, 'call open at a stop inside its callee; continued at its return site, assumed to return'),
                          (f, 'unresolved call: computed transfer remains unresolved'),
                          (f + 2, 'call past a stop; assumed to return')])

    def test_uses_leaves_out_a_call_on_a_branch_that_never_returns(self):
        # call f; mov [DATA_VA], eax; ret; f: call eax; test eax, eax; jz L; ret; L: call g; jmp $; g: ret
        c = Code().branch('e8', 'f').emit('a3 00 20 40 00 c3')
        c.label('f').emit('ff d0 85 c0').branch('74', 'L').emit('c3')
        c.label('L').branch('e8', 'g').emit('eb fe').label('g').emit('c3')
        f = c.labels['f']
        r = report(c, 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 5])
        row, = r['conditionalAccesses']
        self.assertEqual([(d['site'] - CODE_RAW, d['reason']) for d in row['dependsOn']],
                         [(0, 'call open at a stop inside its callee; continued at its return site, assumed to return'),
                          (f, 'unresolved call: computed transfer remains unresolved')])

    def test_uses_continues_at_the_return_site_of_a_path_limit_inside_a_callee(self):
        # call f; mov [ebx], eax; ret; f: test eax, eax; jz L; mov ebx, DATA_VA; ret; L: mov ebx, CODE_VA; ret
        # One path: the branch that points ebx at the query is dropped at the path limit.
        c = Code().branch('e8', 'f').emit('89 03 c3')
        c.label('f').emit('85 c0').branch('74', 'L').emit('bb 00 20 40 00 c3').label('L').emit('bb 00 10 40 00 c3')
        branch = c.labels['f'] + 2
        r = report(c, 'uses', query={'offset': DATA_VA, 'width': 4}, maxPaths=1)
        self.assertEqual([(e['site'] - CODE_RAW, e['address'], [(d['site'] - CODE_RAW, d['reason']) for d in e['dependsOn']])
                          for e in r['conditionalAccesses']],
                         [(5, 'possible alias',
                           [(0, 'call open at a stop inside its callee; continued at its return site, assumed to return'),
                            (branch, 'path limit')])])
        self.assertFalse(r['negativeUsable'])
        # The trace command's gaps carry no call stack.
        self.assertNotIn('callStack', report(c, maxPaths=1)['gaps'][0])

    def test_uses_does_not_continue_past_a_pe32_iret(self):
        # call f; mov [DATA_VA], eax; ret; f: call eax; iretd. IRET stops the PE32 trace, so it returns to no caller.
        r = report('e8 06 00 00 00 a3 00 20 40 00 c3 ff d0 cf', 'uses', query={'offset': DATA_VA, 'width': 4})
        self.assertEqual(r['conditionalAccesses'], [])
        self.assertFalse(r['negativeUsable'])

    def test_uses_keeps_a_stop_in_the_entry_function_without_a_caller_continuation(self):
        # call eax; mov [DATA_VA], eax; ret: no call is open at the stop, so dependsOn names the stop alone.
        r = report('ff d0 a3 00 20 40 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 2])
        row, = r['conditionalAccesses']
        self.assertEqual(row['dependsOn'], [{'site': CODE_RAW, 'reason': 'unresolved call: computed transfer remains unresolved'}])
        self.assertFalse(r['negativeUsable'])

    def test_uses_does_not_continue_past_a_callee_that_cannot_return(self):
        # call f; mov [DATA_VA], eax; ret; f: call eax; jmp $
        code = 'e8 06 00 00 00 a3 00 20 40 00 c3 ff d0 eb fe'
        r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4})
        self.assertEqual(r['conditionalAccesses'], [])
        self.assertFalse(r['negativeUsable'])
        with self.assertRaisesRegex(ValueError, 'Positive variable-use control 517 missed'):
            report(code, 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 5])
        # A stop at the callee's return is that return failing, so its caller is not continued either.
        # call f; mov [DATA_VA], eax; ret; f: push 0; ret
        r = report('e8 06 00 00 00 a3 00 20 40 00 c3 6a 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4})
        self.assertEqual(r['conditionalAccesses'], [])
        self.assertFalse(r['negativeUsable'])

    def test_uses_records_the_limit_of_a_walk_to_a_callee_return(self):
        # call f; mov [DATA_VA], eax; ret; f: mov dx, 0x3c8; out dx, al; nop * 10; ret
        code = 'e8 06 00 00 00 a3 00 20 40 00 c3 66 ba c8 03 ee' + ' 90' * 10 + ' c3'
        for limit, rows in ((100, [5]), (6, [])):
            with self.subTest(limit=limit):
                r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4}, instructionLimit=limit)
                self.assertEqual([e['site'] - CODE_RAW for e in r['conditionalAccesses']], rows)
                self.assertEqual({'site': CODE_RAW + 15, 'reason': 'instruction limit'} in r['gaps'], limit == 6)
                self.assertFalse(r['negativeUsable'])

    def test_pop_addresses_its_destination_after_the_stack_pointer_moves(self):
        # push 1; push 2; push 3; pop dword [esp+4]; pop eax; pop ebx; ret
        regs = report('6a 01 6a 02 6a 03 8f 44 24 04 58 5b c3')['paths'][0]['registers']
        self.assertEqual((regs['eax']['value'], regs['ebx']['value']), (2, 3))

    def test_mapping_is_source_derived_and_does_not_mutate_config(self):
        data, config = fixture('b8 78 56 34 12 c3')
        image = Image(data, config)
        self.assertNotIn('ip', config['regions'][0])
        self.assertEqual(image.near_target(CODE_RAW, CODE_VA), CODE_RAW)
        self.assertEqual(image.file_offset(DATA_VA + 17), DATA_RAW + 17)
        r = run_report(data, config, 'trace')
        self.assertTrue(r['completeWithinModel'], r)
        self.assertEqual(r['paths'][0]['registers']['eax']['value'], 0x12345678)
        self.assertEqual(r['sourceMapping']['imageBase'], BASE)
        self.assertEqual(r['instructionModel']['bits'], 32)

    def test_loader_rejects_unsupported_or_ambiguous_inputs(self):
        original, config = fixture('c3')
        for at, width, value in ((0x84, 2, 0x8664), (0x98, 2, 0x20b), (60, 4, 0xffff),
                                 (0x94, 2, 95), (0x98 + 92, 4, 17), (0x98 + 28, 4, 0xfffff000),
                                 (0x178 + 20, 4, 0x100), (0x1a0 + 20, 4, CODE_RAW),
                                 (0x1a0 + 12, 4, 0x1000), (0x178 + 16, 4, 0x1000)):
            with self.subTest(at=at, value=value):
                data = bytearray(original); struct.pack_into('<H' if width == 2 else '<I', data, at, value)
                with self.assertRaises(ValueError):
                    Image(bytes(data), config)
        with self.assertRaises(ValueError):
            Image(original[:0x190], config)
        for overrides in ({'ip': CODE_VA + 1}, {'segment': 1}, {'resident': True},
                          {'start': DATA_RAW, 'end': DATA_RAW + 1}, {'end': CODE_RAW + 513}):
            with self.assertRaises(ValueError):
                Image(original, {**config, 'regions': [{**config['regions'][0], **overrides}]})
        for overrides in ({'bits': 16}, {'addressModel': 'segmented16'}, {'relocations': [{'site': 1}]}):
            with self.assertRaises(ValueError):
                Image(original, {**config, **overrides})

    def test_zero_fill_is_not_raw_code(self):
        data, config = fixture('c3')
        data = bytearray(data); struct.pack_into('<I', data, 0x178 + 8, 0x800)
        image = Image(bytes(data), config)
        self.assertIsNone(image.file_offset(CODE_VA + 0x300))
        self.assertIsNone(image.near_target(CODE_RAW, CODE_VA + 0x300))

    def test_alignment_padding_past_virtual_size_is_not_loaded(self):
        data, config = fixture('c3')
        data = bytearray(data)
        struct.pack_into('<I', data, 0x178 + 8, 0x10); struct.pack_into('<I', data, 0x1a0 + 8, 0x10)
        data = bytes(data)
        image = Image(data, config)
        self.assertEqual(image.file_offset(DATA_VA + 0xf), DATA_RAW + 0xf)
        self.assertIsNone(image.file_offset(DATA_VA + 0x10))
        self.assertIsNone(image.file_offset(DATA_VA + 0xc, 8))
        self.assertEqual(image.config['peMetadata']['sections'][0]['loadedRawSize'], 0x10)
        with self.assertRaises(ValueError):
            Image(data, {**config, 'regions': [{**config['regions'][0], 'end': CODE_RAW + 0x11}]})

    def test_malformed_regions_fail_with_a_diagnosable_error(self):
        data, config = fixture('c3')
        for regions in ({'name': 'text'}, [1], ['text'], [None]):
            with self.subTest(regions=regions), self.assertRaises(ValueError):
                Image(data, {**config, 'regions': regions})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'source.bin').write_bytes(data)
            config.update(source='source.bin', xxh3=xxhash.xxh3_128_hexdigest(data), regions=[1])
            (path / 'config.json').write_text(json.dumps(config))
            process = subprocess.run([*ENGINE, 'trace', str(path / 'config.json')],
                                     capture_output=True, text=True, env=ENGINE_ENV)
            self.assertEqual(process.returncode, 1, process.stderr)
            self.assertTrue(process.stderr.startswith('Evidence report: '), process.stderr)
            self.assertNotIn('Traceback', process.stderr)

    def test_arguments_follow_esp_ebp_and_32bit_return_frame(self):
        c = Code().emit('68 78 56 34 12').branch('e8', 'callee').emit('83 c4 04 c3')
        c.label('callee').emit('55 89 e5 8b 45 08 c9 c3')
        r = report(c, 'arguments')
        self.assertTrue(r['completeWithinModel'], r)
        arg = next(e for e in events(r, 'read') if e.get('argument'))
        self.assertEqual(arg['value']['value'], 0x12345678)
        self.assertEqual(arg['argument']['returnFrameBytes'], 4)
        self.assertEqual(arg['argument']['offsetFromEntrySP'], 4)
        self.assertEqual(arg['width'], 4)

    def test_a_nested_near_return_over_a_four_byte_frame_has_no_segment_check(self):
        # A PE32 near call's frame is four bytes, the width of its near return. Nothing reads a segment.
        r = report('e8 01 00 00 00 c3 c3')
        self.assertTrue(r['completeWithinModel'], r)
        check = next(e for e in events(r, 'return') if e['depth'] == 1)['returnCheck']
        self.assertEqual((check['frameBytes'], check['instructionBytes'], check['target']), (4, 4, 'matches the call'))
        self.assertNotIn('segment', check)

    def test_arguments_follow_an_enter_frame(self):
        c = Code().emit('68 78 56 34 12').branch('e8', 'callee').emit('83 c4 04 c3')
        c.label('callee').emit('c8 08 00 00 8b 45 08 c9 c3')
        r = report(c, 'trace')
        self.assertTrue(r['completeWithinModel'], r)
        saved = next(e for e in events(r, 'write') if e['site'] == CODE_RAW + c.labels['callee'])
        self.assertEqual((saved['role'], saved['width']), ('push', 4))
        arg = next(e for e in events(r, 'read') if e.get('argument'))
        self.assertEqual(arg['value']['value'], 0x12345678)
        self.assertEqual(arg['argument']['offsetFromEntrySP'], 4)
        self.assertEqual(arg['argument']['pushProducers'], [CODE_RAW])

    def test_callee_cleanup_and_pointer_plus_independent_word(self):
        c = Code().emit('68 00 20 40 00 66 68 07 00').branch('e8', 'callee').emit('c3')
        c.label('callee').emit('55 89 e5 66 8b 45 08 8b 55 0a c9 c2 06 00')
        r = report(c, 'arguments')
        self.assertTrue(r['completeWithinModel'], r)
        args = [e for e in events(r, 'read') if e.get('argument')]
        self.assertEqual([(e['width'], e['value']['value']) for e in args], [(2, 7), (4, DATA_VA)])
        self.assertTrue(all(e['argument']['grouping'] == 'consumed width only' for e in args))

    def test_argument_frame_maps_a_word_and_a_pointer_under_callee_cleanup(self):
        # A dword push and a word push, read as a word and a dword, released by RET 6.
        c = Code().emit('68 00 20 40 00 66 68 07 00').branch('e8', 'callee').emit('c3')
        c.label('callee').emit('55 89 e5 66 8b 45 08 8b 55 0a c9 c2 06 00')
        frame = report(c, 'arguments')['paths'][0]['argumentFrames'][0]
        self.assertEqual((frame['returnFrameBytes'], frame['calleeCleanupBytes'], frame['mappedBytes']), (4, 6, 6))
        self.assertEqual([(s['offset'], s['width'], s['writerWidth']) for s in frame['slots']], [(0, 2, 2), (2, 4, 4)])
        self.assertEqual([(g['offset'], g['width']) for g in frame['groupings']], [(0, 2), (2, 4)])
        self.assertTrue(frame['settledOnThisPath'], frame['openReasons'])

    def test_returns_preserve_low_byte_predicate_and_discarded_width(self):
        for value in ('01 00 ff ff', '00 01 00 00'):
            c = Code().branch('e8', 'callee').emit('84 c0').branch('74', 'zero').emit('c3')
            c.label('zero').emit('c3').label('callee').emit('b8 ' + value + ' c3')
            r = report(c, 'returns', returnContracts=[{'entry': CODE_RAW + c.labels['callee'], 'register': 'eax',
                                                      'failures': [0xffff0001], 'evidence': 'synthetic failure contract'}])
            self.assertTrue(r['completeWithinModel'], r)
            branch = events(r, 'branch')[0]
            self.assertEqual(branch['left']['bits'], 8)
            self.assertEqual(branch['taken'], value.startswith('00'))
            self.assertEqual(events(r, 'return')[0]['resultContracts'][0]['matchesFailureEncoding'], value.startswith('01'))

    def test_effects_keep_write_before_failure_and_bypassed_write(self):
        c = Code().emit('c7 05 00 20 40 00 01 00 00 00 85 c9').branch('74', 'failure')
        c.emit('c7 05 04 20 40 00 02 00 00 00 b8 01 00 00 00 c3')
        c.label('failure').emit('b8 ff ff ff ff c3')
        r = report(c, 'effects')
        self.assertTrue(r['completeWithinModel'], r)
        self.assertEqual(sorted(len([e for e in p['events'] if e['kind'] == 'write']) for p in r['paths']), [1, 2])
        self.assertTrue(all(p['events'][0]['kind'] == 'write' for p in r['paths']))

    def test_memory_sib_flat_alias_and_fs_base_unknown(self):
        r = report('b9 00 20 40 00 ba 01 00 00 00 c7 44 91 08 44 33 22 11 a1 0c 20 40 00 c3', 'memory')
        self.assertTrue(r['completeWithinModel'], r)
        self.assertEqual(events(r, 'read')[-1]['value']['value'], 0x11223344)
        self.assertEqual(events(r, 'write')[0]['offset']['value'], DATA_VA + 12)
        r = report('c7 05 00 20 40 00 11 11 11 11 64 a1 00 20 40 00 c3', 'memory', registers={'fs': 0})
        read = events(r, 'read')[-1]
        self.assertIsNone(read['segment']['value'])
        self.assertIsNone(read['value']['value'])
        self.assertEqual(read['missingByteProducers'], [0, 1, 2, 3])

    def test_overlap_keeps_unknown_neighbor_bytes(self):
        for prefix, expected, missing in (('c7 05 00 20 40 00 00 01 02 03 ', 0x03020100, []), ('', None, [1, 2, 3])):
            r = report(prefix + 'c6 05 00 20 40 00 00 a1 00 20 40 00 c3', 'memory')
            read = events(r, 'read')[-1]
            self.assertEqual(read['value']['value'], expected)
            self.assertEqual(read['missingByteProducers'], missing)

    def test_guards_use_actual_value_and_unknown_call_invalidates_reload(self):
        c = Code().emit('a1 00 20 40 00 83 f8 00').branch('74', 'exit').emit('8b 10')
        c.label('model').branch('e8', 'outside').emit('a1 00 20 40 00 ff d0').label('exit').emit('c3')
        c.label('outside').emit('c3')
        r = report(c, 'guards', callModels=[{'site': CODE_RAW + c.labels['model'], 'evidence': 'unknown external writer', 'cases': [{}]}])
        indirect = next(e for e in events(r, 'call') if e['indirectValue'])
        self.assertFalse(indirect['guards'][0]['sameTargetValue'])
        dereference = next(e for e in events(r, 'read') if e['site'] == CODE_RAW + 10)
        self.assertTrue(dereference['guards'][0]['samePointerValue'])
        self.assertFalse(r['completeWithinModel'])

    def test_call_model_uses_flat_near_frame_even_after_encoded_push_cs(self):
        c = Code().branch('eb', 'call').emit('0e').label('call').branch('e8', 'external').emit('c3').label('external').emit('c3')
        model = {'site': CODE_RAW + c.labels['call'], 'evidence': 'synthetic flat callee', 'cases': [{}]}
        for extra in ({}, {'returnBytes': 4}):
            r = report(c, callModels=[{**model, **extra}])
            self.assertTrue(r['completeWithinModel'])
            self.assertTrue(r['paths'][0]['conditionalModels'])
        with self.assertRaisesRegex(ValueError, 'encoded call frame'):
            report(c, callModels=[{**model, 'returnBytes': 2}])

    def test_conversions_toggle_the_flat_default_operand_size(self):
        r = report('b8 80 00 00 00 66 98 98 99 66 99 c3')
        rows = [(e['decoderMnemonic'], e['sourceRegister'], e['destinationRegister'], e['effectiveOperandBits'],
                 e['mnemonicWidthMismatch'], e['result']['value']) for e in events(r, 'conversion')]
        self.assertEqual(rows, [('cbw', 'al', 'ax', 16, False, 0xff80), ('cwde', 'ax', 'eax', 32, False, 0xffffff80),
                                ('cdq', 'eax', 'edx', 32, False, 0xffffffff), ('cwd', 'ax', 'dx', 16, False, 0xffff)])

    def test_cfg_operands_use_flat_stack_segment_and_full_far_pointer(self):
        c = Code().branch('e8', 'external').emit('8b 45 08 c5 1d 00 20 40 00 66 c5 1d 00 20 40 00 c3')
        c.label('external').emit('c3')
        data, config = fixture(c.bytes(), query={'offset': DATA_VA, 'width': 1})
        config['regions'][0]['end'] = CODE_RAW + c.labels['external']
        r = run_report(data, config, 'uses')
        found = {e['site'] - CODE_RAW: (e['width'], e['effectiveSegmentRegister']) for e in r['conditionalAccesses']}
        self.assertEqual(found, {5: (4, 'ss'), 8: (6, 'ds'), 14: (4, 'ds')})
        self.assertFalse(r['negativeUsable'])

    def test_incoming_late_cross_region_and_raw_embedded_candidate(self):
        c = Code().label('target').emit('c3').label('caller').branch('e8', 'target').emit('c3')
        c.label('raw').branch('e8', 'target')
        r = report(c, 'incoming', entries=(0, 1), target=CODE_RAW, controls=[CODE_RAW + 1])
        self.assertEqual([x['site'] for x in r['confirmed']], [CODE_RAW + 1])
        self.assertEqual([x['site'] for x in r['candidates']], [CODE_RAW + c.labels['raw']])
        self.assertEqual(r['confirmed'][0]['provenance']['encoding'], 'relative32')
        self.assertEqual(r['confirmed'][0]['provenance']['loadedTarget'], CODE_VA)
        self.assertFalse(r['negativeUsable'])
        with self.assertRaisesRegex(ValueError, 'control'):
            report(c, 'incoming', entries=(0, 1), target=CODE_RAW, controls=[CODE_RAW + c.labels['raw']])

    def test_variable_uses_keep_data_between_entries_and_failed_control(self):
        code = 'a1 00 20 40 00 c3 ff ff a3 00 20 40 00 c3'
        r = report(code, 'uses', entries=(0, 8), query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW, CODE_RAW + 8])
        self.assertEqual({e['site'] for e in r['matches']}, {CODE_RAW, CODE_RAW + 8})
        self.assertTrue(r['undecodedRanges'])
        with self.assertRaisesRegex(ValueError, 'control'):
            report(code, 'uses', entries=(0, 8), query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 6])

    def test_dispatch_decodes_scale_normalization_gate_and_source_mapping(self):
        c = Code().emit('83 e0 01 83 f9 02').branch('73', 'exit').label('dispatch').emit('ff 24 85 00 20 40 00')
        c.label('exit').emit('c3')
        data, config = fixture(c.bytes())
        data = bytearray(data); struct.pack_into('<II', data, DATA_RAW, CODE_VA, CODE_VA + c.labels['exit'])
        d = {'site': CODE_RAW + c.labels['dispatch'], 'inputRegister': 'eax', 'indexRegister': 'eax', 'inputs': [0, 2, 1],
             'indexEvidence': 'decoded mask and SIB', 'table': {'start': DATA_RAW, 'count': 2, 'stride': 4, 'width': 4,
             'offset': DATA_VA, 'countEvidence': 'synthetic two rows', 'mappingEvidence': 'PE data section'}}
        r = run_report(bytes(data), {**config, 'dispatch': d, 'registers': {'ecx': 0}}, 'dispatch')
        self.assertEqual([x['outcomes'][0]['position'] for x in r['cases']], [0, 0, 1])
        r = run_report(bytes(data), {**config, 'dispatch': d, 'registers': {'ecx': 2}}, 'dispatch')
        self.assertTrue(all(x['outcomes'][0]['status'] == 'returned-before-dispatch' for x in r['cases']))
        d['table']['start'] += 4
        with self.assertRaisesRegex(ValueError, 'mapping'):
            run_report(bytes(data), {**config, 'dispatch': d}, 'dispatch')

    def test_allocation_width_extent_and_flat_write_comparison(self):
        c = Code().emit('b8 ff ff ff ff 83 c0 05').label('call').branch('e8', 'allocator')
        c.label('after').emit('c7 00 11 22 33 44 c3').label('allocator').emit('b8 00 20 40 00 b9 01 00 00 00 c3')
        r = report(c, 'allocation', allocations=[{'site': CODE_RAW + c.labels['call'], 'requestRegister': 'eax',
            'unitBytes': 1, 'unitEvidence': 'synthetic byte request',
            'extent': {'site': CODE_RAW + c.labels['after'], 'register': 'ecx', 'unitBytes': 16, 'evidence': 'synthetic paragraphs'},
            'pointer': {'site': CODE_RAW + c.labels['after'], 'offsetRegister': 'eax', 'evidence': 'flat pointer'}}])
        a = r['allocations'][0]
        self.assertEqual(a['requestedBytes'], 4)
        self.assertEqual(a['requestModulus'], 1 << 32)
        self.assertEqual(a['observedExtentBytes'], 16)
        self.assertTrue(a['writeComparisons'][0]['withinObservedExtent'])
        self.assertEqual(a['writeComparisons'][0]['relativeStart'], 0)

    def test_unsupported_and_limit_paths_cannot_be_complete(self):
        for code in ('66 c3', '67 a1 00 20 c3', '0f 31 c3', 'cb', 'ff d0', '8e d8 c3', '0f a0 c3'):
            r = report(code)
            self.assertFalse(r['completeWithinModel'], (code, r))
            self.assertTrue(r['paths'][0]['stop'])
        r = report('90 c3', maxSteps=1)
        self.assertFalse(r['completeWithinModel'])
        r = report('c3', 'incoming', target=CODE_RAW)
        self.assertEqual(r['counts']['confirmed'], 0)
        self.assertFalse(r['negativeUsable'])

    @unittest.skipUnless(READER.exists(), 'needs the reader package beside the engine')
    def test_node_cli_parses_pe_and_reports_mapping(self):
        data, config = fixture('b8 01 00 00 00 c3')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'source.bin').write_bytes(data)
            config.update(source='source.bin', xxh3=xxhash.xxh3_128_hexdigest(data))
            environment = {**ENGINE_ENV, 'EVIDENCE_PYTHON': sys.executable}
            (path / 'config.json').write_text(json.dumps(config))
            process = subprocess.run(['node', str(READER), 'trace', str(path / 'config.json')], capture_output=True, text=True, env=environment)
            self.assertEqual(process.returncode, 0, process.stderr)
            r = json.loads(process.stdout)
            self.assertEqual(r['sourceIdentity']['xxh3'], config['xxh3'])
            self.assertEqual(r['sourceMapping']['format'], 'PE32/i386')
            config['regions'][0]['ip'] = CODE_VA + 1
            (path / 'config.json').write_text(json.dumps(config))
            process = subprocess.run(['node', str(READER), 'trace', str(path / 'config.json')], capture_output=True, text=True, env=environment)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('mapping', process.stderr)

    def test_overlapping_entries_never_verify_a_false_call_boundary(self):
        # Entry 0's MOV embeds an E8 opcode. Entry 1 conflicts with that path.
        code = 'b8 e8 fa ff ff ff c3'
        r = report(code, 'incoming', entries=(0, 1), target=CODE_RAW)
        self.assertEqual(r['counts']['confirmed'], 0)
        self.assertEqual(r['candidates'][0]['site'], CODE_RAW + 1)
        self.assertTrue(any('overlapping' in g['reason'] for g in r['gaps']))
        with self.assertRaisesRegex(ValueError, 'control'):
            report(code, 'incoming', entries=(0, 1), target=CODE_RAW, controls=[CODE_RAW + 1])

    def test_cross_region_rel32_and_negative_displacement(self):
        c = Code().label('target').emit('c3').label('caller').branch('e8', 'target').emit('c3')
        data, config = fixture(c.bytes())
        config['regions'] = [{'name': 'target', 'start': CODE_RAW, 'end': CODE_RAW + 1, 'entries': [CODE_RAW], 'evidence': 'synthetic target'},
                             {'name': 'caller', 'start': CODE_RAW + 1, 'end': CODE_RAW + 7, 'entries': [CODE_RAW + 1], 'evidence': 'synthetic late caller'}]
        r = run_report(data, {**config, 'target': CODE_RAW, 'controls': [CODE_RAW + 1]}, 'incoming')
        self.assertEqual(r['confirmed'][0]['target'], CODE_RAW)
        self.assertEqual(r['confirmed'][0]['region'], 'caller')
        r = run_report(data, {**config, 'target': CODE_RAW, 'searchRegions': ['target']}, 'incoming')
        self.assertEqual(r['counts']['confirmed'], 0)
        self.assertEqual([x['name'] for x in r['searched']], ['target'])
        self.assertFalse(r['negativeUsable'])

    def test_instruction_and_scan_limits_remain_gaps(self):
        c = Code().emit('c3').label('caller').branch('e8', 'target').emit('c3')
        c.labels['target'] = 0
        r = report(c, 'incoming', entries=(0, 1), target=CODE_RAW, instructionLimit=1, scanLimit=1)
        self.assertTrue(any('instruction limit' in g['reason'] for g in r['gaps']))
        self.assertTrue(any('raw scan limit' in g['reason'] for g in r['gaps']))
        self.assertFalse(r['negativeUsable'])

    def test_unknown_fs_alias_does_not_count_as_flat_variable_use(self):
        r = report('64 a1 00 20 40 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4})
        self.assertEqual(r['matches'], [])
        self.assertTrue(r['unresolvedAccesses'])
        with self.assertRaisesRegex(ValueError, 'control'):
            report('64 a1 00 20 40 00 c3', 'uses', query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW])

    def test_dereference_before_check_has_no_retroactive_guard(self):
        r = report('8b 10 83 f8 00 74 00 c3', 'guards')
        self.assertEqual(events(r, 'read')[0]['guards'], [])

    def test_allocation_failure_retains_earlier_mutation(self):
        c = Code().emit('c7 05 00 20 40 00 01 00 00 00 b8 08 00 00 00').label('call').branch('e8', 'allocator')
        c.emit('c3').label('allocator').emit('b8 00 00 00 00 c3')
        r = report(c, 'allocation', allocations=[{'site': CODE_RAW + c.labels['call'], 'requestRegister': 'eax', 'unitBytes': 1, 'unitEvidence': 'synthetic request'}])
        self.assertEqual(events(r, 'write')[0]['value']['value'], 1)
        self.assertEqual(r['allocations'][0]['returnedRegisters']['eax']['value'], 0)
        self.assertIsNone(r['allocations'][0]['observedExtentBytes'])
        self.assertIn('unproven', r['allocations'][0]['rollback'])

    def test_prefixed_call_and_indirect_import_remain_visible(self):
        # Harmless segment prefix on direct call: raw scan starts at E8 but the
        # established entry is the prefix. Both interpretations stay distinct.
        c = Code().emit('2e').branch('e8', 'target').emit('c3').label('target').emit('c3')
        r = report(c, 'incoming', entries=(0,), target=CODE_RAW + c.labels['target'], controls=[CODE_RAW])
        self.assertEqual(r['confirmed'][0]['site'], CODE_RAW)
        self.assertEqual(r['candidates'][0]['site'], CODE_RAW + 1)
        r = report('ff 15 00 20 40 00 c3', 'incoming', target=CODE_RAW)
        self.assertTrue(r['unresolved'])
        self.assertTrue(r['gaps'])
        self.assertFalse(r['negativeUsable'])

    def test_32bit_stack_overwrite_and_boundary_are_rejected(self):
        c = Code().branch('e8', 'callee').emit('c3').label('callee').emit('c7 04 24 00 00 00 00 c3')
        r = report(c)
        self.assertFalse(r['completeWithinModel'])
        self.assertIn('return target', r['paths'][0]['stop'])
        r = report('a1 fe ff ff ff c3')
        self.assertFalse(r['completeWithinModel'])
        self.assertIn('address boundary', r['paths'][0]['stop'])


    def test_overlapping_use_is_unresolved_and_cannot_be_a_control(self):
        code = 'b8 a1 00 20 40 00 c3'
        r = report(code, 'uses', entries=(0, 1), query={'offset': DATA_VA, 'width': 4})
        self.assertFalse(any(e['site'] == CODE_RAW + 1 for e in r['matches']))
        self.assertTrue(any(e['site'] == CODE_RAW + 1 for e in r['unresolvedAccesses']))
        with self.assertRaisesRegex(ValueError, 'control'):
            report(code, 'uses', entries=(0, 1), query={'offset': DATA_VA, 'width': 4}, controls=[CODE_RAW + 1])
        overlap = next(e for e in r['unresolvedAccesses'] if e['site'] == CODE_RAW + 1)
        self.assertEqual(overlap['classification'], 'unverified overlapping instruction path')

    def test_use_beyond_walk_limit_is_not_called_an_overlap(self):
        code = 'a1 00 20 40 00 90 a1 00 20 40 00 c3'
        r = report(code, 'uses', query={'offset': DATA_VA, 'width': 4}, instructionLimit=1)
        late = next(e for e in r['unresolvedAccesses'] if e['site'] == CODE_RAW + 6)
        self.assertEqual(late['classification'], 'outside the bounded entry walk')
        self.assertFalse(r['negativeUsable'])




    def test_flat_string_width_and_saved_flags(self):
        result=report('b8 44 33 22 11 b9 02 00 00 00 bf 00 20 40 00 fc f3 ab c3')
        self.assertTrue(result['completeWithinModel'])
        self.assertEqual([e['width'] for e in events(result,'write')],[4,4])
        self.assertEqual(result['paths'][0]['registers']['edi']['value'],DATA_VA+8)
        result=report('fd 9c fc 9d aa c3',registers={'esp':0x800000,'edi':DATA_VA})
        self.assertTrue(events(result,'flags-restore')[0]['intactLocalSnapshot'])
        self.assertEqual(events(result,'flags-restore')[0]['width'],4)
        self.assertEqual(result['paths'][0]['registers']['edi']['value'],DATA_VA-1)


    def test_flat_iret_is_not_a_local_real_mode_frame(self):
        result=report('cf')
        self.assertFalse(result['completeWithinModel'])
        self.assertIn('segmented16',result['paths'][0]['stop'])

if __name__ == '__main__':
    unittest.main()
