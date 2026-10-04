using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class BmpDecoderTests
{
    [Fact]
    public void Decodes24BitSinglePixel()
    {
        var image = BmpDecoder.Decode(Bmp(1, 1, 24, Rows24(1, 1, (x, y) => (10, 20, 30))));
        Assert.Equal((1, 1), (image.Width, image.Height));
        Assert.Equal([10, 20, 30, 255], image.Rgba);
    }

    [Fact]
    public void Decodes24BitRowsWithPaddingTopToBottom()
    {
        // 3 pixels of 3 bytes pad each row to 12 bytes; the file stores the bottom row first.
        var pixels = Rows24(3, 2, Colour);
        Assert.Equal(24, pixels.Length);
        var image = BmpDecoder.Decode(Bmp(3, 2, 24, pixels));
        Assert.Equal(Expected(3, 2, Colour), image.Rgba);
    }

    [Fact]
    public void Decodes24BitOddWidth()
    {
        var image = BmpDecoder.Decode(Bmp(5, 1, 24, Rows24(5, 1, Colour)));
        Assert.Equal(Expected(5, 1, Colour), image.Rgba);
    }

    [Fact]
    public void DecodesTopDown24Bit()
    {
        var image = BmpDecoder.Decode(Bmp(3, -2, 24, Rows24(3, 2, Colour, topDown: true)));
        Assert.Equal((3, 2), (image.Width, image.Height));
        Assert.Equal(Expected(3, 2, Colour), image.Rgba);
    }

    [Fact]
    public void Decodes32BitAndIgnoresTheUnusedByte()
    {
        // Bottom row first, each pixel stored as blue, green, red, unused.
        byte[] pixels = [3, 2, 1, 0, 6, 5, 4, 99, 9, 8, 7, 0, 12, 11, 10, 0];
        var image = BmpDecoder.Decode(Bmp(2, 2, 32, pixels));
        Assert.Equal([7, 8, 9, 255, 10, 11, 12, 255, 1, 2, 3, 255, 4, 5, 6, 255], image.Rgba);
    }

    [Fact]
    public void Decodes8BitBiRgbThroughItsPalette()
    {
        // Two palette entries; 3 indices pad to 4 bytes per row.
        byte[] palette = [30, 20, 10, 0, 60, 50, 40, 0];
        byte[] pixels = [1, 0, 1, 0, 0, 0, 1, 0];
        var image = BmpDecoder.Decode(Bmp(3, 2, 8, pixels, palette: palette));
        Assert.Equal(
            [10, 20, 30, 255, 10, 20, 30, 255, 40, 50, 60, 255, 40, 50, 60, 255, 10, 20, 30, 255, 40, 50, 60, 255],
            image.Rgba);
    }

    [Fact]
    public void Decodes8BitRle8BottomUp()
    {
        byte[] palette = [30, 20, 10, 0, 60, 50, 40, 0];
        // Bottom row: a run of two index-1 pixels; end of line; top row: one index-0 and one index-1 pixel; end of bitmap.
        byte[] encoded = [2, 1, 0, 0, 1, 0, 1, 1, 0, 1];
        var image = BmpDecoder.Decode(Bmp(2, 2, 8, encoded, compression: 1, palette: palette));
        Assert.Equal([10, 20, 30, 255, 40, 50, 60, 255, 40, 50, 60, 255, 40, 50, 60, 255], image.Rgba);
    }

    [Theory]
    [InlineData(108)]
    [InlineData(124)]
    public void ReadsV4AndV5Headers(int headerSize)
    {
        var image = BmpDecoder.Decode(Bmp(3, 2, 24, Rows24(3, 2, Colour), headerSize: headerSize));
        Assert.Equal(Expected(3, 2, Colour), image.Rgba);
    }

    [Fact]
    public void AcceptsAWrongDeclaredSizeOnlyWhenAsked()
    {
        var file = Bmp(1, 1, 24, Rows24(1, 1, (x, y) => (10, 20, 30)));
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(2), 0);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(file));
        Assert.Equal([10, 20, 30, 255], BmpDecoder.Decode(file, requireDeclaredFileSize: false).Rgba);
    }

    [Fact]
    public void RejectsTruncatedPixelData()
    {
        var file = Bmp(3, 2, 24, Rows24(3, 2, Colour));
        var truncated = file[..^1];
        BinaryPrimitives.WriteUInt32LittleEndian(truncated.AsSpan(2), (uint)truncated.Length);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(truncated));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(file[..^1], requireDeclaredFileSize: false));
    }

    [Fact]
    public void RejectsDeclaredSizeThatDiffersFromLength()
    {
        var file = Bmp(1, 1, 24, Rows24(1, 1, Colour));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode([.. file, 0]));
    }

    [Theory]
    [InlineData(0, 1)]
    [InlineData(1, 0)]
    [InlineData(-1, 1)]
    [InlineData(1, int.MinValue)]
    public void RejectsInvalidDimensions(int width, int height)
    {
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(width, height, 24, new byte[4])));
    }

    [Theory]
    [InlineData(24, 1)] // BI_RLE8 at 24 bits.
    [InlineData(24, 2)] // BI_RLE4.
    [InlineData(32, 3)] // BI_BITFIELDS.
    [InlineData(16, 0)]
    [InlineData(4, 0)]
    [InlineData(1, 0)]
    public void RejectsUnsupportedVariants(int bitsPerPixel, int compression)
    {
        Assert.Throws<InvalidDataException>(() =>
            BmpDecoder.Decode(Bmp(1, 1, bitsPerPixel, new byte[4], compression: compression, palette: new byte[1024])));
    }

    [Fact]
    public void RejectsOtherHeadersAndContainers()
    {
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(1, 1, 24, new byte[4], headerSize: 12)));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(1, 1, 24, new byte[4], headerSize: 64)));
        var notBmp = Bmp(1, 1, 24, new byte[4]);
        notBmp[0] = (byte)'X';
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(notBmp));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(new byte[10]));
    }

    [Fact]
    public void RejectsTopDownRle8()
    {
        Assert.Throws<InvalidDataException>(() =>
            BmpDecoder.Decode(Bmp(1, -1, 8, [0, 1], compression: 1, palette: new byte[8])));
    }

    [Fact]
    public void RejectsBadPalettes()
    {
        // Index 2 of a two-colour palette.
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(1, 1, 8, [2, 0, 0, 0], palette: new byte[8])));
        // More than 256 colours declared.
        var oversized = Bmp(1, 1, 8, [0, 0, 0, 0], palette: new byte[8]);
        BinaryPrimitives.WriteUInt32LittleEndian(oversized.AsSpan(46), 257);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(oversized));
        // A declared palette that runs into the pixel data.
        var overlapping = Bmp(1, 1, 8, [0, 0, 0, 0], palette: new byte[8]);
        BinaryPrimitives.WriteUInt32LittleEndian(overlapping.AsSpan(46), 3);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(overlapping));
    }

    [Fact]
    public void RejectsPixelOffsetPastTheEnd()
    {
        var file = Bmp(1, 1, 24, new byte[4]);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(10), (uint)file.Length + 1);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(file));
    }

    [Fact]
    public void RejectsOversizedDimensionsWithoutAllocating()
    {
        var huge = Bmp(int.MaxValue, int.MaxValue, 24, new byte[4]);
        var wide = Bmp(int.MaxValue, 1, 32, new byte[4]);
        var before = GC.GetAllocatedBytesForCurrentThread();
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(huge));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(huge, maximumPixels: int.MaxValue));
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(wide, maximumPixels: int.MaxValue));
        Assert.True(GC.GetAllocatedBytesForCurrentThread() - before < 1024 * 1024);
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(3, 2, 24, Rows24(3, 2, Colour)), maximumPixels: 5));
        Assert.Throws<ArgumentOutOfRangeException>(() => BmpDecoder.Decode(Bmp(1, 1, 24, new byte[4]), maximumPixels: -1));

        // The default is ImageLimits.DefaultMaximumPixels: 4097x4096 is past it, 4096x4096 fails later.
        var over = Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(4097, 4096, 24, new byte[4])));
        Assert.Contains("configured limit", over.Message);
        var at = Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(Bmp(4096, 4096, 24, new byte[4])));
        Assert.DoesNotContain("configured limit", at.Message);
    }

    [Fact]
    public void RejectsTruncatedFilesWithinTheLimitBeforeAllocatingTheImage()
    {
        // 4096x4096 is within the default limit; the RGBA buffer would be 64 MiB.
        var truncated = Bmp(4096, 4096, 24, new byte[4]);
        var unterminated = Bmp(4096, 4096, 8, [0, 0], compression: 1, palette: new byte[8]);
        var before = GC.GetAllocatedBytesForCurrentThread();
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(truncated));
        Assert.True(GC.GetAllocatedBytesForCurrentThread() - before < 1024 * 1024);
        before = GC.GetAllocatedBytesForCurrentThread();
        Assert.Throws<InvalidDataException>(() => BmpDecoder.Decode(unterminated));
        // Only the one-byte-per-pixel index buffer of the RLE8 stream is allocated.
        Assert.True(GC.GetAllocatedBytesForCurrentThread() - before < 4096L * 4096 * 2);
    }

    private static (byte Red, byte Green, byte Blue) Colour(int x, int y) =>
        ((byte)(10 * x + 1), (byte)(50 + 10 * y), (byte)(200 - x - y));

    private static byte[] Expected(int width, int height, Func<int, int, (byte, byte, byte)> colour)
    {
        var rgba = new byte[width * height * 4];
        for (var y = 0; y < height; y++)
            for (var x = 0; x < width; x++)
            {
                var (red, green, blue) = colour(x, y);
                rgba[(y * width + x) * 4] = red;
                rgba[(y * width + x) * 4 + 1] = green;
                rgba[(y * width + x) * 4 + 2] = blue;
                rgba[(y * width + x) * 4 + 3] = 255;
            }
        return rgba;
    }

    /// <summary>24-bit BGR rows padded to 4 bytes, in file order, for an image whose display row y is given by <paramref name="colour"/>.</summary>
    private static byte[] Rows24(int width, int height, Func<int, int, (byte, byte, byte)> colour, bool topDown = false)
    {
        var stride = (width * 3 + 3) & ~3;
        var pixels = new byte[stride * height];
        for (var y = 0; y < height; y++)
        {
            var fileRow = topDown ? y : height - 1 - y;
            for (var x = 0; x < width; x++)
            {
                var (red, green, blue) = colour(x, y);
                pixels[fileRow * stride + x * 3] = blue;
                pixels[fileRow * stride + x * 3 + 1] = green;
                pixels[fileRow * stride + x * 3 + 2] = red;
            }
        }
        return pixels;
    }

    private static byte[] Bmp(
        int width, int height, int bitsPerPixel, byte[] pixels,
        int compression = 0, byte[]? palette = null, int headerSize = 40)
    {
        palette ??= [];
        var infoSize = Math.Max(headerSize, 40);
        var pixelOffset = 14 + infoSize + palette.Length;
        var file = new byte[pixelOffset + pixels.Length];
        file[0] = (byte)'B';
        file[1] = (byte)'M';
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(2), (uint)file.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(10), (uint)pixelOffset);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(14), (uint)headerSize);
        BinaryPrimitives.WriteInt32LittleEndian(file.AsSpan(18), width);
        BinaryPrimitives.WriteInt32LittleEndian(file.AsSpan(22), height);
        BinaryPrimitives.WriteUInt16LittleEndian(file.AsSpan(26), 1);
        BinaryPrimitives.WriteUInt16LittleEndian(file.AsSpan(28), (ushort)bitsPerPixel);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(30), (uint)compression);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(34), (uint)pixels.Length);
        BinaryPrimitives.WriteUInt32LittleEndian(file.AsSpan(46), (uint)(palette.Length / 4));
        palette.CopyTo(file.AsSpan(14 + infoSize));
        pixels.CopyTo(file.AsSpan(pixelOffset));
        return file;
    }
}
