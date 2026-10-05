using System.Runtime.ExceptionServices;

namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes one InstallShield 3 archive member while it is read. A stored member is its stored bytes;
// a compressed one is PKWARE DCL data that ends with the end code.
internal sealed class InstallShieldArchiveMemberStream : Stream
{
    private const int InputBuffer = 16 * 1024;

    private readonly string path;
    private readonly long length;
    private readonly bool stored;
    private readonly long dataOffset;
    private readonly long storedSize;
    private readonly Func<Stream> openArchive;
    private readonly byte[] input = new byte[InputBuffer];
    private readonly byte[] skip = new byte[InputBuffer];

    private Stream? archive;
    private PkwareDclExploder? exploder;
    private long storedRead;
    private int inputStart;
    private int inputCount;
    private long produced;
    private long position;
    private bool verified;
    // The failure of an earlier read, rethrown by every later read until a seek decodes again from the start.
    private ExceptionDispatchInfo? failure;

    public InstallShieldArchiveMemberStream(
        string path, long length, bool stored, long dataOffset, long storedSize, Func<Stream> openArchive)
    {
        this.path = path;
        this.length = length;
        this.stored = stored;
        this.dataOffset = dataOffset;
        this.storedSize = storedSize;
        this.openArchive = openArchive;
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
        ValidateBufferArguments(buffer, offset, count);
        return Read(buffer.AsSpan(offset, count));
    }

    public override int Read(Span<byte> destination)
    {
        if (destination.IsEmpty) return 0;
        failure?.Throw();
        try
        {
            if (archive is null || position < produced) Restart();
            while (produced < position)
                Decode(skip.AsSpan(0, (int)Math.Min(skip.Length, position - produced)));
            if (position >= length)
            {
                if (!verified) Finish();
                return 0;
            }
            var count = Decode(destination[..(int)Math.Min(destination.Length, length - position)]);
            position += count;
            return count;
        }
        catch (Exception exception)
        {
            // A failed check leaves the decoder part way through the member. Without this, a caller that
            // retries would be handed bytes out of place.
            failure = ExceptionDispatchInfo.Capture(exception);
            throw;
        }
    }

    public override ValueTask<int> ReadAsync(Memory<byte> destination, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return ValueTask.FromResult(Read(destination.Span));
    }

    public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken cancellationToken)
    {
        ValidateBufferArguments(buffer, offset, count);
        return ReadAsync(buffer.AsMemory(offset, count), cancellationToken).AsTask();
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        var next = origin switch
        {
            SeekOrigin.Begin => offset,
            SeekOrigin.Current => checked(position + offset),
            SeekOrigin.End => checked(length + offset),
            _ => throw new ArgumentOutOfRangeException(nameof(origin))
        };
        if (next < 0 || next > length) throw new IOException("Seek lies outside the InstallShield 3 archive member.");
        if (failure is not null)
        {
            failure = null;
            CloseArchive();
        }
        return position = next;
    }

    public override void Flush() { }
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing) CloseArchive();
        base.Dispose(disposing);
    }

    private void CloseArchive()
    {
        archive?.Dispose();
        archive = null;
    }

    private void Restart()
    {
        CloseArchive();
        archive = openArchive();
        archive.Position = dataOffset;
        storedRead = 0;
        inputStart = 0;
        inputCount = 0;
        exploder = stored ? null : new PkwareDclExploder(NextByte);
        produced = 0;
        verified = false;
    }

    // Decodes up to destination's length, never past the declared size, and checks the member once
    // its last byte is decoded, before that byte is returned.
    private int Decode(Span<byte> destination)
    {
        int count;
        if (stored)
        {
            count = ReadStored(destination);
        }
        else
        {
            count = Explode(destination);
            if (count == 0) throw Invalid($"ends after {produced} of its {length} bytes");
        }
        produced += count;
        if (produced == length) Finish();
        return count;
    }

    private void Finish()
    {
        if (exploder is not null)
        {
            // The end code must follow the last declared byte, and the stored data must end with it.
            Span<byte> one = stackalloc byte[1];
            if (Explode(one) != 0) throw Invalid($"expands past its declared size of {length} bytes");
            if (exploder.HasTrailingBytes()) throw Invalid("has stored data left after its end code");
        }
        verified = true;
    }

    private int Explode(Span<byte> destination)
    {
        try
        {
            return exploder!.Read(destination);
        }
        catch (InvalidDataException exception) when (exception.Source != nameof(InstallShieldArchiveMemberStream))
        {
            throw new InvalidDataException(
                $"InstallShield 3 archive member '{path}' does not decode after {produced} bytes: {exception.Message}", exception);
        }
    }

    private int ReadStored(Span<byte> destination)
    {
        var want = (int)Math.Min(destination.Length, storedSize - storedRead);
        if (want == 0) return 0;
        var read = archive!.Read(destination[..want]);
        if (read == 0) throw Invalid("ends early: the archive is shorter than when it was opened");
        storedRead += read;
        return read;
    }

    private int NextByte()
    {
        if (inputStart == inputCount)
        {
            inputStart = 0;
            inputCount = ReadStored(input);
            if (inputCount == 0) return -1;
        }
        return input[inputStart++];
    }

    // Source marks the exceptions this stream raises, so Explode passes them on unwrapped.
    private InvalidDataException Invalid(string problem) =>
        new($"InstallShield 3 archive member '{path}' {problem}.") { Source = nameof(InstallShieldArchiveMemberStream) };
}
