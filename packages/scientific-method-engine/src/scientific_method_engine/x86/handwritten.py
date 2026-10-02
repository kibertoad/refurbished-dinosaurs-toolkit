"""The handwritten semantics backend (ADR 0003): value computation, flag predicates and string bodies.

ADR 0003 decision 6 freezes this module. No mnemonic, flag rule or value computation is added here;
the pypcode backend replaces it group by group, and phase 5 deletes it.
"""
from capstone.x86 import X86_OP_REG, X86_OP_MEM
from . import semantics
from .machine import ALIASES, StopPath
from .values import Value, const, unknown, op, extract, join, resize, sources

CARRY_BRANCHES = {"jb": True, "jc": True, "jnae": True, "jae": False, "jnb": False, "jnc": False}
# Branches taken when CF or OF is set; logic operations clear both whatever their operands.
CLEARED_BY_LOGIC = {**CARRY_BRANCHES, "jo": True, "jno": False}


def predicate(state, mnemonic):
    flags = state.flags
    if flags is None and state.carry is not None and mnemonic in CARRY_BRANCHES:
        info = {"predicate": mnemonic, "flag": "CF", "carry": state.carry.report()}
        if state.carry.number is None:
            return None, {**info, "reason": "carry unresolved"}
        return bool(state.carry.number) == CARRY_BRANCHES[mnemonic], info
    if flags is None:
        return None, {"predicate": mnemonic, "reason": "flag producer unresolved",
                      "flagProducer": state.unknown_flag_site, "flagGeneration": state.flag_epoch}
    a, b, operation, site = flags
    info = {"predicate": mnemonic, "flagProducer": site, "operation": operation,
            "left": a.report(), "right": b.report()}
    if a.number is not None and b.number is not None:
        x, y = a.number, b.number
    elif operation in ("cmp", "sub", "xor") and a.term == b.term:
        # Any value compared with, subtracted from or XORed with itself yields zero.
        x = y = 0
    elif operation in ("test", "and", "or", "xor") and mnemonic in CLEARED_BY_LOGIC:
        return not CLEARED_BY_LOGIC[mnemonic], info
    else:
        return None, info
    bits = a.bits
    if operation in ("cmp", "sub"):
        raw = x - y
        result = raw % (1 << bits)
        cf = x < y
        of = bool(((x ^ y) & (x ^ result)) & (1 << (bits - 1)))
    elif operation == "add":
        raw = x + y
        result = raw % (1 << bits)
        cf = raw >= 1 << bits
        of = bool((~(x ^ y) & (x ^ result)) & (1 << (bits - 1)))
    elif operation in ("test", "and", "or", "xor"):
        result = {"test": x & y, "and": x & y, "or": x | y, "xor": x ^ y}[operation]
        cf = of = False
    else:
        return None, info
    zf, sf = result == 0, bool(result & (1 << (bits - 1)))
    conditions = {"je": zf, "jz": zf, "jne": not zf, "jnz": not zf,
                  "jb": cf, "jc": cf, "jnae": cf, "jae": not cf, "jnb": not cf, "jnc": not cf,
                  "jbe": cf or zf, "jna": cf or zf, "ja": not cf and not zf, "jnbe": not cf and not zf,
                  "jl": sf != of, "jnge": sf != of, "jge": sf == of, "jnl": sf == of,
                  "jle": zf or sf != of, "jng": zf or sf != of, "jg": not zf and sf == of,
                  "jnle": not zf and sf == of, "js": sf, "jns": not sf, "jo": of, "jno": not of}
    return conditions.get(mnemonic), info


def ordinary(state, ins, image):
    m, operands = ins.mnemonic, ins.operands
    if m in ("cld", "std", "cli", "sti"):
        value = const(1 if m in ("std", "sti") else 0, 1, state.at)
        flag = "DF" if m in ("cld", "std") else "IF"
        if flag == "DF": state.direction_flag = value
        else: state.interrupt_flag = value
        state.event("flag-write", flag=flag, value=value.report(),
                    interpretation="local flag effect only; interrupts and timing are not simulated")
        return
    if m in ("pushf", "pushfd", "popf", "popfd"):
        bits = 32 if (0x66 in ins.prefix) != state.flat else 16
        if m.startswith("push"): state.save_flags(bits)
        else: state.restore_flags(bits)
        return
    if m == "nop":
        return
    if m in ("mov", "movzx", "movsx"):
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment selector assignment requires a descriptor model")
        value = state.get(ins, operands[1], image)
        result = resize(value, operands[0].size * 8, signed=m == "movsx")
        state.put(ins, operands[0], result)
        if not state.value_transfers:
            return

        def location(operand):
            return {"kind": "register", "register": ins.reg_name(operand.reg)} if operand.type == X86_OP_REG else {"kind": "memory"} if operand.type == X86_OP_MEM else {"kind": "immediate"}
        destination_container = ALIASES[ins.reg_name(operands[0].reg)][0] if operands[0].type == X86_OP_REG else None
        state.event("value-transfer", operation=m, source=location(operands[1]), destination=location(operands[0]),
                    destinationContainer=destination_container, destinationContainerValue=state.reg(destination_container).report() if destination_container else None,
                    sourceBits=value.bits, destinationBits=result.bits, sourceValue=value.report(), resultValue=result.report(),
                    conversion="truncate" if result.bits < value.bits else "signExtend" if m == "movsx" else "zeroExtend" if result.bits > value.bits else "sameWidth")
        return
    if m == "xchg":
        values = [state.get(ins, operand, image) for operand in operands]
        addresses = [state.address(ins, operand) if operand.type == X86_OP_MEM else None for operand in operands]
        for index, operand in enumerate(operands):
            value = values[1-index]
            if addresses[index] is None:
                state.put(ins, operand, value)
            else:
                segment, offset, register = addresses[index]
                state.access(segment, offset, operand.size, resize(value, operand.size * 8), addressing_register=register)
        return
    if m == "imul" and len(operands) in (2, 3):
        left, right = (state.get(ins, operand, image) for operand in (operands if len(operands) == 2 else operands[1:]))
        left = resize(left, operands[0].size * 8)
        right = resize(right, left.bits, signed=True)
        result = op("mul", left, right, state.at)
        state.put(ins, operands[0], result)
        state.forget_flags()  # CF/OF require the full signed product; other flags are undefined.
        state.event("arithmetic", operation="imul", left=left.report(), right=right.report(),
                    result=result.report(), modulus=1 << left.bits, flags="unresolved signed-product overflow")
        return
    if m == "lea":
        segment, offset, register = state.address(ins, operands[1])
        state.put(ins, operands[0], offset)
        state.event("address-formation", value=offset.report(), addressingSegment=segment.report(),
                    addressingSegmentRegister=register, destinationRegister=ins.reg_name(operands[0].reg),
                    note="LEA does not access memory; this addressing default does not bind a later dereference")
        return
    if m in ("lds", "les"):
        if state.flat:
            raise StopPath("Descriptor loads are outside the PE32 flat model")
        segment, offset, register = state.address(ins, operands[1])
        if operands[0].size != 2:
            raise StopPath("Only 16:16 pointer loads are supported")
        value = state.access(segment, offset, 4, role="far-pointer", addressing_register=register)
        state.put(ins, operands[0], extract(value, 0, 16))
        state.setreg("ds" if m == "lds" else "es", extract(value, 16, 16), state.at)
        return
    if m == "push":
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment stack operations require a descriptor model")
        state.push(state.get(ins, operands[0], image))
        return
    if m == "pop":
        if state.flat and operands[0].type == X86_OP_REG and ins.reg_name(operands[0].reg) in state.segment_bases:
            raise StopPath("Segment selector assignment requires a descriptor model")
        state.put(ins, operands[0], state.pop(operands[0].size))
        return
    if m == "leave":
        if 0x66 in ins.prefix:
            raise StopPath("Operand-size override on LEAVE is unsupported")
        state.setreg(state.sp, state.reg(state.bp), state.at)
        state.setreg(state.bp, state.pop(state.bits // 8), state.at)
        return
    if m in ("cmp", "test"):
        a, b = (state.get(ins, o, image) for o in operands)
        state.set_flags(a, resize(b, a.bits), m)
        state.event("compare", operation=m, left=a.report(), right=b.report())
        return
    if m in ("add", "sub", "and", "or", "xor", "shl", "sal", "shr", "sar"):
        a, b = (state.get(ins, o, image) for o in operands)
        b = resize(b, a.bits)
        result = op("shl" if m == "sal" else m, a, b, state.at)
        state.put(ins, operands[0], result)
        if m in ("add", "sub", "and", "or", "xor"):
            state.set_flags(a, b, m)
        else:
            shift_carry(state, m, a, b)
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("inc", "dec"):
        a = state.get(ins, operands[0], image)
        result = op("add" if m == "inc" else "sub", a, const(1, a.bits), state.at)
        state.put(ins, operands[0], result)
        # Carry is preserved; the other flags are not modeled for INC/DEC.
        state.forget_flags(keep_carry=True)
        return
    if m in ("cbw", "cwde"):
        # Capstone 5 names these inconsistently in 16-bit mode. Use effective size.
        # The prefix toggles the mode's default operand size (16-bit real mode, 32-bit flat).
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("ax", "eax") if wide else ("al", "ax")
        source_value = state.reg(source)
        value = resize(source_value, 32 if wide else 16, True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=32 if wide else 16, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cwde" if wide else "cbw"), result=value.report(),
                    sourceValue=source_value.report(), sourceBits=source_value.bits, destinationBits=value.bits, conversion="signExtend")
        return
    if m in ("cwd", "cdq"):
        wide = (0x66 in ins.prefix) != state.flat
        source, destination = ("eax", "edx") if wide else ("ax", "dx")
        bits = 32 if wide else 16
        source_value = state.reg(source)
        value = resize(extract(source_value, bits-1, 1), bits, signed=True)
        state.setreg(destination, value, state.at)
        state.event("conversion", sourceRegister=source, destinationRegister=destination,
                    effectiveOperandBits=bits, decoderMnemonic=m,
                    mnemonicWidthMismatch=m != ("cdq" if wide else "cwd"), result=value.report(),
                    sourceValue=source_value.report(), sourceBits=source_value.bits, destinationBits=value.bits, conversion="signFillHighHalf")
        return
    if m in ("clc", "stc", "cmc"):
        if m == "cmc":
            value = op("xor", state.carry_value(), const(1, 1), state.at)
        else:
            value = const(int(m == "stc"), 1, state.at)
        state.forget_flags()
        state.carry = value
        state.event("flag-write", flag="CF", value=value.report(), interpretation="local carry effect")
        return
    if m in ("not", "neg"):
        a = state.get(ins, operands[0], image)
        if m == "not":
            state.put(ins, operands[0], op("xor", a, const((1 << a.bits) - 1, a.bits), state.at))
            return
        result = op("sub", const(0, a.bits), a, state.at)
        state.put(ins, operands[0], result)
        state.set_flags(const(0, a.bits, state.at), a, "sub")
        state.event("arithmetic", operation="neg", left=a.report(), result=result.report(), modulus=1 << a.bits)
        return
    if m in ("adc", "sbb"):
        a, b = (state.get(ins, o, image) for o in operands)
        b = resize(b, a.bits)
        carry = state.carry_value()
        name = "add" if m == "adc" else "sub"
        result = op(name, op(name, a, b, state.at), resize(carry, a.bits), state.at)
        state.put(ins, operands[0], result)
        state.forget_flags()
        if None not in (a.number, b.number, carry.number):
            raw = a.number + b.number + carry.number if m == "adc" else a.number - b.number - carry.number
            state.carry = const(int(raw < 0 or raw >= 1 << a.bits), 1, state.at)
        else:
            state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
        state.event("arithmetic", operation=m, left=a.report(), right=b.report(), carryIn=carry.report(),
                    result=result.report(), carryOut=state.carry.report(), modulus=1 << a.bits)
        return
    if m in ("rol", "ror", "rcl", "rcr"):
        a = state.get(ins, operands[0], image)
        count = state.get(ins, operands[1], image) if len(operands) > 1 else const(1, 8)
        if count.number is None:
            raise StopPath("rotate count unresolved")
        masked = count.number & 31
        if masked == 0:
            return  # The value and flags are unchanged.
        bits = a.bits
        through = m in ("rcl", "rcr")
        n = masked % (bits + 1 if through else bits)
        if through and n == 0:
            # A full rotation through CF restores the value and CF; OF is undefined.
            state.forget_flags(keep_carry=True)
            return
        carry_in = resize(state.carry_value(), bits)
        # Each form is an OR of shifted copies (positive shifts left), built once so the
        # expression does not repeat the operand for every bit rotated.
        parts = {"rol": [(a, n), (a, n - bits)],
                 "ror": [(a, -n), (a, bits - n)],
                 "rcl": [(a, n), (carry_in, n - 1), (a, n - bits - 1)],
                 "rcr": [(a, -n), (carry_in, bits - n), (a, bits + 1 - n)]}[m]
        value = None
        for part, shift in parts:
            if abs(shift) >= bits:
                continue  # Every bit leaves the operand; op() would mask the count instead.
            if shift:
                part = op("shl" if shift > 0 else "shr", part, const(abs(shift), bits), state.at)
            value = part if value is None else op("or", value, part, state.at)
        # CF is the last bit rotated out: the result's low bit for ROL/RCL and its high bit for ROR/RCR.
        out = {"rol": (bits - n) % bits, "ror": (n - 1) % bits, "rcl": bits - n, "rcr": n - 1}[m]
        carry = extract(a, out, 1)
        state.put(ins, operands[0], value)
        state.forget_flags()
        state.carry = Value(1, carry.term, sources(carry, site=state.at))
        state.event("arithmetic", operation=m, left=a.report(), count=n, result=value.report(),
                    carryOut=state.carry.report(), modulus=1 << bits)
        return
    if m in ("mul", "imul") and len(operands) == 1:
        source = state.get(ins, operands[0], image)
        bits = source.bits
        low_reg, high_reg = {8: ("al", "ah"), 16: ("ax", "dx"), 32: ("eax", "edx")}[bits]
        signed = m == "imul"
        multiplicand = state.reg(low_reg)
        product = op("mul", resize(multiplicand, 2 * bits, signed), resize(source, 2 * bits, signed), state.at)
        low = extract(product, 0, bits)
        if bits == 8:
            state.setreg("ax", product, state.at)
        else:
            state.setreg(low_reg, low, state.at)
            state.setreg(high_reg, extract(product, bits, bits), state.at)
        state.forget_flags()
        if product.number is None:
            state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
        else:
            # CF and OF say whether the high half carries information beyond the low half.
            state.carry = const(int(resize(low, 2 * bits, signed).number != product.number), 1, state.at)
        state.event("arithmetic", operation=m, left=multiplicand.report(), right=source.report(),
                    result=product.report(), resultBits=2 * bits, carryOut=state.carry.report())
        return
    if m in ("div", "idiv"):
        divisor = state.get(ins, operands[0], image)
        bits = divisor.bits
        signed = m == "idiv"
        if bits == 8:
            dividend = state.reg("ax")
        else:
            dividend = join([state.reg({16: "ax", 32: "eax"}[bits]), state.reg({16: "dx", 32: "edx"}[bits])])
        quotient_reg, remainder_reg = {8: ("al", "ah"), 16: ("ax", "dx"), 32: ("eax", "edx")}[bits]
        fault = None
        if divisor.number == 0:
            raise StopPath("divide by zero raises interrupt 0; its handler is not modeled")
        if None not in (dividend.number, divisor.number):
            x, y = dividend.number, divisor.number
            if signed:
                x -= (x >> (2 * bits - 1)) << (2 * bits)
                y -= (y >> (bits - 1)) << bits
            q = abs(x) // abs(y) * (1 if (x < 0) == (y < 0) else -1)
            r = x - q * y
            if not ((-(1 << (bits - 1)) <= q < 1 << (bits - 1)) if signed else q < 1 << bits):
                raise StopPath("divide overflow raises interrupt 0; its handler is not modeled")
            quotient, remainder = const(q, bits, state.at), const(r, bits, state.at)
        else:
            wide = resize(divisor, 2 * bits, signed)
            origin = sources(dividend, divisor, site=state.at)
            quotient = extract(Value(2 * bits, ("sdiv" if signed else "udiv", dividend.term, wide.term), origin), 0, bits)
            remainder = extract(Value(2 * bits, ("smod" if signed else "umod", dividend.term, wide.term), origin), 0, bits)
            fault = "possible divide error (interrupt 0) unresolved; this path assumes none"
            state.conditional.append({"site": state.at, "assumption": "no divide error"})
        state.setreg(quotient_reg, quotient, state.at)
        state.setreg(remainder_reg, remainder, state.at)
        state.forget_flags()
        state.event("arithmetic", operation=m, dividend=dividend.report(), divisor=divisor.report(),
                    quotient=quotient.report(), remainder=remainder.report(), fault=fault)
        return
    raise StopPath("Unsupported instruction semantics: " + m)


def shift_carry(state, m, a, count):
    """CF after SHL/SHR/SAR: the last bit shifted out, when the count is known."""
    n = None if count.number is None else count.number & 31
    if n == 0:
        return  # A zero count leaves every flag unchanged.
    state.forget_flags()
    if n is None or n > a.bits:
        state.carry = unknown(f"carry:{state.at}:{state.flag_serial}", 1, state.at)
    elif m in ("shl", "sal"):
        state.carry = Value(1, extract(a, a.bits - n, 1).term, sources(a, site=state.at))
    else:
        state.carry = Value(1, extract(a, n - 1, 1).term, sources(a, site=state.at))


def string_iteration(state, ins, operation, width, source_name, delta):
    si, di = ("esi", "edi") if state.flat else ("si", "di")
    if operation in ("movs", "lods"):
        value = state.access(state.segment(source_name), state.reg(si), width, role="string-source", addressing_register=source_name)
        state.setreg(si, op("add", state.reg(si), const(delta, state.bits), state.at), state.at)
    else:
        value = state.reg({1:"al",2:"ax",4:"eax"}[width])
    if operation in ("movs", "stos"):
        state.access(state.segment("es"), state.reg(di), width, value, role="string-destination", addressing_register="es")
        state.setreg(di, op("add", state.reg(di), const(delta, state.bits), state.at), state.at)
    else:
        state.setreg({1:"al",2:"ax",4:"eax"}[width], value, state.at)


class Handwritten:
    """The handwritten semantics backend: ``ordinary``, ``predicate`` and ``string_iteration``."""

    name = "handwritten"

    def ordinary(self, state, ins, image):
        ordinary(state, ins, image)

    def condition(self, state, mnemonic):
        return predicate(state, mnemonic)

    def string_iteration(self, state, ins, operation, width, source_segment, delta):
        string_iteration(state, ins, operation, width, source_segment, delta)

    def __deepcopy__(self, memo):
        # Backends hold no path state, so every copied path shares one.
        return self


semantics.register(Handwritten(), default=True)
