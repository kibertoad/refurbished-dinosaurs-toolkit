using System.Buffers.Binary;
using RefurbishedDinosaurs.Media.Avi;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class MicrosoftAdpcmStreamTests
{
    // The seven published coefficient pairs, laid out the way the movies'
    // format extension blocks carry them.
    private static byte[] StandardExtra(ushort samplesPerBlock) =>
    [
        (byte)samplesPerBlock, (byte)(samplesPerBlock >> 8),
        7, 0,
        0x00, 0x01, 0x00, 0x00,   // 256, 0
        0x00, 0x02, 0x00, 0xFF,   // 512, -256
        0x00, 0x00, 0x00, 0x00,   // 0, 0
        0xC0, 0x00, 0x40, 0x00,   // 192, 64
        0xF0, 0x00, 0x00, 0x00,   // 240, 0
        0xCC, 0x01, 0x30, 0xFF,   // 460, -208
        0x88, 0x01, 0x18, 0xFF,   // 392, -232
    ];

    private static AviAudioFormat MonoFormat(int blockAlign, ushort samplesPerBlock) =>
        new(2, 1, 22050, blockAlign, blockAlign, 4, StandardExtra(samplesPerBlock));

    private static AviAudioFormat StereoFormat(int blockAlign, ushort samplesPerBlock) =>
        new(2, 2, 22050, blockAlign, blockAlign, 4, StandardExtra(samplesPerBlock));

    private static byte[] Word(short value) =>
        [(byte)(ushort)value, (byte)((ushort)value >> 8)];

    private static byte[] MonoBlock(int predictor, ushort delta, short sample1, short sample2, params byte[] nibbles)
    {
        var block = new List<byte> { (byte)predictor };
        block.AddRange(Word((short)delta));
        block.AddRange(Word(sample1));
        block.AddRange(Word(sample2));
        block.AddRange(nibbles);
        return [.. block];
    }

    [Fact]
    public void DecodesMonoSeedsAndNibbles()
    {
        // block align 9: a 7-byte preamble and two nibble bytes, six
        // samples per block (two seeds plus the nibbles).
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        // predictor 1 (512, -256), delta 256, seeds -500 and 1000, then
        // nibbles A (error -6), 3, 0, 0.
        byte[] block = MonoBlock(1, 256, 1000, -500, 0xA3, 0x00);

        short[] samples = stream.DecodeBlock(block);

        // Seeds emit oldest first. Nibble A adapts through table[10]=512
        // (not the -6 error's magnitude), so nibble 3 steps by 512.
        Assert.Equal([-500, 1000, 964, 2464, 3964, 5464], samples);
    }

    [Fact]
    public void PredictorSumTruncatesTowardZero()
    {
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        // predictor 5 (460, -208): 460 * -101 = -46460, which /256 is
        // -181.48; truncation reads -181 while a right shift reads -182.
        // Nibble 0 contributes no error, so the samples are the predictors.
        byte[] block = MonoBlock(5, 16, -101, 0, 0x00, 0x00);

        short[] samples = stream.DecodeBlock(block);

        Assert.Equal([0, -101, -181, -243, -289, -321], samples);
    }

    [Fact]
    public void StepSizeFloorsAtSixteen()
    {
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        // delta 1 with nibble 0 collapses the step size to the floor, so
        // the second nibble (8, error -8) steps by 16: sample -128.
        byte[] block = MonoBlock(0, 1, 0, 0, 0x08, 0x00);

        short[] samples = stream.DecodeBlock(block);

        Assert.Equal([0, 0, 0, -128, -128, -128], samples);
    }

    [Fact]
    public void DecodesStereoWithInterleavedPreambleAndNibbles()
    {
        // block align 16 for two channels: a 14-byte preamble whose fields
        // group across channels (predictors, deltas, sample 1s, sample 2s),
        // then bytes of left/right nibble pairs.
        var stream = new MicrosoftAdpcmStream(StereoFormat(16, 4));
        var block = new List<byte>
        {
            0, 6,                                   // predictors L (256,0), R (392,-232)
        };
        block.AddRange(Word(100));                  // delta L
        block.AddRange(Word(200));                  // delta R
        block.AddRange(Word(500));                  // sample 1 L
        block.AddRange(Word(-300));                 // sample 1 R
        block.AddRange(Word(200));                  // sample 2 L
        block.AddRange(Word(400));                  // sample 2 R
        block.Add(0x12);                            // nibbles 1/2
        block.Add(0x88);                            // nibbles 8/8

        short[] samples = stream.DecodeBlock([.. block]);

        // Seeds interleave per sample, left first; the right channel's
        // first predicted sum (-210400/256) also exercises truncation, and
        // nibble 2 adapts through table[2]=230, not its +2 error.
        Assert.Equal([200, 400, 500, -300, 600, -421, -112, -1804], samples);
    }

    [Fact]
    public void EmitsEachBlocksSeedsAcrossChunks()
    {
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        byte[] first = MonoBlock(0, 16, 0, 0, 0x00, 0x00);
        byte[] second = MonoBlock(0, 16, 40, -40, 0x00, 0x00);

        short[] a = stream.DecodeBlock(first);
        short[] b = stream.DecodeBlock(second);

        Assert.Equal([0, 0, 0, 0, 0, 0], a);
        // The next block's header pair is emitted again as its own seeds;
        // predictor 0 copies sample 1 through to every nibble.
        Assert.Equal([-40, 40, 40, 40, 40, 40], b);
    }

    [Fact]
    public void DecodeConcatenatesChunkBlocks()
    {
        byte[] first = MonoBlock(0, 16, 0, 0, 0x00, 0x00);
        byte[] second = MonoBlock(0, 16, 40, -40, 0x00, 0x00);

        short[] samples = MicrosoftAdpcmStream.Decode(MonoFormat(9, 6), [first, second]);

        Assert.Equal(12, samples.Length);
        Assert.Equal(-40, samples[6]);
        Assert.Equal(40, samples[7]);
    }

    [Fact]
    public void RejectsWrongFormatTag()
    {
        AviAudioFormat format = new(1, 1, 22050, 512, 512, 4, StandardExtra(1012));
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsUnsupportedChannelCount()
    {
        AviAudioFormat format = new(2, 3, 22050, 512, 512, 4, StandardExtra(1012));
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsWrongBitDepth()
    {
        AviAudioFormat format = new(2, 1, 22050, 512, 512, 8, StandardExtra(1012));
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsTruncatedExtension()
    {
        AviAudioFormat format = new(2, 1, 22050, 512, 512, 4, [0xF4, 0x03]);
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsCoefficientTableLongerThanExtension()
    {
        byte[] extra = [0xF4, 0x03, 0xFF, 0x00];
        AviAudioFormat format = new(2, 1, 22050, 512, 512, 4, extra);
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsSamplesPerBlockDisagreeingWithBlockAlign()
    {
        AviAudioFormat format = new(2, 1, 22050, 512, 512, 4, StandardExtra(1011));
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(format));
    }

    [Fact]
    public void RejectsPredictorOutsideCoefficientTable()
    {
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        byte[] block = MonoBlock(7, 16, 0, 0, 0x00, 0x00);
        Assert.Throws<InvalidDataException>(() => stream.DecodeBlock(block));
    }

    [Fact]
    public void RejectsShortBlock()
    {
        var stream = new MicrosoftAdpcmStream(MonoFormat(9, 6));
        Assert.Throws<InvalidDataException>(() => stream.DecodeBlock(new byte[8]));
    }

    [Fact]
    public void RejectsPartialBlockInChunk()
    {
        byte[] whole = MonoBlock(0, 16, 0, 0, 0x00);
        byte[] ragged = [.. whole, .. new byte[4]];
        Assert.Throws<InvalidDataException>(
            () => MicrosoftAdpcmStream.Decode(MonoFormat(9, 6), [whole, ragged]));
    }

    [Fact]
    public void RejectsOutputLimitBeforeDecodingOrAllocatingSamples()
    {
        var chunk = new byte[32773];
        var chunks = Enumerable.Repeat(chunk, 513).ToArray();
        Assert.Throws<InvalidDataException>(() => MicrosoftAdpcmStream.Decode(MonoFormat(32773, 65534), chunks));
    }

    [Fact]
    public void RejectsInvalidSampleRateAndBlockSize()
    {
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(MonoFormat(9, 6) with { SamplesPerSecond = 0 }));
        Assert.Throws<InvalidDataException>(() => new MicrosoftAdpcmStream(MonoFormat(9, 6) with { BlockAlign = 65536 }));
    }
}
