using System.Buffers.Binary;

namespace Toad.Discovery.LegacyFormats;

public sealed record SmackerAudioBuffer(int SampleRate, byte[] Samples)
{
    public byte[] ToPcm16LittleEndian()
    {
        var pcm = new byte[checked(Samples.Length * 2)];
        for (var index = 0; index < Samples.Length; index++)
            BinaryPrimitives.WriteInt16LittleEndian(pcm.AsSpan(index * 2),
                checked((short)((Samples[index] - 128) << 8)));
        return pcm;
    }
}

public static class SmackerAudioDecoder
{
    private const int MaximumDecodedBytes = 16 * 1024 * 1024;
    private const int MaximumTreeDepth = 27;
    private const int MaximumLeaves = 256;

    public static SmackerAudioBuffer Decode(ReadOnlySpan<byte> packet, SmackerAudioTrack track)
    {
        ArgumentNullException.ThrowIfNull(track);
        if (!track.IsCompressed || track.Is16Bit || track.IsStereo)
            throw new NotSupportedException("Only packed 8-bit mono Smacker audio is supported.");
        if (packet.Length <= 4)
            throw new InvalidDataException("Smacker audio packet is truncated.");
        var outputLength = BinaryPrimitives.ReadUInt32LittleEndian(packet);
        if (outputLength == 0 || outputLength > MaximumDecodedBytes || outputLength > int.MaxValue
            || track.MaximumDecodedBytes > 0 && outputLength > track.MaximumDecodedBytes)
            throw new InvalidDataException("Smacker audio output length is invalid.");

        var reader = new LittleEndianBitReader(packet[4..]);
        if (!reader.ReadBit())
            throw new InvalidDataException("Smacker audio packet contains no sample data.");
        if (reader.ReadBit() || reader.ReadBit())
            throw new InvalidDataException("Smacker audio packet does not match its mono 8-bit track.");

        reader.ReadBit();
        var nodes = new List<HuffmanNode>();
        var leaves = 0;
        var root = ReadTree(ref reader, nodes, 0, ref leaves);
        reader.ReadBit();

        var output = new byte[checked((int)outputLength)];
        var predictor = reader.ReadBits(8);
        output[0] = (byte)predictor;
        for (var index = 1; index < output.Length; index++)
        {
            predictor = (predictor + DecodeSymbol(ref reader, nodes, root)) & 0xFF;
            output[index] = (byte)predictor;
        }
        return new SmackerAudioBuffer(track.SampleRate, output);
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
