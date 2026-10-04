using System.Buffers.Binary;

namespace RefurbishedDinosaurs.Media.Avi;

/// <summary>Decodes Microsoft ADPCM blocks into interleaved signed 16-bit PCM.</summary>
public sealed class MicrosoftAdpcmStream
{
    /// <summary>The <c>WAVE_FORMAT_TAG</c> for Microsoft ADPCM.</summary>
    public const int AdpcmFormatTag = 2;

    /// <summary>The largest coefficient table the format may declare.</summary>
    public const int MaxCoefficients = 256;

    /// <summary>
    /// The published adaptation table: the step-size multiplier (in
    /// 256ths) each nibble selects. Negative errors and the largest
    /// positive nibble adapt fastest, mirroring the coefficient pairs'
    /// sign symmetry.
    /// </summary>
    private static readonly int[] AdaptationTable =
    [
        230, 230, 230, 230, 307, 409, 512, 614,
        768, 614, 512, 409, 307, 230, 230, 230,
    ];

    private readonly int[] _coefficient1;
    private readonly int[] _coefficient2;

    /// <summary>
    /// Prepares a decoder for one audio stream. The format must be Microsoft
    /// ADPCM with one or two channels and an extension block carrying
    /// <c>samplesPerBlock</c>, the coefficient-pair count, and the pairs.
    /// </summary>
    public MicrosoftAdpcmStream(AviAudioFormat format)
    {
        if (format is null)
            throw new ArgumentNullException(nameof(format));
        if (format.FormatTag != AdpcmFormatTag)
            throw new InvalidDataException(
                $"Audio format tag {format.FormatTag} is not Microsoft ADPCM ({AdpcmFormatTag}).");
        if (format.Channels is < 1 or > 2)
            throw new InvalidDataException($"ADPCM channel count {format.Channels} is outside 1..2.");
        if (format.BitsPerSample != 4)
            throw new InvalidDataException($"ADPCM bit depth {format.BitsPerSample} is not 4.");
        if (format.SamplesPerSecond is < 1000 or > 192000 || format.BlockAlign > 65535)
            throw new InvalidDataException("ADPCM sample rate or block size is invalid.");
        Channels = format.Channels;
        BlockAlign = format.BlockAlign;
        SamplesPerSecond = format.SamplesPerSecond;

        var extra = format.Extra;
        if (extra.Length < 4)
            throw new InvalidDataException("ADPCM format extension is shorter than its two leading words.");
        SamplesPerBlock = BinaryPrimitives.ReadUInt16LittleEndian(extra);
        int pairs = BinaryPrimitives.ReadUInt16LittleEndian(extra.AsSpan(2));
        if (pairs < 1 || pairs > MaxCoefficients || extra.Length < 4 + pairs * 4)
            throw new InvalidDataException(
                $"ADPCM coefficient table ({pairs} pairs) does not fit the extension block.");
        _coefficient1 = new int[pairs];
        _coefficient2 = new int[pairs];
        for (int i = 0; i < pairs; i++)
        {
            _coefficient1[i] = BinaryPrimitives.ReadInt16LittleEndian(extra.AsSpan(4 + i * 4));
            _coefficient2[i] = BinaryPrimitives.ReadInt16LittleEndian(extra.AsSpan(6 + i * 4));
        }

        int preamble = 7 * Channels;
        int nibblesPerChannel = (BlockAlign - preamble) * 2 / Channels;
        if (BlockAlign <= preamble || SamplesPerBlock != nibblesPerChannel + 2)
            throw new InvalidDataException(
                $"ADPCM block align {BlockAlign} and samples per block {SamplesPerBlock} disagree " +
                $"(the blocks hold {nibblesPerChannel + 2} samples per channel).");
    }

    /// <summary>The channel count, 1 or 2.</summary>
    public int Channels { get; }

    /// <summary>The declared block size in bytes.</summary>
    public int BlockAlign { get; }

    /// <summary>The sample rate.</summary>
    public int SamplesPerSecond { get; }

    /// <summary>The samples per block per channel, seeds included.</summary>
    public int SamplesPerBlock { get; }

    /// <summary>
    /// Decodes one complete block and returns its interleaved samples (left
    /// and right alternating for stereo). The preamble supplies the block's
    /// own predictor, step size, and history; the two seed samples are
    /// emitted every block because the encoder wrote its carried-over pair
    /// into each header.
    /// </summary>
    public short[] DecodeBlock(ReadOnlySpan<byte> block)
    {
        var samples = new short[SamplesPerBlock * Channels];
        DecodeBlock(block, samples);
        return samples;
    }

    private void DecodeBlock(ReadOnlySpan<byte> block, Span<short> samples)
    {
        if (block.Length != BlockAlign)
            throw new InvalidDataException($"ADPCM block is {block.Length} bytes, expected {BlockAlign}.");
        int preamble = 7 * Channels;
        // Every block carries its own predictor, step size and history, so no state outlives it.
        Span<ChannelState> states = stackalloc ChannelState[Channels];
        for (int channel = 0; channel < Channels; channel++)
        {
            int predictor = block[channel];
            if ((uint)predictor >= _coefficient1.Length)
                throw new InvalidDataException($"ADPCM predictor {predictor} is outside the coefficient table.");
            // The header's step size is a signed word, as the reference decoders read it.
            int headerDelta = BinaryPrimitives.ReadInt16LittleEndian(block[(Channels + channel * 2)..]);
            states[channel] = new ChannelState(
                _coefficient1[predictor],
                _coefficient2[predictor],
                headerDelta,
                BinaryPrimitives.ReadInt16LittleEndian(block[(Channels * 3 + channel * 2)..]),
                BinaryPrimitives.ReadInt16LittleEndian(block[(Channels * 5 + channel * 2)..]));
            samples[channel] = (short)states[channel].Sample2;
            samples[Channels + channel] = (short)states[channel].Sample1;
        }

        int position = preamble;
        int sampleIndex = 2;
        while (sampleIndex < SamplesPerBlock)
        {
            int byteValue = block[position++];
            if (Channels == 1)
            {
                ref var mono = ref states[0];
                samples[sampleIndex++] = (short)mono.Decode(byteValue >> 4);
                if (sampleIndex < SamplesPerBlock)
                    samples[sampleIndex++] = (short)mono.Decode(byteValue & 0x0f);
            }
            else
            {
                ref var left = ref states[0];
                ref var right = ref states[1];
                samples[sampleIndex * 2] = (short)left.Decode(byteValue >> 4);
                samples[sampleIndex * 2 + 1] = (short)right.Decode(byteValue & 0x0f);
                sampleIndex++;
            }
        }
    }

    /// <summary>
    /// Decodes a complete chunk list into one interleaved PCM buffer. Every
    /// chunk must be a whole number of blocks; a trailing partial block is
    /// rejected rather than read past.
    /// </summary>
    public static short[] Decode(AviAudioFormat format, IReadOnlyList<byte[]> chunks)
    {
        var stream = new MicrosoftAdpcmStream(format);
        ArgumentNullException.ThrowIfNull(chunks);
        long totalSamples = 0;
        foreach (var chunk in chunks)
        {
            ArgumentNullException.ThrowIfNull(chunk);
            if (chunk.Length % stream.BlockAlign != 0)
                throw new InvalidDataException(
                    $"ADPCM chunk is {chunk.Length} bytes, not a whole number of {stream.BlockAlign}-byte blocks.");
            totalSamples += (long)(chunk.Length / stream.BlockAlign) * stream.SamplesPerBlock * stream.Channels;
            if (totalSamples > 32 * 1024 * 1024)
                throw new InvalidDataException("ADPCM decoded output exceeds 64 MiB.");
        }
        var decoded = new short[totalSamples];
        int blockSamples = stream.SamplesPerBlock * stream.Channels;
        int written = 0;
        foreach (var chunk in chunks)
        {
            for (int offset = 0; offset < chunk.Length; offset += stream.BlockAlign)
            {
                stream.DecodeBlock(chunk.AsSpan(offset, stream.BlockAlign), decoded.AsSpan(written, blockSamples));
                written += blockSamples;
            }
        }
        return decoded;
    }

    private struct ChannelState
    {
        private int _coefficient1;
        private int _coefficient2;
        private int _delta;
        private int _sample1;
        private int _sample2;

        public ChannelState(int coefficient1, int coefficient2, int delta, int sample1, int sample2)
        {
            _coefficient1 = coefficient1;
            _coefficient2 = coefficient2;
            _delta = delta;
            _sample1 = sample1;
            _sample2 = sample2;
        }

        public readonly int Sample1 => _sample1;
        public readonly int Sample2 => _sample2;

        public int Decode(int nibble)
        {
            int error = nibble < 8 ? nibble : nibble - 16;
            // The predictor sum divides rather than shifts: the reference
            // decoders truncate toward zero, and `>> 8` would floor, reading
            // one low for every negative sum (verified differentially).
            long predicted = ((long)_sample1 * _coefficient1 + (long)_sample2 * _coefficient2) / 256;
            int sample = (int)Math.Clamp(predicted + (long)error * _delta, short.MinValue, short.MaxValue);
            _delta = (int)Math.Min(int.MaxValue, ((long)AdaptationTable[nibble] * _delta) >> 8);
            if (_delta < 16)
                _delta = 16;
            _sample2 = _sample1;
            _sample1 = sample;
            return sample;
        }
    }
}
