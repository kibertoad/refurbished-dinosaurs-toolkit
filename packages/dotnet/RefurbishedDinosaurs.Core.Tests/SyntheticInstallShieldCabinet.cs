using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;

namespace RefurbishedDinosaurs.Core.Tests;

/// <summary>One member of a synthetic InstallShield cabinet set.</summary>
internal sealed record CabinetFile(
    string Directory, string Name, byte[] Data, bool Compressed = true, bool Obfuscated = false,
    int? LinkTo = null, bool Invalid = false);

/// <summary>
/// Writes InstallShield 5 and 6 cabinet sets for tests. No open tool writes the format, so this
/// follows the layout Unshield (MIT) reads: a header with a cabinet descriptor and file table, and
/// volumes whose headers record the first and last member they hold, split members included.
/// </summary>
internal static class SyntheticInstallShieldCabinet
{
    public const int DescriptorOffset = 0x200;
    public const int VolumeDataOffset = 0x200;
    private const int FileTableOffset = 0x280;
    private const int ChunkInput = 0x8000;

    public static uint VersionWord(int major) => major == 5 ? 0x01005000u : 0x02000000u | (uint)(major * 100);

    /// <summary>Returns each file of the set by name: <c>data1.hdr</c>, <c>data1.cab</c>, <c>data2.cab</c>...</summary>
    public static Dictionary<string, byte[]> Build(
        int major, IReadOnlyList<CabinetFile> files, long volumeCapacity = long.MaxValue, uint? versionWord = null)
    {
        var raws = files.Select(file => file.LinkTo is null && !file.Invalid ? Raw(file) : []).ToArray();
        var parts = Layout(files, raws, volumeCapacity, out var volumeCount);
        var result = new Dictionary<string, byte[]>
        {
            ["data1.hdr"] = Header(major, files, raws, parts, versionWord ?? VersionWord(major))
        };
        for (var volume = 1; volume <= volumeCount; volume++)
            result[$"data{volume}.cab"] = Volume(major, files, raws, parts, volume, versionWord ?? VersionWord(major));
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

    private static byte[] Raw(CabinetFile file)
    {
        var raw = file.Compressed ? Chunks(file.Data) : file.Data.ToArray();
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

    private static List<Part>[] Layout(IReadOnlyList<CabinetFile> files, byte[][] raws, long capacity, out int volumeCount)
    {
        var parts = files.Select(_ => new List<Part>()).ToArray();
        var volume = 1;
        long used = 0;
        for (var index = 0; index < files.Count; index++)
        {
            if (files[index].LinkTo is not null || files[index].Invalid) continue;
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
                parts[index].Add(new(volume, VolumeDataOffset + used, start, take));
                used += take;
                start += take;
                remaining -= take;
            } while (remaining > 0);
        }
        volumeCount = volume;
        return parts;
    }

    private static byte[] Header(
        int major, IReadOnlyList<CabinetFile> files, byte[][] raws, List<Part>[] parts, uint versionWord)
    {
        var directories = files.Select(file => file.Directory).Distinct().ToList();
        var table = new MemoryStream();
        var writer = new BinaryWriter(table);
        var offsets = new long[directories.Count + (major == 5 ? files.Count : 0)];
        writer.Write(new byte[offsets.Length * 4]);
        long descriptors = 0;
        if (major != 5)
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
            // Version 5 descriptors carry no split flag: readers tell a split member from the volume headers.
            var flags = (ushort)((data.Compressed ? 4 : 0) | (data.Obfuscated ? 2 : 0) |
                                 (major != 5 && parts[target].Count > 1 ? 1 : 0) | (file.Invalid ? 8 : 0));
            var md5 = MD5.HashData(data.Data);
            long dataOffset = file.Invalid ? 0 : first?.Offset ?? VolumeDataOffset;
            if (major == 5)
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
                writer.Write(md5);
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

        var header = new byte[DescriptorOffset + FileTableOffset + tableBytes.Length];
        var span = header.AsSpan();
        BinaryPrimitives.WriteUInt32LittleEndian(span, 0x28635349);
        BinaryPrimitives.WriteUInt32LittleEndian(span[4..], versionWord);
        BinaryPrimitives.WriteUInt32LittleEndian(span[12..], DescriptorOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(span[16..], (uint)(FileTableOffset + tableBytes.Length));
        var descriptor = span[DescriptorOffset..];
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x0c..], FileTableOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x14..], (uint)tableBytes.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x18..], (uint)tableBytes.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x1c..], (uint)directories.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x28..], (uint)files.Count);
        BinaryPrimitives.WriteUInt32LittleEndian(descriptor[0x2c..], (uint)descriptors);
        tableBytes.CopyTo(descriptor[FileTableOffset..]);
        return header;
    }

    private static byte[] Volume(
        int major, IReadOnlyList<CabinetFile> files, byte[][] raws, List<Part>[] parts, int volume, uint versionWord)
    {
        var held = Enumerable.Range(0, files.Count)
            .Where(index => parts[index].Any(part => part.Volume == volume)).ToArray();
        var length = VolumeDataOffset + parts.SelectMany(list => list).Where(part => part.Volume == volume)
            .Sum(part => part.Length);
        var bytes = new byte[length];
        var span = bytes.AsSpan();
        BinaryPrimitives.WriteUInt32LittleEndian(span, 0x28635349);
        BinaryPrimitives.WriteUInt32LittleEndian(span[4..], versionWord);
        foreach (var index in held)
            foreach (var part in parts[index].Where(part => part.Volume == volume))
                raws[index].AsSpan((int)part.Start, (int)part.Length).CopyTo(span[(int)part.Offset..]);

        (long Offset, long Expanded, long Compressed) Record(int index)
        {
            var part = parts[index].Single(item => item.Volume == volume);
            var expanded = files[index].Compressed ? files[index].Data.Length : part.Length;
            return (part.Offset, expanded, part.Length);
        }

        var first = held.Length == 0 ? (0L, 0L, 0L) : Record(held[0]);
        var last = held.Length == 0 ? (0L, 0L, 0L) : Record(held[^1]);
        var fields = span[20..];
        if (major == 5)
        {
            uint[] values =
            [
                VolumeDataOffset, 0, (uint)(held.Length == 0 ? 0 : held[0]), (uint)(held.Length == 0 ? 0 : held[^1]),
                (uint)first.Item1, (uint)first.Item2, (uint)first.Item3, (uint)last.Item1, (uint)last.Item2, (uint)last.Item3
            ];
            for (var index = 0; index < values.Length; index++)
                BinaryPrimitives.WriteUInt32LittleEndian(fields[(index * 4)..], values[index]);
        }
        else
        {
            BinaryPrimitives.WriteUInt64LittleEndian(fields, VolumeDataOffset);
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
