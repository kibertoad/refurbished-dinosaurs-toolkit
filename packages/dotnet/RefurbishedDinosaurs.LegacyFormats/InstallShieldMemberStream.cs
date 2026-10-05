using System.Buffers.Binary;
using System.IO.Compression;
using System.Runtime.ExceptionServices;
using System.Security.Cryptography;

namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes one InstallShield cabinet member while it is read. Stored bytes may be obfuscated. A
// compressed member is read in the form the caller chose for the set (Unshield's default and its -O):
// - LengthPrefixedChunks: a run of chunks, each a 16-bit little-endian length and raw deflate data
//   that expands to at most 64 KiB on its own.
// - MarkerDelimitedChunks: raw deflate data with no lengths, flushed to a byte boundary after each
//   chunk, so each chunk ends with an empty stored block (00 00 FF FF) and no block is final. The
//   stored bytes are decoded as one deflate stream: an empty stored block is a valid block, and the
//   block after it starts on the next byte. Unshield instead splits the bytes at each 00 00 FF FF it
//   finds and inflates each piece apart, which also splits at those bytes inside a chunk's data.
internal sealed class InstallShieldMemberStream : DecodingMemberStream
{
    private const int ChunkLimit = 64 * 1024;
    // The empty stored block that ends each marker-delimited chunk, as its last four bytes read
    // most significant first: 00 00 FF FF.
    private const uint Marker = 0x0000ffff;
    private const string LengthPrefixedHint =
        " The set was opened to read length-prefixed chunks; a set whose compressed data has no chunk lengths " +
        "(what Unshield reads with -O) is opened with InstallShieldCompressedFormat.MarkerDelimitedChunks";

    private readonly string path;
    private readonly long length;
    private readonly InstallShieldCompressedFormat? format;
    private readonly bool obfuscated;
    private readonly byte[]? md5;
    private readonly InstallShieldSegment[] segments;
    private readonly Func<int, Stream> openVolume;
    private readonly long rawLength;
    private readonly byte[] buffer = new byte[ChunkLimit];
    private readonly byte[] chunk;

    private Stream? volume;
    private int openVolumeNumber;
    private int segment;
    private long segmentLeft;
    private long rawRead;
    private uint lastFour;
    // Set when a marker-delimited member's decoder asks for stored bytes past the last one.
    private bool rawExhausted;
    private uint seed;
    private IncrementalHash? hash;
    private DeflateStream? inflater;
    private long produced;
    private long bufferStart;
    private int bufferCount;
    private bool verified;

    // format is null for a member stored without compression.
    public InstallShieldMemberStream(
        string path, long length, InstallShieldCompressedFormat? format, bool obfuscated, byte[]? md5,
        InstallShieldSegment[] segments, Func<int, Stream> openVolume)
        : base(length, "InstallShield cabinet member")
    {
        this.path = path;
        this.length = length;
        this.format = format;
        chunk = format == InstallShieldCompressedFormat.LengthPrefixedChunks ? new byte[ushort.MaxValue] : [];
        this.obfuscated = obfuscated;
        this.md5 = md5;
        this.segments = segments;
        this.openVolume = openVolume;
        rawLength = segments.Sum(item => item.Length);
        Restart();
    }

    protected override int ReadAt(long position, Span<byte> destination)
    {
        if (position >= length)
        {
            // A seek to the end skips decoding; the member is still checked before reporting its end.
            if (produced == length && !verified) Finish();
            while (!verified) DecodeNext();
            return 0;
        }
        while (position >= bufferStart + bufferCount) DecodeNext();
        var available = (int)(bufferStart + bufferCount - position);
        var count = Math.Min(available, destination.Length);
        buffer.AsSpan((int)(position - bufferStart), count).CopyTo(destination);
        return count;
    }

    protected override void Moved(long next, bool failed)
    {
        if (next < bufferStart || failed) Restart();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            inflater?.Dispose();
            volume?.Dispose();
            hash?.Dispose();
        }
        base.Dispose(disposing);
    }

    private void Restart()
    {
        inflater?.Dispose();
        inflater = null;
        volume?.Dispose();
        volume = null;
        openVolumeNumber = 0;
        segment = -1;
        segmentLeft = 0;
        rawRead = 0;
        lastFour = 0;
        rawExhausted = false;
        seed = 0;
        hash?.Dispose();
        hash = md5 is null ? null : IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        produced = 0;
        bufferStart = 0;
        bufferCount = 0;
        verified = false;
    }

    // Decodes the next chunk (or the next block of stored or inflated bytes) into the buffer.
    private void DecodeNext()
    {
        int count;
        switch (format)
        {
            case InstallShieldCompressedFormat.LengthPrefixedChunks:
                count = DecodeChunk();
                break;
            case InstallShieldCompressedFormat.MarkerDelimitedChunks:
                var want = (int)Math.Min(ChunkLimit, length - produced);
                count = Inflate(want);
                // The decoder stops short only when its stored bytes run out or a final block ends it.
                if (count < want) throw Invalid($"ends after {produced + count} of its {length} bytes");
                break;
            default:
                count = (int)Math.Min(ChunkLimit, length - produced);
                ReadRaw(buffer.AsSpan(0, count));
                break;
        }

        bufferStart = produced;
        bufferCount = count;
        produced += count;
        if (produced > length) throw Invalid($"expands past its declared size of {length} bytes");
        hash?.AppendData(buffer, 0, count);
        if (produced == length) Finish();
        else if (rawRead == rawLength && format != InstallShieldCompressedFormat.MarkerDelimitedChunks) throw Invalid($"ends after {produced} of its {length} bytes");
    }

    // Reads the next length-prefixed chunk and inflates it into the buffer.
    private int DecodeChunk()
    {
        Span<byte> prefix = stackalloc byte[2];
        ReadChunkBytes(prefix);
        var chunkLength = BinaryPrimitives.ReadUInt16LittleEndian(prefix);
        if (chunkLength == 0)
            throw Invalid("has a compressed chunk of length zero." + LengthPrefixedHint);
        ReadChunkBytes(chunk.AsSpan(0, chunkLength));
        int count;
        bool overruns;
        try
        {
            using var chunkInflater = new DeflateStream(new MemoryStream(chunk, 0, chunkLength), CompressionMode.Decompress);
            count = 0;
            int read;
            while (count < ChunkLimit && (read = chunkInflater.Read(buffer, count, ChunkLimit - count)) > 0) count += read;
            overruns = count == ChunkLimit && chunkInflater.ReadByte() >= 0;
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException(
                $"InstallShield cabinet member '{path}' has a chunk that does not inflate.{LengthPrefixedHint}.", exception);
        }
        if (overruns) throw Invalid($"has a compressed chunk that expands past {ChunkLimit} bytes." + LengthPrefixedHint);
        return count;
    }

    private void ReadChunkBytes(Span<byte> destination)
    {
        if (destination.Length > rawLength - rawRead)
            throw Invalid($"has a compressed chunk that runs past its {rawLength} stored bytes." + LengthPrefixedHint);
        ReadRaw(destination);
    }

    // Inflates up to count bytes of a marker-delimited member into the buffer, returning fewer only
    // when the deflate stream ends.
    private int Inflate(int count)
    {
        var filled = 0;
        inflater ??= new DeflateStream(new RawReader(this), CompressionMode.Decompress);
        try
        {
            int read;
            while (filled < count && (read = inflater.Read(buffer, filled, count - filled)) > 0) filled += read;
        }
        catch (StoredBytesException exception)
        {
            // A failure reading the stored bytes is reported as itself, not as bad deflate data.
            ExceptionDispatchInfo.Capture(exception.InnerException!).Throw();
            throw;
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException(
                $"InstallShield cabinet member '{path}' has marker-delimited compressed data that does not inflate, " +
                $"within its first {rawRead} stored bytes.", exception);
        }
        return filled;
    }

    // Called once every expanded byte is decoded, before the last of them is returned.
    private void Finish()
    {
        // Chunks can follow the last expanded byte (a zero-length member can still hold one); any that
        // expand to bytes overrun the member.
        if (format == InstallShieldCompressedFormat.LengthPrefixedChunks)
        {
            while (rawRead < rawLength)
                if (DecodeChunk() != 0) throw Invalid($"expands past its declared size of {length} bytes");
        }
        else if (format == InstallShieldCompressedFormat.MarkerDelimitedChunks)
        {
            if (Inflate(1) != 0) throw Invalid($"expands past its declared size of {length} bytes");
            // The form has no final block, so a decoder that read the whole stream asked for bytes past
            // the last one. One that stopped on a final block never did, and the bytes after that block
            // were not decoded, however many of them it had already taken in.
            if (!rawExhausted)
                throw Invalid("has marker-delimited compressed data that ends with a final deflate block, which the form does not use");
        }
        if (rawRead != rawLength) throw Invalid($"has data left after its declared size of {length} bytes");
        if (format == InstallShieldCompressedFormat.MarkerDelimitedChunks && rawLength > 0 && (rawLength < 4 || lastFour != Marker))
            throw Invalid("has marker-delimited compressed data that does not end with the 00 00 FF FF marker");
        if (hash is not null && !hash.GetCurrentHash().AsSpan().SequenceEqual(md5!))
            throw Invalid("does not match the MD5 the cabinet header records");
        verified = true;
    }

    private void ReadRaw(Span<byte> destination)
    {
        if (destination.Length > rawLength - rawRead)
            throw Invalid($"needs more than its {rawLength} stored bytes");
        var filled = 0;
        while (filled < destination.Length)
        {
            if (segmentLeft == 0)
            {
                var next = segments[++segment];
                if (volume is null || openVolumeNumber != next.Volume)
                {
                    volume?.Dispose();
                    volume = openVolume(next.Volume);
                    openVolumeNumber = next.Volume;
                }
                volume.Position = next.Offset;
                segmentLeft = next.Length;
            }
            var want = (int)Math.Min(destination.Length - filled, segmentLeft);
            var read = volume!.Read(destination.Slice(filled, want));
            if (read == 0) throw Invalid("ends early: its cabinet volume is shorter than when it was opened");
            filled += read;
            segmentLeft -= read;
        }
        rawRead += destination.Length;
        if (obfuscated)
        {
            for (var index = 0; index < destination.Length; index++, seed++)
            {
                var value = (byte)(destination[index] ^ 0xd5);
                value = (byte)((value >> 2) | (value << 6));
                destination[index] = (byte)(value - seed % 0x47);
            }
        }
        if (format == InstallShieldCompressedFormat.MarkerDelimitedChunks)
            foreach (var value in destination[Math.Max(0, destination.Length - 4)..]) lastFour = (lastFour << 8) | value;
    }

    private InvalidDataException Invalid(string problem) =>
        new($"InstallShield cabinet member '{path}' {problem}.");

    // The member's stored bytes, deobfuscated, as the stream a marker-delimited member's decoder reads.
    private sealed class RawReader(InstallShieldMemberStream member) : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => false;
        public override bool CanWrite => false;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }

        public override int Read(byte[] buffer, int offset, int count) => Read(buffer.AsSpan(offset, count));

        public override int Read(Span<byte> destination)
        {
            var count = (int)Math.Min(destination.Length, member.rawLength - member.rawRead);
            if (count == 0) member.rawExhausted = true;
            try
            {
                if (count > 0) member.ReadRaw(destination[..count]);
            }
            catch (Exception exception)
            {
                throw new StoredBytesException(exception);
            }
            return count;
        }

        public override void Flush() { }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    // Carries a failure to read the stored bytes out through the decoder.
    private sealed class StoredBytesException(Exception inner) : Exception(inner.Message, inner);
}
