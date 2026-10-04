using System.Buffers;
using System.Buffers.Binary;
using System.Text;
using System.Text.RegularExpressions;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>One track of a cue sheet.</summary>
/// <param name="Number">The track number.</param>
/// <param name="Mode">The track mode as written, such as <c>MODE1/2352</c> or <c>AUDIO</c>.</param>
/// <param name="StartSector">The sector of the track's <c>INDEX 01</c>, at 75 sectors per second.</param>
public sealed record CueTrack(int Number, string Mode, int StartSector)
{
    /// <summary>The sector of the track's <c>INDEX 00</c>, where its pregap begins, or <see langword="null"/> without one.</summary>
    public int? PregapSector { get; init; }
}

/// <summary>Reads the tracks of a <c>.cue</c> file for a single-file raw disc image.</summary>
public static class CueSheet
{
    /// <summary>
    /// The number of sectors in the leading data track: where track 2 begins, at its <c>INDEX 00</c>
    /// pregap when it has one, since the pregap is stored in the image ahead of the track's audio.
    /// </summary>
    /// <exception cref="InvalidDataException">The first track is not <c>MODE1</c> or there is no second track.</exception>
    public static int DataTrackSectors(IEnumerable<string> lines)
    {
        var tracks = Tracks(lines);
        if (tracks.Length < 2 || !tracks[0].Mode.StartsWith("MODE1", StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("Cue sheet does not begin with a MODE1 data track followed by another track.");
        return tracks[1].PregapSector ?? tracks[1].StartSector;
    }

    /// <summary>Every track that has an <c>INDEX 01</c>, in file order, with its <c>INDEX 00</c> if it has one.</summary>
    /// <exception cref="InvalidDataException">No track has an index.</exception>
    public static CueTrack[] Tracks(IEnumerable<string> lines)
    {
        var result = new List<CueTrack>();
        var number = 0; var mode = ""; int? pregap = null;
        foreach (var line in lines)
        {
            var track = Regex.Match(line, @"^\s*TRACK\s+(\d+)\s+(\S+)", RegexOptions.IgnoreCase);
            if (track.Success) { number = int.Parse(track.Groups[1].Value); mode = track.Groups[2].Value; pregap = null; continue; }
            var index = Regex.Match(line, @"INDEX\s+0([01])\s+(\d+):(\d+):(\d+)", RegexOptions.IgnoreCase);
            if (number > 0 && index.Success)
            {
                var sector = checked((int.Parse(index.Groups[2].Value) * 60 + int.Parse(index.Groups[3].Value)) * 75 + int.Parse(index.Groups[4].Value));
                if (index.Groups[1].Value == "0") { pregap = sector; continue; }
                result.Add(new(number, mode, sector) { PregapSector = pregap }); number = 0;
            }
        }
        if (result.Count == 0) throw new InvalidDataException("Cue sheet contains no indexed tracks.");
        return result.ToArray();
    }
}

/// <summary>Extracts a CD audio track from a raw disc image as a WAVE file.</summary>
public static class CddaWave
{
    /// <summary>Bytes of 16-bit stereo 44.1 kHz audio per raw sector.</summary>
    public const int BytesPerSector = 2352;

    /// <summary>
    /// The most sectors one WAVE file can hold. The RIFF chunk size is a 32-bit field that counts the
    /// 36 header bytes after it as well as the audio, so the audio is at most <c>uint.MaxValue - 36</c>
    /// bytes, about 6.8 hours.
    /// </summary>
    public const long MaximumSectors = WavePcm16Writer.MaximumDataLength / BytesPerSector;

    private const int BufferSize = 128 * 1024;

    /// <summary>
    /// Writes a 16-bit stereo 44.1 kHz PCM WAVE file holding <paramref name="sectorCount"/> raw sectors of
    /// <paramref name="source"/>, starting at <paramref name="startSector"/>. For a track of a cue/bin
    /// image, pass the <see cref="CueBinTrackExtent.StartSector"/> and <see cref="CueBinTrackExtent.Sectors"/>
    /// of its <see cref="CueBinSheet.TrackExtent"/>.
    /// </summary>
    /// <param name="source">The raw image, readable and seekable. It is not disposed.</param>
    /// <param name="output">Receives the WAVE file at its current position and is flushed. It is not disposed.</param>
    /// <param name="startSector">The first raw sector to write.</param>
    /// <param name="sectorCount">Sectors to write, at most <see cref="MaximumSectors"/>.</param>
    /// <exception cref="ArgumentNullException">A stream is null.</exception>
    /// <exception cref="ArgumentException">
    /// <paramref name="source"/> cannot read or seek, or <paramref name="output"/> cannot write.
    /// </exception>
    /// <exception cref="ArgumentOutOfRangeException">
    /// A sector value is negative, or <paramref name="sectorCount"/> exceeds <see cref="MaximumSectors"/>.
    /// Nothing is written.
    /// </exception>
    /// <exception cref="EndOfStreamException">
    /// The range ends past the image. Nothing is written when the image's length shows it at the start;
    /// an image that shrinks while it is read leaves a partial file.
    /// </exception>
    public static void Write(Stream source, Stream output, long startSector, long sectorCount)
    {
        var dataLength = CheckRange(source, output, startSector, sectorCount);
        WavePcm16Writer.WriteHeader(output, (uint)dataLength, 2, 44100);
        source.Position = startSector * BytesPerSector;
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            for (var remaining = dataLength; remaining > 0;)
            {
                var chunk = buffer.AsSpan(0, (int)Math.Min(BufferSize, remaining));
                source.ReadExactly(chunk);
                output.Write(chunk);
                remaining -= chunk.Length;
            }
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        output.Flush();
    }

    /// <summary>
    /// Writes the WAVE file <see cref="Write"/> writes, reading and writing asynchronously and checking
    /// <paramref name="cancellationToken"/> before each read.
    /// </summary>
    /// <param name="source">The raw image, readable and seekable. It is not disposed.</param>
    /// <param name="output">Receives the WAVE file at its current position and is flushed. It is not disposed.</param>
    /// <param name="startSector">The first raw sector to write.</param>
    /// <param name="sectorCount">Sectors to write, at most <see cref="MaximumSectors"/>.</param>
    /// <param name="cancellationToken">Cancels the copy. A copy cancelled after it started leaves a partial file.</param>
    /// <exception cref="ArgumentNullException">A stream is null.</exception>
    /// <exception cref="ArgumentException">
    /// <paramref name="source"/> cannot read or seek, or <paramref name="output"/> cannot write.
    /// </exception>
    /// <exception cref="ArgumentOutOfRangeException">
    /// A sector value is negative, or <paramref name="sectorCount"/> exceeds <see cref="MaximumSectors"/>.
    /// Nothing is written.
    /// </exception>
    /// <exception cref="EndOfStreamException">
    /// The range ends past the image. Nothing is written when the image's length shows it at the start;
    /// an image that shrinks while it is read leaves a partial file.
    /// </exception>
    /// <exception cref="OperationCanceledException"><paramref name="cancellationToken"/> was cancelled.</exception>
    public static async Task WriteAsync(
        Stream source,
        Stream output,
        long startSector,
        long sectorCount,
        CancellationToken cancellationToken = default)
    {
        var dataLength = CheckRange(source, output, startSector, sectorCount);
        cancellationToken.ThrowIfCancellationRequested();
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            WavePcm16Writer.FormatHeader(buffer, (uint)dataLength, 2, 44100);
            await output.WriteAsync(buffer.AsMemory(0, WavePcm16Writer.HeaderSize), cancellationToken).ConfigureAwait(false);
            source.Position = startSector * BytesPerSector;
            for (var remaining = dataLength; remaining > 0;)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var chunk = buffer.AsMemory(0, (int)Math.Min(BufferSize, remaining));
                await source.ReadExactlyAsync(chunk, cancellationToken).ConfigureAwait(false);
                await output.WriteAsync(chunk, cancellationToken).ConfigureAwait(false);
                remaining -= chunk.Length;
            }
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        await output.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    // Every check that can fail before a byte is written. Returns the audio's length in bytes.
    private static long CheckRange(Stream source, Stream output, long startSector, long sectorCount)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(output);
        if (!source.CanRead || !source.CanSeek)
            throw new ArgumentException("The image stream must be readable and seekable.", nameof(source));
        if (!output.CanWrite) throw new ArgumentException("The output stream must be writable.", nameof(output));
        ArgumentOutOfRangeException.ThrowIfNegative(startSector);
        ArgumentOutOfRangeException.ThrowIfNegative(sectorCount);
        ArgumentOutOfRangeException.ThrowIfGreaterThan(sectorCount, MaximumSectors);
        var imageSectors = source.Length / BytesPerSector;
        if (startSector > imageSectors || sectorCount > imageSectors - startSector)
            throw new EndOfStreamException(
                $"{sectorCount} sectors from sector {startSector} end past the image's {imageSectors} sectors.");
        return sectorCount * BytesPerSector;
    }
}

/// <summary>
/// Reads the 2048-byte user data of the leading data track of a raw image with 2352-byte sectors, such
/// as a <c>.bin</c> from a cue/bin pair.
/// </summary>
public sealed class RawMode1Image : IDisposable
{
    private const int RawSector = 2352;
    private const int PayloadOffset = 16;
    private const int PayloadSize = 2048;
    private readonly FileStream _stream;
    /// <summary>Sectors in the data track.</summary>
    public int SectorCount { get; }

    /// <summary>Opens the image for reading.</summary>
    /// <param name="path">The raw image.</param>
    /// <param name="sectorCount">Sectors in the data track, from <see cref="CueSheet.DataTrackSectors"/>.</param>
    public RawMode1Image(string path, int sectorCount)
    {
        if (sectorCount < 0) throw new ArgumentOutOfRangeException(nameof(sectorCount));
        _stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        SectorCount = sectorCount;
    }

    /// <summary>Reads <paramref name="count"/> bytes at <paramref name="offset"/> in the track's user data.</summary>
    /// <exception cref="EndOfStreamException">The range lies outside the data track.</exception>
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

    /// <summary>Closes the image file.</summary>
    public void Dispose() => _stream.Dispose();
}

/// <summary>A file in an ISO 9660 file system.</summary>
/// <param name="Path">Path from the root with <c>/</c> separators, without the <c>;1</c> version suffix.</param>
/// <param name="Extent">The first sector of the file's data.</param>
/// <param name="Size">The file's size in bytes.</param>
public sealed record IsoFile(string Path, uint Extent, uint Size);

/// <summary>
/// Lists and reads the files of an ISO 9660 file system on a <see cref="RawMode1Image"/>. For
/// <c>.iso</c> files, cue/bin pairs or directories, use <see cref="OriginalContentSource.Open(string)"/>.
/// </summary>
public sealed class Iso9660
{
    private const int Sector = 2048;
    private readonly RawMode1Image _image;
    private readonly List<IsoFile> _files = [];
    /// <summary>Every file, depth first in directory order.</summary>
    public IReadOnlyList<IsoFile> Files => _files;

    /// <summary>Reads the primary volume descriptor and the whole directory tree.</summary>
    /// <exception cref="InvalidDataException">The track has no primary volume or a directory record is malformed.</exception>
    public Iso9660(RawMode1Image image)
    {
        _image = image;
        var descriptor = image.Read(16L * Sector, Sector);
        if (descriptor[0] != 1 || Encoding.ASCII.GetString(descriptor, 1, 5) != "CD001")
            throw new InvalidDataException("Track 1 is not an ISO-9660 primary volume.");
        ReadDirectory(ParseRecord(descriptor.AsSpan(156), ""), "", new HashSet<uint>());
    }

    /// <summary>Reads a whole file into memory.</summary>
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
