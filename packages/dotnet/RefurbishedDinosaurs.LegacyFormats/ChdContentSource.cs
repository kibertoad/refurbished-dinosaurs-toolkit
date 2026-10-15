using System.Globalization;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

// The sectors of a CD CHD laid out as the BIN image chdman made it from: each track's stored
// frames in order, without the padding chdman adds to a multiple of 4 frames, and with audio
// samples in the little-endian order of a BIN.
internal sealed class ChdDisc
{
    private readonly ImageSnapshot snapshot;
    private readonly long[] binStarts;
    private readonly bool[] contentHunks;

    public ChdDisc(ImageSnapshot snapshot, ChdImage image)
    {
        this.snapshot = snapshot;
        Image = image;
        binStarts = new long[image.Tracks.Count];
        contentHunks = new bool[image.HunkCount];
        long sectors = 0;
        for (var index = 0; index < image.Tracks.Count; index++)
        {
            var track = image.Tracks[index];
            binStarts[index] = sectors;
            sectors += track.Frames;
            var firstHunk = track.ChdFrame / image.FramesPerHunk;
            var lastHunk = (track.ChdFrame + track.Frames - 1) / image.FramesPerHunk;
            contentHunks.AsSpan((int)firstHunk, (int)(lastHunk - firstHunk + 1)).Fill(true);
        }
        Sectors = sectors;
    }

    public ChdImage Image { get; }

    // Sectors of the BIN image: every track's stored frames.
    public long Sectors { get; }

    public long BinStart(int trackIndex) => binStarts[trackIndex];

    // The track holding a BIN sector, by its index in Image.Tracks.
    public int TrackOf(long sector)
    {
        var index = Array.BinarySearch(binStarts, sector);
        return index >= 0 ? index : ~index - 1;
    }

    // Opens the file again, checked against the snapshot, with a reader of its own.
    public ChdHunkReader OpenReader() => new(Image, snapshot.OpenBuffered(), contentHunks);
}

// A read-only seekable stream over a CD CHD's sectors: the raw 2352-byte sectors of the BIN image,
// or for a CHD of 2048-byte sectors, their user data. Each stream reads the file through its own
// handle and keeps the last hunk it decoded.
internal sealed class ChdSectorStream : Stream
{
    private readonly ChdDisc disc;
    private readonly int sectorSize;
    private readonly ChdHunkReader reader;
    private readonly byte[] hunk;
    private readonly byte[] sector;
    private int cachedHunk = -1;
    private long cachedSector = -1;
    private long position;

    public ChdSectorStream(ChdDisc disc, int sectorSize)
    {
        this.disc = disc;
        this.sectorSize = sectorSize;
        reader = disc.OpenReader();
        hunk = new byte[disc.Image.HunkBytes];
        sector = new byte[sectorSize];
    }

    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => disc.Sectors * sectorSize;

    public override long Position
    {
        get => position;
        set => position = value >= 0 && value <= Length ? value : throw new IOException("Seek lies outside the CHD image.");
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ValidateBufferArguments(buffer, offset, count);
        return Read(buffer.AsSpan(offset, count));
    }

    public override int Read(Span<byte> buffer)
    {
        var total = (int)Math.Min(buffer.Length, Length - position);
        var done = 0;
        while (done < total)
        {
            var index = position / sectorSize;
            var within = (int)(position % sectorSize);
            var count = Math.Min(total - done, sectorSize - within);
            LoadSector(index);
            sector.AsSpan(within, count).CopyTo(buffer[done..]);
            position += count;
            done += count;
        }
        return total;
    }

    // Reads complete synchronously; the decoding is CPU work on data already read.
    public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return ValueTask.FromResult(Read(buffer.Span));
    }

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        ValidateBufferArguments(buffer, offset, count);
        return ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
    }

    private void LoadSector(long index)
    {
        if (index == cachedSector) return;
        cachedSector = -1;
        var trackIndex = disc.TrackOf(index);
        var track = disc.Image.Tracks[trackIndex];
        var frame = track.ChdFrame + (index - disc.BinStart(trackIndex));
        var hunkIndex = (int)(frame / disc.Image.FramesPerHunk);
        if (hunkIndex != cachedHunk)
        {
            cachedHunk = -1;
            reader.ReadHunk(hunkIndex, hunk);
            cachedHunk = hunkIndex;
        }
        var data = hunk.AsSpan((int)(frame % disc.Image.FramesPerHunk) * ChdImage.FrameSize, sectorSize);
        if (track.Type == ChdTrackType.Audio)
        {
            // A CHD stores samples big-endian, a BIN little-endian.
            for (var at = 0; at < sectorSize; at += 2)
            {
                sector[at] = data[at + 1];
                sector[at + 1] = data[at];
            }
        }
        else data.CopyTo(sector);
        cachedSector = index;
    }

    public override long Seek(long offset, SeekOrigin origin) => Position = origin switch
    {
        SeekOrigin.Begin => offset,
        SeekOrigin.Current => checked(position + offset),
        SeekOrigin.End => checked(Length + offset),
        _ => throw new ArgumentOutOfRangeException(nameof(origin)),
    };

    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) reader.Dispose();
        base.Dispose(disposing);
    }
}

// A CD CHD read as the disc image it holds. The ISO 9660 volume and its files are read by the same
// reader as a cue/bin source, from the sectors ChdSectorStream gives.
internal sealed class ChdContentSource : OriginalContentSource
{
    private readonly ChdDisc disc;
    private readonly OriginalContentSource volume;
    private readonly CueBinSheet? sheet;

    private ChdContentSource(ChdDisc disc, OriginalContentSource volume, CueBinSheet? sheet)
    {
        this.disc = disc;
        this.volume = volume;
        this.sheet = sheet;
    }

    public static ChdContentSource Open(ImageSnapshot snapshot)
    {
        ChdImage image;
        using (var file = snapshot.OpenBuffered()) image = ChdImage.Read(file);
        var disc = new ChdDisc(snapshot, image);
        var tracks = image.Tracks;
        if (tracks[0].Type == ChdTrackType.Mode1Cooked)
        {
            if (tracks.Count > 1)
                throw new NotSupportedException(
                    "The CHD's data track stores 2048-byte sectors and other tracks follow it; only a CHD of one " +
                    "such track, as chdman makes from an .iso image, is read. A CHD made from a cue/bin image stores raw sectors.");
            if (disc.Sectors <= 16)
                throw new InvalidDataException("The CHD's data track does not hold an ISO 9660 volume.");
            return new ChdContentSource(disc,
                new Iso9660ContentSource(() => new ChdSectorStream(disc, 2048), ContentSourceKinds.Chd, null), null);
        }
        if (tracks.Skip(1).Any(track => track.Type == ChdTrackType.Mode1Cooked))
            throw new NotSupportedException(
                "The CHD mixes tracks of raw sectors with tracks of 2048-byte sectors, which no cue/bin image holds.");

        var sheet = Sheet(snapshot.Path, disc);
        sheet.ValidateBinLength(disc.Sectors * CueBinSheet.RawSectorSize);
        long dataSectors = sheet.DataTrackSectors ?? disc.Sectors;
        if (dataSectors <= 16)
            throw new InvalidDataException("The CHD's data track does not hold an ISO 9660 volume.");
        var mode = sheet.DataTrackMode;
        var volume = new Iso9660ContentSource(
            () => new RawDataTrackUserDataStream(new ChdSectorStream(disc, CueBinSheet.RawSectorSize), dataSectors, mode),
            ContentSourceKinds.Chd, null,
            openVolume: () => new RawDataTrackUserDataStream(
                new ChdSectorStream(disc, CueBinSheet.RawSectorSize), dataSectors, mode, emptyForm2AsZeros: true));
        return new ChdContentSource(disc, volume, sheet);
    }

    // The cue sheet of the BIN image the CHD holds, parsed and checked as a cue/bin source's sheet
    // is, so the CHD is held to the same layout: a MODE1/2352 or MODE2/2352 data track at sector 0
    // followed by audio tracks, each with its stored pregap as INDEX 00.
    private static CueBinSheet Sheet(string chdPath, ChdDisc disc)
    {
        var text = new StringBuilder();
        text.Append(CultureInfo.InvariantCulture, $"FILE \"{BinName(chdPath)}\" BINARY\n");
        for (var index = 0; index < disc.Image.Tracks.Count; index++)
        {
            var track = disc.Image.Tracks[index];
            var type = track.Type switch
            {
                ChdTrackType.Mode1Raw => "MODE1/2352",
                ChdTrackType.Mode2Raw => "MODE2/2352",
                _ => "AUDIO",
            };
            text.Append(CultureInfo.InvariantCulture, $"TRACK {track.Number:D2} {type}\n");
            var start = disc.BinStart(index);
            if (track.StoredPregap > 0) text.Append(CultureInfo.InvariantCulture, $"INDEX 00 {Msf(start)}\n");
            text.Append(CultureInfo.InvariantCulture, $"INDEX 01 {Msf(start + track.StoredPregap)}\n");
        }
        try
        {
            return CueBinSheet.Parse(text.ToString());
        }
        catch (InvalidDataException exception)
        {
            throw new NotSupportedException(
                $"The CHD's tracks do not form a disc image this reader opens: {exception.Message}", exception);
        }
    }

    // The BIN the sheet names: the CHD's name with a .bin extension, or disc.bin when that name is
    // not one a cue sheet can give.
    private static string BinName(string chdPath)
    {
        var name = Path.GetFileNameWithoutExtension(chdPath) + ".bin";
        if (name.Contains('"')) return "disc.bin";
        try { return PortableAssetPath.Relative(name); }
        catch (InvalidDataException) { return "disc.bin"; }
    }

    private static string Msf(long sector) =>
        string.Create(CultureInfo.InvariantCulture, $"{sector / 4500:D2}:{sector / 75 % 60:D2}:{sector % 75:D2}");

    public override string Kind => ContentSourceKinds.Chd;
    public override string? Label => volume.Label;
    public override CueBinSheet? Cue => sheet;
    public override IReadOnlyList<ContentSourceEntry> Files => volume.Files;
    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry) => volume.TryGetFile(relativePath, out entry);
    public override Stream OpenRead(string relativePath) => volume.OpenRead(relativePath);
    public override long? VolumeBlocks => volume.VolumeBlocks;
    public override Stream OpenVolume() => volume.OpenVolume();

    public override Stream OpenBin() =>
        sheet is null ? base.OpenBin() : new ChdSectorStream(disc, CueBinSheet.RawSectorSize);

    public override void Dispose() => volume.Dispose();
}
