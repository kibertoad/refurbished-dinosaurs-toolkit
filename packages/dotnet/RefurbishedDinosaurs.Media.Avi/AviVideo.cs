using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.Media.Avi;

/// <summary>One compressed video frame of an <see cref="AviVideo"/>.</summary>
/// <param name="Data">The frame's codec payload, exactly as stored in the file.</param>
public sealed record AviVideoFrame(byte[] Data);

/// <summary>
/// The audio stream an <see cref="AviVideo"/> carries, as its
/// <c>WAVEFORMATEX</c> header. Microsoft ADPCM uses
/// (<see cref="FormatTag"/> 2), so the raw chunks are exposed rather than
/// decoded samples.
/// </summary>
public sealed record AviAudioFormat(
    int FormatTag,
    int Channels,
    int SamplesPerSecond,
    int AverageBytesPerSecond,
    int BlockAlign,
    int BitsPerSample,
    byte[] Extra);

/// <summary>
/// A parsed AVI container: the video stream's geometry, frame rate, codec,
/// palette and compressed frames, and the audio stream's format and chunks.
/// The frames are the raw codec payloads; <see cref="RleVideoSurface"/> and
/// <see cref="CinepakSurface"/> turn them into pixels.
/// </summary>
public sealed record AviVideo(
    int Width,
    int Height,
    int MicrosecondsPerFrame,
    int DeclaredFrameCount,
    string Codec,
    uint Compression,
    byte[]? Palette,
    IReadOnlyList<AviVideoFrame> Frames,
    AviAudioFormat? Audio,
    IReadOnlyList<byte[]> AudioChunks)
{
    /// <summary>The <c>BI_RLE8</c> compression tag.</summary>
    public const uint Rle8Compression = 1;

    /// <summary>The <c>cvid</c> compression FourCC, lower case, as a little-endian tag.</summary>
    private const uint CinepakFourCc = 0x64697663;

    /// <summary>Whether the video stream is 8-bit Microsoft RLE.</summary>
    public bool IsRle8 => Compression == Rle8Compression;

    /// <summary>
    /// Whether the video stream is Cinepak: the stream handler or the bitmap
    /// compression is <c>cvid</c>, in either case.
    /// </summary>
    public bool IsCinepak =>
        string.Equals(Codec, "cvid", StringComparison.OrdinalIgnoreCase)
        || (Compression | 0x20202020u) == CinepakFourCc;

    /// <summary>The declared frame rate in frames per second, or 0 when undeclared.</summary>
    public double FramesPerSecond => MicrosecondsPerFrame > 0 ? 1_000_000.0 / MicrosecondsPerFrame : 0;
}

/// <summary>
/// Bounded reader for the AVI (RIFF) container with one video and at most one audio stream. It reads the <c>hdrl</c> stream headers and the
/// <c>movi</c> chunk list; it does not decode the video or audio codecs.
/// Every length is checked and malformed input fails with a descriptive
/// <see cref="InvalidDataException"/>.
/// </summary>
public static class AviReader
{
    /// <summary>Upper bound on a single frame's compressed payload.</summary>
    public const long MaxFrameBytes = 64L * 1024 * 1024;

    /// <summary>Upper bound on the declared video dimensions.</summary>
    public const int MaxDimension = 4096;

    /// <summary>Upper bound on the number of video frames, to fail fast on hostile counts.</summary>
    public const int MaxFrames = 100_000;

    /// <summary>Upper bound on the whole source, to fail fast on hostile files.</summary>
    public const long MaxSourceBytes = 256L * 1024 * 1024;

    /// <summary>Decodes a complete AVI file held in memory.</summary>
    public static AviVideo Decode(ReadOnlySpan<byte> source)
    {
        if (source.Length < 12)
            throw new InvalidDataException($"AVI file is {source.Length} bytes, shorter than the RIFF header.");
        if (!source[..4].SequenceEqual("RIFF"u8) || !source[8..12].SequenceEqual("AVI "u8))
            throw new InvalidDataException("Not an AVI file: the RIFF/AVI signature is missing.");
        if (source.Length > MaxSourceBytes)
            throw new InvalidDataException($"AVI file is {source.Length} bytes, above the {MaxSourceBytes}-byte limit.");

        if ((long)BinaryPrimitives.ReadUInt32LittleEndian(source[4..]) + 8 != source.Length)
            throw new InvalidDataException("AVI RIFF length does not match the source.");
        uint microsecondsPerFrame = 0;
        int declaredFrameCount = 0;
        var streamTypes = new List<string>();
        var streamHandlers = new List<string>();
        var streamFormats = new List<byte[]>();
        int moviStart = -1;
        int moviEnd = -1;

        int offset = 12;
        while (offset + 8 <= source.Length)
        {
            var id = source.Slice(offset, 4);
            uint size = BinaryPrimitives.ReadUInt32LittleEndian(source[(offset + 4)..]);
            long bodyEnd = (long)offset + 8 + size;
            if (bodyEnd + (size & 1) > source.Length)
                throw new InvalidDataException($"AVI chunk at {offset} declares {size} bytes, past the end of the file.");
            int body = offset + 8;

            if (id.SequenceEqual("LIST"u8))
            {
                if (size < 4)
                    throw new InvalidDataException($"AVI LIST at {offset} is too short to hold its type.");
                var listType = source.Slice(body, 4);
                if (listType.SequenceEqual("hdrl"u8))
                {
                    if (streamTypes.Count != 0) throw new InvalidDataException("Duplicate AVI header list.");
                    var header = ParseHeaderList(source.Slice(body + 4, (int)size - 4));
                    microsecondsPerFrame = header.MicrosecondsPerFrame;
                    declaredFrameCount = header.FrameCount;
                    streamTypes.AddRange(header.StreamTypes);
                    streamHandlers.AddRange(header.StreamHandlers);
                    streamFormats.AddRange(header.StreamFormats);
                }
                else if (listType.SequenceEqual("movi"u8))
                {
                    if (moviStart >= 0) throw new InvalidDataException("Duplicate AVI movi list.");
                    moviStart = body + 4;
                    moviEnd = (int)bodyEnd;
                }
            }

            offset = (int)bodyEnd + ((int)size & 1);
        }

        if (offset != source.Length) throw new InvalidDataException("AVI trailing chunk header is truncated.");
        if (streamTypes.Count(type => type == "vids") > 1 || streamTypes.Count(type => type == "auds") > 1)
            throw new NotSupportedException("AVI requires one video and at most one audio stream.");
        if (microsecondsPerFrame == 0 || microsecondsPerFrame > int.MaxValue || declaredFrameCount <= 0 || declaredFrameCount > MaxFrames)
            throw new InvalidDataException("AVI frame timing or count is invalid.");
        if (moviStart < 0)
            throw new InvalidDataException("AVI file has no movi list.");

        int videoStream = FindStream(streamTypes, "vids");
        if (videoStream < 0)
            throw new InvalidDataException("AVI file has no video stream.");

        var (width, height, compression, palette) = ParseVideoFormat(streamFormats[videoStream]);
        if (width <= 0 || height <= 0 || width > MaxDimension || height > MaxDimension)
            throw new InvalidDataException($"AVI video dimensions {width}x{height} are out of range.");

        int audioStream = FindStream(streamTypes, "auds");
        AviAudioFormat? audio = audioStream >= 0 ? ParseAudioFormat(streamFormats[audioStream]) : null;

        var frames = new List<AviVideoFrame>();
        var audioChunks = new List<byte[]>();
        int position = moviStart;
        int chunks = 0;
        while (position + 8 <= moviEnd)
        {
            if (++chunks > MaxFrames * 4)
                throw new InvalidDataException("AVI movi chunk count is excessive.");
            var id = source.Slice(position, 4);
            uint size = BinaryPrimitives.ReadUInt32LittleEndian(source[(position + 4)..]);
            long bodyEnd = (long)position + 8 + size;
            if (bodyEnd + (size & 1) > moviEnd)
                throw new InvalidDataException($"AVI movi chunk at {position} overruns the list.");
            if (size > MaxFrameBytes)
                throw new InvalidDataException($"AVI frame is {size} bytes, above the {MaxFrameBytes}-byte limit.");

            if (id.SequenceEqual("LIST"u8))
                throw new NotSupportedException("Nested AVI movi lists are not supported.");
            if (id[2] == (byte)'p' && id[3] == (byte)'c')
                throw new NotSupportedException("AVI palette-change chunks are not supported.");
            int body = position + 8;
            if (MatchesChunkId(id, videoStream, "dc") || MatchesChunkId(id, videoStream, "db"))
            {
                if (frames.Count >= MaxFrames)
                    throw new InvalidDataException($"AVI declares more than {MaxFrames} video frames.");
                frames.Add(new AviVideoFrame(source.Slice(body, (int)size).ToArray()));
            }
            else if (audioStream >= 0 && MatchesChunkId(id, audioStream, "wb"))
            {
                audioChunks.Add(source.Slice(body, (int)size).ToArray());
            }

            position = (int)bodyEnd + ((int)size & 1);
        }

        if (position != moviEnd) throw new InvalidDataException("AVI movi chunk header is truncated.");
        if (frames.Count != declaredFrameCount) throw new InvalidDataException("AVI frame count disagrees with its header.");
        if (frames.Count == 0)
            throw new InvalidDataException("AVI file has no video frames.");

        var video = new AviVideo(
            width,
            height,
            (int)microsecondsPerFrame,
            declaredFrameCount,
            streamHandlers[videoStream],
            compression,
            palette,
            frames,
            audio,
            audioChunks);
        // Palettized Cinepak codebooks hold palette indices, which CinepakSurface would draw as gray.
        if (video.IsCinepak && palette is not null)
            throw new NotSupportedException("AVI 8-bit palettized Cinepak is not supported.");
        return video;
    }

    private sealed record HeaderList(
        uint MicrosecondsPerFrame,
        int FrameCount,
        IReadOnlyList<string> StreamTypes,
        IReadOnlyList<string> StreamHandlers,
        IReadOnlyList<byte[]> StreamFormats);

    private static HeaderList ParseHeaderList(ReadOnlySpan<byte> body)
    {
        uint microsecondsPerFrame = 0;
        int frameCount = 0;
        var types = new List<string>();
        var handlers = new List<string>();
        var formats = new List<byte[]>();
        bool hasMainHeader = false;

        int offset = 0;
        while (offset + 8 <= body.Length)
        {
            var id = body.Slice(offset, 4);
            uint size = BinaryPrimitives.ReadUInt32LittleEndian(body[(offset + 4)..]);
            long end = (long)offset + 8 + size;
            if (end + (size & 1) > body.Length)
                throw new InvalidDataException($"AVI header chunk at {offset} overruns the header list.");
            int chunkBody = offset + 8;

            if (id.SequenceEqual("avih"u8))
            {
                if (hasMainHeader) throw new InvalidDataException("Duplicate AVI main header.");
                hasMainHeader = true;
                if (size < 40)
                    throw new InvalidDataException("AVI main header is shorter than 40 bytes.");
                microsecondsPerFrame = BinaryPrimitives.ReadUInt32LittleEndian(body[chunkBody..]);
                frameCount = (int)BinaryPrimitives.ReadUInt32LittleEndian(body[(chunkBody + 16)..]);
            }
            else if (id.SequenceEqual("LIST"u8) && size >= 4 && body.Slice(chunkBody, 4).SequenceEqual("strl"u8))
            {
                if (types.Count >= 100) throw new InvalidDataException("AVI stream count exceeds 100.");
                ParseStreamList(body.Slice(chunkBody + 4, (int)size - 4), types, handlers, formats);
            }

            offset = (int)end + ((int)size & 1);
        }

        if (offset != body.Length) throw new InvalidDataException("AVI header chunk is truncated.");
        return new HeaderList(microsecondsPerFrame, frameCount, types, handlers, formats);
    }

    private static void ParseStreamList(
        ReadOnlySpan<byte> body,
        List<string> types,
        List<string> handlers,
        List<byte[]> formats)
    {
        string type = "";
        string handler = "";
        byte[]? format = null;
        bool hasStreamHeader = false;

        int offset = 0;
        while (offset + 8 <= body.Length)
        {
            var id = body.Slice(offset, 4);
            uint size = BinaryPrimitives.ReadUInt32LittleEndian(body[(offset + 4)..]);
            long end = (long)offset + 8 + size;
            if (end + (size & 1) > body.Length)
                throw new InvalidDataException($"AVI stream chunk at {offset} overruns the stream list.");
            int chunkBody = offset + 8;

            if (id.SequenceEqual("strh"u8))
            {
                if (hasStreamHeader) throw new InvalidDataException("Duplicate AVI stream header.");
                hasStreamHeader = true;
                if (size < 8)
                    throw new InvalidDataException("AVI stream header is shorter than 8 bytes.");
                type = FourCc(body.Slice(chunkBody, 4));
                handler = FourCc(body.Slice(chunkBody + 4, 4));
            }
            else if (id.SequenceEqual("strf"u8))
            {
                if (format is not null || size > MaxFrameBytes)
                    throw new InvalidDataException("Duplicate or excessive AVI stream format.");
                format = body.Slice(chunkBody, (int)size).ToArray();
            }

            offset = (int)end + ((int)size & 1);
        }

        if (offset != body.Length) throw new InvalidDataException("AVI stream chunk is truncated.");
        types.Add(type);
        handlers.Add(handler);
        formats.Add(format ?? []);
    }

    private static int FindStream(IReadOnlyList<string> types, string type)
    {
        for (int i = 0; i < types.Count; i++)
            if (types[i] == type)
                return i;
        return -1;
    }

    private static (int Width, int Height, uint Compression, byte[]? Palette) ParseVideoFormat(byte[] format)
    {
        if (format.Length < 40)
            throw new InvalidDataException("AVI video format is shorter than the 40-byte bitmap header.");
        var span = format.AsSpan();
        if (BinaryPrimitives.ReadUInt32LittleEndian(span) != 40 || BinaryPrimitives.ReadUInt16LittleEndian(span[12..]) != 1)
            throw new NotSupportedException("AVI requires a 40-byte BITMAPINFOHEADER with one plane.");
        int width = BinaryPrimitives.ReadInt32LittleEndian(span[4..]);
        int height = BinaryPrimitives.ReadInt32LittleEndian(span[8..]);
        int bits = BinaryPrimitives.ReadUInt16LittleEndian(span[14..]);
        uint compression = BinaryPrimitives.ReadUInt32LittleEndian(span[16..]);

        if (bits is not (8 or 24)) throw new NotSupportedException("AVI video depth must be 8 or 24 bits.");
        if (compression == AviVideo.Rle8Compression && bits != 8)
            throw new InvalidDataException("RLE8 requires an 8-bit bitmap.");
        byte[]? palette = null;
        if (bits == 8)
        {
            // biClrUsed: 0 means a full 256-entry table; entries past it stay black.
            uint colorsUsed = BinaryPrimitives.ReadUInt32LittleEndian(span[32..]);
            if (colorsUsed > 256)
                throw new InvalidDataException($"AVI indexed video declares {colorsUsed} palette entries, above 256.");
            int entries = colorsUsed == 0 ? 256 : (int)colorsUsed;
            if (format.Length < 40 + entries * 4)
                throw new InvalidDataException($"AVI indexed video requires its complete {entries}-entry palette.");
            palette = new byte[256 * 3];
            for (int i = 0; i < entries; i++)
            {
                palette[i * 3] = format[40 + i * 4 + 2];      // red
                palette[i * 3 + 1] = format[40 + i * 4 + 1];  // green
                palette[i * 3 + 2] = format[40 + i * 4];      // blue
            }
        }

        return (width, height, compression, palette);
    }

    private static AviAudioFormat ParseAudioFormat(byte[] format)
    {
        if (format.Length < 16)
            throw new InvalidDataException("AVI audio format is shorter than 16 bytes.");
        var span = format.AsSpan();
        int extraLength = format.Length >= 18 ? BinaryPrimitives.ReadUInt16LittleEndian(span[16..]) : 0;
        int extraStart = 18;
        int available = format.Length > extraStart ? format.Length - extraStart : 0;
        if (extraLength > available)
            throw new InvalidDataException("AVI audio format's extension length overruns the format block.");
        byte[] extra = extraLength > 0 ? format.AsSpan(extraStart, extraLength).ToArray() : [];
        return new AviAudioFormat(
            BinaryPrimitives.ReadUInt16LittleEndian(span),
            BinaryPrimitives.ReadUInt16LittleEndian(span[2..]),
            (int)BinaryPrimitives.ReadUInt32LittleEndian(span[4..]),
            (int)BinaryPrimitives.ReadUInt32LittleEndian(span[8..]),
            BinaryPrimitives.ReadUInt16LittleEndian(span[12..]),
            BinaryPrimitives.ReadUInt16LittleEndian(span[14..]),
            extra);
    }

    private static bool MatchesChunkId(ReadOnlySpan<byte> id, int streamIndex, string suffix) =>
        id.Length == 4
        && streamIndex is >= 0 and <= 99
        && id[0] == (byte)('0' + streamIndex / 10)
        && id[1] == (byte)('0' + streamIndex % 10)
        && id[2] == (byte)suffix[0]
        && id[3] == (byte)suffix[1];

    private static string FourCc(ReadOnlySpan<byte> value)
    {
        int length = value.Length;
        while (length > 0 && value[length - 1] == 0)
            length--;
        return Encoding.ASCII.GetString(value[..length]).TrimEnd();
    }
}
