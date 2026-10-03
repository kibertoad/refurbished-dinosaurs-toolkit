using System.Buffers.Binary;
using RefurbishedDinosaurs.Media.Smacker;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class SmackerConstantFrameTests
{
    [Fact]
    public void AbsentTreesDecodeConstantZeroWithoutVideoBits()
    {
        var bytes = Movie(0, 0, []);
        var movie = SmackerMovieDecoder.Decode(bytes);
        var layout = SmackerMovieDecoder.DecodeFrameLayout(movie, 0, bytes, new byte[768]);
        var pixels = Enumerable.Repeat((byte)7, 16).ToArray();
        var decoder = new SmackerVideoDecoder(movie, bytes);
        decoder.DecodeFrame(bytes.AsSpan(layout.Video.Offset, layout.Video.Length), pixels, false);
        Assert.Equal(new byte[16], pixels);
        using var stream = new SmackerMovieStream(new MemoryStream(bytes));
        Assert.Equal(0, stream.ReadFrame(0, []));
    }

    [Fact]
    public void EmptyFrameCannotContainAPaletteOrAudioHeader()
    {
        foreach (byte flags in new byte[] { 1, 2 })
        {
            var bytes = Movie(0, flags, []);
            var movie = SmackerMovieDecoder.Decode(bytes);
            Assert.Throws<InvalidDataException>(() =>
                SmackerMovieDecoder.DecodeFrameLayout(movie, 0, bytes, new byte[768]));
        }
    }

    [Fact]
    public void TreePresenceBitsStillRequireACompletePrefix()
    {
        // Even a constant-tree prefix must contain all four presence bits.
        var bytes = Movie(0, 0, []);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(52), 0);
        Array.Resize(ref bytes, bytes.Length - 1);
        var movie = SmackerMovieDecoder.Decode(bytes);
        Assert.Throws<InvalidDataException>(() => new SmackerVideoDecoder(movie, bytes));
    }

    [Fact]
    public void EmptyPackedMonoAudioAgreesAcrossAudioApis()
    {
        var track = new SmackerAudioTrack(0, 22050, 4096, true, false, false);
        Assert.Empty(SmackerAudioDecoder.Decode([0, 0, 0, 0, 0], track).Samples);
        Assert.Empty(SmackerAudioDecoder.DecodePcm16([0, 0, 0, 0, 0], track).Samples);
        Assert.Throws<InvalidDataException>(() => SmackerAudioDecoder.Decode([0, 0, 0, 0, 1], track));
    }

    [Fact]
    public void PartialDeltaPaletteKeepsUnmentionedEntries()
    {
        var bytes = Movie(4, 1, [1, 0x80, 0xFF, 0]);
        var movie = SmackerMovieDecoder.Decode(bytes);
        var previous = Enumerable.Repeat((byte)7, 768).ToArray();
        var layout = SmackerMovieDecoder.DecodeFrameLayout(movie, 0, bytes, previous);
        Assert.Equal(previous, layout.Palette);
        Assert.Equal(0, layout.Video.Length);
    }

    [Fact]
    public void ConstantBlockRunCannotExceedFrame()
    {
        var bits = new List<int> { 0, 0, 0, 1, 1, 0 };
        void Add(int value, int count)
        {
            for (var bit = 0; bit < count; bit++) bits.Add((value >> bit) & 1);
        }
        Add(7, 8); // Constant low-byte tree: FILL, run of two blocks.
        bits.AddRange([0, 1, 0]);
        Add(9, 8); // Constant high-byte tree: palette index nine.
        bits.Add(0);
        Add(0xfffd, 16); Add(0xfffe, 16); Add(0xffff, 16);
        bits.AddRange([0, 0]);
        var trees = new byte[(bits.Count + 7) / 8];
        for (var bit = 0; bit < bits.Count; bit++) trees[bit / 8] |= (byte)(bits[bit] << (bit % 8));
        var bytes = Movie(0, 0, []);
        Array.Resize(ref bytes, 109 + trees.Length);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(52), trees.Length);
        trees.CopyTo(bytes, 109);
        var movie = SmackerMovieDecoder.Decode(bytes);
        var decoder = new SmackerVideoDecoder(movie, bytes);
        Assert.Throws<InvalidDataException>(() => decoder.DecodeFrame([], new byte[16], false));
    }

    private static byte[] Movie(int size, byte flags, byte[] payload)
    {
        var bytes = new byte[110 + size];
        "SMK2"u8.CopyTo(bytes);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(4), 4);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(8), 4);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(12), 1);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(16), 100);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(52), 1);
        foreach (int offset in new[] { 56, 60, 64, 68 })
            BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(offset), 4);
        BinaryPrimitives.WriteInt32LittleEndian(bytes.AsSpan(104), size);
        bytes[108] = flags;
        payload.CopyTo(bytes, 110);
        return bytes;
    }
}
