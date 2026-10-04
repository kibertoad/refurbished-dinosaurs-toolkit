using RefurbishedDinosaurs.Media.Avi;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class CinepakVideoDecoderTests
{
    [Fact]
    public void DecodesAKeyframeFromItsCodebooks()
    {
        var surface = new CinepakSurface(4, 4);
        surface.DecodeFrame(BuildFrame(includeCodebook: true, [0, 1, 0, 1]));

        var rgba = surface.ToRgba();
        AssertPixel(rgba, 4, 0, 0, 10);
        AssertPixel(rgba, 4, 1, 0, 20);
        AssertPixel(rgba, 4, 2, 0, 50);
        AssertPixel(rgba, 4, 3, 0, 60);
        AssertPixel(rgba, 4, 0, 1, 30);
        AssertPixel(rgba, 4, 1, 1, 40);
        AssertPixel(rgba, 4, 2, 1, 70);
        AssertPixel(rgba, 4, 3, 1, 80);
        AssertPixel(rgba, 4, 0, 3, 30);
        AssertPixel(rgba, 4, 3, 3, 80);
    }

    [Fact]
    public void ReusesTheCodebookOnAnInterFrame()
    {
        var surface = new CinepakSurface(4, 4);
        surface.DecodeFrame(BuildFrame(includeCodebook: true, [0, 1, 0, 1]));
        // A frame whose strip carries no codebook must keep the previous one.
        surface.DecodeFrame(BuildFrame(includeCodebook: false, [0, 1, 0, 1]));

        var rgba = surface.ToRgba();
        AssertPixel(rgba, 4, 0, 0, 10);
        AssertPixel(rgba, 4, 3, 3, 80);
    }

    [Fact]
    public void AppliesChromaToTheCodebook()
    {
        // A 6-byte entry with U=0 and V=0 stays gray; a nonzero V shifts red/blue.
        var surface = new CinepakSurface(4, 4);
        surface.DecodeFrame(BuildFrame(includeCodebook: true, [0, 0, 0, 0], chroma: (0, 10)));

        var rgba = surface.ToRgba();
        // Entry 0 luma 10, V=10 -> red = 10 + 20 = 30, green = 10 - 0 - 10 = 0, blue = 10.
        AssertPixel(rgba, 4, 0, 0, 30, expectedGreen: 0, expectedBlue: 10);
    }

    [Fact]
    public void LeavesTheSurfaceUnchangedForEmptyAndStriplessFrames()
    {
        var surface = new CinepakSurface(4, 4);
        surface.DecodeFrame(BuildFrame(includeCodebook: true, [0, 1, 0, 1]));
        var before = surface.ToRgba();

        // An AVI dropped frame is an empty chunk; a header-only frame declares no strips.
        surface.DecodeFrame([]);
        surface.DecodeFrame([1, 0, 0, 10, 0, 4, 0, 4, 0, 0]);

        Assert.Equal(before, surface.ToRgba());
    }

    [Fact]
    public void DecodesAStripWhoseRightEdgeIsTheUnpaddedWidth()
    {
        var surface = new CinepakSurface(3, 4);
        var frame = BuildFrame(includeCodebook: true, [0, 1, 0, 1]);
        frame[5] = 3;  // frame width
        frame[21] = 3; // strip right edge
        surface.DecodeFrame(frame);

        var rgba = surface.ToRgba();
        AssertPixel(rgba, 3, 0, 0, 10);
        AssertPixel(rgba, 3, 2, 0, 50);
        AssertPixel(rgba, 3, 2, 1, 70);
    }

    [Fact]
    public void RejectsFrameWithoutHeader()
    {
        var surface = new CinepakSurface(4, 4);
        Assert.Throws<InvalidDataException>(() => surface.DecodeFrame([1, 2, 3]));
    }

    [Fact]
    public void RejectsStripOutsideTheSurface()
    {
        var surface = new CinepakSurface(4, 4);
        var frame = BuildFrame(includeCodebook: true, [0, 1, 0, 1]);
        // Grow the strip's right edge past the surface width (bytes 10-11 of the strip header).
        frame[20] = 0;
        frame[21] = 8;
        Assert.Throws<InvalidDataException>(() => surface.DecodeFrame(frame));
    }

    [Fact]
    public void RejectsOutOfRangeDimensions()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new CinepakSurface(0, 4));
        Assert.Throws<ArgumentOutOfRangeException>(() => new CinepakSurface(CinepakSurface.MaxDimension + 1, 4));
    }

    [Fact]
    public void RejectsClippedStripsChunksAndVectors()
    {
        foreach (int offset in new[] { 13, 25 })
        {
            var frame = BuildFrame(true, [0, 1, 0, 1]);
            frame[offset] = 255;
            Assert.Throws<InvalidDataException>(() => new CinepakSurface(4, 4).DecodeFrame(frame));
        }
        var shortVectors = BuildFrame(true, [0]);
        Assert.Throws<InvalidDataException>(() => new CinepakSurface(4, 4).DecodeFrame(shortVectors));
        var wrongCount = BuildFrame(true, [0, 1, 0, 1]);
        wrongCount[9] = CinepakSurface.MaxStrips + 1;
        Assert.Throws<InvalidDataException>(() => new CinepakSurface(4, 4).DecodeFrame(wrongCount));
        var wrongLength = BuildFrame(true, [0, 1, 0, 1]);
        wrongLength[3] ^= 1;
        Assert.Throws<InvalidDataException>(() => new CinepakSurface(4, 4).DecodeFrame(wrongLength));
    }

    private static void AssertPixel(byte[] rgba, int width, int x, int y, byte red, byte? expectedGreen = null, byte? expectedBlue = null)
    {
        int offset = (y * width + x) * 4;
        Assert.Equal(red, rgba[offset]);
        Assert.Equal(expectedGreen ?? red, rgba[offset + 1]);
        Assert.Equal(expectedBlue ?? red, rgba[offset + 2]);
        Assert.Equal(255, rgba[offset + 3]);
    }

    private static byte[] BuildFrame(bool includeCodebook, byte[] indices, (sbyte U, sbyte V) chroma = default)
    {
        var body = new List<byte>();
        if (includeCodebook)
            body.AddRange(Chunk(0x20, BuildCodebook(chroma)));
        body.AddRange(Chunk(0x30, BuildVectors(indices)));

        var strip = new List<byte> { 0x10 };
        strip.AddRange(U24(12 + body.Count));
        strip.AddRange(U16(0));
        strip.AddRange(U16(0));
        strip.AddRange(U16(4));
        strip.AddRange(U16(4));
        strip.AddRange(body);

        var frame = new List<byte> { 1 };
        frame.AddRange(U24(10 + strip.Count));
        frame.AddRange(U16(4));
        frame.AddRange(U16(4));
        frame.AddRange(U16(1));
        frame.AddRange(strip);
        return frame.ToArray();
    }

    private static byte[] BuildCodebook((sbyte U, sbyte V) chroma)
    {
        var codebook = new byte[256 * 6];
        WriteEntry(codebook, 0, [10, 20, 30, 40], chroma);
        WriteEntry(codebook, 1, [50, 60, 70, 80], chroma);
        return codebook;
    }

    private static void WriteEntry(byte[] codebook, int index, byte[] luma, (sbyte U, sbyte V) chroma)
    {
        int offset = index * 6;
        for (int i = 0; i < 4; i++)
            codebook[offset + i] = luma[i];
        codebook[offset + 4] = (byte)chroma.U;
        codebook[offset + 5] = (byte)chroma.V;
    }

    private static byte[] BuildVectors(byte[] indices)
    {
        var body = new List<byte> { 0xFF, 0xFF, 0xFF, 0xFF };
        body.AddRange(indices);
        return body.ToArray();
    }

    private static byte[] Chunk(int id, byte[] body)
    {
        var output = new List<byte> { (byte)id };
        output.AddRange(U24(body.Length + 4));
        output.AddRange(body);
        return output.ToArray();
    }

    private static byte[] U16(int value) => [(byte)(value >> 8), (byte)value];

    private static byte[] U24(int value) => [(byte)(value >> 16), (byte)(value >> 8), (byte)value];
}
