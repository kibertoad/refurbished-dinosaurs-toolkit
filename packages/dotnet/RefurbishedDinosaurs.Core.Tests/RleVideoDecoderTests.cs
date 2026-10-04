using RefurbishedDinosaurs.Media.Avi;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class RleVideoDecoderTests
{
    [Fact]
    public void DecodesRunsLinesAndEndOfBitmap()
    {
        var surface = new RleVideoSurface(3, 2);
        surface.DecodeFrame([3, 5, 0, 0, 2, 7, 0, 1]);

        Assert.Equal(new byte[] { 5, 5, 5, 7, 7, 0 }, surface.Indices);
    }

    [Fact]
    public void DecodesLiteralRunWithPadding()
    {
        var surface = new RleVideoSurface(5, 1);
        // literal of three, one pad byte, then a two-pixel run.
        surface.DecodeFrame([0, 3, 1, 2, 3, 0, 2, 9, 0, 0, 0, 1]);

        Assert.Equal(new byte[] { 1, 2, 3, 9, 9 }, surface.Indices);
    }

    [Fact]
    public void DecodesDelta()
    {
        var surface = new RleVideoSurface(4, 3);
        surface.DecodeFrame([1, 9, 0, 2, 1, 1, 1, 8, 0, 1]);

        Assert.Equal(9, surface.Indices[0]);
        Assert.Equal(8, surface.Indices[6]);
    }

    [Fact]
    public void AccumulatesFramesCumulatively()
    {
        var surface = new RleVideoSurface(2, 1);
        surface.DecodeFrame([1, 4, 0, 1]);
        surface.DecodeFrame([0, 2, 1, 0, 1, 7, 0, 1]);

        Assert.Equal(new byte[] { 4, 7 }, surface.Indices);
    }

    [Fact]
    public void LeavesTheSurfaceUnchangedForAnEmptyFrame()
    {
        var surface = new RleVideoSurface(2, 1);
        surface.DecodeFrame([1, 4, 0, 1]);
        // An AVI dropped frame is an empty chunk.
        surface.DecodeFrame([]);

        Assert.Equal(new byte[] { 4, 0 }, surface.Indices);
    }

    [Fact]
    public void FlipsToDisplayOrderInRgba()
    {
        var surface = new RleVideoSurface(2, 2);
        // Bottom row first, matching the stream order.
        surface.DecodeFrame([1, 1, 1, 2, 0, 0, 1, 3, 1, 4, 0, 1]);
        var palette = BuildPalette();

        var rgba = surface.ToRgba(palette);

        // Display row 0 is the surface's top row (indices 3 and 4).
        Assert.Equal(palette[3 * 3], rgba[0]);
        Assert.Equal(palette[4 * 3], rgba[4]);
        Assert.Equal(255, rgba[3]);
        Assert.Equal(palette[1 * 3], rgba[8]);
        Assert.Equal(palette[2 * 3], rgba[12]);
    }

    [Fact]
    public void RejectsTruncatedDelta()
    {
        var surface = new RleVideoSurface(2, 2);
        Assert.Throws<InvalidDataException>(() => surface.DecodeFrame([0, 2, 1]));
    }

    [Fact]
    public void RejectsTruncatedLiteral()
    {
        var surface = new RleVideoSurface(2, 2);
        Assert.Throws<InvalidDataException>(() => surface.DecodeFrame([0, 5, 1, 2]));
    }

    [Fact]
    public void RejectsOutOfRangeDimensions()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new RleVideoSurface(0, 4));
        Assert.Throws<ArgumentOutOfRangeException>(() => new RleVideoSurface(RleVideoSurface.MaxDimension + 1, 4));
    }

    [Fact]
    public void RejectsShortPalette()
    {
        var surface = new RleVideoSurface(1, 1);
        Assert.Throws<InvalidDataException>(() => surface.ToRgba(new byte[3]));
    }

    [Theory]
    [InlineData(new byte[] { 3, 1, 0, 1 })]
    [InlineData(new byte[] { 0, 2, 3, 0, 0, 1 })]
    [InlineData(new byte[] { 1, 1 })]
    [InlineData(new byte[] { 0, 3, 1, 2, 3 })]
    [InlineData(new byte[] { 1 })]
    public void RejectsOutOfBoundsRunsAndMissingTermination(byte[] data)
    {
        Assert.Throws<InvalidDataException>(() => new RleVideoSurface(2, 1).DecodeFrame(data));
    }

    private static byte[] BuildPalette()
    {
        var palette = new byte[256 * 3];
        for (int i = 0; i < 256; i++)
        {
            palette[i * 3] = (byte)i;
            palette[i * 3 + 1] = (byte)(i + 1);
            palette[i * 3 + 2] = (byte)(i + 2);
        }
        return palette;
    }
}
