using System.Buffers.Binary;
using RefurbishedDinosaurs.Media.Fli;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class FliTests
{
    [Fact]
    public void StreamsPlaybackRecordsAndIndexesOptionalRingFrame()
    {
        byte[] first = Frame((16, new byte[] { 1, 2, 3, 4 }));
        byte[] ring = Frame((13, Array.Empty<byte>()));
        using var input = new MemoryStream(Movie(1, first, ring));
        using (var movie = new FliMovieStream(input, leaveOpen: true))
        {
            Assert.Equal((4, 1, 1, true), (movie.Width, movie.Height, movie.FrameCount, movie.HasRingFrame));
            Assert.Equal(TimeSpan.FromMilliseconds(100), movie.HeaderFrameDuration);
            var buffer = new byte[movie.MaximumFrameLength];
            var surface = new FliSurface(movie.Width, movie.Height);
            surface.DecodeFrame(buffer.AsSpan(0, movie.ReadFrame(0, buffer)));
            Assert.Equal(new byte[] { 1, 2, 3, 4 }, surface.Indices.ToArray());
            surface.DecodeFrame(buffer.AsSpan(0, movie.ReadFrame(1, buffer)));
            Assert.Equal(new byte[4], surface.Indices.ToArray());
            Assert.Throws<ArgumentOutOfRangeException>(() => movie.ReadFrame(2, buffer));
        }
        Assert.True(input.CanRead);
    }

    [Fact]
    public void AppliesPaletteDeltaAndBrunWithPersistentState()
    {
        var surface = new FliSurface(4, 1);
        surface.DecodeFrame(Frame((11, new byte[] { 1, 0, 1, 1, 63, 32, 0 }), (15, new byte[] { 1, 4, 1 })));
        Assert.Equal(new byte[] { 255, 130, 0 }, surface.Palette.Span.Slice(3, 3).ToArray());
        Assert.Equal(new byte[] { 1, 1, 1, 1 }, surface.Indices.ToArray());
        surface.DecodeFrame(Frame((12, new byte[] { 0, 0, 1, 0, 1, 1, 2, 8, 9 })));
        Assert.Equal(new byte[] { 1, 8, 9, 1 }, surface.Indices.ToArray());
        surface.DecodeFrame(Frame());
        Assert.Equal(new byte[] { 1, 8, 9, 1 }, surface.Indices.ToArray());
    }

    [Fact]
    public void SupportsLiteralBrunAndRepeatedDelta()
    {
        var surface = new FliSurface(4, 1);
        surface.DecodeFrame(Frame((15, new byte[] { 1, 252, 1, 2, 3, 4 })));
        surface.DecodeFrame(Frame((12, new byte[] { 0, 0, 1, 0, 1, 1, 254, 7 })));
        Assert.Equal(new byte[] { 1, 7, 7, 4 }, surface.Indices.ToArray());
    }

    [Theory]
    [InlineData(15, new byte[] { 1, 0 })]
    [InlineData(15, new byte[] { 1, 5, 2 })]
    [InlineData(16, new byte[] { 1 })]
    [InlineData(11, new byte[] { 1, 0, 0, 1, 64, 0, 0 })]
    [InlineData(12, new byte[] { 0, 0, 2, 0 })]
    public void RejectsMalformedChunkPayloads(int type, byte[] data)
    {
        var surface = new FliSurface(4, 1);
        Assert.Throws<InvalidDataException>(() => surface.DecodeFrame(Frame(((ushort)type, data))));
    }

    [Fact]
    public void RejectsUnsupportedFormatsAndInvalidExtents()
    {
        var source = Movie(1, Frame());
        BinaryPrimitives.WriteUInt16LittleEndian(source.AsSpan(4), 0xAF12);
        Assert.Throws<NotSupportedException>(() => new FliMovieStream(new MemoryStream(source)));
        source = Movie(2, Frame());
        Assert.Throws<InvalidDataException>(() => new FliMovieStream(new MemoryStream(source)));
        source = Movie(1, Frame());
        source[128] = 255;
        Assert.Throws<InvalidDataException>(() => new FliMovieStream(new MemoryStream(source)));
        Assert.Throws<NotSupportedException>(() => new FliSurface(4, 1).DecodeFrame(Frame((99, Array.Empty<byte>()))));
        var broken = Frame((16, new byte[] { 1, 2, 3, 4 }));
        broken[16] = 255;
        Assert.Throws<InvalidDataException>(() => new FliSurface(4, 1).DecodeFrame(broken));
    }

    private static byte[] Movie(int declared, params byte[][] records)
    {
        var header = new byte[128];
        BinaryPrimitives.WriteUInt32LittleEndian(header, (uint)(128 + records.Sum(r => r.Length)));
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(4), 0xAF11);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(6), (ushort)declared);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(8), 4);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(10), 1);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(12), 8);
        BinaryPrimitives.WriteUInt16LittleEndian(header.AsSpan(16), 7);
        return header.Concat(records.SelectMany(r => r)).ToArray();
    }

    private static byte[] Frame(params (ushort Type, byte[] Data)[] chunks)
    {
        var record = new byte[16 + chunks.Sum(c => c.Data.Length + 6)];
        BinaryPrimitives.WriteUInt32LittleEndian(record, (uint)record.Length);
        BinaryPrimitives.WriteUInt16LittleEndian(record.AsSpan(4), 0xF1FA);
        BinaryPrimitives.WriteUInt16LittleEndian(record.AsSpan(6), (ushort)chunks.Length);
        int offset = 16;
        foreach (var (type, data) in chunks)
        {
            BinaryPrimitives.WriteUInt32LittleEndian(record.AsSpan(offset), (uint)(data.Length + 6));
            BinaryPrimitives.WriteUInt16LittleEndian(record.AsSpan(offset + 4), type);
            data.CopyTo(record, offset + 6);
            offset += data.Length + 6;
        }
        return record;
    }
}
