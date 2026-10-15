using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace RefurbishedDinosaurs.Core.Tests;

// One track of a CHD the writer makes: its CHT2 type, its frames in the BIN (a stored pregap
// included) and the stored pregap.
internal sealed record SyntheticChdTrack(string Type, int Frames, int StoredPregap = 0);

// How the writer stores the hunks, and the damage or unsupported features it writes on purpose.
internal sealed record SyntheticChdOptions
{
    // Store every hunk with the cdzl codec (deflate), or, when false, stored uncompressed in a CHD
    // with codecs; Uncompressed writes a CHD with no codecs and a plain map.
    public bool Deflate { get; init; } = true;
    public bool Uncompressed { get; init; }
    // Write a hunk equal to an earlier one as a reference to it.
    public bool SelfReferences { get; init; } = true;
    // Codec tags for the header's four slots; null for cdzl alone.
    public string[]? Codecs { get; init; }
    public uint Version { get; init; } = 5;
    public bool ParentSha1 { get; init; }
    // The hunk whose CRC-16 the map records wrongly, or -1.
    public int WrongCrcHunk { get; init; } = -1;
    // The metadata tag of the track entries; CHTR writes the old form without pregaps.
    public string TrackTag { get; init; } = "CHT2";
    // Extra metadata entries, by tag, written before the tracks.
    public (string Tag, string Text)[] ExtraMetadata { get; init; } = [];
    public int FramesPerHunk { get; init; } = 8;
}

// Writes minimal MAME CHD version 5 CD images for tests: the frame layout, track metadata and hunk
// map chdman writes, with deflate (cdzl) or uncompressed hunks.
internal static class SyntheticChdWriter
{
    private const int FrameSize = 2448;
    private const int SectorSize = 2352;
    private const int HeaderSize = 124;

    public static byte[] Write(byte[] bin, IReadOnlyList<SyntheticChdTrack> tracks, SyntheticChdOptions? options = null)
    {
        options ??= new SyntheticChdOptions();
        var hunkBytes = options.FramesPerHunk * FrameSize;

        // The CHD's frames: each track's sectors, audio byte-swapped to big-endian, with an empty
        // subcode block, then zero frames up to a multiple of 4.
        var frames = new List<byte[]>();
        var binSector = 0;
        foreach (var track in tracks)
        {
            for (var index = 0; index < track.Frames; index++, binSector++)
            {
                var frame = new byte[FrameSize];
                bin.AsSpan(binSector * SectorSize, SectorSize).CopyTo(frame);
                if (track.Type == "AUDIO")
                    for (var at = 0; at < SectorSize; at += 2) (frame[at], frame[at + 1]) = (frame[at + 1], frame[at]);
                frames.Add(frame);
            }
            while (frames.Count % 4 != 0) frames.Add(new byte[FrameSize]);
        }
        var logical = frames.SelectMany(frame => frame).ToArray();
        var hunkCount = (logical.Length + hunkBytes - 1) / hunkBytes;
        var hunks = new byte[hunkCount][];
        for (var hunk = 0; hunk < hunkCount; hunk++)
        {
            hunks[hunk] = new byte[hunkBytes];
            logical.AsSpan(hunk * hunkBytes, Math.Min(hunkBytes, logical.Length - hunk * hunkBytes)).CopyTo(hunks[hunk]);
        }

        var metadata = new List<(string Tag, byte[] Data)>();
        foreach (var (tag, text) in options.ExtraMetadata) metadata.Add((tag, Encoding.ASCII.GetBytes(text + "\0")));
        for (var index = 0; index < tracks.Count; index++)
        {
            var track = tracks[index];
            var text = options.TrackTag == "CHTR"
                ? $"TRACK:{index + 1} TYPE:{track.Type} SUBTYPE:NONE FRAMES:{track.Frames}"
                : $"TRACK:{index + 1} TYPE:{track.Type} SUBTYPE:NONE FRAMES:{track.Frames} PREGAP:{track.StoredPregap} " +
                  $"PGTYPE:{(track.StoredPregap > 0 ? "V" + track.Type : "MODE1")} PGSUB:NONE POSTGAP:0";
            metadata.Add((options.TrackTag, Encoding.ASCII.GetBytes(text + "\0")));
        }

        using var file = new MemoryStream();
        file.Write(new byte[HeaderSize]);
        long mapOffset;
        if (options.Uncompressed)
        {
            mapOffset = file.Position;
            file.Write(new byte[hunkCount * 4]);
            var metaOffset = WriteMetadata(file, metadata);
            var map = new byte[hunkCount * 4];
            for (var hunk = 0; hunk < hunkCount; hunk++)
            {
                if (!hunks[hunk].AsSpan().ContainsAnyExcept((byte)0)) continue;
                var block = (file.Length + hunkBytes - 1) / hunkBytes;
                file.SetLength(block * hunkBytes);
                file.Position = file.Length;
                file.Write(hunks[hunk]);
                BinaryPrimitives.WriteUInt32BigEndian(map.AsSpan(4 * hunk), (uint)block);
            }
            file.Position = mapOffset;
            file.Write(map);
            WriteHeader(file, options, [], logical, hunkBytes, mapOffset, metaOffset);
            return file.ToArray();
        }

        var metadataOffset = WriteMetadata(file, metadata);
        var firstOffset = file.Position;
        var entries = new (byte Type, int Length, long Offset, ushort Crc)[hunkCount];
        for (var hunk = 0; hunk < hunkCount; hunk++)
        {
            var crc = Crc16(hunks[hunk]);
            if (hunk == options.WrongCrcHunk) crc ^= 0x5555;
            var earlier = options.SelfReferences
                ? Array.FindIndex(hunks, 0, hunk, other => other.AsSpan().SequenceEqual(hunks[hunk])) : -1;
            if (earlier >= 0)
            {
                entries[hunk] = (5, 0, earlier, 0);
                continue;
            }
            var offset = file.Position;
            if (options.Deflate)
            {
                var data = CdDeflate(hunks[hunk], options.FramesPerHunk);
                file.Write(data);
                entries[hunk] = (0, data.Length, offset, crc);
            }
            else
            {
                file.Write(hunks[hunk]);
                entries[hunk] = (4, hunkBytes, offset, crc);
            }
        }
        mapOffset = file.Position;
        file.Write(CompressedMap(entries, firstOffset));
        WriteHeader(file, options, options.Codecs ?? ["cdzl"], logical, hunkBytes, mapOffset, metadataOffset);
        return file.ToArray();
    }

    private static long WriteMetadata(MemoryStream file, List<(string Tag, byte[] Data)> metadata)
    {
        var first = file.Position;
        for (var index = 0; index < metadata.Count; index++)
        {
            var (tag, data) = metadata[index];
            var entry = new byte[16];
            Encoding.ASCII.GetBytes(tag).CopyTo(entry, 0);
            entry[4] = 1;
            entry[5] = (byte)(data.Length >> 16);
            entry[6] = (byte)(data.Length >> 8);
            entry[7] = (byte)data.Length;
            var next = index + 1 < metadata.Count ? file.Position + 16 + data.Length : 0;
            BinaryPrimitives.WriteInt64BigEndian(entry.AsSpan(8), next);
            file.Write(entry);
            file.Write(data);
        }
        return metadata.Count == 0 ? 0 : first;
    }

    private static void WriteHeader(
        MemoryStream file, SyntheticChdOptions options, string[] codecs, byte[] logical, int hunkBytes,
        long mapOffset, long metaOffset)
    {
        var header = new byte[HeaderSize];
        "MComprHD"u8.CopyTo(header);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(8), HeaderSize);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(12), options.Version);
        for (var slot = 0; slot < codecs.Length; slot++) Encoding.ASCII.GetBytes(codecs[slot]).CopyTo(header, 16 + 4 * slot);
        BinaryPrimitives.WriteInt64BigEndian(header.AsSpan(32), logical.Length);
        BinaryPrimitives.WriteInt64BigEndian(header.AsSpan(40), mapOffset);
        BinaryPrimitives.WriteInt64BigEndian(header.AsSpan(48), metaOffset);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(56), (uint)hunkBytes);
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(60), FrameSize);
        SHA1.HashData(logical).CopyTo(header, 64);
        SHA1.HashData(logical).CopyTo(header, 84);
        if (options.ParentSha1) header.AsSpan(104, 20).Fill(0xAB);
        file.Position = 0;
        file.Write(header);
    }

    // The cdzl form of a hunk: an empty ECC bitmap, the 2-byte length of the deflated sectors, the
    // deflated sectors, then the deflated subcode.
    private static byte[] CdDeflate(byte[] hunk, int frames)
    {
        var sectors = new byte[frames * SectorSize];
        var subcode = new byte[frames * 96];
        for (var frame = 0; frame < frames; frame++)
        {
            hunk.AsSpan(frame * FrameSize, SectorSize).CopyTo(sectors.AsSpan(frame * SectorSize));
            hunk.AsSpan(frame * FrameSize + SectorSize, 96).CopyTo(subcode.AsSpan(frame * 96));
        }
        var baseData = Deflate(sectors);
        var subData = Deflate(subcode);
        using var output = new MemoryStream();
        output.Write(new byte[(frames + 7) / 8]);
        output.WriteByte((byte)(baseData.Length >> 8));
        output.WriteByte((byte)baseData.Length);
        output.Write(baseData);
        output.Write(subData);
        return output.ToArray();
    }

    private static byte[] Deflate(byte[] data)
    {
        using var output = new MemoryStream();
        using (var deflater = new DeflateStream(output, CompressionLevel.Optimal, leaveOpen: true)) deflater.Write(data);
        return output.ToArray();
    }

    // A v5 compressed map whose Huffman tree gives all 16 types 4-bit codes, so each type is its
    // own value in 4 bits, followed by each hunk's 24-bit length and CRC, or 24-bit self reference.
    private static byte[] CompressedMap((byte Type, int Length, long Offset, ushort Crc)[] entries, long firstOffset)
    {
        var bits = new BitWriter();
        for (var code = 0; code < 16; code++) bits.Write(4, 4);
        foreach (var entry in entries) bits.Write(entry.Type, 4);
        var raw = new byte[entries.Length * 12];
        for (var hunk = 0; hunk < entries.Length; hunk++)
        {
            var (type, length, offset, crc) = entries[hunk];
            if (type == 5) bits.Write((uint)offset, 24);
            else
            {
                if (type != 4) bits.Write((uint)length, 24);
                bits.Write(crc, 16);
            }
            var entry = raw.AsSpan(hunk * 12, 12);
            entry[0] = type;
            entry[1] = (byte)(length >> 16); entry[2] = (byte)(length >> 8); entry[3] = (byte)length;
            for (var index = 0; index < 6; index++) entry[4 + index] = (byte)(offset >> (40 - 8 * index));
            BinaryPrimitives.WriteUInt16BigEndian(entry[10..], crc);
        }
        var data = bits.ToArray();
        var header = new byte[16];
        BinaryPrimitives.WriteUInt32BigEndian(header, (uint)data.Length);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(4), (ushort)(firstOffset >> 32));
        BinaryPrimitives.WriteUInt32BigEndian(header.AsSpan(6), (uint)firstOffset);
        BinaryPrimitives.WriteUInt16BigEndian(header.AsSpan(10), Crc16(raw));
        header[12] = 24;
        header[13] = 24;
        header[14] = 24;
        return [.. header, .. data];
    }

    private static ushort Crc16(ReadOnlySpan<byte> data)
    {
        ushort crc = 0xFFFF;
        foreach (var value in data)
        {
            crc ^= (ushort)(value << 8);
            for (var bit = 0; bit < 8; bit++) crc = (ushort)((crc & 0x8000) != 0 ? (crc << 1) ^ 0x1021 : crc << 1);
        }
        return crc;
    }

    private sealed class BitWriter
    {
        private readonly List<byte> bytes = [];
        private int used = 8;

        public void Write(uint value, int count)
        {
            for (var bit = count - 1; bit >= 0; bit--)
            {
                if (used == 8)
                {
                    bytes.Add(0);
                    used = 0;
                }
                if (((value >> bit) & 1) != 0) bytes[^1] |= (byte)(0x80 >> used);
                used++;
            }
        }

        public byte[] ToArray() => [.. bytes];
    }
}
