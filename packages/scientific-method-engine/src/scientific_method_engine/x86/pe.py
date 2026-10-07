"""Bounded PE32/i386 source mappings. No loader execution or inferred code entries."""
from copy import deepcopy
import struct


def pe32(data):
    """Parse the headers and section table of a PE32/i386 executable.

    ``data`` is the whole file as bytes. Returns a dict with ``format``, ``imageBase``,
    ``sizeOfImage``, ``sizeOfHeaders``, ``mappingProvenance``, ``loadAssumption`` and ``sections``;
    each section has ``index``, ``name``, ``rva``, ``va``, ``virtualSize``, ``rawStart``,
    ``rawSize``, ``loadedRawSize`` (raw bytes actually loaded), ``mappedExtent``, ``rawIgnored`` and
    ``executable``. A section whose PointerToRawData is 0 has no file bytes: its SizeOfRawData still
    counts toward ``mappedExtent``, its ``loadedRawSize`` is 0, and its ``rawIgnored`` says why
    (None on every other section). What a loader puts in such a section is not assumed. Raises
    ``ValueError`` for anything other than an MZ-stubbed PE32 for i386, and for truncated,
    overlapping or ambiguous sections, a nonzero PointerToRawData below SizeOfHeaders among them.
    The mapping assumes the preferred image base; rebasing and imports are not simulated.
    """
    def span(at, size):
        if at < 0 or size < 0 or at + size > len(data):
            raise ValueError("PE source range is truncated")
    def word(at):
        span(at, 2)
        return struct.unpack_from('<H', data, at)[0]
    def dword(at):
        span(at, 4)
        return struct.unpack_from('<I', data, at)[0]
    span(0, 64)
    if data[:2] != b'MZ':
        raise ValueError("PE source needs an MZ header")
    nt = dword(60)
    span(nt, 24)
    if nt < 64 or data[nt:nt + 4] != b'PE\0\0' or word(nt + 4) != 0x14c:
        raise ValueError("Only PE32/i386 sources are supported")
    count, optional_size = word(nt + 6), word(nt + 20)
    optional = nt + 24
    span(optional, optional_size)
    if not 1 <= count <= 96 or optional_size < 96 or word(optional) != 0x10b:
        raise ValueError("Invalid PE32 section count or optional header")
    directories = dword(optional + 92)
    if directories > 16 or 96 + directories * 8 > optional_size:
        raise ValueError("PE data directories escape optional header")
    base, size, headers = dword(optional + 28), dword(optional + 56), dword(optional + 60)
    table = optional + optional_size
    span(table, count * 40)
    if not size or base + size > 1 << 32 or headers < table + count * 40 or headers > len(data) or headers > size:
        raise ValueError("Invalid PE image/header extent")
    sections, file_sizes = [], []
    for i in range(count):
        at = table + i * 40
        virtual_size, rva, raw_size, raw = (dword(at + j) for j in (8, 12, 16, 20))
        extent = max(virtual_size, raw_size)
        # A zero PointerToRawData gives the section no file bytes, whatever SizeOfRawData says.
        ignored = "PointerToRawData is 0" if raw_size and not raw else None
        file_size = 0 if ignored else raw_size
        # Raw bytes past VirtualSize are file-alignment padding, not loaded source.
        loaded = min(file_size, virtual_size) if virtual_size else file_size
        if not extent or rva < headers or rva + extent > size:
            raise ValueError("PE section escapes image or overlaps headers")
        if file_size:
            span(raw, file_size)
            if raw < headers:
                raise ValueError("PE section raw bytes overlap headers")
        section = {"index": i, "name": data[at:at + 8].split(b'\0')[0].decode('ascii', errors='replace'),
                   "rva": rva, "va": base + rva, "virtualSize": virtual_size,
                   "rawStart": raw, "rawSize": raw_size, "loadedRawSize": loaded, "mappedExtent": extent,
                   "rawIgnored": ignored, "executable": bool(dword(at + 36) & 0x20000000)}
        for prior, prior_size in file_sizes:
            if max(rva, prior['rva']) < min(rva + extent, prior['rva'] + prior['mappedExtent']):
                raise ValueError("Ambiguous PE virtual section mapping")
            if file_size and prior_size and max(raw, prior['rawStart']) < min(raw + file_size, prior['rawStart'] + prior_size):
                raise ValueError("Overlapping PE raw sections")
        sections.append(section)
        file_sizes.append((section, file_size))
    return {"format": "PE32/i386", "imageBase": base, "sizeOfImage": size,
            "sizeOfHeaders": headers, "sections": sections,
            "mappingProvenance": "source COFF/PE optional header and section table",
            "loadAssumption": "preferred image base; rebasing, imports and runtime patching are not simulated"}


def prepare_pe(data, config):
    metadata = pe32(data)
    result = deepcopy(config)
    if config.get('bits', 32) != 32 or config.get('addressModel', 'flat32') != 'flat32':
        raise ValueError("PE32 requires the 32-bit flat model")
    if config.get('relocations') or config.get('targetSelector'):
        raise ValueError("MZ relocation/overlay inputs cannot be used for PE32")
    regions = result.get('regions', [])
    if not isinstance(regions, list) or not all(isinstance(r, dict) for r in regions):
        raise ValueError("Regions must be a list of objects")
    result.update(bits=32, addressModel='flat32', peMetadata=metadata)
    for region in regions:
        start, end = region.get('start'), region.get('end')
        if type(start) is not int or type(end) is not int or end <= start:
            raise ValueError("PE code bounds must be integer file offsets")
        section = next((s for s in metadata['sections'] if s['executable'] and s['rawStart'] <= start < end <= s['rawStart'] + s['loadedRawSize']), None)
        if section is None:
            raise ValueError("PE code region must be within loaded raw executable section bytes")
        va = section['va'] + start - section['rawStart']
        if region.get('ip', va) != va or region.get('segment', 0) != 0 or region.get('resident', False):
            raise ValueError("PE region mapping disagrees with source section table")
        region.update(ip=va, segment=0, resident=False, sectionIndex=section['index'])
    return result
