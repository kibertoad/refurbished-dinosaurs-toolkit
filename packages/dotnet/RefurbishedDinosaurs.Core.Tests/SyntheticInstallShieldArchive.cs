using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.Core.Tests;

/// <summary>One member of a synthetic InstallShield 3 archive.</summary>
internal sealed record ArchiveFile(
    string Directory, string Name, byte[] Data, bool Stored = false, bool Invalid = false,
    bool CodedLiterals = true, int DictionaryBits = 6);

/// <summary>
/// Writes InstallShield 3 archives for tests, in the layout unshieldv3 (Apache-2.0) and idecomp read:
/// a 255-byte header region, the members' data, then the directory table and the file table. Members
/// are compressed with <see cref="SyntheticPkwareDcl"/> unless stored.
/// </summary>
internal static class SyntheticInstallShieldArchive
{
    public const int DataStart = 255;

    /// <summary>
    /// Builds an archive. Files are written in the order given and directories in the order they first
    /// appear, so files of one directory must be given together for the directory counts to place them.
    /// Every file entry names part 1 as its first and last part.
    /// </summary>
    public static byte[] Build(IReadOnlyList<ArchiveFile> files, ushort archiveFlags = 0, byte totalParts = 1, byte partNumber = 1)
    {
        var directories = files.Select(file => file.Directory).Distinct().ToList();
        using var output = new MemoryStream();
        output.Write(new byte[DataStart]);
        var offsets = new long[files.Count];
        var stored = new byte[files.Count][];
        for (var index = 0; index < files.Count; index++)
        {
            var file = files[index];
            stored[index] = file.Stored ? file.Data : SyntheticPkwareDcl.Implode(file.Data, file.CodedLiterals, file.DictionaryBits);
            offsets[index] = output.Position;
            output.Write(stored[index]);
        }

        var directoriesOffset = output.Position;
        foreach (var directory in directories)
        {
            var name = Encoding.Latin1.GetBytes(directory);
            var entry = new byte[11 + name.Length];
            BinaryPrimitives.WriteUInt16LittleEndian(entry, (ushort)files.Count(file => file.Directory == directory));
            BinaryPrimitives.WriteUInt16LittleEndian(entry.AsSpan(2), (ushort)entry.Length);
            BinaryPrimitives.WriteUInt16LittleEndian(entry.AsSpan(4), (ushort)name.Length);
            name.CopyTo(entry, 6);
            output.Write(entry);
        }

        var filesOffset = output.Position;
        for (var index = 0; index < files.Count; index++)
        {
            var file = files[index];
            var name = Encoding.Latin1.GetBytes(file.Name);
            var entry = new byte[43 + name.Length];
            entry[0] = 1;
            BinaryPrimitives.WriteUInt16LittleEndian(entry.AsSpan(1), (ushort)directories.IndexOf(file.Directory));
            BinaryPrimitives.WriteUInt32LittleEndian(entry.AsSpan(3), (uint)file.Data.Length);
            BinaryPrimitives.WriteUInt32LittleEndian(entry.AsSpan(7), (uint)stored[index].Length);
            BinaryPrimitives.WriteUInt32LittleEndian(entry.AsSpan(11), (uint)offsets[index]);
            // 1 January 1996, 12:00, as a DOS date and time.
            BinaryPrimitives.WriteUInt32LittleEndian(entry.AsSpan(15), 0x6000_2021u);
            BinaryPrimitives.WriteUInt32LittleEndian(entry.AsSpan(19), 0x20);
            BinaryPrimitives.WriteUInt16LittleEndian(entry.AsSpan(23), (ushort)entry.Length);
            BinaryPrimitives.WriteUInt16LittleEndian(entry.AsSpan(25), (ushort)((file.Stored ? 0x10 : 0) | (file.Invalid ? 0x20 : 0)));
            entry[28] = 1;
            entry[29] = (byte)name.Length;
            name.CopyTo(entry, 30);
            output.Write(entry);
        }

        var bytes = output.ToArray();
        var header = bytes.AsSpan();
        BinaryPrimitives.WriteUInt32LittleEndian(header, 0x8c655d13);
        header[4] = 0x3a;
        header[5] = 1;
        header[6] = 2;
        BinaryPrimitives.WriteUInt16LittleEndian(header[10..], archiveFlags);
        BinaryPrimitives.WriteUInt16LittleEndian(header[12..], (ushort)files.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(header[14..], 0x6000_2021u);
        BinaryPrimitives.WriteUInt32LittleEndian(header[18..], (uint)bytes.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(header[22..], (uint)files.Sum(file => file.Data.Length));
        header[30] = totalParts;
        header[31] = partNumber;
        BinaryPrimitives.WriteUInt32LittleEndian(header[41..], (uint)directoriesOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(header[45..], (uint)(filesOffset - directoriesOffset));
        BinaryPrimitives.WriteUInt16LittleEndian(header[49..], (ushort)directories.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(header[51..], (uint)filesOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(header[55..], (uint)(bytes.Length - filesOffset));
        return bytes;
    }

    /// <summary>Where the file table entry of file <paramref name="index"/> starts.</summary>
    public static int FileEntry(byte[] archive, int index)
    {
        var offset = (int)BinaryPrimitives.ReadUInt32LittleEndian(archive.AsSpan(51));
        for (var skip = 0; skip < index; skip++) offset += BinaryPrimitives.ReadUInt16LittleEndian(archive.AsSpan(offset + 23));
        return offset;
    }
}

/// <summary>
/// Compresses data in the PKWARE Data Compression Library format for tests, with greedy matching. The
/// code tables are the format's fixed ones, written as runs the way zlib's contrib/blast lists them.
/// </summary>
internal static class SyntheticPkwareDcl
{
    private static readonly (int Code, int Length)[] LiteralCodes = Canonical(
    [
        11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
        9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
        7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
        8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
        44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
        44, 173
    ]);
    private static readonly (int Code, int Length)[] LengthCodes = Canonical([2, 35, 36, 53, 38, 23]);
    private static readonly (int Code, int Length)[] DistanceCodes = Canonical([2, 20, 53, 230, 247, 151, 248]);
    private static readonly int[] LengthBase = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264];
    private static readonly int[] LengthExtra = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8];

    /// <summary>Compresses <paramref name="data"/>, ending with the end code.</summary>
    public static byte[] Implode(byte[] data, bool codedLiterals, int dictionaryBits, bool matches = true)
    {
        var writer = new BitWriter();
        writer.Bits(codedLiterals ? 1 : 0, 8);
        writer.Bits(dictionaryBits, 8);
        var maximumDistance = 64 << dictionaryBits;
        var position = 0;
        while (position < data.Length)
        {
            var (length, distance) = matches ? LongestMatch(data, position, maximumDistance) : (0, 0);
            if (length >= 2)
            {
                writer.Bits(1, 1);
                WriteLength(writer, length);
                var low = length == 2 ? 2 : dictionaryBits;
                writer.Code(DistanceCodes[(distance - 1) >> low]);
                writer.Bits((distance - 1) & ((1 << low) - 1), low);
                position += length;
                continue;
            }
            writer.Bits(0, 1);
            if (codedLiterals) writer.Code(LiteralCodes[data[position]]);
            else writer.Bits(data[position], 8);
            position++;
        }
        writer.Bits(1, 1);
        WriteLength(writer, 519);
        return writer.ToArray();
    }

    private static void WriteLength(BitWriter writer, int length)
    {
        var symbol = Enumerable.Range(0, 16).Single(index =>
            length >= LengthBase[index] && length < LengthBase[index] + (1 << LengthExtra[index]));
        writer.Code(LengthCodes[symbol]);
        writer.Bits(length - LengthBase[symbol], LengthExtra[symbol]);
    }

    // The longest earlier match of up to 518 bytes. A two-byte match must lie within 256 bytes.
    private static (int Length, int Distance) LongestMatch(byte[] data, int position, int maximumDistance)
    {
        var best = (Length: 0, Distance: 0);
        var limit = Math.Min(518, data.Length - position);
        for (var distance = 1; distance <= Math.Min(maximumDistance, position); distance++)
        {
            var length = 0;
            while (length < limit && data[position + length] == data[position + length - distance]) length++;
            if (length == 2 && distance > 256) continue;
            if (length > best.Length) best = (length, distance);
            if (length == limit) break;
        }
        return best;
    }

    // Canonical codes in the order a reader that inverts each bit counts them: by length, then symbol.
    private static (int Code, int Length)[] Canonical(byte[] runs)
    {
        var lengths = new List<int>();
        foreach (var run in runs)
            for (var repeat = (run >> 4) + 1; repeat > 0; repeat--) lengths.Add(run & 15);
        var codes = new (int, int)[lengths.Count];
        var next = 0;
        for (var length = 1; length <= 13; length++)
        {
            for (var symbol = 0; symbol < lengths.Count; symbol++)
                if (lengths[symbol] == length) codes[symbol] = (next++, length);
            next <<= 1;
        }
        return codes;
    }

    private sealed class BitWriter
    {
        private readonly List<byte> bytes = [];
        private int buffer;
        private int count;

        public void Bits(int value, int width)
        {
            for (var bit = 0; bit < width; bit++) Bit((value >> bit) & 1);
        }

        // A code goes most significant bit first, each bit inverted.
        public void Code((int Code, int Length) code)
        {
            for (var bit = code.Length - 1; bit >= 0; bit--) Bit(((code.Code >> bit) & 1) ^ 1);
        }

        public byte[] ToArray()
        {
            var result = new List<byte>(bytes);
            if (count > 0) result.Add((byte)buffer);
            return [.. result];
        }

        private void Bit(int bit)
        {
            buffer |= bit << count;
            if (++count < 8) return;
            bytes.Add((byte)buffer);
            buffer = 0;
            count = 0;
        }
    }
}
