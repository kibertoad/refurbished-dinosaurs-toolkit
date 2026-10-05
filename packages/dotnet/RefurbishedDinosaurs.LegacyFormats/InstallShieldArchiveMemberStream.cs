namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes one InstallShield 3 archive member while it is read. A stored member is its stored bytes;
// a compressed one is PKWARE DCL data that ends with the end code.
internal sealed class InstallShieldArchiveMemberStream : DecodingMemberStream
{
    private const int InputBuffer = 16 * 1024;

    private readonly string path;
    private readonly long length;
    private readonly bool stored;
    private readonly long dataOffset;
    private readonly long storedSize;
    private readonly Func<Stream> openArchive;
    // Compressed bytes read ahead of the decoder; a stored member is read straight into the caller's buffer.
    private readonly byte[] input;
    // Where a forward seek decodes the bytes it skips, made on the first such seek.
    private byte[]? skip;

    private Stream? archive;
    private PkwareDclExploder? exploder;
    private long storedRead;
    private int inputStart;
    private int inputCount;
    private long produced;
    private bool verified;
    // Set while NextByte reads the archive. Explode leaves a failure raised there unwrapped, so a
    // failure of the archive or its container is not reported as undecodable member data.
    private bool readingInput;

    public InstallShieldArchiveMemberStream(
        string path, long length, bool stored, long dataOffset, long storedSize, Func<Stream> openArchive)
        : base(length, "InstallShield 3 archive member")
    {
        this.path = path;
        this.length = length;
        this.stored = stored;
        this.dataOffset = dataOffset;
        this.storedSize = storedSize;
        this.openArchive = openArchive;
        input = stored ? [] : new byte[InputBuffer];
    }

    protected override int ReadAt(long position, Span<byte> destination)
    {
        if (archive is null || position < produced) Restart();
        while (produced < position)
        {
            skip ??= new byte[InputBuffer];
            Decode(skip.AsSpan(0, (int)Math.Min(skip.Length, position - produced)));
        }
        if (position >= length)
        {
            if (!verified) Finish();
            return 0;
        }
        return Decode(destination[..(int)Math.Min(destination.Length, length - position)]);
    }

    protected override void Moved(long next, bool failed)
    {
        if (failed) CloseArchive();
    }

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
        readingInput = false;
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
        catch (InvalidDataException exception) when (!readingInput)
        {
            throw new InvalidDataException(
                $"InstallShield 3 archive member '{path}' does not decode after {exploder!.Produced} bytes: {exception.Message}", exception);
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
            // Left set when ReadStored throws; Restart clears it.
            readingInput = true;
            inputCount = ReadStored(input);
            readingInput = false;
            if (inputCount == 0) return -1;
        }
        return input[inputStart++];
    }

    private InvalidDataException Invalid(string problem) =>
        new($"InstallShield 3 archive member '{path}' {problem}.");
}
