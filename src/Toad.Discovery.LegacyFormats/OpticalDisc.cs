using System.Buffers.Binary;
using System.Text;
using System.Text.RegularExpressions;

namespace Toad.Discovery.LegacyFormats;

public sealed record CueTrack(int Number, string Mode, int StartSector);

public static class CueSheet
{
    public static int DataTrackSectors(IEnumerable<string> lines)
    {
        var tracks = Tracks(lines);
        if (tracks.Length < 2 || !tracks[0].Mode.StartsWith("MODE1", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Cue sheet does not begin with a MODE1 data track followed by another track.");
        return tracks[1].StartSector;
    }

    public static CueTrack[] Tracks(IEnumerable<string> lines)
    {
        var result = new List<CueTrack>();
        var number = 0; var mode = "";
        foreach (var line in lines)
        {
            var track = Regex.Match(line, @"^\s*TRACK\s+(\d+)\s+(\S+)", RegexOptions.IgnoreCase);
            if (track.Success) { number = int.Parse(track.Groups[1].Value); mode = track.Groups[2].Value; continue; }
            var index = Regex.Match(line, @"INDEX\s+01\s+(\d+):(\d+):(\d+)", RegexOptions.IgnoreCase);
            if (number > 0 && index.Success)
            {
                var sector = checked((int.Parse(index.Groups[1].Value) * 60 + int.Parse(index.Groups[2].Value)) * 75 + int.Parse(index.Groups[3].Value));
                result.Add(new(number, mode, sector)); number = 0;
            }
        }
        if (result.Count == 0) throw new InvalidDataException("Cue sheet contains no indexed tracks.");
        return result.ToArray();
    }
}

public static class CddaWave
{
    public const int BytesPerSector = 2352;

    public static void Write(Stream source, Stream output, int startSector, int sectorCount)
    {
        if (!source.CanSeek || !source.CanRead || !output.CanWrite || startSector < 0 || sectorCount < 0)
            throw new ArgumentException("Invalid CDDA streams or sector range.");
        var dataLength = checked(sectorCount * BytesPerSector);
        using var writer = new BinaryWriter(output, Encoding.ASCII, true);
        writer.Write("RIFF"u8); writer.Write(dataLength + 36); writer.Write("WAVEfmt "u8); writer.Write(16);
        writer.Write((short)1); writer.Write((short)2); writer.Write(44100); writer.Write(44100 * 4);
        writer.Write((short)4); writer.Write((short)16); writer.Write("data"u8); writer.Write(dataLength);
        source.Position = (long)startSector * BytesPerSector;
        var remaining = dataLength; var buffer = new byte[128 * 1024];
        while (remaining > 0)
        {
            var read = source.Read(buffer, 0, Math.Min(buffer.Length, remaining));
            if (read == 0) throw new EndOfStreamException();
            output.Write(buffer, 0, read); remaining -= read;
        }
    }
}

public sealed class RawMode1Image : IDisposable
{
    private const int RawSector = 2352;
    private const int PayloadOffset = 16;
    private const int PayloadSize = 2048;
    private readonly FileStream _stream;
    public int SectorCount { get; }

    public RawMode1Image(string path, int sectorCount)
    {
        if (sectorCount < 0) throw new ArgumentOutOfRangeException(nameof(sectorCount));
        _stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        SectorCount = sectorCount;
    }

    public byte[] Read(long offset, int count)
    {
        if (offset < 0 || count < 0 || offset + count > (long)SectorCount * PayloadSize)
            throw new EndOfStreamException();
        var result = new byte[count]; var written = 0;
        while (written < count)
        {
            var logical = offset + written; var sector = logical / PayloadSize;
            var within = (int)(logical % PayloadSize); var take = Math.Min(count - written, PayloadSize - within);
            _stream.Position = sector * RawSector + PayloadOffset + within;
            _stream.ReadExactly(result.AsSpan(written, take)); written += take;
        }
        return result;
    }

    public void Dispose() => _stream.Dispose();
}

public sealed record IsoFile(string Path, uint Extent, uint Size);

public sealed class Iso9660
{
    private const int Sector = 2048;
    private readonly RawMode1Image _image;
    private readonly List<IsoFile> _files = [];
    public IReadOnlyList<IsoFile> Files => _files;

    public Iso9660(RawMode1Image image)
    {
        _image = image;
        var descriptor = image.Read(16L * Sector, Sector);
        if (descriptor[0] != 1 || Encoding.ASCII.GetString(descriptor, 1, 5) != "CD001")
            throw new InvalidDataException("Track 1 is not an ISO-9660 primary volume.");
        ReadDirectory(ParseRecord(descriptor.AsSpan(156), ""), "", new HashSet<uint>());
    }

    public byte[] ReadFile(IsoFile file) => _image.Read((long)file.Extent * Sector, checked((int)file.Size));

    private void ReadDirectory(IsoFile directory, string prefix, HashSet<uint> visited)
    {
        if (!visited.Add(directory.Extent)) return;
        var bytes = ReadFile(directory);
        for (var offset = 0; offset < bytes.Length;)
        {
            var length = bytes[offset];
            if (length == 0) { offset = (offset / Sector + 1) * Sector; continue; }
            if (length < 34 || offset + length > bytes.Length) throw new InvalidDataException("Invalid ISO directory record.");
            var record = bytes.AsSpan(offset, length); var nameLength = record[32];
            if (33 + nameLength > record.Length) throw new InvalidDataException("Invalid ISO filename length.");
            var rawName = Encoding.ASCII.GetString(record.Slice(33, nameLength)); offset += length;
            if (rawName is "\0" or "\u0001") continue;
            var name = rawName.Split(';')[0].TrimEnd('.');
            var entry = ParseRecord(record, prefix.Length == 0 ? name : $"{prefix}/{name}");
            if ((record[25] & 2) != 0) ReadDirectory(entry, entry.Path, visited); else _files.Add(entry);
        }
    }

    private static IsoFile ParseRecord(ReadOnlySpan<byte> record, string path) => new(path,
        BinaryPrimitives.ReadUInt32LittleEndian(record.Slice(2, 4)),
        BinaryPrimitives.ReadUInt32LittleEndian(record.Slice(10, 4)));
}
