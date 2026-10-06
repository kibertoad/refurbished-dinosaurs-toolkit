using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace RefurbishedDinosaurs.Core.Tests;

/// <summary>
/// One member of a synthetic InstallShield cabinet set. <paramref name="Stored"/> replaces the bytes
/// written for the member (before obfuscation), so a test can store malformed data under a valid
/// header. An <paramref name="Outside"/> member is stored outside the cabinet: its bytes are not
/// written, and its data offset is the length of the volume that would hold it.
/// </summary>
internal sealed record CabinetFile(
    string Directory, string Name, byte[] Data, bool Compressed = true, bool Obfuscated = false,
    int? LinkTo = null, bool Invalid = false, byte[]? Stored = null, bool Outside = false);

/// <summary>
/// One file group of a synthetic InstallShield cabinet set: a name and a range of file-table indexes,
/// written as given, so a range may be reversed or reach past the table. Groups with the same
/// <paramref name="List"/> are chained in the order given.
/// </summary>
internal sealed record CabinetGroup(string Name, int FirstFile, int LastFile, int List = 0);

/// <summary>
/// Writes InstallShield cabinet sets of major version 0, 5 and 6 for tests. No open tool writes the
/// format, so this follows the layout Unshield (MIT) reads: a header with a cabinet descriptor and
/// file table, and volumes whose headers record the first and last member they hold, split members
/// included. Version 0 uses the version 5 layout with descriptors that end after the data offset
/// and a split flag on split members.
/// </summary>
internal static class SyntheticInstallShieldCabinet
{
    public const int DescriptorOffset = 0x200;
    public const int VolumeDataOffset = 0x200;
    public const int FileTableOffset = 0x280;
    private const int ChunkInput = 0x8000;

    public static uint VersionWord(int major) => major switch
    {
        0 => 0x01000004u,
        5 => 0x01005000u,
        _ => 0x02000000u | (uint)(major * 100)
    };

    // Major versions 0 and 5 share the descriptor and volume header layout.
    private static bool Version5Layout(int major) => major is 0 or 5;

    /// <summary>
    /// Returns each file of the set by name: <c>data1.hdr</c>, <c>data1.cab</c>, <c>data2.cab</c>...
    /// With <paramref name="headerInCabinet"/>, there is no <c>data1.hdr</c>: <c>data1.cab</c> starts
    /// with the header region and holds its members after it. <paramref name="cabinetDescriptorSize"/>
    /// replaces the cabinet descriptor size the common header declares, which by default covers the
    /// descriptor and everything after it (the file table, descriptors and names). <paramref name="groups"/>
    /// are written after the file table, as Unshield reads them: each list's head in the descriptor's
    /// group lists at 0x3e, a 12-byte list entry and a group descriptor per group. Without groups, the
    /// lists are all zero. With <paramref name="markerDelimited"/>, compressed members are written as
    /// marker-delimited chunks (<see cref="MarkerChunks"/>) instead of length-prefixed ones.
    /// </summary>
    public static Dictionary<string, byte[]> Build(
        int major, IReadOnlyList<CabinetFile> files, long volumeCapacity = long.MaxValue, uint? versionWord = null,
        bool headerInCabinet = false, uint? cabinetDescriptorSize = null, IReadOnlyList<CabinetGroup>? groups = null,
        bool markerDelimited = false)
    {
        var word = versionWord ?? VersionWord(major);
        groups ??= [];
        var raws = files.Select(file => file.LinkTo is null && !file.Invalid ? Raw(file, markerDelimited) : []).ToArray();
        // The header's length does not depend on where the members lie, so a first pass sizes it.
        var dataStart = headerInCabinet
            ? Header(major, files, raws, Layout(files, raws, volumeCapacity, VolumeDataOffset, out _), word, groups).Length
            : VolumeDataOffset;
        var parts = Layout(files, raws, volumeCapacity, dataStart, out var volumeCount);
        var header = Header(major, files, raws, parts, word, groups);
        if (cabinetDescriptorSize is { } size) BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(16), size);
        var result = new Dictionary<string, byte[]>();
        if (!headerInCabinet) result["data1.hdr"] = header;
        for (var volume = 1; volume <= volumeCount; volume++)
            result[$"data{volume}.cab"] = Volume(major, files, raws, parts, volume, word,
                volume == 1 && headerInCabinet ? header : null, volume == 1 ? dataStart : VolumeDataOffset);
        return result;
    }

    public static void WriteTo(string directory, Dictionary<string, byte[]> set)
    {
        System.IO.Directory.CreateDirectory(directory);
        foreach (var (name, bytes) in set) File.WriteAllBytes(Path.Combine(directory, name), bytes);
    }

    public static byte[] Chunks(byte[] data)
    {
        using var output = new MemoryStream();
        Span<byte> prefix = stackalloc byte[2];
        for (var offset = 0; offset < data.Length; offset += ChunkInput)
        {
            using var chunk = new MemoryStream();
            using (var deflate = new DeflateStream(chunk, CompressionLevel.Optimal, leaveOpen: true))
                deflate.Write(data, offset, Math.Min(ChunkInput, data.Length - offset));
            BinaryPrimitives.WriteUInt16LittleEndian(prefix, checked((ushort)chunk.Length));
            output.Write(prefix);
            output.Write(chunk.ToArray());
        }
        return output.ToArray();
    }

    /// <summary>
    /// The marker-delimited form of <paramref name="data"/> (what Unshield reads with <c>-O</c>): raw
    /// deflate data with no chunk lengths, flushed after every <paramref name="chunkInput"/> bytes of
    /// input so each chunk ends with the empty stored block <c>00 00 FF FF</c>, and no final block.
    /// Each chunk is compressed on its own unless <paramref name="continuous"/>, which keeps one
    /// stream whose chunks may refer back to earlier ones.
    /// </summary>
    public static byte[] MarkerChunks(
        byte[] data, int chunkInput = ChunkInput, CompressionLevel level = CompressionLevel.Optimal, bool continuous = false)
    {
        using var output = new MemoryStream();
        // DeflateStream.Flush ends what it has written with an empty stored block. The final block that
        // disposing writes is cut off by copying the output before it.
        if (continuous)
        {
            using var deflate = new DeflateStream(output, level, leaveOpen: true);
            for (var offset = 0; offset < data.Length; offset += chunkInput)
            {
                deflate.Write(data, offset, Math.Min(chunkInput, data.Length - offset));
                deflate.Flush();
            }
            return output.ToArray();
        }
        for (var offset = 0; offset < data.Length; offset += chunkInput)
        {
            using var chunk = new MemoryStream();
            using var deflate = new DeflateStream(chunk, level, leaveOpen: true);
            deflate.Write(data, offset, Math.Min(chunkInput, data.Length - offset));
            deflate.Flush();
            output.Write(chunk.ToArray());
        }
        return output.ToArray();
    }

    /// <summary>
    /// The bytes the set stores for <paramref name="file"/>: compressed in the form
    /// <paramref name="markerDelimited"/> chooses, and obfuscated, as a volume would hold them. A test
    /// writes them beside the header as the file of a member stored outside the cabinet.
    /// </summary>
    public static byte[] StoredBytes(CabinetFile file, bool markerDelimited = false) => Raw(file, markerDelimited);

    private static byte[] Raw(CabinetFile file, bool markerDelimited)
    {
        var raw = file.Stored?.ToArray()
                  ?? (!file.Compressed ? file.Data.ToArray() : markerDelimited ? MarkerChunks(file.Data) : Chunks(file.Data));
        if (!file.Obfuscated) return raw;
        uint seed = 0;
        for (var index = 0; index < raw.Length; index++, seed++)
        {
            var value = (byte)(raw[index] + seed % 0x47);
            value = (byte)((value << 2) | (value >> 6));
            raw[index] = (byte)(value ^ 0xd5);
        }
        return raw;
    }

    private static List<Part>[] Layout(
        IReadOnlyList<CabinetFile> files, byte[][] raws, long capacity, long firstDataStart, out int volumeCount)
    {
        var parts = files.Select(_ => new List<Part>()).ToArray();
        var volume = 1;
        long used = 0;
        var outside = new List<(int Index, int Volume)>();
        for (var index = 0; index < files.Count; index++)
        {
            if (files[index].LinkTo is not null || files[index].Invalid) continue;
            if (files[index].Outside)
            {
                outside.Add((index, volume));
                continue;
            }
            long remaining = raws[index].Length;
            long start = 0;
            do
            {
                if (used == capacity)
                {
                    volume++;
                    used = 0;
                }
                var take = Math.Min(remaining, capacity - used);
                parts[index].Add(new(volume, (volume == 1 ? firstDataStart : VolumeDataOffset) + used, start, take));
                used += take;
                start += take;
                remaining -= take;
            } while (remaining > 0);
        }
        volumeCount = volume;
        // A member stored outside gets an empty part at the end of its volume, so its data offset is
        // the volume's length and the volume header's index range covers it.
        foreach (var (index, held) in outside)
            parts[index].Add(new(held, (held == 1 ? firstDataStart : VolumeDataOffset) +
                parts.SelectMany(list => list).Where(part => part.Volume == held).Sum(part => part.Length), 0, 0));
        return parts;
    }

    private static byte[] Header(
        int major, IReadOnlyList<CabinetFile> files, byte[][] raws, List<Part>[] parts, uint versionWord,
        IReadOnlyList<CabinetGroup> groups)
    {
        var directories = files.Select(file => file.Directory).Distinct().ToList();
        var table = new MemoryStream();
        var writer = new BinaryWriter(table);
        var offsets = new long[directories.Count + (Version5Layout(major) ? files.Count : 0)];
        writer.Write(new byte[offsets.Length * 4]);
        long descriptors = 0;
        if (!Version5Layout(major))
        {
            descriptors = table.Position;
            writer.Write(new byte[files.Count * 0x57]);
        }
        for (var index = 0; index < directories.Count; index++)
        {
            offsets[index] = table.Position;
            WriteString(writer, directories[index]);
        }
        var names = new long[files.Count];
        for (var index = 0; index < files.Count; index++)
        {
            names[index] = table.Position;
            WriteString(writer, files[index].Name);
        }

        for (var index = 0; index < files.Count; index++)
        {
            var file = files[index];
            var target = file.LinkTo ?? index;
            var data = files[target];
            var raw = raws[target];
            var first = parts[target].FirstOrDefault();
            // Version 5 descriptors carry no split flag: readers tell a split member from the volume
            // headers. Version 0 and 6 descriptors carry it.
            var flags = (ushort)((data.Compressed ? 4 : 0) | (data.Obfuscated ? 2 : 0) |
                                 (major != 5 && parts[target].Count > 1 ? 1 : 0) | (file.Invalid ? 8 : 0));
            var md5 = MD5.HashData(data.Data);
            long dataOffset = file.Invalid ? 0 : first?.Offset ?? VolumeDataOffset;
            if (Version5Layout(major))
            {
                offsets[directories.Count + index] = table.Position;
                writer.Write((uint)names[index]);
                writer.Write((ushort)directories.IndexOf(file.Directory));
                writer.Write((ushort)0);
                writer.Write(flags);
                writer.Write((uint)data.Data.Length);
                writer.Write((uint)raw.Length);
                writer.Write(new byte[0x14]);
                writer.Write((uint)dataOffset);
                if (major == 5) writer.Write(md5);
            }
            else
            {
                var at = table.Position;
                table.Position = descriptors + index * 0x57;
                writer.Write(flags);
                writer.Write((ulong)data.Data.Length);
                writer.Write((ulong)raw.Length);
                writer.Write((ulong)dataOffset);
                writer.Write(md5);
                writer.Write(new byte[0x10]);
                writer.Write((uint)names[index]);
                writer.Write((ushort)directories.IndexOf(file.Directory));
                writer.Write(new byte[0x0c]);
                writer.Write((uint)(file.LinkTo ?? 0));
                writer.Write(0u);
                writer.Write((byte)(file.LinkTo is null ? 0 : 1));
                writer.Write((ushort)(first?.Volume ?? 1));
                table.Position = at;
            }
        }
        table.Position = 0;
        foreach (var offset in offsets) writer.Write((uint)offset);
        writer.Flush();
        var tableBytes = table.ToArray();
        var (groupBytes, heads) = Groups(major, groups, FileTableOffset + tableBytes.Length);

        var header = new byte[DescriptorOffset + FileTableOffset + tableBytes.Length + groupBytes.Length];
        var span = header.AsSpan();
        BinaryPrimitives.WriteUInt32LittleEndian(span, 0x28635349);
        BinaryPrimitives.WriteUInt32LittleEndian(span[4..], versionWord);
        BinaryPrimitives.WriteUInt32LittleEndian(span[12..], DescriptorOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(span[16..], (uint)(header.Length - DescriptorOffset));
        var descriptor = span[DescriptorOffset..];
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x0c..], FileTableOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x14..], (uint)tableBytes.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x18..], (uint)tableBytes.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x1c..], (uint)directories.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x28..], (uint)files.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x2c..], (uint)descriptors);
        tableBytes.CopyTo(descriptor[FileTableOffset..]);
        groupBytes.CopyTo(descriptor[(FileTableOffset + tableBytes.Length)..]);
        for (var list = 0; list < heads.Length; list++)
            BinaryPrimitives.WriteUInt32LittleEndian(descriptor[(GroupListsOffset + 4 * list)..], heads[list]);
        return header;
    }

    /// <summary>Where the cabinet descriptor holds the heads of its 71 file group lists.</summary>
    public const int GroupListsOffset = 0x3e;

    // The group lists' entries, the group descriptors and their names, placed at start bytes into the
    // cabinet descriptor, with the head of each list. A group descriptor holds its name offset and,
    // at 0x4c and 0x50 (version 0 and 5) or 0x16 and 0x1a (version 6), its first and last file.
    private static (byte[] Bytes, uint[] Heads) Groups(int major, IReadOnlyList<CabinetGroup> groups, int start)
    {
        var (firstAt, size) = Version5Layout(major) ? (0x4c, 0x54) : (0x16, 0x1e);
        var heads = new uint[71];
        var bytes = new MemoryStream();
        var writer = new BinaryWriter(bytes);
        var previous = new Dictionary<int, long>();
        foreach (var group in groups)
        {
            var entry = start + bytes.Position;
            var descriptor = entry + 12;
            var name = descriptor + size;
            if (previous.TryGetValue(group.List, out var before))
            {
                // Chain this entry from the previous one in the same list.
                var at = bytes.Position;
                bytes.Position = before - start + 8;
                writer.Write((uint)entry);
                bytes.Position = at;
            }
            else
                heads[group.List] = (uint)entry;
            previous[group.List] = entry;

            writer.Write((uint)name);
            writer.Write((uint)descriptor);
            writer.Write(0u);
            var fields = new byte[size];
            BinaryPrimitives.WriteUInt32LittleEndian(fields, (uint)name);
            BinaryPrimitives.WriteInt32LittleEndian(fields.AsSpan(firstAt), group.FirstFile);
            BinaryPrimitives.WriteInt32LittleEndian(fields.AsSpan(firstAt + 4), group.LastFile);
            writer.Write(fields);
            WriteString(writer, group.Name);
        }
        writer.Flush();
        return (bytes.ToArray(), heads);
    }

    private static byte[] Volume(
        int major, IReadOnlyList<CabinetFile> files, byte[][] raws, List<Part>[] parts, int volume, uint versionWord,
        byte[]? header, long dataStart)
    {
        var held = Enumerable.Range(0, files.Count)
            .Where(index => parts[index].Any(part => part.Volume == volume)).ToArray();
        var length = dataStart + parts.SelectMany(list => list).Where(part => part.Volume == volume)
            .Sum(part => part.Length);
        var bytes = new byte[length];
        var span = bytes.AsSpan();
        // The header's bytes between the common header and the descriptor are zero, so the volume
        // fields written below fit there.
        header?.CopyTo(span);
        BinaryPrimitives.WriteUInt32LittleEndian(span, 0x28635349);
        BinaryPrimitives.WriteUInt32LittleEndian(span[4..], versionWord);
        foreach (var index in held)
            foreach (var part in parts[index].Where(part => part.Volume == volume))
                raws[index].AsSpan((int)part.Start, (int)part.Length).CopyTo(span[(int)part.Offset..]);

        (long Offset, long Expanded, long Compressed) Record(int index)
        {
            var part = parts[index].Single(item => item.Volume == volume);
            // A member stored outside records its whole sizes, as an unsplit member does.
            if (files[index].Outside) return (part.Offset, files[index].Data.Length, raws[index].Length);
            var expanded = files[index].Compressed ? files[index].Data.Length : part.Length;
            return (part.Offset, expanded, part.Length);
        }

        var first = held.Length == 0 ? (0L, 0L, 0L) : Record(held[0]);
        var last = held.Length == 0 ? (0L, 0L, 0L) : Record(held[^1]);
        var fields = span[20..];
        if (Version5Layout(major))
        {
            uint[] values =
            [
                (uint)dataStart, 0, (uint)(held.Length == 0 ? 0 : held[0]), (uint)(held.Length == 0 ? 0 : held[^1]),
                (uint)first.Item1, (uint)first.Item2, (uint)first.Item3, (uint)last.Item1, (uint)last.Item2, (uint)last.Item3
            ];
            for (var index = 0; index < values.Length; index++)
                BinaryPrimitives.WriteUInt32LittleEndian(fields[(index * 4)..], values[index]);
        }
        else
        {
            BinaryPrimitives.WriteUInt64LittleEndian(fields, (ulong)dataStart);
            BinaryPrimitives.WriteUInt32LittleEndian(fields[8..], (uint)(held.Length == 0 ? 0 : held[0]));
            BinaryPrimitives.WriteUInt32LittleEndian(fields[12..], (uint)(held.Length == 0 ? 0 : held[^1]));
            long[] values = [first.Item1, first.Item2, first.Item3, last.Item1, last.Item2, last.Item3];
            for (var index = 0; index < values.Length; index++)
                BinaryPrimitives.WriteUInt64LittleEndian(fields[(16 + index * 8)..], (ulong)values[index]);
        }
        return bytes;
    }

    private static void WriteString(BinaryWriter writer, string value)
    {
        writer.Write(Encoding.Latin1.GetBytes(value));
        writer.Write((byte)0);
    }

    private sealed record Part(int Volume, long Offset, long Start, long Length);
}
