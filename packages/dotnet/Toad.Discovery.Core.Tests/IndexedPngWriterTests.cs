using System.Buffers.Binary;
using System.IO.Compression;
using Toad.Discovery.Core.Imaging;
using Xunit;

namespace Toad.Discovery.Core.Tests;

public sealed class IndexedPngWriterTests
{
    [Fact]
    public void WritesDeterministicIndexedImage()
    {
        var rgb = new byte[IndexedPalette.ByteSize];
        for (var index = 0; index < IndexedPalette.ColorCount; index++)
        {
            rgb[index * 3] = (byte)index;
            rgb[index * 3 + 1] = (byte)(255 - index);
            rgb[index * 3 + 2] = 17;
        }
        var palette = new IndexedPalette(rgb);
        using var first = new MemoryStream();
        using var second = new MemoryStream();

        IndexedPngWriter.Write(first, 2, 2, [1, 2, 3, 4], palette);
        IndexedPngWriter.Write(second, 2, 2, [1, 2, 3, 4], palette);

        Assert.Equal(first.ToArray(), second.ToArray());
        var png = first.ToArray();
        Assert.Equal(new byte[] { 137, 80, 78, 71, 13, 10, 26, 10 }, png[..8]);
        Assert.Equal(rgb, png.AsSpan(8 + 12 + 13 + 8, IndexedPalette.ByteSize).ToArray());
        var idatOffset = 8 + 12 + 13 + 12 + IndexedPalette.ByteSize;
        Assert.Equal("IDAT", System.Text.Encoding.ASCII.GetString(png, idatOffset + 4, 4));
        var compressedLength = checked((int)BinaryPrimitives.ReadUInt32BigEndian(png.AsSpan(idatOffset)));
        using var compressed = new MemoryStream(png, idatOffset + 8, compressedLength);
        using var zlib = new ZLibStream(compressed, CompressionMode.Decompress);
        using var scanlines = new MemoryStream();
        zlib.CopyTo(scanlines);
        Assert.Equal(new byte[] { 0, 1, 2, 0, 3, 4 }, scanlines.ToArray());
    }

    [Fact]
    public void RejectsMismatchedDimensionsAndShortPalettes()
    {
        using var output = new MemoryStream();
        var palette = new IndexedPalette(new byte[IndexedPalette.ByteSize]);
        Assert.Throws<ArgumentException>(() => IndexedPngWriter.Write(output, 2, 2, [1, 2, 3], palette));
        Assert.Throws<ArgumentException>(() => IndexedPngWriter.Write(output, 0, 1, [], palette));
        Assert.Throws<ArgumentException>(() =>
            IndexedPngWriter.Write(output, 1, 1, [0], new IndexedPalette(new byte[3])));
    }
}
