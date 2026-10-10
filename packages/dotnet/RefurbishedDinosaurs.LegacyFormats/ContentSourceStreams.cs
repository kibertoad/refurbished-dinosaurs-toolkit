namespace RefurbishedDinosaurs.LegacyFormats;

// The files a cue/bin source chose and what it read from them when it opened.
internal sealed record CueBinFiles(CueBinSheet Sheet, string CuePath, byte[] CueBytes, ImageSnapshot Bin);

// The length and last-write time of an .iso or BIN image when its source opened. The source checked
// that file, so each time it opens the file again it compares both, and reading a file that changed
// fails instead of describing bytes the source never checked.
internal sealed class ImageSnapshot
{
    private readonly DateTime lastWriteTimeUtc;

    private ImageSnapshot(string path, long length, DateTime lastWriteTimeUtc)
    {
        Path = path;
        Length = length;
        this.lastWriteTimeUtc = lastWriteTimeUtc;
    }

    public string Path { get; }
    public long Length { get; }

    public static ImageSnapshot Take(string path)
    {
        using var stream = CddaTrackFingerprints.OpenImage(path);
        return new(path, stream.Length, File.GetLastWriteTimeUtc(stream.SafeFileHandle));
    }

    // For the audio checks and OpenBin, which read large ranges asynchronously.
    public Stream Open() => Checked(CddaTrackFingerprints.OpenImage(Path));

    // For the data track, which reads one raw sector at a time.
    public Stream OpenBuffered() => Checked(new FileStream(Path, FileMode.Open, FileAccess.Read, FileShare.Read));

    // One stat of the open handle per stream, so the check describes the file the stream reads.
    private FileStream Checked(FileStream stream)
    {
        try
        {
            var length = stream.Length;
            var lastWrite = File.GetLastWriteTimeUtc(stream.SafeFileHandle);
            if (length != Length || lastWrite != lastWriteTimeUtc)
                throw new IOException(
                    $"The image {Path} changed after the source was opened: it was {Length} bytes " +
                    $"written at {lastWriteTimeUtc:O} and is now {length} bytes written at {lastWrite:O}. " +
                    "Open the source again.");
            return stream;
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }
}

internal sealed class ExtentReadStream : Stream
{
    private readonly long start;
    private readonly long length;
    private long position;

    private readonly Stream stream;

    public ExtentReadStream(Stream stream, long start, long length)
    {
        this.stream = stream;
        this.start = start;
        this.length = length;
        stream.Position = start;
    }

    public override bool CanRead => true;
    public override bool CanSeek => true;
    public override bool CanWrite => false;
    public override long Length => length;
    public override long Position
    {
        get => position;
        set => Seek(value, SeekOrigin.Begin);
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ArgumentNullException.ThrowIfNull(buffer);
        ArgumentOutOfRangeException.ThrowIfNegative(offset);
        ArgumentOutOfRangeException.ThrowIfNegative(count);
        if (buffer.Length - offset < count) throw new ArgumentException("Buffer range is invalid.");
        var bounded = (int)Math.Min(count, length - position);
        if (bounded <= 0) return 0;
        var read = Counted(stream.Read(buffer, offset, bounded));
        position += read;
        return read;
    }

    public override int Read(Span<byte> buffer)
    {
        var bounded = (int)Math.Min(buffer.Length, length - position);
        if (bounded <= 0) return 0;
        var read = Counted(stream.Read(buffer[..bounded]));
        position += read;
        return read;
    }

    public override async ValueTask<int> ReadAsync(
        Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        var bounded = (int)Math.Min(buffer.Length, length - position);
        if (bounded <= 0) return 0;
        var read = Counted(await stream.ReadAsync(buffer[..bounded], cancellationToken));
        position += read;
        return read;
    }

    // The image was checked to hold the extent when it was opened, so an image that ends inside the
    // extent has changed since. Ending the stream early would hash a prefix as if it were the whole.
    private static int Counted(int read) =>
        read > 0 ? read : throw new EndOfStreamException("The image ended inside an ISO9660 extent.");

    public override long Seek(long offset, SeekOrigin origin)
    {
        var next = origin switch
        {
            SeekOrigin.Begin => offset,
            SeekOrigin.Current => checked(position + offset),
            SeekOrigin.End => checked(length + offset),
            _ => throw new ArgumentOutOfRangeException(nameof(origin))
        };
        if (next < 0 || next > length) throw new IOException("Seek lies outside the ISO9660 extent.");
        stream.Position = checked(start + next);
        return position = next;
    }

    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) stream.Dispose();
        base.Dispose(disposing);
    }

    public override async ValueTask DisposeAsync()
    {
        await stream.DisposeAsync();
        GC.SuppressFinalize(this);
    }
}

// A view of a stream that other views share, with its own position. Each read seeks the shared
// stream and reads it while holding the gate, so views can be read at the same time. Every view of
// one shared stream gets the same gate from GateFor, also across sources opened over it. Disposing a
// view leaves the shared stream open, since its caller owns it.
internal sealed class SharedStreamView(Stream shared, SemaphoreSlim gate) : Stream
{
    private static readonly System.Runtime.CompilerServices.ConditionalWeakTable<Stream, SemaphoreSlim> Gates = new();

    private long position;
    private bool disposed;

    // The gate for every view of shared, kept for as long as shared is alive.
    public static SemaphoreSlim GateFor(Stream shared) => Gates.GetValue(shared, _ => new SemaphoreSlim(1, 1));

    public override bool CanRead => !disposed;
    public override bool CanSeek => !disposed;
    public override bool CanWrite => false;
    public override long Length
    {
        get
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            gate.Wait();
            try { return shared.Length; }
            finally { gate.Release(); }
        }
    }
    public override long Position
    {
        get => position;
        set
        {
            ObjectDisposedException.ThrowIf(disposed, this);
            ArgumentOutOfRangeException.ThrowIfNegative(value);
            position = value;
        }
    }

    public override int Read(byte[] buffer, int offset, int count)
    {
        ValidateBufferArguments(buffer, offset, count);
        return Read(buffer.AsSpan(offset, count));
    }

    public override int Read(Span<byte> buffer)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        gate.Wait();
        try
        {
            shared.Position = position;
            var read = shared.Read(buffer);
            position += read;
            return read;
        }
        finally { gate.Release(); }
    }

    public override async ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        await gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            shared.Position = position;
            var read = await shared.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            position += read;
            return read;
        }
        finally { gate.Release(); }
    }

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        ValidateBufferArguments(buffer, offset, count);
        return ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
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
        disposed = true;
        base.Dispose(disposing);
    }
}
