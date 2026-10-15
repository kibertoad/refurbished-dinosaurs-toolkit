using System.Buffers.Binary;
using System.Security.Cryptography;

namespace RefurbishedDinosaurs.LegacyFormats;

// A FLAC file (RFC 9639) of 16-bit stereo 44.1 kHz audio, the CD's own samples, read as the
// interleaved little-endian samples a BIN or WAVE holds. The STREAMINFO block is read when the file
// is opened. The frames are decoded the first time a stream reads from the file: every frame's
// CRC-8 and CRC-16 are checked, the samples must add up to the count STREAMINFO declares, and their
// MD5 must equal the one STREAMINFO records, unless it records none (all zeros). That pass records
// where each frame starts, so later reads decode only the frames they need.
internal sealed class FlacAudioFile : ISectorStore
{
    // A frame of 65535 samples of two 16-bit channels, stored verbatim, takes about 256 KiB; an
    // escaped residual of 31 bits a sample about 500 KiB. A frame is read from a window that holds
    // at least this much of the file from its start, or the rest of the file.
    private const int MaximumFrameBytes = 1024 * 1024;
    private const string ReadOnly = "a disc-image source reads FLAC only of 16-bit stereo 44.1 kHz audio, the CD's own samples.";

    private readonly ImageSnapshot file;
    private readonly long firstFrame;
    private readonly byte[] md5;
    private readonly Lazy<FrameIndex> index;

    private FlacAudioFile(ImageSnapshot file, long firstFrame, long samples, byte[] md5)
    {
        this.file = file;
        this.firstFrame = firstFrame;
        Samples = samples;
        this.md5 = md5;
        index = new Lazy<FrameIndex>(BuildIndex, LazyThreadSafetyMode.ExecutionAndPublication);
    }

    // The stereo samples the file holds, as its STREAMINFO declares them.
    public long Samples { get; }

    // Whether the stream starts with the FLAC signature.
    public static bool HasSignature(Stream stream)
    {
        Span<byte> tag = stackalloc byte[4];
        stream.Position = 0;
        return stream.ReadAtLeast(tag, 4, throwOnEndOfStream: false) == 4 && tag.SequenceEqual("fLaC"u8);
    }

    // Reads the metadata blocks of a FLAC file, or says why the file is not one this reads.
    public static (FlacAudioFile? File, string? Problem) Read(ImageSnapshot file, Stream stream)
    {
        if (!HasSignature(stream)) return (null, "it does not start with the FLAC signature.");
        var header = new byte[4];
        byte[]? info = null;
        long at = 4;
        while (true)
        {
            stream.Position = at;
            if (stream.ReadAtLeast(header, 4, throwOnEndOfStream: false) < 4)
                return (null, "its FLAC metadata blocks run past the end of the file.");
            var last = (header[0] & 0x80) != 0;
            var type = header[0] & 0x7F;
            var length = header[1] << 16 | header[2] << 8 | header[3];
            if (at == 4)
            {
                if (type != 0 || length != 34) return (null, "its first FLAC metadata block is not a STREAMINFO block.");
                info = new byte[34];
                if (stream.ReadAtLeast(info, 34, throwOnEndOfStream: false) < 34)
                    return (null, "its FLAC STREAMINFO block runs past the end of the file.");
            }
            else if (type == 127) return (null, "it has a FLAC metadata block of the invalid type 127.");
            at += 4 + length;
            if (at > stream.Length) return (null, "its FLAC metadata blocks run past the end of the file.");
            if (last) break;
        }
        if (info is null) return (null, "it has no FLAC STREAMINFO block.");

        // STREAMINFO: 16 bits each of minimum and maximum block size, 24 bits each of minimum and
        // maximum frame size, then 20 bits of sample rate, 3 of channels less one, 5 of bits per
        // sample less one, 36 of total samples, and the 128-bit MD5.
        var packed = BinaryPrimitives.ReadUInt64BigEndian(info.AsSpan(10));
        var rate = (int)(packed >> 44);
        var channels = (int)(packed >> 41 & 7) + 1;
        var bits = (int)(packed >> 36 & 31) + 1;
        var samples = (long)(packed & 0xF_FFFF_FFFF);
        if (rate != 44100 || channels != 2 || bits != 16)
            return (null, $"it holds FLAC audio of {channels} channels, {bits} bits and {rate} Hz; {ReadOnly}");
        if (samples == 0)
            return (null, "its FLAC STREAMINFO does not give the number of samples, so the length of its audio is not known.");
        return (new FlacAudioFile(file, at, samples, info[18..34]), null);
    }

    public Stream OpenSectors() => new FlacPcmStream(this);

    private FrameIndex BuildIndex()
    {
        var starts = new List<long>();
        var offsets = new List<long>();
        var decoder = new FlacDecoder();
        var output = new byte[4 * FlacDecoder.MaximumBlockSize];
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        using var stream = file.OpenBuffered();
        var window = new FrameWindow(stream);
        long decoded = 0, offset = firstFrame;
        while (decoded < Samples)
        {
            var span = window.At(offset);
            var count = Decode(decoder, span, (int)Math.Min(Samples - decoded, FlacDecoder.MaximumBlockSize), output, out var length);
            starts.Add(decoded);
            offsets.Add(offset);
            hash.AppendData(output, 0, 4 * count);
            decoded += count;
            offset += length;
        }
        // Bytes after the last frame, such as a tag some tools append, hold no samples.
        var digest = hash.GetHashAndReset();
        if (md5.AsSpan().ContainsAnyExcept((byte)0) && !digest.AsSpan().SequenceEqual(md5))
            throw new InvalidDataException(
                $"The FLAC file {file.Path} decodes to samples whose MD5 differs from the one its STREAMINFO records.");
        return new FrameIndex([.. starts], [.. offsets]);
    }

    // The bytes of the file from a frame's start, read through a buffer twice the largest frame. It
    // is filled again only when fewer bytes than the largest frame remain in it past that start and
    // the file goes on, so frames decoded in order read each byte of the file about once.
    private sealed class FrameWindow(Stream stream)
    {
        private readonly byte[] buffer = new byte[2 * MaximumFrameBytes];
        private long start = -1;
        private int length;

        public ReadOnlySpan<byte> At(long offset)
        {
            var end = start + length;
            // A fill shorter than the buffer reached the end of the file.
            var reachesEnd = length < buffer.Length;
            if (start < 0 || offset < start || offset > end || (end - offset < MaximumFrameBytes && !reachesEnd))
            {
                stream.Position = offset;
                length = stream.ReadAtLeast(buffer, buffer.Length, throwOnEndOfStream: false);
                start = offset;
                end = start + length;
            }
            return buffer.AsSpan((int)(offset - start), (int)(end - offset));
        }
    }

    private int Decode(FlacDecoder decoder, ReadOnlySpan<byte> input, int maximumSamples, Span<byte> output, out int length)
    {
        try
        {
            if (input.IsEmpty) throw new InvalidDataException("the file ends before the samples its STREAMINFO declares.");
            return decoder.DecodeFrame(input, maximumSamples, output, bigEndian: false, out length);
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException($"The FLAC file {file.Path} cannot be decoded: {exception.Message}", exception);
        }
    }

    // Where each frame starts, by its first sample and its byte offset in the file.
    private sealed record FrameIndex(long[] Starts, long[] Offsets);

    // The samples of a FLAC file as a read-only seekable stream of little-endian bytes. Each stream
    // reads the file through its own handle and keeps the last frame it decoded.
    private sealed class FlacPcmStream : DecodingMemberStream
    {
        private readonly FlacAudioFile audio;
        private readonly Stream file;
        private readonly FrameWindow window;
        private readonly FlacDecoder decoder = new();
        private readonly byte[] frame = new byte[4 * FlacDecoder.MaximumBlockSize];
        private int cachedFrame = -1;
        private int cachedSamples;

        public FlacPcmStream(FlacAudioFile audio) : base(audio.Samples * 4, "FLAC audio")
        {
            this.audio = audio;
            file = audio.file.OpenBuffered();
            window = new FrameWindow(file);
        }

        protected override int ReadAt(long position, Span<byte> destination)
        {
            var total = (int)Math.Min(destination.Length, Length - position);
            if (total == 0) return 0;
            var index = audio.index.Value;
            var done = 0;
            while (done < total)
            {
                var sample = position / 4;
                var number = Array.BinarySearch(index.Starts, sample);
                if (number < 0) number = ~number - 1;
                if (number != cachedFrame)
                {
                    cachedFrame = -1;
                    var span = window.At(index.Offsets[number]);
                    var remaining = audio.Samples - index.Starts[number];
                    cachedSamples = audio.Decode(decoder, span, (int)Math.Min(remaining, FlacDecoder.MaximumBlockSize), frame, out _);
                    cachedFrame = number;
                }
                var within = (int)(position - index.Starts[number] * 4);
                var count = Math.Min(total - done, 4 * cachedSamples - within);
                frame.AsSpan(within, count).CopyTo(destination[done..]);
                done += count;
                position += count;
            }
            return total;
        }

        // Each read finds its frame from the position, and a failed decode leaves no frame cached.
        protected override void Moved(long next, bool failed) { }

        protected override void Dispose(bool disposing)
        {
            if (disposing) file.Dispose();
            base.Dispose(disposing);
        }
    }
}
