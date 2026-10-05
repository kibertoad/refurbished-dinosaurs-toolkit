using System.Runtime.ExceptionServices;

namespace RefurbishedDinosaurs.LegacyFormats;

// A read-only seekable stream over one archive member that is decoded while it is read. It keeps the
// position, bounds seeks, refuses use after disposal, and makes a failed read stick: every later read
// rethrows it until a seek, after which the subclass decodes again from the start.
internal abstract class DecodingMemberStream(long length, string description) : Stream
{
    private long position;
    private bool disposed;
    private ExceptionDispatchInfo? failure;

    public sealed override bool CanRead => !disposed;
    public sealed override bool CanSeek => !disposed;
    public sealed override bool CanWrite => false;
    public sealed override long Length => length;
    public sealed override long Position
    {
        get => position;
        set => Seek(value, SeekOrigin.Begin);
    }

    public sealed override int Read(byte[] buffer, int offset, int count)
    {
        ValidateBufferArguments(buffer, offset, count);
        return Read(buffer.AsSpan(offset, count));
    }

    public sealed override int Read(Span<byte> destination)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        if (destination.IsEmpty) return 0;
        failure?.Throw();
        int count;
        try
        {
            count = ReadAt(position, destination);
        }
        catch (Exception exception)
        {
            // A failed check or read leaves the decoder part way through the member. Without this, a
            // caller that retries would be handed bytes out of place.
            failure = ExceptionDispatchInfo.Capture(exception);
            throw;
        }
        position += count;
        return count;
    }

    public sealed override ValueTask<int> ReadAsync(Memory<byte> destination, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return ValueTask.FromResult(Read(destination.Span));
    }

    public sealed override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        ValidateBufferArguments(buffer, offset, count);
        return ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
    }

    public sealed override long Seek(long offset, SeekOrigin origin)
    {
        ObjectDisposedException.ThrowIf(disposed, this);
        var next = origin switch
        {
            SeekOrigin.Begin => offset,
            SeekOrigin.Current => checked(position + offset),
            SeekOrigin.End => checked(length + offset),
            _ => throw new ArgumentOutOfRangeException(nameof(origin))
        };
        if (next < 0 || next > length) throw new IOException($"Seek lies outside the {description}.");
        var failed = failure is not null;
        failure = null;
        Moved(next, failed);
        return position = next;
    }

    public sealed override void Flush() { }
    public sealed override void SetLength(long value) => throw new NotSupportedException();
    public sealed override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        disposed = true;
        base.Dispose(disposing);
    }

    // Reads the decoded bytes at position into destination, which is not empty, and returns how many
    // were read; 0 only at the end, once the member has been checked. position lies within the member.
    protected abstract int ReadAt(long position, Span<byte> destination);

    // Called when a seek sets the position to next. failed is true when an earlier read failed; the
    // next read must then decode again from the start.
    protected abstract void Moved(long next, bool failed);
}
