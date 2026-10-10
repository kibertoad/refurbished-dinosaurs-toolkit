using System.Text;
using System.Text.RegularExpressions;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>One track of a <see cref="CueBinSheet"/>.</summary>
/// <param name="Number">The track number, from 1.</param>
/// <param name="Type">
/// The track type in upper case: <c>MODE1/2352</c> or <c>MODE2/2352</c> for the first, <c>AUDIO</c> for the rest.
/// </param>
/// <param name="Indices">Sector of each <c>INDEX</c>, keyed by index number, at 75 sectors per second.</param>
public sealed record CueBinTrack(int Number, string Type, IReadOnlyDictionary<int, int> Indices);

/// <summary>The sectors of one track in a cue/bin image, as <see cref="CueBinSheet.TrackExtent"/> gives them.</summary>
/// <param name="Track">The track number.</param>
/// <param name="StartSector">The first sector, the track's <c>INDEX 01</c>.</param>
/// <param name="EndSector">The sector after the last one.</param>
public sealed record CueBinTrackExtent(int Track, long StartSector, long EndSector)
{
    /// <summary>Sectors in the track.</summary>
    public long Sectors => EndSector - StartSector;
}

/// <summary>One <c>FILE</c> entry of a <see cref="CueBinSheet"/> and the tracks it holds.</summary>
/// <param name="Path">The file, as the relative path the sheet gives.</param>
/// <param name="Type">The file type in upper case, such as <c>BINARY</c>, <c>WAVE</c> or <c>MP3</c>.</param>
/// <param name="FirstTrack">The number of the first track the entry holds.</param>
/// <param name="LastTrack">The number of the last track the entry holds.</param>
public sealed record CueBinFile(string Path, string Type, int FirstTrack, int LastTrack);

/// <summary>
/// A checked cue sheet for a raw disc image: one <c>MODE1/2352</c> or <c>MODE2/2352</c> data track
/// followed by any number of audio tracks. The first <c>FILE</c> is the <c>.bin</c> image, which
/// holds the data track and may hold audio tracks after it. Each later <c>FILE</c> holds only audio
/// tracks; the sheet records them, but they are not part of the image and are never read.
/// </summary>
/// <param name="ReferencedFile">The first <c>FILE</c> the sheet names, the image, as a relative path.</param>
/// <param name="Tracks">
/// Every track of the sheet in order, numbered consecutively from 1, including those in later
/// <c>FILE</c> entries. Only the first <see cref="ImageTracks"/> are in the image.
/// </param>
public sealed partial record CueBinSheet(string ReferencedFile, IReadOnlyList<CueBinTrack> Tracks)
{
    /// <summary>
    /// The sheet's <c>FILE</c> entries in order. The first is <see cref="ReferencedFile"/>. A sheet
    /// built without this property set has the one <c>BINARY</c> entry holding every track, and keeps
    /// it when <c>with</c> replaces <see cref="Tracks"/>.
    /// </summary>
    /// <exception cref="ArgumentException">
    /// When set: the entries are empty, the first is not <see cref="ReferencedFile"/>, or they do not
    /// hold tracks 1 to the number of <see cref="Tracks"/> in order, each entry at least one.
    /// </exception>
    /// <exception cref="InvalidOperationException">
    /// When read: <c>with</c> replaced <see cref="ReferencedFile"/> or <see cref="Tracks"/> after the
    /// entries were set, so they no longer describe the sheet.
    /// </exception>
    public IReadOnlyList<CueBinFile> Files
    {
        get
        {
            if (files is null) return [new(ReferencedFile, "BINARY", 1, Tracks.Count)];
            if (FilesMismatch(files) is { } problem)
                throw new InvalidOperationException($"The sheet's FILE entries no longer match it: {problem}");
            return files;
        }
        init
        {
            ArgumentNullException.ThrowIfNull(value);
            if (FilesMismatch(value) is { } problem) throw new ArgumentException(problem, nameof(Files));
            files = value.ToArray();
        }
    }

    private readonly CueBinFile[]? files;

    private string? FilesMismatch(IReadOnlyList<CueBinFile> entries)
    {
        if (entries.Count == 0) return "a sheet has at least one FILE.";
        if (entries[0].Path != ReferencedFile) return "the first FILE must be ReferencedFile.";
        var next = 1;
        foreach (var entry in entries)
        {
            if (entry is null || entry.FirstTrack != next || entry.LastTrack < entry.FirstTrack)
                return "the FILE entries must hold consecutive tracks from 1, each at least one.";
            next = entry.LastTrack + 1;
        }
        return next == Tracks.Count + 1 ? null : "the FILE entries must hold every track and no other.";
    }

    /// <summary>
    /// The number of tracks stored in the image, the first <c>FILE</c>: tracks 1 to this number.
    /// Later tracks are in other files, which <see cref="TrackExtent"/> refuses.
    /// </summary>
    public int ImageTracks => Files[0].LastTrack;

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
    /// Sectors of the image that belong to the data track, or <see langword="null"/> when the image
    /// holds no second track to bound it and the data track runs to the end of the image. A second
    /// track in another <c>FILE</c> does not bound it.
    /// </summary>
    /// <remarks>
    /// The data track ends where the next track's content begins. A pregap the second track declares
    /// with <c>INDEX 00</c> is stored in the image ahead of the audio, so <c>INDEX 00</c>, not
    /// <c>INDEX 01</c>, is the boundary; taking <c>INDEX 01</c> would read the pregap as data.
    /// </remarks>
    public int? DataTrackSectors => ImageTracks > 1 ? StoredStart(1) : null;

    /// <summary>
    /// The sectors of track <paramref name="number"/> in the image: from its <c>INDEX 01</c> to the
    /// next track's <c>INDEX 00</c>, that track's <c>INDEX 01</c> when it has no <c>INDEX 00</c>, or
    /// the end of the image for the image's last track. A track's own pregap, before its
    /// <c>INDEX 01</c>, is left out. A track whose next track's pregap or audio begins at its own
    /// <c>INDEX 01</c> has no sectors.
    /// </summary>
    /// <param name="number">The track number, from 1 to the number of tracks.</param>
    /// <param name="imageSectors">Raw sectors in the image, which bound the image's last track.</param>
    /// <exception cref="ArgumentOutOfRangeException">The sheet has no such track, or <paramref name="imageSectors"/> is negative.</exception>
    /// <exception cref="InvalidDataException">
    /// The track is in a later <c>FILE</c>, not in the image, or it ends past the image or before it starts.
    /// </exception>
    public CueBinTrackExtent TrackExtent(int number, long imageSectors)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(number, 1);
        ArgumentOutOfRangeException.ThrowIfGreaterThan(number, Tracks.Count);
        ArgumentOutOfRangeException.ThrowIfNegative(imageSectors);
        if (number > ImageTracks)
            throw new InvalidDataException(
                $"Track {number:D2} is stored in {Truncate(FileOf(number).Path)}, not in the BIN image " +
                $"{Truncate(ReferencedFile)}; only the tracks of the sheet's first FILE are read.");
        long start = Tracks[number - 1].Indices[1];
        long end = number < ImageTracks ? StoredStart(number) : imageSectors;
        if (end > imageSectors || start > end)
            throw new InvalidDataException($"Track {number:D2} ends past the BIN image or before it starts.");
        return new(number, start, end);
    }

    // The sector layout of the data track's user data, from the type the sheet declares for it. A
    // MODE2/2352 track is read as CD-XA Form 1; its sectors are checked as they are read.
    internal RawDataTrackMode DataTrackMode =>
        Tracks[0].Type == "MODE2/2352" ? RawDataTrackMode.Mode2Form1 : RawDataTrackMode.Mode1;

    // The first sector the image stores for the track at this list position: its INDEX 00 when it
    // declares a pregap, else its INDEX 01.
    private int StoredStart(int position) =>
        Tracks[position].Indices.TryGetValue(0, out var pregap) ? pregap : Tracks[position].Indices[1];

    /// <summary>The <c>FILE</c> entry that holds track <paramref name="number"/>.</summary>
    /// <exception cref="ArgumentOutOfRangeException">No entry holds the track.</exception>
    public CueBinFile FileOf(int number) =>
        Files.FirstOrDefault(file => number >= file.FirstTrack && number <= file.LastTrack)
        ?? throw new ArgumentOutOfRangeException(nameof(number), number, "The cue sheet has no such track.");

    /// <summary>Reads and parses a cue sheet file, whatever its extension.</summary>
    /// <exception cref="FileNotFoundException">The file does not exist.</exception>
    /// <exception cref="InvalidDataException">The file is too large or is not a supported cue sheet.</exception>
    public static CueBinSheet Load(string path) => Read(path).Sheet;

    // Reads the file once and parses those bytes, so a caller holding the bytes holds what was parsed.
    // The text is decoded as File.ReadAllText decodes it: UTF-8 unless a byte order mark says otherwise.
    internal static (byte[] Bytes, CueBinSheet Sheet) Read(string path)
    {
        if (!File.Exists(path)) throw new FileNotFoundException("Cue sheet not found.", path);
        byte[] bytes;
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            // Sized from the file's length plus one byte, so a sheet read whole needs no second buffer.
            // The buffer grows when the file grew after the length was taken, up to one byte past the
            // limit, so a file over the limit is caught however it got there.
            var buffer = new byte[(int)Math.Min(stream.Length, MaximumCueLength) + 1];
            var length = 0;
            while (true)
            {
                if (length == buffer.Length)
                {
                    if (length > MaximumCueLength) throw new InvalidDataException("Cue sheet is too large.");
                    Array.Resize(ref buffer, (int)Math.Min(2L * buffer.Length, MaximumCueLength + 1L));
                }
                var read = stream.Read(buffer, length, buffer.Length - length);
                if (read == 0) break;
                length += read;
            }
            bytes = buffer[..length];
        }
        using var reader = new StreamReader(new MemoryStream(bytes, writable: false), Encoding.UTF8,
            detectEncodingFromByteOrderMarks: true);
        return (bytes, Parse(reader.ReadToEnd()));
    }

    /// <summary>
    /// Parses a cue sheet. Every <c>FILE</c> it names must be a relative path
    /// <see cref="PortableAssetPath.Relative"/> accepts and hold at least one track. The first
    /// <c>FILE</c> is the image: it must be <c>BINARY</c> and begin with a <c>MODE1/2352</c> or
    /// <c>MODE2/2352</c> track whose <c>INDEX 01</c> is at <c>00:00:00</c>. Every other track must be
    /// <c>AUDIO</c>, so a later <c>FILE</c>, of any type, holds only audio tracks. The sheet must
    /// number its 1 to 99 tracks consecutively from 1, give each track an <c>INDEX 01</c>, and keep
    /// every index in order, within a track and across the tracks of one <c>FILE</c>; each
    /// <c>FILE</c>'s indices count from its own start. A <c>TRACK</c> before the first <c>FILE</c>
    /// and an <c>INDEX</c> between a <c>FILE</c> and its first <c>TRACK</c> are rejected, and a <c>FILE</c>, <c>TRACK</c> or <c>INDEX</c> line it cannot read is
    /// rejected, not skipped.
    /// </summary>
    /// <exception cref="InvalidDataException">The text breaks one of these rules or a timestamp is invalid.</exception>
    public static CueBinSheet Parse(string text)
    {
        ArgumentNullException.ThrowIfNull(text);
        if (text.Length > MaximumCueLength) throw new InvalidDataException("Cue sheet is too large.");

        // Each FILE with the list position of its first track.
        var files = new List<(string Path, string Type, int FirstPosition)>();
        var tracks = new List<(int Number, string Type, Dictionary<int, int> Indices)>();
        foreach (var line in text.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n'))
        {
            var file = FilePattern().Match(line);
            if (file.Success)
            {
                var path = file.Groups["quoted"].Success ? file.Groups["quoted"].Value : file.Groups["plain"].Value;
                var type = file.Groups["type"].Value.ToUpperInvariant();
                if (files.Count == 0 && type != "BINARY")
                    throw new InvalidDataException(
                        $"Unsupported cue FILE type {Truncate(type)} for the first FILE, the image; expected BINARY.");
                if (files.Count > 0 && files[^1].FirstPosition == tracks.Count)
                    throw new InvalidDataException($"Cue FILE {Truncate(files[^1].Path)} holds no track.");
                files.Add((path, type, tracks.Count));
                continue;
            }

            var track = TrackPattern().Match(line);
            if (track.Success)
            {
                if (files.Count == 0) throw new InvalidDataException("Cue TRACK appears before FILE.");
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
            // An index counts from the start of the FILE it follows, so one between a later FILE and
            // that FILE's first TRACK cannot belong to the track before, which another FILE holds.
            if (files[^1].FirstPosition == tracks.Count)
                throw new InvalidDataException(
                    $"Cue INDEX appears in FILE {Truncate(files[^1].Path)} before its first TRACK; a track's " +
                    "indices must follow it in the FILE that holds it.");
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

        if (files.Count == 0 || string.IsNullOrWhiteSpace(files[0].Path))
            throw new InvalidDataException("Cue sheet has no FILE entry.");
        if (files[^1].FirstPosition == tracks.Count)
            throw new InvalidDataException($"Cue FILE {Truncate(files[^1].Path)} holds no track.");
        foreach (var (path, _, _) in files)
        {
            try { PortableAssetPath.Relative(path); }
            catch (InvalidDataException exception)
            {
                throw new InvalidDataException(
                    $"Cue FILE {Truncate(path)} must be a safe relative path. {exception.Message}", exception);
            }
        }
        if (tracks.Count > 99)
            throw new InvalidDataException("Cue sheet must contain between 1 and 99 tracks.");
        if (tracks[0].Type is not ("MODE1/2352" or "MODE2/2352"))
            throw new InvalidDataException(
                $"First cue track must be MODE1/2352 or MODE2/2352, not {Truncate(tracks[0].Type)}.");

        var previous = -1;
        var fileIndex = 0;
        for (var offset = 0; offset < tracks.Count; offset++)
        {
            var (number, type, indices) = tracks[offset];
            // Each FILE's indices count from the start of that file.
            if (fileIndex + 1 < files.Count && files[fileIndex + 1].FirstPosition == offset)
            {
                fileIndex++;
                previous = -1;
            }
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
                throw new InvalidDataException(fileIndex == 0
                    ? $"Unsupported non-audio track {number:D2}: {Truncate(type)}."
                    : $"Unsupported non-audio track {number:D2}: {Truncate(type)} in FILE " +
                      $"{Truncate(files[fileIndex].Path)}. Only the first FILE may hold the data track; " +
                      "later FILE entries may hold only AUDIO tracks.");
            // Every index of a track must follow every index of the track before it in the same
            // FILE, and the indices of one track must rise with their numbers.
            foreach (var (_, sector) in indices.OrderBy(entry => entry.Key))
            {
                if (sector < previous) throw new InvalidDataException("Cue track indices are not in order.");
                previous = sector;
            }
        }

        var entries = new CueBinFile[files.Count];
        for (var i = 0; i < files.Count; i++)
        {
            var last = i + 1 < files.Count ? files[i + 1].FirstPosition : tracks.Count;
            entries[i] = new(files[i].Path, files[i].Type, files[i].FirstPosition + 1, last);
        }
        return new(files[0].Path, tracks.Select(track =>
            new CueBinTrack(track.Number, track.Type, track.Indices)).ToArray()) { Files = entries };
    }

    private static string Truncate(string value) => value.Length <= 80 ? value : value[..80] + "...";

    /// <summary>
    /// Checks that <paramref name="path"/> is a whole number of raw sectors and that every index of
    /// the tracks in the image, those of the first <c>FILE</c>, lies inside it.
    /// </summary>
    /// <exception cref="FileNotFoundException">The image does not exist.</exception>
    /// <exception cref="InvalidDataException">The image's length or an index does not fit.</exception>
    public void ValidateBin(string path)
    {
        var info = new FileInfo(path);
        if (!info.Exists) throw new FileNotFoundException("BIN image not found.", path);
        ValidateBinLength(info.Length);
    }

    // Checks an image of the given length, for a caller that took the length from its own stat of the file.
    internal void ValidateBinLength(long length)
    {
        if (length == 0 || length % RawSectorSize != 0)
            throw new InvalidDataException($"BIN length must be a positive multiple of {RawSectorSize} bytes.");
        var sectors = length / RawSectorSize;
        foreach (var track in Tracks.Take(ImageTracks))
            foreach (var index in track.Indices)
                if (index.Value >= sectors)
                    throw new InvalidDataException(
                        $"Track {track.Number:D2} INDEX {index.Key:D2} starts outside the BIN image.");
    }

    /// <summary>
    /// Finds the sheet and image a path names: a <c>.bin</c> file, a directory holding a <c>.cue</c>
    /// and the image, or any other file, which is read as the cue sheet whatever its extension. The
    /// image is the file the sheet's first <c>FILE</c> names, else the <c>.bin</c> with the same
    /// name, else the only <c>.bin</c> in the directory; for a <c>.bin</c> input, the sheet is the
    /// <c>.cue</c> with the same name, else the only one. <c>CueBytes</c> are the bytes of the
    /// sheet that were parsed.
    /// </summary>
    internal static (string CuePath, string BinPath, CueBinSheet Sheet, byte[] CueBytes) Resolve(string input)
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
            if (Path.GetExtension(fullInput).Equals(".bin", StringComparison.OrdinalIgnoreCase)) binPath = fullInput;
            else cuePath = fullInput;
        }
        else if (!Directory.Exists(fullInput))
            throw new FileNotFoundException("Cue/bin source does not exist.", fullInput);

        byte[] cueBytes;
        CueBinSheet sheet;
        if (cuePath is not null && !Path.GetExtension(cuePath).Equals(".cue", StringComparison.OrdinalIgnoreCase))
        {
            // A file given by any other name is read as a cue sheet. Say so when it is not one, since
            // the input may be an image passed in place of its sheet.
            try { (cueBytes, sheet) = Read(cuePath); }
            catch (InvalidDataException exception)
            {
                throw new InvalidDataException(
                    $"{Path.GetFileName(cuePath)} was read as a cue sheet, since a cue/bin input that is " +
                    $"a file and not a .bin is the sheet: {exception.Message}", exception);
            }
        }
        else
        {
            var cues = Enumerate(directory, ".cue");
            cuePath ??= MatchStem(binPath, cues) ?? Single(cues, "cue sheet");
            (cueBytes, sheet) = Read(cuePath);
        }
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
        return (cuePath, binPath, sheet, cueBytes);
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
        if (!File.Exists(path)) return null;
        // On a case-insensitive file system the sheet may spell the name in another case. Report the
        // name as the directory lists it, so the chosen path names the file on disk.
        var name = Path.GetFileName(path);
        var listed = Directory.EnumerateFiles(Path.GetDirectoryName(path)!, "*", SearchOption.TopDirectoryOnly)
            .Where(entry => Path.GetFileName(entry).Equals(name, StringComparison.OrdinalIgnoreCase)).ToArray();
        return listed.FirstOrDefault(entry => Path.GetFileName(entry).Equals(name, StringComparison.Ordinal))
            ?? (listed.Length == 1 ? listed[0] : path);
    }
}

// The sector layouts a data track's user data is read from.
internal enum RawDataTrackMode
{
    // MODE1/2352: sync, header, 2048 bytes of user data at byte 16, EDC and ECC.
    Mode1,
    // MODE2/2352 CD-XA Form 1: sync, header, the 4-byte subheader written twice, 2048 bytes of user
    // data at byte 24, EDC and ECC.
    Mode2Form1,
}

/// <summary>
/// Presents the user data of a MODE1/2352 or MODE2/2352 Form 1 track as a flat stream of 2048-byte
/// sectors.
/// </summary>
/// <remarks>
/// Each raw sector is read whole and its layout checked before the payload is handed on: the sync
/// pattern and the mode byte for both modes, and for MODE2 the two copies of the subheader agreeing
/// and its submode marking Form 1. A cue sheet only declares what a track is; without these checks
/// an image whose sectors are another mode or form, or a BIN that does not match its sheet, would be
/// read at the wrong offset and surface as a malformed ISO 9660 volume instead of as an image to
/// dump again. The EDC and ECC are not checked, and neither is the address in the header. With
/// <c>leaveOpen</c>, disposing the stream leaves <c>source</c> open, as <see cref="Iso9660"/> needs
/// for the image the caller keeps.
/// <para>
/// With <c>emptyForm2AsZeros</c>, a MODE2 Form 2 sector whose 2324 data bytes are all zero reads as
/// 2048 zero bytes instead of throwing. Volume reads pass it, so padding a CD-XA master leaves in
/// Form 2 inside the volume space reads as the zero blocks a MODE1 image of the disc holds there.
/// A Form 2 sector that carries any nonzero data byte still throws.
/// </para>
/// </remarks>
internal sealed class RawDataTrackUserDataStream(
    Stream source, long sectorCount, RawDataTrackMode mode, bool leaveOpen = false,
    bool emptyForm2AsZeros = false) : Stream
{
    private const int LogicalSectorSize = 2048;
    private const int ModeOffset = 15;
    private const int SubheaderOffset = 16;
    private const int SubheaderSize = 4;
    // Bit 5 of the submode byte marks a Form 2 sector.
    private const byte Form2Submode = 0x20;
    // A Form 2 sector's data follows the subheader copies and fills the sector up to its 4-byte EDC.
    private const int Form2DataOffset = 24;
    private const int Form2DataSize = 2324;
    private static ReadOnlySpan<byte> SyncPattern =>
        [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00];
    private readonly byte[] sector = new byte[CueBinSheet.RawSectorSize];
    private readonly int userDataOffset = mode == RawDataTrackMode.Mode1 ? 16 : 24;
    private readonly byte modeByte = mode == RawDataTrackMode.Mode1 ? (byte)1 : (byte)2;
    private readonly string declared = mode == RawDataTrackMode.Mode1 ? "MODE1/2352" : "MODE2/2352";
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
            sector.AsSpan(userDataOffset + within, count).CopyTo(buffer[(total - remaining)..]);
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
        if (disposing && !leaveOpen) source.Dispose();
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
                $"Sector {index} has no {declared} sync pattern; the BIN does not match its cue sheet.");
        if (sector[ModeOffset] != modeByte)
            throw new InvalidDataException(
                $"Sector {index} is mode {sector[ModeOffset]}, but the cue sheet declares {declared}.");
        if (mode == RawDataTrackMode.Mode2Form1) CheckSubheader(index);
        sectorIndex = index;
    }

    private void CheckSubheader(long index)
    {
        var first = sector.AsSpan(SubheaderOffset, SubheaderSize);
        var second = sector.AsSpan(SubheaderOffset + SubheaderSize, SubheaderSize);
        if (!first.SequenceEqual(second))
            throw new InvalidDataException(
                $"Sector {index} is MODE2 but its two subheader copies differ, so it is not a CD-XA " +
                "Form 1 sector. Only MODE2/2352 tracks of CD-XA Form 1 sectors are supported.");
        if ((first[2] & Form2Submode) == 0) return;
        if (!emptyForm2AsZeros)
            throw new InvalidDataException(
                $"Sector {index} is a MODE2 Form 2 sector. Form 2 sectors carry 2324 bytes of user data " +
                "without ECC and cannot be read as ISO 9660 user data; only Form 1 sectors are " +
                "supported, so a file stored in Form 2 sectors cannot be read from this source.");
        // An empty Form 2 sector's 2048 bytes at the Form 1 user data offset lie inside its zero
        // data, so the sector is handed on as it is.
        if (sector.AsSpan(Form2DataOffset, Form2DataSize).ContainsAnyExcept((byte)0))
            throw new InvalidDataException(
                $"Sector {index} is a MODE2 Form 2 sector that carries data. Form 2 sectors carry 2324 " +
                "bytes of user data without ECC and cannot be read as ISO 9660 user data; the volume " +
                "reads only Form 1 sectors and empty Form 2 sectors, whose data bytes are all zero.");
    }

    private long ValidatePosition(long value) => value >= 0 && value <= Length
        ? value : throw new IOException("Seek lies outside the raw data track.");
}
