using System.Buffers.Binary;
using RefurbishedDinosaurs.Media.Avi;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class AviReaderTests
{
    [Fact]
    public void ReadsVideoGeometryCodecPaletteAndFrames()
    {
        var palette = BuildPalette();
        var avi = AviReader.Decode(BuildAvi(
            width: 4,
            height: 4,
            frames: [new byte[] { 1, 2, 3 }, new byte[] { 4, 5 }],
            audioChunks: [new byte[] { 9, 9 }],
            palette: palette));

        Assert.Equal(4, avi.Width);
        Assert.Equal(4, avi.Height);
        Assert.Equal(66666, avi.MicrosecondsPerFrame);
        Assert.Equal(2, avi.DeclaredFrameCount);
        Assert.Equal("RLE", avi.Codec);
        Assert.Equal(1u, avi.Compression);
        Assert.True(avi.IsRle8);
        Assert.False(avi.IsCinepak);
        Assert.Equal(palette, avi.Palette);
        Assert.Equal(2, avi.Frames.Count);
        Assert.Equal(new byte[] { 1, 2, 3 }, avi.Frames[0].Data);
        Assert.Single(avi.AudioChunks);
        Assert.Equal(new byte[] { 9, 9 }, avi.AudioChunks[0]);
        Assert.NotNull(avi.Audio);
        Assert.Equal(1, avi.Audio!.FormatTag);
        Assert.Equal(22050, avi.Audio.SamplesPerSecond);
        Assert.Equal(15.00015, avi.FramesPerSecond, 4);
    }

    [Fact]
    public void ReportsCinepakByFourCc()
    {
        var avi = AviReader.Decode(BuildAvi(
            width: 4,
            height: 4,
            frames: [new byte[] { 1 }],
            audioChunks: [],
            palette: null,
            codec: "cvid",
            compression: 0x64697663,
            bits: 24));

        Assert.True(avi.IsCinepak);
        Assert.False(avi.IsRle8);
        Assert.Null(avi.Palette);
    }

    [Fact]
    public void RejectsNonAviSource()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]));
    }

    [Fact]
    public void RejectsChunkThatOverrunsTheFile()
    {
        var avi = BuildAvi(4, 4, [new byte[] { 1 }], [], BuildPalette());
        // Corrupt the first top-level chunk's declared size to run past the file.
        BinaryPrimitives.WriteUInt32LittleEndian(avi.AsSpan(16, 4), 0xFFFFFF);
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(avi));
    }

    [Fact]
    public void RejectsFileWithoutVideoStream()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(BuildAvi(
            width: 4, height: 4, frames: [], audioChunks: [], palette: BuildPalette(), includeVideo: false)));
    }

    [Fact]
    public void RejectsFileWithoutMoviList()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(BuildAvi(
            width: 4, height: 4, frames: [], audioChunks: [], palette: BuildPalette(), includeMovi: false)));
    }

    [Fact]
    public void RejectsFileWithoutFrames()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(BuildAvi(
            width: 4, height: 4, frames: [], audioChunks: [], palette: BuildPalette())));
    }

    [Fact]
    public void RejectsOutOfRangeDimensions()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(BuildAvi(
            width: AviReader.MaxDimension + 1, height: 4, frames: [new byte[] { 1 }], audioChunks: [], palette: BuildPalette())));
    }

    [Fact]
    public void RejectsRiffMismatchTrailingHeaderAndZeroTiming()
    {
        var avi = BuildAvi(4, 4, [new byte[] { 1 }], [], BuildPalette());
        var badLength = avi.ToArray();
        badLength[4] ^= 1;
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(badLength));
        var trailing = avi.Concat(new byte[] { 0 }).ToArray();
        BinaryPrimitives.WriteUInt32LittleEndian(trailing.AsSpan(4), (uint)(trailing.Length - 8));
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(trailing));
        int main = avi.AsSpan().IndexOf("avih"u8) + 8;
        BinaryPrimitives.WriteUInt32LittleEndian(avi.AsSpan(main), 0);
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(avi));
    }

    [Fact]
    public void RejectsExcessiveAndInconsistentCounts()
    {
        foreach (uint count in new uint[] { 0, 2, AviReader.MaxFrames + 1 })
        {
            var avi = BuildAvi(4, 4, [new byte[] { 1 }], [], BuildPalette());
            int main = avi.AsSpan().IndexOf("avih"u8) + 8;
            BinaryPrimitives.WriteUInt32LittleEndian(avi.AsSpan(main + 16), count);
            Assert.Throws<InvalidDataException>(() => AviReader.Decode(avi));
        }
    }

    [Fact]
    public void RejectsMissingPaletteAndUnsupportedMoviLayouts()
    {
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(BuildAvi(4, 4, [new byte[] { 1 }], [], null)));
        foreach (byte[] id in new[] { "LIST"u8.ToArray(), "00pc"u8.ToArray() })
        {
            var avi = BuildAvi(4, 4, [new byte[] { 1 }], [], BuildPalette());
            int frame = avi.AsSpan().IndexOf("00dc"u8);
            id.CopyTo(avi, frame);
            Assert.Throws<NotSupportedException>(() => AviReader.Decode(avi));
        }
    }

    [Fact]
    public void RejectsMissingOddChunkPadding()
    {
        var avi = BuildAvi(4, 4, [new byte[] { 1 }], [], BuildPalette());
        // The one-byte final video chunk requires a pad inside movi and RIFF.
        avi = avi[..^1];
        BinaryPrimitives.WriteUInt32LittleEndian(avi.AsSpan(4), (uint)(avi.Length - 8));
        int movi = avi.AsSpan().IndexOf("movi"u8);
        int size = movi - 4;
        BinaryPrimitives.WriteUInt32LittleEndian(avi.AsSpan(size), BinaryPrimitives.ReadUInt32LittleEndian(avi.AsSpan(size)) - 1);
        Assert.Throws<InvalidDataException>(() => AviReader.Decode(avi));
    }

    private static byte[] BuildAvi(
        int width,
        int height,
        IReadOnlyList<byte[]> frames,
        IReadOnlyList<byte[]> audioChunks,
        byte[]? palette,
        string codec = "RLE ",
        uint compression = 1,
        int bits = 8,
        bool includeVideo = true,
        bool includeAudio = true,
        bool includeMovi = true)
    {
        var header = new List<byte>();
        header.AddRange(Chunk("avih", BuildAvih(width, height, frames.Count)));
        if (includeVideo)
            header.AddRange(List("strl", VideoStream(codec, width, height, bits, compression, palette)));
        if (includeAudio)
            header.AddRange(List("strl", AudioStream()));

        var hdrl = List("hdrl", header.ToArray());

        var moviBody = new List<byte>();
        for (int i = 0; i < frames.Count; i++)
        {
            moviBody.AddRange(Chunk("00dc", frames[i]));
            if (i < audioChunks.Count)
                moviBody.AddRange(Chunk("01wb", audioChunks[i]));
        }

        var riff = new List<byte>();
        riff.AddRange(hdrl);
        if (includeMovi)
            riff.AddRange(List("movi", moviBody.ToArray()));

        return Wrap("AVI ", riff.ToArray());
    }

    private static byte[] BuildAvih(int width, int height, int frameCount)
    {
        var body = new byte[56];
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(0), 66666);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(16), (uint)frameCount);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(24), 2);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(32), (uint)width);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(36), (uint)height);
        return body;
    }

    private static byte[] VideoStream(string codec, int width, int height, int bits, uint compression, byte[]? palette)
    {
        var stream = new List<byte>();
        stream.AddRange(Chunk("strh", BuildStrh("vids", codec)));
        stream.AddRange(Chunk("strf", BuildBitmapInfo(width, height, bits, compression, palette)));
        return stream.ToArray();
    }

    private static byte[] AudioStream()
    {
        var stream = new List<byte>();
        stream.AddRange(Chunk("strh", BuildStrh("auds", "")));
        var format = new byte[16];
        BinaryPrimitives.WriteUInt16LittleEndian(format.AsSpan(0), 1);
        BinaryPrimitives.WriteUInt16LittleEndian(format.AsSpan(2), 1);
        BinaryPrimitives.WriteUInt32LittleEndian(format.AsSpan(4), 22050);
        BinaryPrimitives.WriteUInt32LittleEndian(format.AsSpan(8), 44100);
        BinaryPrimitives.WriteUInt16LittleEndian(format.AsSpan(12), 2);
        BinaryPrimitives.WriteUInt16LittleEndian(format.AsSpan(14), 16);
        stream.AddRange(Chunk("strf", format));
        return stream.ToArray();
    }

    private static byte[] BuildStrh(string type, string handler)
    {
        var body = new byte[56];
        WriteFourCc(body.AsSpan(0), type);
        WriteFourCc(body.AsSpan(4), handler);
        return body;
    }

    private static byte[] BuildBitmapInfo(int width, int height, int bits, uint compression, byte[]? palette)
    {
        int length = 40 + (palette is not null ? 256 * 4 : 0);
        var body = new byte[length];
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(0), 40);
        BinaryPrimitives.WriteInt32LittleEndian(body.AsSpan(4), width);
        BinaryPrimitives.WriteInt32LittleEndian(body.AsSpan(8), height);
        BinaryPrimitives.WriteUInt16LittleEndian(body.AsSpan(12), 1);
        BinaryPrimitives.WriteUInt16LittleEndian(body.AsSpan(14), (ushort)bits);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(16), compression);
        BinaryPrimitives.WriteUInt32LittleEndian(body.AsSpan(20), (uint)(width * height));
        if (palette is not null)
        {
            for (int i = 0; i < 256; i++)
            {
                body[40 + i * 4] = palette[i * 3 + 2];
                body[40 + i * 4 + 1] = palette[i * 3 + 1];
                body[40 + i * 4 + 2] = palette[i * 3];
            }
        }
        return body;
    }

    private static byte[] BuildPalette()
    {
        var palette = new byte[256 * 3];
        for (int i = 0; i < palette.Length; i++)
            palette[i] = (byte)(i % 251);
        return palette;
    }

    private static void WriteFourCc(Span<byte> destination, string value)
    {
        for (int i = 0; i < 4; i++)
            destination[i] = i < value.Length ? (byte)value[i] : (byte)0x20;
    }

    private static byte[] Chunk(string id, byte[] body)
    {
        var output = new List<byte>();
        WriteFourCc(output, id, 4);
        WriteUInt32(output, (uint)body.Length);
        output.AddRange(body);
        if ((body.Length & 1) != 0)
            output.Add(0);
        return output.ToArray();
    }

    private static byte[] List(string type, byte[] body)
    {
        var output = new List<byte>();
        WriteFourCc(output, "LIST", 4);
        WriteUInt32(output, (uint)(body.Length + 4));
        WriteFourCc(output, type, 4);
        output.AddRange(body);
        if (((body.Length + 4) & 1) != 0)
            output.Add(0);
        return output.ToArray();
    }

    private static byte[] Wrap(string type, byte[] body)
    {
        var output = new List<byte>();
        WriteFourCc(output, "RIFF", 4);
        WriteUInt32(output, (uint)(body.Length + 4));
        WriteFourCc(output, type, 4);
        output.AddRange(body);
        return output.ToArray();
    }

    private static void WriteFourCc(List<byte> output, string value, int length)
    {
        for (int i = 0; i < length; i++)
            output.Add(i < value.Length ? (byte)value[i] : (byte)0x20);
    }

    private static void WriteUInt32(List<byte> output, uint value)
    {
        output.Add((byte)value);
        output.Add((byte)(value >> 8));
        output.Add((byte)(value >> 16));
        output.Add((byte)(value >> 24));
    }
}
