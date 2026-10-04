using System.Buffers.Binary;

namespace RefurbishedDinosaurs.Media.Smacker;

/// <summary>Decoded 8-bit mono audio.</summary>
/// <param name="SampleRate">Samples per second.</param>
/// <param name="Samples">Unsigned 8-bit samples centred on 128; empty when the packet declares no data.</param>
public sealed record SmackerAudioBuffer(int SampleRate, byte[] Samples)
{
    /// <summary>Converts the samples to signed 16-bit little-endian PCM.</summary>
    public byte[] ToPcm16LittleEndian()
    {
        var pcm = new byte[checked(Samples.Length * 2)];
        for (var index = 0; index < Samples.Length; index++)
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(index * 2),
                checked((short)((Samples[index] - 128) << 8)));
        return pcm;
    }
}

/// <summary>Decoded signed 16-bit little-endian Smacker audio.</summary>
/// <param name="SampleRate">Samples per second per channel.</param>
/// <param name="Channels">One or two interleaved channels.</param>
/// <param name="Samples">Interleaved signed PCM bytes; empty when the packet declares no data.</param>
public sealed record SmackerPcm16Buffer(int SampleRate, int Channels, byte[] Samples);

/// <summary>Decodes Smacker audio packets.</summary>
public static class SmackerAudioDecoder
{
    private const int MaximumDecodedBytes = 16 * 1024 * 1024;
    private const int MaximumTreeDepth = 27;
    private const int MaximumLeaves = 256;

    /// <summary>Decodes Huffman-packed 8/16-bit mono/stereo audio to interleaved PCM16.</summary>
    /// <param name="packet">Packet bytes after the chunk length field, including decoded byte count.</param>
    /// <param name="track">The declared packed-audio profile.</param>
    /// <exception cref="NotSupportedException">Uncompressed audio is not supported.</exception>
    /// <exception cref="InvalidDataException">The packet or metadata is malformed or exceeds 16 MiB decoded source samples.</exception>
    public static SmackerPcm16Buffer DecodePcm16(ReadOnlySpan<byte> packet, SmackerAudioTrack track)
    {
        ArgumentNullException.ThrowIfNull(track);
        if (!track.IsCompressed) throw new NotSupportedException("Only Huffman-packed Smacker audio is supported.");
        var (channels, samples) = DecodeCore(packet, track, pcm16: true);
        return new(track.SampleRate, channels, samples);
    }

    /// <summary>Decodes one Huffman-packed 8-bit mono packet.</summary>
    /// <param name="packet">The packet bytes, from <see cref="SmackerAudioPacket.Data"/>.</param>
    /// <param name="track">The packet's track.</param>
    /// <returns>The samples; empty when the packet declares no data.</returns>
    /// <exception cref="NotSupportedException">The track is uncompressed, 16-bit or stereo.</exception>
    /// <exception cref="InvalidDataException">The packet or track metadata is malformed.</exception>
    public static SmackerAudioBuffer Decode(ReadOnlySpan<byte> packet, SmackerAudioTrack track)
    {
        ArgumentNullException.ThrowIfNull(track);
        if (!track.IsCompressed || track.Is16Bit || track.IsStereo)
            throw new NotSupportedException("Only packed 8-bit mono Smacker audio is supported.");
        return new SmackerAudioBuffer(track.SampleRate, DecodeCore(packet, track, pcm16: false).Samples);
    }

    // Writes signed PCM16 when pcm16 is set; otherwise the predictor bytes themselves (8-bit tracks only).
    private static (int Channels, byte[] Samples) DecodeCore(
        ReadOnlySpan<byte> packet, SmackerAudioTrack track, bool pcm16)
    {
        if (track.SampleRate is < 1000 or > 192000 || track.MaximumDecodedBytes < 0)
            throw new InvalidDataException("Smacker audio metadata is invalid.");
        if (packet.Length <= 4) throw new InvalidDataException("Smacker audio packet is truncated.");
        int channels = track.IsStereo ? 2 : 1;
        int bytesPerSample = track.Is16Bit ? 2 : 1;
        uint length = BinaryPrimitives.ReadUInt32LittleEndian(packet);
        if (length > MaximumDecodedBytes || length % (channels * bytesPerSample) != 0
            || track.MaximumDecodedBytes > 0 && length > track.MaximumDecodedBytes)
            throw new InvalidDataException("Smacker audio decoded length is invalid.");
        var reader = new LittleEndianBitReader(packet[4..]);
        if (!reader.ReadBit())
        {
            if (length != 0)
                throw new InvalidDataException("Smacker audio packet declares samples but contains no data.");
            return (channels, Array.Empty<byte>());
        }
        if (reader.ReadBit() != track.IsStereo || reader.ReadBit() != track.Is16Bit
            || length < channels * bytesPerSample)
            throw new InvalidDataException("Smacker audio packet does not match its declared profile.");
        var trees = new List<HuffmanNode>[channels * bytesPerSample];
        var roots = new int[trees.Length];
        for (int i = 0; i < trees.Length; i++)
        {
            reader.ReadBit();
            trees[i] = [];
            int leaves = 0;
            roots[i] = ReadTree(ref reader, trees[i], 0, ref leaves);
            reader.ReadBit();
        }
        var predictors = new int[channels];
        // The bitstream stores stereo seeds right first. 16-bit seeds are byte-swapped.
        for (int channel = channels - 1; channel >= 0; channel--)
        {
            int value = reader.ReadBits(bytesPerSample * 8);
            predictors[channel] = track.Is16Bit ? ((value & 255) << 8) | (value >> 8) : value;
        }
        int sampleCount = (int)length / bytesPerSample;
        var output = new byte[pcm16 ? checked(sampleCount * 2) : sampleCount];
        for (int sample = 0; sample < sampleCount; sample++)
        {
            int channel = sample % channels;
            if (sample >= channels)
            {
                int tree = channel * bytesPerSample;
                int delta = DecodeSymbol(ref reader, trees[tree], roots[tree]);
                if (track.Is16Bit) delta |= DecodeSymbol(ref reader, trees[tree + 1], roots[tree + 1]) << 8;
                predictors[channel] = (predictors[channel] + delta) & (track.Is16Bit ? 65535 : 255);
            }
            if (!pcm16)
            {
                output[sample] = (byte)predictors[channel];
                continue;
            }
            short pcm = track.Is16Bit ? unchecked((short)predictors[channel]) : (short)((predictors[channel] - 128) << 8);
            BinaryPrimitives.WriteInt16LittleEndian(output.AsSpan(sample * 2), pcm);
        }
        return (channels, output);
    }

    private static int ReadTree(
        ref LittleEndianBitReader reader, List<HuffmanNode> nodes, int depth, ref int leaves)
    {
        if (depth > MaximumTreeDepth)
            throw new InvalidDataException("Smacker audio tree is too deep.");
        if (!reader.ReadBit())
        {
            if (++leaves > MaximumLeaves)
                throw new InvalidDataException("Smacker audio tree has too many leaves.");
            nodes.Add(new HuffmanNode(-1, -1, checked((byte)reader.ReadBits(8))));
            return nodes.Count - 1;
        }

        var node = nodes.Count;
        nodes.Add(default);
        var left = ReadTree(ref reader, nodes, depth + 1, ref leaves);
        var right = ReadTree(ref reader, nodes, depth + 1, ref leaves);
        nodes[node] = new HuffmanNode(left, right, 0);
        return node;
    }

    private static int DecodeSymbol(ref LittleEndianBitReader reader, List<HuffmanNode> nodes, int node)
    {
        for (var depth = 0; depth <= MaximumTreeDepth; depth++)
        {
            var current = nodes[node];
            if (current.Left < 0) return current.Value;
            node = reader.ReadBit() ? current.Right : current.Left;
            if ((uint)node >= nodes.Count)
                throw new InvalidDataException("Smacker audio tree reference is invalid.");
        }
        throw new InvalidDataException("Smacker audio symbol exceeds the tree depth limit.");
    }

    private readonly record struct HuffmanNode(int Left, int Right, byte Value);

    private ref struct LittleEndianBitReader(ReadOnlySpan<byte> source)
    {
        private readonly ReadOnlySpan<byte> _source = source;
        private int _position;

        public bool ReadBit()
        {
            if (_position >= _source.Length * 8)
                throw new InvalidDataException("Smacker audio bitstream is truncated.");
            var value = (_source[_position >> 3] & (1 << (_position & 7))) != 0;
            _position++;
            return value;
        }

        public int ReadBits(int count)
        {
            var value = 0;
            for (var bit = 0; bit < count; bit++)
                if (ReadBit()) value |= 1 << bit;
            return value;
        }
    }
}
