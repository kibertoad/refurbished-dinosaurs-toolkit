namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes data compressed by the PKWARE Data Compression Library ("implode"), the compression of
// InstallShield 3 archive members. The format follows Ben Rudiak-Gould's description
// (comp.compression, 13 August 2001) as zlib's contrib/blast reads it:
//
// - Byte 0 is 0 for literals stored as plain bytes and 1 for Huffman-coded literals. Byte 1 is 4, 5
//   or 6: the number of low distance bits sent as plain bits, so the window is 1, 2 or 4 KiB.
// - Bits follow least significant first. Each item starts with a bit: 0 for a literal, 1 for a
//   length and distance. A length of 519 ends the data.
// - The three Huffman codes are fixed. A code is sent most significant bit first with every bit
//   inverted, so reading a bit and inverting it builds the canonical code.
internal sealed class PkwareDclExploder
{
    private const int MaximumCodeLength = 13;
    private const int Window = 4096;
    private const int EndLength = 519;

    // Code lengths, each byte a run: the high four bits are the run length less one, the low four the
    // code length. They describe 256 literal, 16 length and 64 distance symbols.
    private static readonly Huffman LiteralCode = new(
    [
        11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
        9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
        7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
        8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
        44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
        44, 173
    ], 256);
    private static readonly Huffman LengthCode = new([2, 35, 36, 53, 38, 23], 16);
    private static readonly Huffman DistanceCode = new([2, 20, 53, 230, 247, 151, 248], 64);
    private static readonly int[] LengthBase = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264];
    private static readonly int[] LengthExtra = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8];

    private readonly Func<int> nextByte;
    private readonly byte[] window = new byte[Window];
    private bool codedLiterals;
    private int distanceBits;
    private bool started;
    private uint bitBuffer;
    private int bitCount;
    private long produced;
    private int copyLeft;
    private int copyDistance;

    // nextByte returns the next compressed byte, or -1 when the compressed data has ended.
    public PkwareDclExploder(Func<int> nextByte) => this.nextByte = nextByte;

    // Whether the end code has been read.
    public bool Ended { get; private set; }

    // The code tables as they are built from the run lengths above: the number of codes of each
    // length, and whether the lengths describe a complete code. The tests check the tables with them.
    internal static IEnumerable<(int[] Counts, bool Complete)> Tables() =>
        [(LiteralCode.Counts, LiteralCode.Complete), (LengthCode.Counts, LengthCode.Complete), (DistanceCode.Counts, DistanceCode.Complete)];

    // Fills destination with the next expanded bytes. Returns fewer only once the end code is read,
    // and 0 from then on.
    public int Read(Span<byte> destination)
    {
        if (!started) Start();
        var count = 0;
        while (count < destination.Length && !Ended)
        {
            if (copyLeft > 0)
            {
                var from = (int)((produced - copyDistance) & (Window - 1));
                destination[count++] = Put(window[from]);
                copyLeft--;
                continue;
            }
            if (Bits(1) == 0)
            {
                destination[count++] = Put((byte)(codedLiterals ? LiteralCode.Decode(this) : Bits(8)));
                continue;
            }
            var symbol = LengthCode.Decode(this);
            var length = LengthBase[symbol] + Bits(LengthExtra[symbol]);
            if (length == EndLength)
            {
                Ended = true;
                break;
            }
            var low = length == 2 ? 2 : distanceBits;
            var distance = ((DistanceCode.Decode(this) << low) | Bits(low)) + 1;
            if (distance > produced)
                throw new InvalidDataException(
                    $"PKWARE DCL data refers {distance} bytes back after only {produced} bytes.");
            copyLeft = length;
            copyDistance = distance;
        }
        return count;
    }

    // Whether whole compressed bytes are left after the end code; reads one if there is. Bits left in
    // the byte that holds the end code are padding.
    public bool HasTrailingBytes() => nextByte() >= 0;

    private void Start()
    {
        started = true;
        var literals = Bits(8);
        if (literals > 1)
            throw new InvalidDataException($"PKWARE DCL data starts with literal mode {literals}; only 0 and 1 exist.");
        codedLiterals = literals == 1;
        distanceBits = Bits(8);
        if (distanceBits is < 4 or > 6)
            throw new InvalidDataException($"PKWARE DCL data declares a dictionary of {distanceBits} bits; only 4, 5 and 6 exist.");
    }

    private byte Put(byte value)
    {
        window[produced & (Window - 1)] = value;
        produced++;
        return value;
    }

    private int Bits(int count)
    {
        while (bitCount < count)
        {
            var next = nextByte();
            if (next < 0) throw new InvalidDataException("PKWARE DCL data ends before its end code.");
            bitBuffer |= (uint)next << bitCount;
            bitCount += 8;
        }
        var value = (int)(bitBuffer & ((1u << count) - 1));
        bitBuffer >>= count;
        bitCount -= count;
        return value;
    }

    private sealed class Huffman
    {
        private readonly int[] symbols;

        public Huffman(byte[] runs, int symbolCount)
        {
            var lengths = new List<int>(symbolCount);
            foreach (var run in runs)
                for (var repeat = (run >> 4) + 1; repeat > 0; repeat--) lengths.Add(run & 15);
            if (lengths.Count != symbolCount)
                throw new InvalidOperationException($"A PKWARE DCL code table describes {lengths.Count} symbols, not {symbolCount}.");
            Counts = new int[MaximumCodeLength + 1];
            foreach (var length in lengths) Counts[length]++;
            var left = 1;
            for (var length = 1; length <= MaximumCodeLength; length++) left = (left << 1) - Counts[length];
            Complete = left == 0;
            // Symbols in canonical order: by code length, then by symbol.
            symbols = Enumerable.Range(0, symbolCount).Where(symbol => lengths[symbol] != 0)
                .OrderBy(symbol => lengths[symbol]).ThenBy(symbol => symbol).ToArray();
        }

        public int[] Counts { get; }
        public bool Complete { get; }

        public int Decode(PkwareDclExploder reader)
        {
            int code = 0, first = 0, index = 0;
            for (var length = 1; length <= MaximumCodeLength; length++)
            {
                code |= reader.Bits(1) ^ 1;
                var count = Counts[length];
                if (code - first < count) return symbols[index + code - first];
                index += count;
                first = (first + count) << 1;
                code <<= 1;
            }
            throw new InvalidDataException("PKWARE DCL data holds a code that is not in its code table.");
        }
    }
}
