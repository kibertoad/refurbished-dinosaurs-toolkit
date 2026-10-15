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

// Opens a CHD's sectors as a disc image's raw stream (2352-byte sectors) or as a data track of
// 2048-byte sectors.
internal sealed class ChdSectorStore(ChdDisc disc, int sectorSize) : ISectorStore
{
    public Stream OpenSectors() => new ChdSectorStream(disc, sectorSize);
}

// A CD CHD read as a disc image: the BIN image chdman read, as ChdDisc lays it out, with the track
// layout the CHD's metadata gives. A CHD made from an .iso holds one MODE1 track of 2048-byte
// sectors, read as a cue sheet's MODE1/2048 track is.
internal static class ChdDiscImage
{
    public static DiscImage Read(string path)
    {
        // Recorded before the CHD is checked, so the source describes only the file it checked.
        var snapshot = ImageSnapshot.Take(path);
        ChdImage image;
        using (var file = snapshot.OpenBuffered()) image = ChdImage.Read(file);
        var disc = new ChdDisc(snapshot, image);
        var name = Path.GetFileName(path);
        var tracks = image.Tracks;
        var builder = new DiscLayoutBuilder();

        if (tracks[0].Type == ChdTrackType.Mode1Cooked)
        {
            if (tracks.Count > 1)
                throw new NotSupportedException(
                    $"The CHD {name} stores its data track as 2048-byte sectors and other tracks follow it; only a CHD of " +
                    "one such track, as chdman makes from an .iso image, is read. A CHD made from a cue/bin image stores raw sectors.");
            builder.Track(1, "MODE1/2048");
            builder.Index(1, 0);
            builder.Unavailable(disc.Sectors, DiscImageOpener.CookedReason(name));
            return new(DiscImageFormats.Chd, [path], null, null, builder.Sheet(name, []), builder.Segments, builder.Position,
                new CookedDiscDataTrack(new ChdSectorStore(disc, 2048), 0, disc.Sectors), new Dictionary<int, string>());
        }
        if (tracks[0].Type == ChdTrackType.Audio)
            throw new NotSupportedException($"The CHD {name} begins with an audio track; only discs whose first track holds the data are read.");
        if (tracks[0].StoredPregap > 0)
            throw new NotSupportedException($"The CHD {name} stores a pregap before track 01's data; the data track must start at sector 0.");

        var raw = new ChdSectorStore(disc, CueBinSheet.RawSectorSize);
        for (var index = 0; index < tracks.Count; index++)
        {
            var track = tracks[index];
            if (index > 0 && track.Type != ChdTrackType.Audio)
                throw new NotSupportedException(track.Type == ChdTrackType.Mode1Cooked
                    ? $"The CHD {name} mixes tracks of raw sectors with a track of 2048-byte sectors, which no disc image form holds."
                    : $"The CHD {name} gives track {track.Number:D2} a data type; only track 01 may hold data.");
            builder.Track(track.Number, track.Type switch
            {
                ChdTrackType.Mode1Raw => "MODE1/2352",
                ChdTrackType.Mode2Raw => "MODE2/2352",
                _ => "AUDIO",
            });
            // The two seconds before track 01 lie before LBA 0, outside every image form. A later
            // track's unstored pregap stays in the layout, as a cue sheet's PREGAP does.
            if (index > 0 && track.UnstoredPregap > 0)
            {
                builder.Index(0, builder.Position);
                builder.Unavailable(track.UnstoredPregap,
                    $"track {track.Number:D2}'s {track.UnstoredPregap}-sector pregap is recorded in the CHD, which does not store it.");
            }
            else if (track.StoredPregap > 0) builder.Index(0, builder.Position);
            builder.Index(1, builder.Position + track.StoredPregap);
            builder.Stored(raw, disc.BinStart(index) * CueBinSheet.RawSectorSize, CueBinSheet.RawSectorSize, track.Frames);
        }
        var dataSectors = builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? builder.Position;
        var mode = tracks[0].Type == ChdTrackType.Mode1Raw ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1;
        return new(DiscImageFormats.Chd, [path], null, null, builder.Sheet(name, []), builder.Segments, builder.Position,
            new RawDiscDataTrack(mode, dataSectors, $"the CHD {name}"), new Dictionary<int, string>());
    }
}
