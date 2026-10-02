using System.Text.RegularExpressions;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>One track of a <see cref="CueBinSheet"/>.</summary>
/// <param name="Number">The track number, from 1.</param>
/// <param name="Type">The track type in upper case: <c>MODE1/2352</c> for the first, <c>AUDIO</c> for the rest.</param>
/// <param name="Indices">Sector of each <c>INDEX</c>, keyed by index number, at 75 sectors per second.</param>
public sealed record CueBinTrack(int Number, string Type, IReadOnlyDictionary<int, int> Indices);

/// <summary>
/// A checked cue sheet for a single-file raw disc image: one <c>MODE1/2352</c> data track followed by
/// any number of audio tracks, all in the one <c>.bin</c> the sheet's <c>FILE</c> line names.
/// </summary>
/// <param name="ReferencedFile">The <c>FILE</c> the sheet names, as a relative path.</param>
/// <param name="Tracks">The tracks in order, numbered consecutively from 1.</param>
public sealed partial record CueBinSheet(string ReferencedFile, IReadOnlyList<CueBinTrack> Tracks)
{
    /// <summary>Bytes in one raw sector of the image.</summary>
    public const int RawSectorSize = 2352;

    /// <summary>The largest cue sheet, in characters, that <see cref="Parse"/> accepts.</summary>
    public const int MaximumCueLength = 1024 * 1024;

    // Source-generated and non-backtracking: a cue sheet is untrusted input, and these patterns mix
    // \s+ with \S+ in a way a backtracking engine can be made to walk quadratically.
    [GeneratedRegex("^\\s*FILE\\s+(?:\"(?<quoted>[^\"]+)\"|(?<plain>\\S+))\\s+(?<type>\\S+)\\s*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.NonBacktracking)]
    private static partial Regex FilePattern();
    [GeneratedRegex("^\\s*(?:FILE|TRACK|INDEX)(?:\\s|$)",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.NonBacktracking)]
    private static partial Regex LayoutCommandPattern();
    [GeneratedRegex("^\\s*TRACK\\s+(?<number>\\d+)\\s+(?<type>\\S+)\\s*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.NonBacktracking)]
    private static partial Regex TrackPattern();
    [GeneratedRegex("^\\s*INDEX\\s+(?<number>\\d+)\\s+(?<minute>\\d+):(?<second>\\d+):(?<frame>\\d+)\\s*$",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant | RegexOptions.NonBacktracking)]
    private static partial Regex IndexPattern();

    /// <summary>
    /// Sectors of the image that belong to the data track, or <see langword="null"/> when there is no
    /// second track to bound it and the data track runs to the end of the image.
    /// </summary>
    /// <remarks>
    /// The data track ends where the next track's content begins. A pregap the second track declares
    /// with <c>INDEX 00</c> is stored in the image ahead of the audio, so <c>INDEX 00</c>, not
    /// <c>INDEX 01</c>, is the boundary; taking <c>INDEX 01</c> would read the pregap as data.
    /// </remarks>
    public int? DataTrackSectors => Tracks.Count > 1
        ? (Tracks[1].Indices.TryGetValue(0, out var pregap) ? pregap : Tracks[1].Indices[1])
        : null;

    /// <summary>Reads and parses a <c>.cue</c> file.</summary>
    /// <exception cref="FileNotFoundException">The file does not exist.</exception>
    /// <exception cref="InvalidDataException">The file is too large or is not a supported cue sheet.</exception>
    public static CueBinSheet Load(string path)
    {
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("Cue sheet not found.", path);
        if (info.Length > MaximumCueLength) throw new InvalidDataException("Cue sheet is too large.");
        return Parse(File.ReadAllText(path));
    }

    /// <summary>
    /// Parses a cue sheet. It must name exactly one <c>BINARY</c> <c>FILE</c> by a safe relative path,
    /// number its 1 to 99 tracks consecutively from 1, begin with a <c>MODE1/2352</c> track whose
    /// <c>INDEX 01</c> is at <c>00:00:00</c> followed only by <c>AUDIO</c> tracks, give each track an
    /// <c>INDEX 01</c>, and keep every index in order, within a track and across tracks. A
    /// <c>FILE</c>, <c>TRACK</c> or <c>INDEX</c> line it cannot read is rejected, not skipped.
    /// </summary>
    /// <exception cref="InvalidDataException">The text breaks one of these rules or a timestamp is invalid.</exception>
    public static CueBinSheet Parse(string text)
    {
        ArgumentNullException.ThrowIfNull(text);
        if (text.Length > MaximumCueLength) throw new InvalidDataException("Cue sheet is too large.");

        string? referencedFile = null;
        var tracks = new List<(int Number, string Type, Dictionary<int, int> Indices)>();
        foreach (var line in text.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n'))
        {
            var file = FilePattern().Match(line);
            if (file.Success)
            {
                if (referencedFile is not null)
                    throw new InvalidDataException("Multi-file cue sheets are not supported.");
                if (!file.Groups["type"].Value.Equals("BINARY", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException($"Unsupported cue FILE type {Truncate(file.Groups["type"].Value)}; expected BINARY.");
                referencedFile = file.Groups["quoted"].Success
                    ? file.Groups["quoted"].Value : file.Groups["plain"].Value;
                continue;
            }

            var track = TrackPattern().Match(line);
            if (track.Success)
            {
                if (!int.TryParse(track.Groups["number"].Value, out var number))
                    throw new InvalidDataException("Cue track number is invalid.");
                tracks.Add((number, track.Groups["type"].Value.ToUpperInvariant(), []));
                continue;
            }

            var index = IndexPattern().Match(line);
            if (!index.Success)
            {
                // A FILE, TRACK or INDEX line the patterns above cannot read changes the layout;
                // skipping it would read the image against the wrong tracks or file.
                if (LayoutCommandPattern().IsMatch(line))
                    throw new InvalidDataException($"Unsupported cue line: {Truncate(line.Trim())}");
                continue;
            }
            if (tracks.Count == 0) throw new InvalidDataException("Cue INDEX appears before TRACK.");
            if (!int.TryParse(index.Groups["number"].Value, out var indexNumber) ||
                !int.TryParse(index.Groups["minute"].Value, out var minute) ||
                !int.TryParse(index.Groups["second"].Value, out var second) ||
                !int.TryParse(index.Groups["frame"].Value, out var frame))
                throw new InvalidDataException("Cue timestamp is invalid.");
            if (second >= 60 || frame >= 75)
                throw new InvalidDataException("Cue timestamp is outside the MM:SS:FF range.");
            int sector;
            try { sector = checked(minute * 60 * 75 + second * 75 + frame); }
            catch (OverflowException exception) { throw new InvalidDataException("Cue timestamp is too large.", exception); }
            var current = tracks[^1];
            if (!current.Indices.TryAdd(indexNumber, sector))
                throw new InvalidDataException($"Duplicate INDEX {indexNumber:D2} in track {current.Number:D2}.");
        }

        if (string.IsNullOrWhiteSpace(referencedFile))
            throw new InvalidDataException("Cue sheet has no FILE entry.");
        var normalized = referencedFile.Replace('\\', '/');
        if (Path.IsPathFullyQualified(referencedFile) || normalized.Split('/').Any(part => part is "" or "." or ".."))
            throw new InvalidDataException("Cue FILE must be a safe relative path.");
        if (tracks.Count is 0 or > 99)
            throw new InvalidDataException("Cue sheet must contain between 1 and 99 tracks.");
        if (tracks[0].Type != "MODE1/2352")
            throw new InvalidDataException("First cue track must be MODE1/2352.");

        var previous = -1;
        for (var offset = 0; offset < tracks.Count; offset++)
        {
            var (number, type, indices) = tracks[offset];
            if (number != offset + 1)
                throw new InvalidDataException("Cue track numbers must be consecutive and start at 1.");
            if (!indices.TryGetValue(1, out var start))
                throw new InvalidDataException($"Track {number:D2} has no INDEX 01.");
            if (indices.TryGetValue(0, out var pregap) && pregap > start)
                throw new InvalidDataException($"Track {number:D2} has INDEX 00 after INDEX 01.");
            // The data track's user data is read from the first sector of the BIN, so its
            // INDEX 01 must be there; a stored pregap ahead of it would be read as the volume.
            if (offset == 0 && start != 0)
                throw new InvalidDataException("Track 01 INDEX 01 must be at 00:00:00.");
            if (offset > 0 && type != "AUDIO")
                throw new InvalidDataException($"Unsupported non-audio track {number:D2}: {type}.");
            // Every index of a track must follow every index of the track before it, and the
            // indices of one track must rise with their numbers.
            foreach (var (_, sector) in indices.OrderBy(entry => entry.Key))
            {
                if (sector < previous) throw new InvalidDataException("Cue track indices are not in order.");
                previous = sector;
            }
        }

        return new(referencedFile, tracks.Select(track =>
            new CueBinTrack(track.Number, track.Type, track.Indices)).ToArray());
    }

    private static string Truncate(string value) => value.Length <= 80 ? value : value[..80] + "...";

    /// <summary>
    /// Checks that <paramref name="path"/> is a whole number of raw sectors and that every index of
    /// the sheet lies inside it.
    /// </summary>
    /// <exception cref="FileNotFoundException">The image does not exist.</exception>
    /// <exception cref="InvalidDataException">The image's length or an index does not fit.</exception>
    public void ValidateBin(string path)
    {
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("BIN image not found.", path);
        if (info.Length == 0 || info.Length % RawSectorSize != 0)
            throw new InvalidDataException($"BIN length must be a positive multiple of {RawSectorSize} bytes.");
        var sectors = info.Length / RawSectorSize;
        foreach (var track in Tracks)
            foreach (var index in track.Indices)
                if (index.Value >= sectors)
                    throw new InvalidDataException(
                        $"Track {track.Number:D2} INDEX {index.Key:D2} starts outside the BIN image.");
    }

    /// <summary>
    /// Finds the sheet and image a path names: a <c>.cue</c> file, a <c>.bin</c> file, or a directory
    /// holding them. The other file is the one the sheet's <c>FILE</c> names, else the one with the
    /// same name, else the only one in the directory.
    /// </summary>
    internal static (string CuePath, string BinPath, CueBinSheet Sheet) Resolve(string input)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(input);
        var fullInput = Path.GetFullPath(input);
        var directory = Directory.Exists(fullInput) ? fullInput : Path.GetDirectoryName(fullInput);
        if (directory is null || !Directory.Exists(directory))
            throw new FileNotFoundException("Cue/bin source does not exist.", fullInput);

        string? cuePath = null;
        string? binPath = null;
        if (File.Exists(fullInput))
        {
            var extension = Path.GetExtension(fullInput);
            if (extension.Equals(".cue", StringComparison.OrdinalIgnoreCase)) cuePath = fullInput;
            else if (extension.Equals(".bin", StringComparison.OrdinalIgnoreCase)) binPath = fullInput;
            else throw new InvalidDataException("Cue/bin source must be a directory or a .cue or .bin file.");
        }
        else if (!Directory.Exists(fullInput))
            throw new FileNotFoundException("Cue/bin source does not exist.", fullInput);

        var cues = Enumerate(directory, ".cue");
        cuePath ??= MatchStem(binPath, cues) ?? Single(cues, "cue sheet");
        var sheet = Load(cuePath);
        var referenced = ResolveReference(directory, sheet.ReferencedFile);
        // A sheet found for a given BIN must not describe another BIN that is present.
        if (binPath is not null && referenced is not null && !string.Equals(referenced, binPath,
                OperatingSystem.IsLinux() ? StringComparison.Ordinal : StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException(
                $"Cue sheet {Path.GetFileName(cuePath)} describes {sheet.ReferencedFile}, not {Path.GetFileName(binPath)}.");
        if (binPath is null)
        {
            var bins = Enumerate(directory, ".bin");
            binPath = referenced ?? MatchStem(cuePath, bins) ?? Single(bins, "BIN image");
        }
        return (cuePath, binPath, sheet);
    }

    private static string[] Enumerate(string directory, string extension) =>
        Directory.EnumerateFiles(directory, "*", SearchOption.TopDirectoryOnly)
            .Where(path => Path.GetExtension(path).Equals(extension, StringComparison.OrdinalIgnoreCase))
            .Order(StringComparer.OrdinalIgnoreCase).ToArray();

    private static string? MatchStem(string? source, IReadOnlyList<string> candidates)
    {
        if (source is null) return null;
        var stem = Path.GetFileNameWithoutExtension(source);
        return candidates.FirstOrDefault(path =>
            Path.GetFileNameWithoutExtension(path).Equals(stem, StringComparison.OrdinalIgnoreCase));
    }

    private static string Single(IReadOnlyList<string> paths, string label) => paths.Count == 1
        ? paths[0] : throw new InvalidDataException($"Could not select exactly one {label}.");

    private static string? ResolveReference(string directory, string reference)
    {
        var root = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var path = Path.GetFullPath(Path.Combine(directory, reference.Replace('/', Path.DirectorySeparatorChar)));
        if (!path.StartsWith(root, OperatingSystem.IsWindows()
                ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
            throw new InvalidDataException("Cue FILE escapes the source directory.");
        return File.Exists(path) ? path : null;
    }
}

/// <summary>
/// Presents the user data of a MODE1/2352 track as a flat stream of 2048-byte sectors.
/// </summary>
/// <remarks>
/// Each raw sector is read whole and its sync pattern and mode byte checked before the payload is
/// handed on. A cue sheet only declares what a track is; without this check an image that is really
/// MODE2, or a BIN that does not match its sheet, would be read at the wrong offset and surface as a
/// malformed ISO 9660 volume instead of as an image to dump again.
/// </remarks>
internal sealed class RawMode1UserDataStream(Stream source, long sectorCount) : Stream
{
    private const int UserDataOffset = 16;
    private const int LogicalSectorSize = 2048;
    private static ReadOnlySpan<byte> SyncPattern =>
        [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00];
    private readonly byte[] sector = new byte[CueBinSheet.RawSectorSize];
    private long sectorIndex = -1;
    private long position;

    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => checked(sectorCount * LogicalSectorSize);
    public override long Position { get => position; set => position = ValidatePosition(value); }

    public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));

    public override int Read(Span<byte> buffer)
    {
        var remaining = (int)Math.Min(buffer.Length, Length - position);
        var total = remaining;
        while (remaining > 0)
        {
            var index = position / LogicalSectorSize;
            var within = (int)(position % LogicalSectorSize);
            var count = Math.Min(remaining, LogicalSectorSize - within);
            ReadRawSector(index);
            sector.AsSpan(UserDataOffset + within, count).CopyTo(buffer[(total - remaining)..]);
            position += count;
            remaining -= count;
        }
        return total;
    }

    public override long Seek(long offset, SeekOrigin origin) => Position = origin switch
    {
        SeekOrigin.Begin => offset,
        SeekOrigin.Current => checked(position + offset),
        SeekOrigin.End => checked(Length + offset),
        _ => throw new ArgumentOutOfRangeException(nameof(origin))
    };

    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) source.Dispose();
        base.Dispose(disposing);
    }

    // Keeps the last sector read, so small sequential reads do not read and check it again.
    private void ReadRawSector(long index)
    {
        if (index == sectorIndex) return;
        sectorIndex = -1;
        source.Position = checked(index * CueBinSheet.RawSectorSize);
        source.ReadExactly(sector);
        if (!sector.AsSpan(0, SyncPattern.Length).SequenceEqual(SyncPattern))
            throw new InvalidDataException(
                $"Sector {index} has no MODE1/2352 sync pattern; the BIN does not match its cue sheet.");
        if (sector[15] != 1)
            throw new InvalidDataException(
                $"Sector {index} is mode {sector[15]}, but the cue sheet declares MODE1/2352.");
        sectorIndex = index;
    }

    private long ValidatePosition(long value) => value >= 0 && value <= Length
        ? value : throw new IOException("Seek lies outside the raw data track.");
}
