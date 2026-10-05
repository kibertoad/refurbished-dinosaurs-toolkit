using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;

namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes one InstallShield cabinet member while it is read. Stored bytes may be obfuscated; compressed
// members are a run of chunks, each a 16-bit little-endian length and raw deflate data that expands
// to at most 64 KiB on its own.
internal sealed class InstallShieldMemberStream : DecodingMemberStream
{
    private const int ChunkLimit = 64 * 1024;

    private readonly string path;
    private readonly long length;
    private readonly bool compressed;
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
    private uint seed;
    private IncrementalHash? hash;
    private long produced;
    private long bufferStart;
    private int bufferCount;
    private bool verified;

    public InstallShieldMemberStream(
        string path, long length, bool compressed, bool obfuscated, byte[]? md5,
        InstallShieldSegment[] segments, Func<int, Stream> openVolume)
        : base(length, "InstallShield cabinet member")
    {
        this.path = path;
        this.length = length;
        this.compressed = compressed;
        chunk = compressed ? new byte[ushort.MaxValue] : [];
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
            volume?.Dispose();
            hash?.Dispose();
        }
        base.Dispose(disposing);
    }

    private void Restart()
    {
        volume?.Dispose();
        volume = null;
        openVolumeNumber = 0;
        segment = -1;
        segmentLeft = 0;
        rawRead = 0;
        seed = 0;
        hash?.Dispose();
        hash = md5 is null ? null : IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        produced = 0;
        bufferStart = 0;
        bufferCount = 0;
        verified = false;
    }

    // Decodes the next chunk (or the next block of stored bytes) into the buffer.
    private void DecodeNext()
    {
        int count;
        if (compressed)
        {
            count = DecodeChunk();
        }
        else
        {
            count = (int)Math.Min(ChunkLimit, length - produced);
            ReadRaw(buffer.AsSpan(0, count));
        }

        bufferStart = produced;
        bufferCount = count;
        produced += count;
        if (produced > length) throw Invalid($"expands past its declared size of {length} bytes");
        hash?.AppendData(buffer, 0, count);
        if (produced == length) Finish();
        else if (rawRead == rawLength) throw Invalid($"ends after {produced} of its {length} bytes");
    }

    // Reads the next compressed chunk and inflates it into the buffer.
    private int DecodeChunk()
    {
        Span<byte> prefix = stackalloc byte[2];
        ReadRaw(prefix);
        var chunkLength = BinaryPrimitives.ReadUInt16LittleEndian(prefix);
        if (chunkLength == 0)
            throw Invalid("has a compressed chunk of length zero. Chunks delimited by 00 00 FF FF markers are not supported");
        ReadRaw(chunk.AsSpan(0, chunkLength));
        int count;
        bool overruns;
        try
        {
            using var inflater = new DeflateStream(new MemoryStream(chunk, 0, chunkLength), CompressionMode.Decompress);
            count = 0;
            int read;
            while (count < ChunkLimit && (read = inflater.Read(buffer, count, ChunkLimit - count)) > 0) count += read;
            overruns = count == ChunkLimit && inflater.ReadByte() >= 0;
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException($"InstallShield cabinet member '{path}' has a chunk that does not inflate.", exception);
        }
        if (overruns) throw Invalid($"has a compressed chunk that expands past {ChunkLimit} bytes");
        return count;
    }

    // Called once every expanded byte is decoded, before the last of them is returned.
    private void Finish()
    {
        // Chunks can follow the last expanded byte (a zero-length member can still hold one); any that
        // expand to bytes overrun the member.
        while (compressed && rawRead < rawLength)
            if (DecodeChunk() != 0) throw Invalid($"expands past its declared size of {length} bytes");
        if (rawRead != rawLength) throw Invalid($"has data left after its declared size of {length} bytes");
        if (hash is not null && !hash.GetCurrentHash().AsSpan().SequenceEqual(md5!))
            throw Invalid("does not match the MD5 the cabinet header records");
        verified = true;
    }

    private void ReadRaw(Span<byte> destination)
    {
        if (destination.Length > rawLength - rawRead)
            throw Invalid($"has a compressed chunk that runs past its {rawLength} stored bytes");
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
        if (!obfuscated) return;
        for (var index = 0; index < destination.Length; index++, seed++)
        {
            var value = (byte)(destination[index] ^ 0xd5);
            value = (byte)((value >> 2) | (value << 6));
            destination[index] = (byte)(value - seed % 0x47);
        }
    }

    private InvalidDataException Invalid(string problem) =>
        new($"InstallShield cabinet member '{path}' {problem}.");
}
