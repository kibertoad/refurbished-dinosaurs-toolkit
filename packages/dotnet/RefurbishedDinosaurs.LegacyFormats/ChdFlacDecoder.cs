using System.Buffers.Binary;

namespace RefurbishedDinosaurs.LegacyFormats;

// Decodes the FLAC frames of chdman's cdfl codec (RFC 9639): frames with no stream header, each of
// two channels of 16-bit samples, written out interleaved and big-endian, as a CD CHD stores its
// audio. Every frame's CRC-8 and CRC-16 are checked.
internal sealed class ChdFlacDecoder
{
    private const int MaximumBlockSize = 65535;
    private int[] left = new int[4096];
    private int[] right = new int[4096];

    // Fills output with output.Length / 4 stereo samples and returns the bytes of input the
    // frames took, so the caller finds what follows them.
    public int Decode(ReadOnlySpan<byte> input, Span<byte> output)
    {
        var total = output.Length / 4;
        var decoded = 0;
        var offset = 0;
        while (decoded < total)
        {
            var frame = new FlacBitReader(input[offset..]);
            var blockSize = DecodeFrame(ref frame, input[offset..], total - decoded);
            for (var index = 0; index < blockSize; index++)
            {
                var at = 4 * (decoded + index);
                BinaryPrimitives.WriteInt16BigEndian(output[at..], (short)left[index]);
                BinaryPrimitives.WriteInt16BigEndian(output[(at + 2)..], (short)right[index]);
            }
            decoded += blockSize;
            offset += frame.BytePosition;
        }
        return offset;
    }

    private int DecodeFrame(ref FlacBitReader bits, ReadOnlySpan<byte> data, int remaining)
    {
        if (bits.Read(15) != 0x7FFC) throw Damaged("a frame does not start with the FLAC sync code");
        bits.Read(1); // blocking strategy
        var blockCode = (int)bits.Read(4);
        var rateCode = (int)bits.Read(4);
        var channels = (int)bits.Read(4);
        var sizeCode = (int)bits.Read(3);
        if (bits.Read(1) != 0) throw Damaged("a frame header sets its reserved bit");
        // The frame or sample number, coded like UTF-8, is not needed.
        var first = (int)bits.Read(8);
        var extra = first < 0x80 ? 0 : first >= 0xFE ? 6 : first >= 0xFC ? 5 : first >= 0xF8 ? 4 : first >= 0xF0 ? 3 : first >= 0xE0 ? 2 : first >= 0xC0 ? 1 : -1;
        if (extra < 0) throw Damaged("a frame number is not coded correctly");
        for (var index = 0; index < extra; index++)
        {
            if (bits.Read(2) != 2) throw Damaged("a frame number is not coded correctly");
            bits.Read(6);
        }
        var blockSize = blockCode switch
        {
            0 => throw Damaged("a frame uses the reserved block size code"),
            1 => 192,
            >= 2 and <= 5 => 576 << (blockCode - 2),
            6 => (int)bits.Read(8) + 1,
            7 => (int)bits.Read(16) + 1,
            _ => 256 << (blockCode - 8),
        };
        switch (rateCode)
        {
            case 12: bits.Read(8); break;
            case 13 or 14: bits.Read(16); break;
            case 15: throw Damaged("a frame uses the invalid sample rate code");
        }
        if (sizeCode is not (0 or 4)) throw Damaged("a frame does not hold 16-bit samples");
        if (channels is not (1 or 8 or 9 or 10)) throw Damaged("a frame does not hold two channels");
        var headerLength = bits.BytePosition;
        if (bits.Overrun) throw Damaged("a frame header is truncated");
        if (Crc8(data[..headerLength]) != bits.Read(8)) throw Damaged("a frame header fails its CRC-8 check");
        if (blockSize > remaining || blockSize > MaximumBlockSize)
            throw Damaged("a frame holds more samples than the hunk");
        if (left.Length < blockSize)
        {
            left = new int[blockSize];
            right = new int[blockSize];
        }

        // The side channel of a stereo decorrelation carries one more bit.
        var leftBits = channels == 9 ? 17 : 16;
        var rightBits = channels is 8 or 10 ? 17 : 16;
        DecodeSubframe(ref bits, left.AsSpan(0, blockSize), leftBits);
        DecodeSubframe(ref bits, right.AsSpan(0, blockSize), rightBits);
        for (var index = 0; index < blockSize; index++)
        {
            int a = left[index], b = right[index];
            switch (channels)
            {
                case 8: right[index] = a - b; break;
                case 9: left[index] = a + b; break;
                case 10:
                    var mid = (a << 1) | (b & 1);
                    left[index] = (mid + b) >> 1;
                    right[index] = (mid - b) >> 1;
                    break;
            }
            if (left[index] is < short.MinValue or > short.MaxValue || right[index] is < short.MinValue or > short.MaxValue)
                throw Damaged("a sample does not fit in 16 bits");
        }
        bits.AlignToByte();
        var frameLength = bits.BytePosition;
        var crc = bits.Read(16);
        if (bits.Overrun) throw Damaged("a frame is truncated");
        if (Crc16Flac(data[..frameLength]) != crc) throw Damaged("a frame fails its CRC-16 check");
        return blockSize;
    }

    private static void DecodeSubframe(ref FlacBitReader bits, Span<int> samples, int sampleBits)
    {
        if (bits.Read(1) != 0) throw Damaged("a subframe sets its padding bit");
        var type = (int)bits.Read(6);
        var wasted = 0;
        if (bits.Read(1) != 0)
        {
            wasted = 1 + bits.ReadUnary(sampleBits);
            if (wasted >= sampleBits) throw Damaged("a subframe wastes every bit");
            sampleBits -= wasted;
        }
        if (type == 0)
            samples.Fill(bits.ReadSigned(sampleBits));
        else if (type == 1)
            for (var index = 0; index < samples.Length; index++) samples[index] = bits.ReadSigned(sampleBits);
        else if (type is >= 8 and <= 12)
            DecodeFixed(ref bits, samples, sampleBits, type - 8);
        else if (type >= 32)
            DecodeLpc(ref bits, samples, sampleBits, type - 31);
        else
            throw Damaged("a subframe uses a reserved type");
        if (bits.Overrun) throw Damaged("a subframe is truncated");
        if (wasted > 0)
            for (var index = 0; index < samples.Length; index++) samples[index] <<= wasted;
    }

    private static void DecodeFixed(ref FlacBitReader bits, Span<int> samples, int sampleBits, int order)
    {
        if (order > samples.Length) throw Damaged("a subframe's predictor order exceeds its block");
        for (var index = 0; index < order; index++) samples[index] = bits.ReadSigned(sampleBits);
        DecodeResidual(ref bits, samples, order);
        for (var index = order; index < samples.Length; index++)
        {
            long prediction = order switch
            {
                0 => 0,
                1 => samples[index - 1],
                2 => 2L * samples[index - 1] - samples[index - 2],
                3 => 3L * samples[index - 1] - 3L * samples[index - 2] + samples[index - 3],
                _ => 4L * samples[index - 1] - 6L * samples[index - 2] + 4L * samples[index - 3] - samples[index - 4],
            };
            samples[index] = Checked(prediction + samples[index]);
        }
    }

    private static void DecodeLpc(ref FlacBitReader bits, Span<int> samples, int sampleBits, int order)
    {
        if (order > samples.Length) throw Damaged("a subframe's predictor order exceeds its block");
        for (var index = 0; index < order; index++) samples[index] = bits.ReadSigned(sampleBits);
        var precision = (int)bits.Read(4) + 1;
        if (precision == 16) throw Damaged("a subframe uses the invalid coefficient precision");
        var shift = bits.ReadSigned(5);
        if (shift < 0) throw Damaged("a subframe uses a negative coefficient shift");
        Span<int> coefficients = stackalloc int[32];
        for (var index = 0; index < order; index++) coefficients[index] = bits.ReadSigned(precision);
        DecodeResidual(ref bits, samples, order);
        for (var index = order; index < samples.Length; index++)
        {
            long sum = 0;
            for (var tap = 0; tap < order; tap++) sum += (long)coefficients[tap] * samples[index - 1 - tap];
            samples[index] = Checked((sum >> shift) + samples[index]);
        }
    }

    // Reads the residual into samples[order..].
    private static void DecodeResidual(ref FlacBitReader bits, Span<int> samples, int order)
    {
        var method = (int)bits.Read(2);
        if (method > 1) throw Damaged("a residual uses a reserved coding method");
        var parameterBits = method == 0 ? 4 : 5;
        var escape = (1 << parameterBits) - 1;
        var partitionOrder = (int)bits.Read(4);
        var partitions = 1 << partitionOrder;
        if (samples.Length % partitions != 0 || samples.Length / partitions < order)
            throw Damaged("a residual's partitions do not fit its block");
        var index = order;
        for (var partition = 0; partition < partitions; partition++)
        {
            var count = samples.Length / partitions - (partition == 0 ? order : 0);
            var parameter = (int)bits.Read(parameterBits);
            if (parameter == escape)
            {
                var width = (int)bits.Read(5);
                for (var sample = 0; sample < count; sample++) samples[index++] = width == 0 ? 0 : bits.ReadSigned(width);
            }
            else
            {
                for (var sample = 0; sample < count; sample++)
                {
                    var quotient = (long)bits.ReadUnary(int.MaxValue);
                    var value = (quotient << parameter) | bits.Read(parameter);
                    if (value > uint.MaxValue) throw Damaged("a residual value is too large");
                    samples[index++] = (int)((value >> 1) ^ -(value & 1));
                }
            }
            if (bits.Overrun) throw Damaged("a residual is truncated");
        }
    }

    private static int Checked(long value) =>
        value is >= int.MinValue and <= int.MaxValue ? (int)value : throw Damaged("a predicted sample overflows");

    private static int Crc8(ReadOnlySpan<byte> data)
    {
        var crc = 0;
        foreach (var value in data)
        {
            crc ^= value;
            for (var bit = 0; bit < 8; bit++) crc = (crc & 0x80) != 0 ? ((crc << 1) ^ 0x07) & 0xFF : (crc << 1) & 0xFF;
        }
        return crc;
    }

    private static uint Crc16Flac(ReadOnlySpan<byte> data)
    {
        var crc = 0;
        foreach (var value in data)
        {
            crc ^= value << 8;
            for (var bit = 0; bit < 8; bit++) crc = (crc & 0x8000) != 0 ? ((crc << 1) ^ 0x8005) & 0xFFFF : (crc << 1) & 0xFFFF;
        }
        return (uint)crc;
    }

    private static InvalidDataException Damaged(string problem) => new($"the FLAC data is damaged: {problem}.");

    // Reads bits from the most significant end of each byte. Reads past the end give zeros and set
    // Overrun, which the decoder checks after each part.
    private ref struct FlacBitReader(ReadOnlySpan<byte> data)
    {
        private readonly ReadOnlySpan<byte> data = data;
        private long position;

        public readonly bool Overrun => position > (long)data.Length * 8;
        public readonly int BytePosition => (int)((position + 7) >> 3);

        public uint Read(int count)
        {
            ulong value = 0;
            while (count > 0)
            {
                var byteIndex = position >> 3;
                var available = 8 - (int)(position & 7);
                var take = Math.Min(available, count);
                uint current = byteIndex < data.Length ? data[(int)byteIndex] : 0u;
                value = (value << take) | ((current >> (available - take)) & ((1u << take) - 1));
                position += take;
                count -= take;
            }
            return (uint)value;
        }

        public int ReadSigned(int count)
        {
            if (count == 0) return 0;
            var value = Read(count);
            var shift = 32 - count;
            return (int)(value << shift) >> shift;
        }

        // Counts zero bits up to the next one bit, and reads that one.
        public int ReadUnary(int limit)
        {
            var zeros = 0;
            while (true)
            {
                if (position >= (long)data.Length * 8)
                {
                    position++;
                    return zeros;
                }
                // A whole zero byte at a byte boundary counts eight zeros at once.
                if ((position & 7) == 0 && data[(int)(position >> 3)] == 0)
                {
                    position += 8;
                    zeros += 8;
                }
                else if (Read(1) != 0) return zeros;
                else zeros++;
                if (zeros > limit) throw Damaged("a unary count is too long");
            }
        }

        public void AlignToByte() => position = (position + 7) & ~7L;
    }
}
