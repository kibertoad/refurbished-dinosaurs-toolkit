using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>
/// The forms of disc image a <see cref="ContentSourceKinds.DiscImage"/> source reads, as
/// <see cref="OriginalContentSource.DiscImageFormat"/> gives them.
/// </summary>
public static class DiscImageFormats
{
    /// <summary>An ISO 9660 image of 2048-byte sectors, such as a <c>.iso</c> file. It holds no CD audio.</summary>
    public const string Iso = "iso";
    /// <summary>
    /// A cue sheet and the files it names: a raw <c>MODE1/2352</c> or <c>MODE2/2352</c> data track, or
    /// an ISO of 2048-byte sectors declared <c>MODE1/2048</c> or <c>MODE2/2048</c>, followed by audio
    /// tracks in the same <c>BINARY</c> file, in later <c>BINARY</c> files of raw sectors, or in PCM
    /// <c>WAVE</c> files.
    /// </summary>
    public const string CueSheet = "cue";
    /// <summary>A CloneCD image: a <c>.ccd</c> table of contents and the <c>.img</c> of raw sectors it describes.</summary>
    public const string CloneCd = "clonecd";
    /// <summary>An Alcohol 120% image: a version 1 <c>.mds</c> descriptor and the <c>.mdf</c> it describes.</summary>
    public const string AlcoholMds = "alcohol-mds";
    /// <summary>A Nero image: one <c>.nrg</c> file of disc-at-once tracks with a version 1 or 2 footer.</summary>
    public const string NeroNrg = "nero-nrg";
}

// One run of sectors of a disc image's raw stream: Sectors raw sectors read from File, each Stride
// bytes apart from Offset, of which the first 2352 are the sector, or sectors stored in no file,
// whose reads throw with Reason.
internal sealed record RawSegment(long StartSector, long Sectors, ImageSnapshot? File, long Offset, int Stride, string? Reason);

// How a disc image stores its data track's user data.
internal abstract record DiscDataTrack(long Sectors);

// The data track is raw sectors from sector 0 of the raw stream, in this layout. LayoutSource names
// the descriptor that declared it in the messages of a sector that does not match, or is null for a
// cue sheet, whose messages name the BIN and its cue sheet.
internal sealed record RawDiscDataTrack(RawDataTrackMode Mode, long Sectors, string? LayoutSource = null) : DiscDataTrack(Sectors);

// The data track is 2048-byte user data sectors stored one after another in File from Offset.
internal sealed record CookedDiscDataTrack(ImageSnapshot File, long Offset, long Sectors) : DiscDataTrack(Sectors);

// Lays out a disc image's tracks as one stream of raw sectors from the data track's INDEX 01, the
// layout every form is read in, and records where each index falls in it.
internal sealed class DiscLayoutBuilder
{
    // 99:59:74, the last address a CD's MSF can give, less the two seconds before LBA 0.
    public const long MaximumSectors = 100L * 60 * 75 - 150;

    private readonly List<RawSegment> segments = [];
    private readonly List<(int Number, string Type, Dictionary<int, int> Indices)> tracks = [];

    public long Position { get; private set; }
    public IReadOnlyList<RawSegment> Segments => segments;
    public int TrackCount => tracks.Count;

    public void Stored(ImageSnapshot file, long offset, int stride, long sectors) => Add(sectors, file, offset, stride, null);

    public void Unavailable(long sectors, string reason) => Add(sectors, null, 0, 0, reason);

    public void Track(int number, string type) => tracks.Add((number, type, []));

    public void Index(int index, long sector)
    {
        if (sector > MaximumSectors) throw TooLong(sector);
        tracks[^1].Indices[index] = (int)sector;
    }

    public int? StartOf(int number, int index) =>
        number <= tracks.Count && tracks[number - 1].Indices.TryGetValue(index, out var sector) ? sector : null;

    private void Add(long sectors, ImageSnapshot? file, long offset, int stride, string? reason)
    {
        if (sectors < 0) throw new InvalidDataException("A track of the disc image ends before it starts.");
        if (sectors == 0) return;
        segments.Add(new(Position, sectors, file, offset, stride, reason));
        Position = checked(Position + sectors);
        if (Position > MaximumSectors) throw TooLong(Position);
    }

    private static NotSupportedException TooLong(long sectors) => new(
        $"The disc image holds {sectors} sectors, more than a CD's 99:59:74 addresses reach; DVD images are not read.");

    // The sheet of the stream, one BINARY FILE named imageName holding every track laid out here,
    // followed by the FILE entries of tracks the image does not hold.
    public CueBinSheet Sheet(string imageName, IReadOnlyList<CueBinFile> unread)
    {
        var all = tracks.Select(track => new CueBinTrack(track.Number, track.Type, track.Indices)).ToList();
        var files = new List<CueBinFile> { new(imageName, "BINARY", 1, tracks.Count) };
        foreach (var file in unread)
        {
            files.Add(file);
            for (var number = file.FirstTrack; number <= file.LastTrack; number++)
                all.Add(new(number, "AUDIO", new Dictionary<int, int>()));
        }
        return new(imageName, all) { Files = files };
    }
}

// A disc image as its form's reader found it: the files it reads, its raw stream and track layout,
// and how to read its data track.
internal sealed record DiscImage(
    string Format,
    IReadOnlyList<string> Files,
    string? CuePath,
    byte[]? CueBytes,
    CueBinSheet? Sheet,
    IReadOnlyList<RawSegment> Segments,
    long Sectors,
    DiscDataTrack Data,
    IReadOnlyDictionary<int, string> UnreadTracks)
{
    // Opens the volume on the data track, checked as OpenCueBin and OpenIso9660 check it.
    public OriginalContentSource Open()
    {
        if (Data.Sectors <= 16)
            throw new InvalidDataException("The disc image's data track does not hold an ISO 9660 volume.");
        Func<Stream> openFiles, openVolume;
        switch (Data)
        {
            case RawDiscDataTrack raw:
                openFiles = () => new RawDataTrackUserDataStream(OpenRaw(), raw.Sectors, raw.Mode, layoutSource: raw.LayoutSource);
                openVolume = () => new RawDataTrackUserDataStream(OpenRaw(), raw.Sectors, raw.Mode, emptyForm2AsZeros: true,
                    layoutSource: raw.LayoutSource);
                break;
            case CookedDiscDataTrack cooked:
                openFiles = openVolume = () => new ExtentReadStream(
                    cooked.File.OpenBuffered(), cooked.Offset, checked(cooked.Sectors * 2048));
                break;
            default:
                throw new InvalidOperationException("Unknown data track layout.");
        }
        var volume = new Iso9660ContentSource(openFiles, ContentSourceKinds.DiscImage, null, openVolume: openVolume);
        return new DiscImageContentSource(volume, this);
    }

    public Stream OpenRaw() => new RawSectorStream(Segments, Sectors);
}

// A disc image read through its ISO 9660 volume, with its raw stream for the audio tracks.
internal sealed class DiscImageContentSource(OriginalContentSource volume, DiscImage image) : OriginalContentSource
{
    public override string Kind => ContentSourceKinds.DiscImage;
    public override string? Label => volume.Label;
    public override IReadOnlyList<ContentSourceEntry> Files => volume.Files;
    public override CueBinSheet? Cue => image.Sheet;
    public override string? CuePath => image.CuePath;
    public override ReadOnlyMemory<byte>? CueSheetBytes =>
        image.CueBytes is null ? default(ReadOnlyMemory<byte>?) : image.CueBytes;
    public override string? DiscImageFormat => image.Format;
    public override IReadOnlyList<string>? ImageFiles => image.Files;
    public override long? VolumeBlocks => volume.VolumeBlocks;
    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry) => volume.TryGetFile(relativePath, out entry);
    public override Stream OpenRead(string relativePath) => volume.OpenRead(relativePath);
    public override Stream OpenVolume() => volume.OpenVolume();
    public override Stream OpenBin() => image.Sheet is null
        ? throw new NotSupportedException($"A {DiscImageFormats.Iso} disc image holds no raw sectors.")
        : image.OpenRaw();
    internal override string? UnreadAudioTrack(int track) => image.UnreadTracks.GetValueOrDefault(track);
    public override void Dispose() => volume.Dispose();
}

// The raw stream of a disc image: its segments one after another as 2352-byte sectors. Each file is
// opened through its snapshot, so a file changed since the image was opened fails, when the stream
// first reads from it. A read of sectors stored in no file throws InvalidDataException with the reason.
internal sealed class RawSectorStream(IReadOnlyList<RawSegment> segments, long sectors) : Stream
{
    private const int Raw = CueBinSheet.RawSectorSize;
    private readonly Dictionary<ImageSnapshot, Stream> opened = [];
    private long position;

    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => sectors * Raw;
    public override long Position
    {
        get => position;
        set => position = value >= 0 && value <= Length ? value : throw new IOException("Seek lies outside the disc image.");
    }

    public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));

    public override int Read(Span<byte> buffer)
    {
        var total = (int)Math.Min(buffer.Length, Length - position);
        var done = 0;
        while (done < total)
        {
            var sector = position / Raw;
            var segment = Find(sector);
            if (segment.Reason is { } reason)
                throw new InvalidDataException($"Sector {sector} of the disc image is stored in no file: {reason}");
            var within = position % Raw;
            var inSegment = (sector - segment.StartSector) * Raw + within;
            // Contiguous sectors are read in one go up to the segment's end; others one sector at a time.
            var count = (int)Math.Min(total - done, segment.Stride == Raw
                ? segment.Sectors * Raw - inSegment
                : Raw - within);
            var stream = StreamFor(segment.File!);
            stream.Position = segment.Stride == Raw
                ? checked(segment.Offset + inSegment)
                : checked(segment.Offset + (sector - segment.StartSector) * segment.Stride + within);
            stream.ReadExactly(buffer.Slice(done, count));
            done += count;
            position += count;
        }
        return total;
    }

    private RawSegment Find(long sector)
    {
        int low = 0, high = segments.Count - 1;
        while (low < high)
        {
            var middle = (low + high + 1) / 2;
            if (segments[middle].StartSector <= sector) low = middle;
            else high = middle - 1;
        }
        return segments[low];
    }

    private Stream StreamFor(ImageSnapshot file)
    {
        if (!opened.TryGetValue(file, out var stream)) opened.Add(file, stream = file.OpenBuffered());
        return stream;
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
        if (disposing)
            foreach (var stream in opened.Values) stream.Dispose();
        base.Dispose(disposing);
    }
}

// Finds the form of disc image a path names and reads it.
internal static class DiscImageOpener
{
    private const int MaximumDescriptorBytes = 16 * 1024 * 1024;
    private static readonly string[] Descriptors = [".cue", ".ccd", ".mds", ".nrg"];

    public static OriginalContentSource Open(string path)
    {
        var full = Path.GetFullPath(path);
        if (Directory.Exists(full)) full = Choose(full);
        else if (!File.Exists(full)) throw new FileNotFoundException("Disc image does not exist.", full);
        var directory = Path.GetDirectoryName(full)!;
        var extension = Path.GetExtension(full).ToLowerInvariant();
        return extension switch
        {
            ".cue" => CueDiscImage.Read(full, null).Open(),
            ".bin" => CueDiscImage.Read(Sibling(directory, full, ".cue", "cue sheet", single: true), full).Open(),
            ".iso" => OpenIso(full),
            ".ccd" => CloneCdImage.Read(full).Open(),
            ".img" => CloneCdImage.Read(Sibling(directory, full, ".ccd", "CloneCD .ccd", single: false)).Open(),
            ".mds" => MdsImage.Read(full).Open(),
            ".mdf" => MdsImage.Read(Sibling(directory, full, ".mds", "Alcohol .mds", single: false)).Open(),
            ".mdx" => throw new NotSupportedException(
                $"{Path.GetFileName(full)} is a DAEMON Tools or Alcohol 2 .mdx image, which is not read. Convert it to a " +
                "cue/bin or CloneCD image."),
            ".nrg" => NrgImage.Read(full).Open(),
            _ => throw new InvalidDataException(
                $"{Path.GetFileName(full)} is not a disc image the disc-image kind reads by its extension: .cue, .bin, " +
                ".iso, .ccd, .img, .mds, .mdf or .nrg."),
        };
    }

    private static OriginalContentSource OpenIso(string path)
    {
        var image = ImageSnapshot.Take(path);
        var volume = new Iso9660ContentSource(image.OpenBuffered, ContentSourceKinds.DiscImage, null);
        return new DiscImageContentSource(volume, new DiscImage(
            DiscImageFormats.Iso, [path], null, null, null, [], 0,
            new CookedDiscDataTrack(image, 0, image.Length / 2048), new Dictionary<int, string>()));
    }

    // The one descriptor in a directory, else its one .iso.
    private static string Choose(string directory)
    {
        var all = Directory.EnumerateFiles(directory, "*", SearchOption.TopDirectoryOnly)
            .Order(StringComparer.OrdinalIgnoreCase).ToArray();
        var descriptors = all.Where(file => Descriptors.Contains(Path.GetExtension(file).ToLowerInvariant())).ToArray();
        var candidates = descriptors.Length > 0
            ? descriptors
            : all.Where(file => Path.GetExtension(file).Equals(".iso", StringComparison.OrdinalIgnoreCase)).ToArray();
        return candidates.Length == 1
            ? candidates[0]
            : throw new InvalidDataException(candidates.Length == 0
                ? $"{directory} holds no disc image: no .cue, .ccd, .mds, .nrg or .iso file."
                : $"{directory} holds several disc images ({string.Join(", ", candidates.Select(Path.GetFileName))}); " +
                  "pass the one to read.");
    }

    // The descriptor with the image's name, or for a .bin the only cue sheet when none has its name.
    private static string Sibling(string directory, string image, string extension, string label, bool single)
    {
        var candidates = CueBinSheet.Enumerate(directory, extension);
        return CueBinSheet.MatchStem(image, candidates)
            ?? (single && candidates.Length == 1 ? candidates[0] : null)
            ?? throw new FileNotFoundException(
                $"{Path.GetFileName(image)} is read through the {label} beside it with the same name, and there is none.",
                Path.ChangeExtension(image, extension));
    }

    // Reads a descriptor file whole, up to the limit.
    internal static byte[] ReadDescriptor(string path, string label)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        if (stream.Length > MaximumDescriptorBytes)
            throw new InvalidDataException($"The {label} {Path.GetFileName(path)} is larger than {MaximumDescriptorBytes} bytes.");
        var bytes = new byte[stream.Length];
        stream.ReadExactly(bytes);
        return bytes;
    }

    internal static string TypeOf(RawDataTrackMode mode, bool cooked) => (mode, cooked) switch
    {
        (RawDataTrackMode.Mode1, false) => "MODE1/2352",
        (RawDataTrackMode.Mode2Form1, false) => "MODE2/2352",
        (RawDataTrackMode.Mode1, true) => "MODE1/2048",
        _ => "MODE2/2048",
    };

    // The reason the cooked data track's raw sectors are not in the raw stream.
    internal static string CookedReason(string file) =>
        $"track 01 is stored in {file} as 2048-byte user data sectors, so its raw sectors are not in the image.";
}

// A cue sheet read as a disc image: every FILE it names, not only the first.
internal static class CueDiscImage
{
    public static DiscImage Read(string cuePath, string? givenImage)
    {
        var bytes = CueBinSheet.ReadBytes(cuePath);
        var (files, tracks) = CueBinSheet.Tokenize(CueBinSheet.Decode(bytes), readGaps: true);
        var directory = Path.GetDirectoryName(cuePath)!;
        var first = tracks[0].Type;
        var cooked = first is "MODE1/2048" or "MODE2/2048";
        if (!cooked && first is not ("MODE1/2352" or "MODE2/2352"))
            throw new InvalidDataException(
                $"First cue track must be MODE1/2352, MODE2/2352, MODE1/2048 or MODE2/2048, not {CueBinSheet.Truncate(first)}.");
        CheckTracks(files, tracks);
        if (cooked && FileTracks(files, tracks.Count, 0).Count > 1)
            throw new InvalidDataException(
                $"Cue FILE {CueBinSheet.Truncate(files[0].Path)} holds the {first} data track and other tracks; a file of " +
                "2048-byte sectors may hold only the data track.");

        var imagePath = CueBinSheet.ResolveReference(directory, files[0].Path)
            ?? throw new FileNotFoundException($"The image {files[0].Path} the cue sheet names does not exist.",
                Path.Combine(directory, files[0].Path));
        if (givenImage is not null && !string.Equals(imagePath, givenImage,
                OperatingSystem.IsLinux() ? StringComparison.Ordinal : StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException(
                $"Cue sheet {Path.GetFileName(cuePath)} describes {files[0].Path}, not {Path.GetFileName(givenImage)}.");
        var image = ImageSnapshot.Take(imagePath);
        var read = new List<string> { cuePath, imagePath };
        var builder = new DiscLayoutBuilder();
        var mode = first.StartsWith("MODE2", StringComparison.Ordinal) ? RawDataTrackMode.Mode2Form1 : RawDataTrackMode.Mode1;
        long imageSectors;
        if (cooked)
        {
            if (image.Length == 0 || image.Length % 2048 != 0)
                throw new InvalidDataException(
                    $"{files[0].Path} must be a positive multiple of 2048 bytes for its {first} track.");
            imageSectors = image.Length / 2048;
            builder.Track(1, first);
            builder.Index(1, 0);
            builder.Unavailable(imageSectors, DiscImageOpener.CookedReason(files[0].Path));
        }
        else
        {
            if (image.Length == 0 || image.Length % CueBinSheet.RawSectorSize != 0)
                throw new InvalidDataException($"BIN length must be a positive multiple of {CueBinSheet.RawSectorSize} bytes.");
            imageSectors = image.Length / CueBinSheet.RawSectorSize;
            if (Place(builder, image, 0, imageSectors, FileTracks(files, tracks.Count, 0), tracks, files[0].Path) is { } problem)
                throw new InvalidDataException(problem);
        }

        var unread = new List<CueBinFile>();
        var reasons = new Dictionary<int, string>();
        string? stopped = null;
        for (var index = 1; index < files.Count; index++)
        {
            var (path, type, _) = files[index];
            var numbers = FileTracks(files, tracks.Count, index);
            var problem = stopped is not null
                ? $"it follows {stopped}, which is not read, so where its tracks lie on the disc is not known."
                : ReadAudioFile(builder, directory, path, type, numbers, tracks, read);
            if (problem is null) continue;
            stopped ??= path;
            unread.Add(new(path, type, numbers[0], numbers[^1]));
            foreach (var number in numbers)
                reasons[number] = $"Audio track {number:D2} is stored in {AssetVerifier.JsonString(path)}, and {problem}";
        }

        var dataSectors = builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? imageSectors;
        DiscDataTrack data = cooked
            ? new CookedDiscDataTrack(image, 0, imageSectors)
            : new RawDiscDataTrack(mode, dataSectors);
        return new(DiscImageFormats.CueSheet, read, cuePath, bytes, builder.Sheet(files[0].Path, unread),
            builder.Segments, builder.Position, data, reasons);
    }

    // Lays out one audio FILE after those before it, or says why it is not read.
    private static string? ReadAudioFile(
        DiscLayoutBuilder builder, string directory, string path, string type, IReadOnlyList<int> numbers,
        List<(int Number, string Type, Dictionary<int, int> Indices, int Pregap)> tracks, List<string> read)
    {
        var full = CueBinSheet.ResolveReference(directory, path);
        if (full is null) return "that file does not exist.";
        if (type is not ("BINARY" or "WAVE"))
            return $"the sheet gives it FILE type {CueBinSheet.Truncate(type)}; a disc-image source reads only BINARY " +
                   "files of raw sectors and WAVE files of 16-bit stereo 44.1 kHz PCM.";
        var file = ImageSnapshot.Take(full);
        long offset = 0, length = file.Length;
        if (type == "WAVE")
        {
            using var stream = file.OpenBuffered();
            var wave = WaveData(stream);
            if (wave.Problem is { } problem) return problem;
            (offset, length) = (wave.Offset, wave.Length);
        }
        if (length == 0 || length % CueBinSheet.RawSectorSize != 0)
            return $"it holds {length} bytes of audio, not a whole number of {CueBinSheet.RawSectorSize}-byte sectors.";
        var placed = Place(builder, file, offset, length / CueBinSheet.RawSectorSize, numbers, tracks, path);
        if (placed is null) read.Add(full);
        return placed;
    }

    // Lays out the tracks of one FILE of raw sectors. Each index counts from the file's start; a
    // PREGAP adds sectors stored in no file before the track's INDEX 01. Returns a problem, or null.
    private static string? Place(
        DiscLayoutBuilder builder, ImageSnapshot file, long offset, long fileSectors, IReadOnlyList<int> numbers,
        List<(int Number, string Type, Dictionary<int, int> Indices, int Pregap)> tracks, string path)
    {
        // Validate first, so a FILE that does not fit adds nothing to the layout.
        foreach (var number in numbers)
            foreach (var (index, sector) in tracks[number - 1].Indices)
                if (sector >= fileSectors)
                    return $"track {number:D2} INDEX {index:D2} starts outside {CueBinSheet.Truncate(path)}.";
        if (tracks[numbers[0] - 1].Indices.Values.Min() != 0)
            return $"{CueBinSheet.Truncate(path)}'s sectors before track {numbers[0]:D2}'s first INDEX belong to no track; " +
                   "the first track of a FILE must have an index at 00:00:00.";

        long cursor = 0;
        foreach (var number in numbers)
        {
            var (_, type, indices, pregap) = tracks[number - 1];
            var firstIndex = indices.Values.Min();
            builder.Stored(file, offset + cursor * CueBinSheet.RawSectorSize, CueBinSheet.RawSectorSize, firstIndex - cursor);
            cursor = firstIndex;
            builder.Track(number, type);
            // The two seconds before track 01 lie before LBA 0, outside every image form.
            if (pregap > 0 && number > 1)
            {
                builder.Index(0, builder.Position);
                builder.Unavailable(pregap,
                    $"track {number:D2}'s {pregap}-sector pregap is declared with PREGAP, which no file stores.");
            }
            foreach (var (index, sector) in indices.OrderBy(entry => entry.Key))
                builder.Index(index, builder.Position + (sector - cursor));
        }
        builder.Stored(file, offset + cursor * CueBinSheet.RawSectorSize, CueBinSheet.RawSectorSize, fileSectors - cursor);
        return null;
    }

    // The rules of a disc-image sheet beyond those Tokenize applies.
    private static void CheckTracks(
        List<(string Path, string Type, int FirstPosition)> files,
        List<(int Number, string Type, Dictionary<int, int> Indices, int Pregap)> tracks)
    {
        var previous = -1;
        var fileIndex = 0;
        for (var offset = 0; offset < tracks.Count; offset++)
        {
            var (number, type, indices, pregap) = tracks[offset];
            if (fileIndex + 1 < files.Count && files[fileIndex + 1].FirstPosition == offset)
            {
                fileIndex++;
                previous = -1;
            }
            if (number != offset + 1)
                throw new InvalidDataException("Cue track numbers must be consecutive and start at 1.");
            if (!indices.TryGetValue(1, out var start))
                throw new InvalidDataException($"Track {number:D2} has no INDEX 01.");
            if (indices.TryGetValue(0, out var stored))
            {
                if (stored > start) throw new InvalidDataException($"Track {number:D2} has INDEX 00 after INDEX 01.");
                if (pregap > 0 && offset > 0)
                    throw new InvalidDataException(
                        $"Track {number:D2} has both PREGAP and INDEX 00, so its pregap is declared twice.");
            }
            if (offset == 0 && start != 0) throw new InvalidDataException("Track 01 INDEX 01 must be at 00:00:00.");
            if (offset > 0 && type != "AUDIO")
                throw new InvalidDataException(
                    $"Unsupported non-audio track {number:D2}: {CueBinSheet.Truncate(type)}. Only track 01 may hold data.");
            foreach (var (_, sector) in indices.OrderBy(entry => entry.Key))
            {
                if (sector < previous) throw new InvalidDataException("Cue track indices are not in order.");
                previous = sector;
            }
        }
    }

    // The numbers of the tracks FILE index holds.
    private static List<int> FileTracks(List<(string Path, string Type, int FirstPosition)> files, int trackCount, int index)
    {
        var last = index + 1 < files.Count ? files[index + 1].FirstPosition : trackCount;
        return Enumerable.Range(files[index].FirstPosition + 1, last - files[index].FirstPosition).ToList();
    }

    // The data chunk of a RIFF WAVE file of 16-bit stereo 44.1 kHz PCM, or why the file is not one.
    internal static (long Offset, long Length, string? Problem) WaveData(Stream stream)
    {
        const string ReadOnly = "a disc-image source reads only BINARY files of raw sectors and WAVE files of 16-bit " +
            "stereo 44.1 kHz PCM, the CD's own samples.";
        var header = new byte[12];
        var got = stream.ReadAtLeast(header, header.Length, throwOnEndOfStream: false);
        ReadOnlySpan<byte> start = header.AsSpan(0, got);
        if (start.StartsWith("fLaC"u8)) return (0, 0, $"it holds FLAC audio; {ReadOnly}");
        if (start.StartsWith("OggS"u8)) return (0, 0, $"it holds Ogg audio; {ReadOnly}");
        if (start.StartsWith("ID3"u8) || (got >= 2 && start[0] == 0xFF && (start[1] & 0xE0) == 0xE0))
            return (0, 0, $"it holds MP3 audio; {ReadOnly}");
        if (got < 12 || !start.StartsWith("RIFF"u8) || !start[8..12].SequenceEqual("WAVE"u8))
            return (0, 0, $"it holds no RIFF WAVE data; {ReadOnly}");
        var riffEnd = Math.Min(stream.Length, 8L + BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(4)));
        var chunk = new byte[8];
        var format = Array.Empty<byte>();
        for (long at = 12; at + 8 <= riffEnd;)
        {
            stream.Position = at;
            stream.ReadExactly(chunk);
            var size = BinaryPrimitives.ReadUInt32LittleEndian(chunk.AsSpan(4));
            var body = at + 8;
            if (chunk.AsSpan(0, 4).SequenceEqual("fmt "u8))
            {
                if (size < 16 || body + size > stream.Length) return (0, 0, $"its fmt chunk is truncated; {ReadOnly}");
                format = new byte[16];
                stream.ReadExactly(format);
            }
            else if (chunk.AsSpan(0, 4).SequenceEqual("data"u8))
            {
                if (format.Length == 0) return (0, 0, $"its data chunk comes before any fmt chunk; {ReadOnly}");
                var tag = BinaryPrimitives.ReadUInt16LittleEndian(format);
                var channels = BinaryPrimitives.ReadUInt16LittleEndian(format.AsSpan(2));
                var rate = BinaryPrimitives.ReadUInt32LittleEndian(format.AsSpan(4));
                var align = BinaryPrimitives.ReadUInt16LittleEndian(format.AsSpan(12));
                var bits = BinaryPrimitives.ReadUInt16LittleEndian(format.AsSpan(14));
                if (tag != 1 || channels != 2 || rate != 44100 || bits != 16 || align != 4)
                    return (0, 0, $"it holds WAVE format {tag} audio of {channels} channels, {bits} bits and {rate} Hz; {ReadOnly}");
                if (body + size > stream.Length)
                    return (0, 0, $"its data chunk declares {size} bytes and runs past the end of the file.");
                return (body, size, null);
            }
            at = body + size + (size & 1);
        }
        return (0, 0, $"it has no data chunk; {ReadOnly}");
    }
}
