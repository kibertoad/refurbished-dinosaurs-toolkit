namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes the raw LZMA streams of chdman's cdlz codec: no header and no end marker, with the
// properties MAME's encoder uses at level 8 (lc=3, lp=0, pb=2). The whole output is one buffer, so
// the dictionary is the output decoded so far.
//
// Adapted from LzmaSpec.cpp, the reference LZMA decoder of the LZMA SDK by Igor Pavlov, which is in
// the public domain.
internal sealed class ChdLzmaDecoder
{
    private const int LiteralContextBits = 3;
    private const int PositionBits = 2;
    private const int States = 12;
    private const int PositionStatesMaximum = 1 << 4;
    private const int LengthToPositionStates = 4;
    private const int EndPositionModelIndex = 14;
    private const int FullDistances = 1 << (EndPositionModelIndex >> 1);
    private const int AlignBits = 4;
    private const ushort ProbabilityInitial = 1 << 10;

    private readonly ushort[] literals = new ushort[0x300 << LiteralContextBits];
    private readonly ushort[] isMatch = new ushort[States << 4];
    private readonly ushort[] isRep = new ushort[States];
    private readonly ushort[] isRepG0 = new ushort[States];
    private readonly ushort[] isRepG1 = new ushort[States];
    private readonly ushort[] isRepG2 = new ushort[States];
    private readonly ushort[] isRep0Long = new ushort[States << 4];
    private readonly ushort[] positionSlots = new ushort[LengthToPositionStates << 6];
    private readonly ushort[] positions = new ushort[1 + FullDistances - EndPositionModelIndex];
    private readonly ushort[] align = new ushort[1 << AlignBits];
    private readonly LengthDecoder lengths = new();
    private readonly LengthDecoder repLengths = new();

    // Fills output from input, which must hold exactly the stream.
    public void Decode(ReadOnlySpan<byte> input, Span<byte> output)
    {
        Reset();
        var range = new RangeDecoder(input);
        uint rep0 = 0, rep1 = 0, rep2 = 0, rep3 = 0;
        var state = 0;
        var position = 0;
        while (position < output.Length)
        {
            var positionState = position & ((1 << PositionBits) - 1);
            if (range.DecodeBit(ref isMatch[(state << 4) + positionState]) == 0)
            {
                output[position] = DecodeLiteral(ref range, output, position, state, rep0);
                position++;
                state = state < 4 ? 0 : state < 10 ? state - 3 : state - 6;
                continue;
            }

            int length;
            if (range.DecodeBit(ref isRep[state]) != 0)
            {
                if (position == 0) throw Damaged();
                if (range.DecodeBit(ref isRepG0[state]) == 0)
                {
                    if (range.DecodeBit(ref isRep0Long[(state << 4) + positionState]) == 0)
                    {
                        if (rep0 >= (uint)position) throw Damaged();
                        state = state < 7 ? 9 : 11;
                        output[position] = output[position - (int)rep0 - 1];
                        position++;
                        continue;
                    }
                }
                else
                {
                    uint distance;
                    if (range.DecodeBit(ref isRepG1[state]) == 0) distance = rep1;
                    else
                    {
                        if (range.DecodeBit(ref isRepG2[state]) == 0) distance = rep2;
                        else
                        {
                            distance = rep3;
                            rep3 = rep2;
                        }
                        rep2 = rep1;
                    }
                    rep1 = rep0;
                    rep0 = distance;
                }
                length = repLengths.Decode(ref range, positionState);
                state = state < 7 ? 8 : 11;
            }
            else
            {
                rep3 = rep2;
                rep2 = rep1;
                rep1 = rep0;
                length = lengths.Decode(ref range, positionState);
                state = state < 7 ? 7 : 10;
                rep0 = DecodeDistance(ref range, length);
                // The end marker; chdman's streams have none, and the output is not yet full.
                if (rep0 == 0xFFFFFFFF) throw Damaged();
            }
            if (rep0 >= (uint)position) throw Damaged();
            length += 2;
            if (length > output.Length - position) throw Damaged();
            var from = position - (int)rep0 - 1;
            for (var index = 0; index < length; index++) output[position + index] = output[from + index];
            position += length;
        }
        if (!range.FinishedAt(input.Length)) throw Damaged();
    }

    private void Reset()
    {
        foreach (var probabilities in new[] { literals, isMatch, isRep, isRepG0, isRepG1, isRepG2, isRep0Long, positionSlots, positions, align })
            probabilities.AsSpan().Fill(ProbabilityInitial);
        lengths.Reset();
        repLengths.Reset();
    }

    private byte DecodeLiteral(ref RangeDecoder range, Span<byte> output, int position, int state, uint rep0)
    {
        var previous = position > 0 ? output[position - 1] : 0;
        var probabilities = literals.AsSpan(0x300 * (previous >> (8 - LiteralContextBits)), 0x300);
        var symbol = 1;
        if (state >= 7)
        {
            if (rep0 >= (uint)position) throw Damaged();
            var match = (int)output[position - (int)rep0 - 1];
            do
            {
                var matchBit = (match >> 7) & 1;
                match <<= 1;
                var bit = range.DecodeBit(ref probabilities[((1 + matchBit) << 8) + symbol]);
                symbol = (symbol << 1) | bit;
                if (matchBit != bit) break;
            }
            while (symbol < 0x100);
        }
        while (symbol < 0x100) symbol = (symbol << 1) | range.DecodeBit(ref probabilities[symbol]);
        return (byte)(symbol - 0x100);
    }

    private uint DecodeDistance(ref RangeDecoder range, int length)
    {
        var lengthState = Math.Min(length, LengthToPositionStates - 1);
        var slot = (uint)range.DecodeTree(positionSlots.AsSpan(lengthState << 6, 1 << 6), 6);
        if (slot < 4) return slot;
        var directBits = (int)(slot >> 1) - 1;
        var distance = (2 | (slot & 1)) << directBits;
        if (slot < EndPositionModelIndex)
            return distance + (uint)range.DecodeReverseTree(positions.AsSpan((int)(distance - slot)), directBits);
        distance += range.DecodeDirectBits(directBits - AlignBits) << AlignBits;
        return distance + (uint)range.DecodeReverseTree(align, AlignBits);
    }

    private sealed class LengthDecoder
    {
        private readonly ushort[] choice = new ushort[2];
        private readonly ushort[] low = new ushort[PositionStatesMaximum << 3];
        private readonly ushort[] middle = new ushort[PositionStatesMaximum << 3];
        private readonly ushort[] high = new ushort[1 << 8];

        public void Reset()
        {
            foreach (var probabilities in new[] { choice, low, middle, high })
                probabilities.AsSpan().Fill(ProbabilityInitial);
        }

        public int Decode(ref RangeDecoder range, int positionState)
        {
            if (range.DecodeBit(ref choice[0]) == 0)
                return range.DecodeTree(low.AsSpan(positionState << 3, 1 << 3), 3);
            if (range.DecodeBit(ref choice[1]) == 0)
                return 8 + range.DecodeTree(middle.AsSpan(positionState << 3, 1 << 3), 3);
            return 16 + range.DecodeTree(high, 8);
        }
    }

    private ref struct RangeDecoder
    {
        private const uint TopValue = 1u << 24;
        private readonly ReadOnlySpan<byte> input;
        private int offset;
        private uint range;
        private uint code;

        public RangeDecoder(ReadOnlySpan<byte> input)
        {
            this.input = input;
            if (input.Length < 5 || input[0] != 0) throw Damaged();
            range = 0xFFFFFFFF;
            code = (uint)(input[1] << 24 | input[2] << 16 | input[3] << 8 | input[4]);
            offset = 5;
            if (code == range) throw Damaged();
        }

        // The stream ends cleanly where every byte was read and the code is zero.
        public readonly bool FinishedAt(int length) => offset == length && code == 0;

        private void Normalize()
        {
            if (range >= TopValue) return;
            if (offset >= input.Length) throw Damaged();
            range <<= 8;
            code = (code << 8) | input[offset++];
        }

        public int DecodeBit(ref ushort probability)
        {
            var bound = (range >> 11) * probability;
            int symbol;
            if (code < bound)
            {
                probability += (ushort)(((1 << 11) - probability) >> 5);
                range = bound;
                symbol = 0;
            }
            else
            {
                probability -= (ushort)(probability >> 5);
                code -= bound;
                range -= bound;
                symbol = 1;
            }
            Normalize();
            return symbol;
        }

        public uint DecodeDirectBits(int count)
        {
            uint result = 0;
            for (; count > 0; count--)
            {
                range >>= 1;
                code -= range;
                var mask = 0u - (code >> 31);
                code += range & mask;
                if (code == range) throw Damaged();
                Normalize();
                result = (result << 1) + (mask + 1);
            }
            return result;
        }

        public int DecodeTree(Span<ushort> probabilities, int bits)
        {
            var index = 1;
            for (var bit = 0; bit < bits; bit++) index = (index << 1) + DecodeBit(ref probabilities[index]);
            return index - (1 << bits);
        }

        public int DecodeReverseTree(Span<ushort> probabilities, int bits)
        {
            var index = 1;
            var symbol = 0;
            for (var bit = 0; bit < bits; bit++)
            {
                var value = DecodeBit(ref probabilities[index]);
                index = (index << 1) + value;
                symbol |= value << bit;
            }
            return symbol;
        }
    }

    private static InvalidDataException Damaged() => new("the LZMA data is damaged.");
}
