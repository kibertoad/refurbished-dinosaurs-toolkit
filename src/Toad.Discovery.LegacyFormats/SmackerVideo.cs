namespace Toad.Discovery.LegacyFormats;

public sealed class SmackerVideoDecoder
{
    private const int MaximumTreeNodes = 1_000_000;
    private static readonly int[] BlockRuns =
    [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
        17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32,
        33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48,
        49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 128, 256, 512, 1024, 2048
    ];

    private readonly int _version;
    private readonly int _width;
    private readonly int _height;
    private readonly HuffmanTree _mMap;
    private readonly HuffmanTree _mClr;
    private readonly HuffmanTree _full;
    private readonly HuffmanTree _type;

    public SmackerVideoDecoder(SmackerMovie movie, ReadOnlySpan<byte> source)
        : this(movie, ExtractTreeData(movie, source), true)
    {
    }

    private SmackerVideoDecoder(SmackerMovie movie, ReadOnlySpan<byte> treeData, bool treeOnly)
    {
        ArgumentNullException.ThrowIfNull(movie);
        if (!treeOnly || treeData.Length != movie.TreeLength)
            throw new InvalidDataException("Smacker tree data has an invalid length.");
        _version = movie.Version;
        _width = movie.Width;
        _height = movie.Height;
        var reader = new LittleEndianBitReader(treeData);
        var skipped = 0;
        _mMap = ReadHeaderTree(ref reader, movie.TreeSizes.MMap, ref skipped);
        _mClr = ReadHeaderTree(ref reader, movie.TreeSizes.MClr, ref skipped);
        _full = ReadHeaderTree(ref reader, movie.TreeSizes.Full, ref skipped);
        _type = ReadHeaderTree(ref reader, movie.TreeSizes.Type, ref skipped);
        if (skipped == 4)
            throw new InvalidDataException("Smacker movie omits every video tree.");
    }

    public static SmackerVideoDecoder FromTreeData(SmackerMovie movie, ReadOnlySpan<byte> treeData) =>
        new(movie, treeData, true);

    private static ReadOnlySpan<byte> ExtractTreeData(SmackerMovie movie, ReadOnlySpan<byte> source)
    {
        ArgumentNullException.ThrowIfNull(movie);
        if (movie.TreeOffset < 0 || movie.TreeLength <= 0
            || movie.TreeOffset > source.Length - movie.TreeLength)
            throw new InvalidDataException("Smacker tree data lies outside the supplied movie.");
        return source.Slice(movie.TreeOffset, movie.TreeLength);
    }

    public void DecodeFrame(ReadOnlySpan<byte> packet, Span<byte> indices, bool isKeyFrame)
    {
        if (indices.Length != checked(_width * _height))
            throw new ArgumentException("Smacker output buffer dimensions are inconsistent.", nameof(indices));
        if (packet.IsEmpty)
            throw new InvalidDataException("Smacker video packet is empty.");
        if (isKeyFrame) indices.Clear();
        _mMap.ResetHistory();
        _mClr.ResetHistory();
        _full.ResetHistory();
        _type.ResetHistory();

        var reader = new LittleEndianBitReader(packet);
        var blockWidth = _width / 4;
        var blockCount = checked(blockWidth * (_height / 4));
        var block = 0;
        while (block < blockCount)
        {
            var type = _type.Decode(ref reader);
            var run = BlockRuns[(type >> 2) & 0x3F];
            switch (type & 3)
            {
                case 0:
                    while (run-- > 0 && block < blockCount)
                    {
                        DecodeMonoBlock(ref reader, indices, blockWidth, block);
                        block++;
                    }
                    break;
                case 1:
                    var mode = 0;
                    if (_version == 4)
                        mode = reader.ReadBit() ? 1 : reader.ReadBit() ? 2 : 0;
                    while (run-- > 0 && block < blockCount)
                    {
                        DecodeFullBlock(ref reader, indices, blockWidth, block, mode);
                        block++;
                    }
                    break;
                case 2:
                    block += Math.Min(run, blockCount - block);
                    break;
                case 3:
                    var color = checked((byte)(type >> 8));
                    while (run-- > 0 && block < blockCount)
                    {
                        FillBlock(indices, blockWidth, block, color);
                        block++;
                    }
                    break;
            }
        }
    }

    private void DecodeMonoBlock(
        ref LittleEndianBitReader reader, Span<byte> output, int blockWidth, int block)
    {
        var colors = _mClr.Decode(ref reader);
        var map = _mMap.Decode(ref reader);
        var high = (byte)((colors >> 8) & 0xFF);
        var low = (byte)(colors & 0xFF);
        var offset = BlockOffset(blockWidth, block);
        for (var row = 0; row < 4; row++)
        {
            for (var column = 0; column < 4; column++)
                output[offset + column] = (map & (1 << column)) != 0 ? high : low;
            map >>= 4;
            offset += _width;
        }
    }

    private void DecodeFullBlock(
        ref LittleEndianBitReader reader, Span<byte> output, int blockWidth, int block, int mode)
    {
        var offset = BlockOffset(blockWidth, block);
        if (mode == 0)
        {
            for (var row = 0; row < 4; row++)
            {
                var right = _full.Decode(ref reader);
                var left = _full.Decode(ref reader);
                WritePair(output, offset, left);
                WritePair(output, offset + 2, right);
                offset += _width;
            }
            return;
        }

        if (mode == 1)
        {
            for (var half = 0; half < 2; half++)
            {
                var pair = _full.Decode(ref reader);
                for (var row = 0; row < 2; row++)
                {
                    output[offset] = output[offset + 1] = (byte)(pair & 0xFF);
                    output[offset + 2] = output[offset + 3] = (byte)((pair >> 8) & 0xFF);
                    offset += _width;
                }
            }
            return;
        }

        for (var half = 0; half < 2; half++)
        {
            var right = _full.Decode(ref reader);
            var left = _full.Decode(ref reader);
            for (var row = 0; row < 2; row++)
            {
                WritePair(output, offset, left);
                WritePair(output, offset + 2, right);
                offset += _width;
            }
        }
    }

    private void FillBlock(Span<byte> output, int blockWidth, int block, byte color)
    {
        var offset = BlockOffset(blockWidth, block);
        for (var row = 0; row < 4; row++)
        {
            output.Slice(offset, 4).Fill(color);
            offset += _width;
        }
    }

    private int BlockOffset(int blockWidth, int block) =>
        checked((block / blockWidth) * (_width * 4) + (block % blockWidth) * 4);

    private static void WritePair(Span<byte> output, int offset, int pair)
    {
        output[offset] = (byte)(pair & 0xFF);
        output[offset + 1] = (byte)((pair >> 8) & 0xFF);
    }

    private static HuffmanTree ReadHeaderTree(
        ref LittleEndianBitReader reader, int declaredSize, ref int skipped)
    {
        if (!reader.ReadBit())
        {
            skipped++;
            return HuffmanTree.Constant();
        }

        var low = ReadByteTree(ref reader);
        var high = ReadByteTree(ref reader);
        int[] escapes = [reader.ReadBits(16), reader.ReadBits(16), reader.ReadBits(16)];
        var maximumNodes = checked((declaredSize + 3) / 4);
        if (maximumNodes is <= 0 or > MaximumTreeNodes)
            throw new InvalidDataException("Smacker video tree size is invalid.");
        var nodes = new List<HuffmanNode>(maximumNodes + 3);
        int[] history = [-1, -1, -1];
        var root = ReadBigTree(ref reader, low, high, nodes, maximumNodes, escapes, history, 0);
        reader.ReadBit();
        for (var index = 0; index < history.Length; index++)
        {
            if (history[index] >= 0) continue;
            history[index] = nodes.Count;
            nodes.Add(new HuffmanNode(-1, -1, 0));
        }
        return new HuffmanTree(nodes.ToArray(), root, history);
    }

    private static ByteTree ReadByteTree(ref LittleEndianBitReader reader)
    {
        if (!reader.ReadBit()) return ByteTree.Constant();
        var nodes = new List<ByteNode>();
        var leaves = 0;
        var root = ReadByteNode(ref reader, nodes, 0, ref leaves);
        reader.ReadBit();
        return new ByteTree(nodes.ToArray(), root);
    }

    private static int ReadByteNode(
        ref LittleEndianBitReader reader, List<ByteNode> nodes, int depth, ref int leaves)
    {
        if (depth > 27)
            throw new InvalidDataException("Smacker byte tree is too deep.");
        if (!reader.ReadBit())
        {
            if (++leaves > 256)
                throw new InvalidDataException("Smacker byte tree has too many leaves.");
            nodes.Add(new ByteNode(-1, -1, checked((byte)reader.ReadBits(8))));
            return nodes.Count - 1;
        }
        var node = nodes.Count;
        nodes.Add(default);
        var left = ReadByteNode(ref reader, nodes, depth + 1, ref leaves);
        var right = ReadByteNode(ref reader, nodes, depth + 1, ref leaves);
        nodes[node] = new ByteNode(left, right, 0);
        return node;
    }

    private static int ReadBigTree(
        ref LittleEndianBitReader reader,
        ByteTree low,
        ByteTree high,
        List<HuffmanNode> nodes,
        int maximumNodes,
        int[] escapes,
        int[] history,
        int depth)
    {
        if (depth > 500 || nodes.Count >= maximumNodes)
            throw new InvalidDataException("Smacker video tree is excessive.");
        if (!reader.ReadBit())
        {
            var value = low.Decode(ref reader) | high.Decode(ref reader) << 8;
            for (var index = 0; index < history.Length; index++)
            {
                if (value != escapes[index]) continue;
                history[index] = nodes.Count;
                value = 0;
                break;
            }
            nodes.Add(new HuffmanNode(-1, -1, value));
            return nodes.Count - 1;
        }
        var node = nodes.Count;
        nodes.Add(default);
        var left = ReadBigTree(ref reader, low, high, nodes, maximumNodes, escapes, history, depth + 1);
        var right = ReadBigTree(ref reader, low, high, nodes, maximumNodes, escapes, history, depth + 1);
        nodes[node] = new HuffmanNode(left, right, 0);
        return node;
    }

    private sealed class HuffmanTree(HuffmanNode[] nodes, int root, int[] history)
    {
        public static HuffmanTree Constant()
        {
            HuffmanNode[] nodes = [new(-1, -1, 0), new(-1, -1, 0)];
            return new HuffmanTree(nodes, 0, [1, 1, 1]);
        }

        public void ResetHistory()
        {
            nodes[history[0]].Value = 0;
            nodes[history[1]].Value = 0;
            nodes[history[2]].Value = 0;
        }

        public int Decode(ref LittleEndianBitReader reader)
        {
            var node = root;
            while (nodes[node].Left >= 0)
                node = reader.ReadBit() ? nodes[node].Right : nodes[node].Left;
            var value = nodes[node].Value;
            if (value != nodes[history[0]].Value)
            {
                nodes[history[2]].Value = nodes[history[1]].Value;
                nodes[history[1]].Value = nodes[history[0]].Value;
                nodes[history[0]].Value = value;
            }
            return value;
        }
    }

    private sealed class ByteTree(ByteNode[] nodes, int root)
    {
        public static ByteTree Constant() => new([new ByteNode(-1, -1, 0)], 0);

        public int Decode(ref LittleEndianBitReader reader)
        {
            var node = root;
            while (nodes[node].Left >= 0)
                node = reader.ReadBit() ? nodes[node].Right : nodes[node].Left;
            return nodes[node].Value;
        }
    }

    private struct HuffmanNode(int left, int right, int value)
    {
        public int Left = left;
        public int Right = right;
        public int Value = value;
    }

    private readonly record struct ByteNode(int Left, int Right, byte Value);

    private ref struct LittleEndianBitReader(ReadOnlySpan<byte> source)
    {
        private readonly ReadOnlySpan<byte> _source = source;
        private long _position;

        public bool ReadBit()
        {
            if (_position >= (long)_source.Length * 8)
                throw new InvalidDataException("Smacker video bitstream is truncated.");
            var position = checked((int)_position++);
            return (_source[position >> 3] & (1 << (position & 7))) != 0;
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
