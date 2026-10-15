using System.Buffers.Binary;
using System.Security.Cryptography;

namespace RefurbishedDinosaurs.Core.Tests;

// What the writer declares in STREAMINFO, so a test can write a file the reader refuses. The frames
// always hold the samples given, as 16-bit stereo.
internal sealed record SyntheticFlacOptions
{
    public int BlockSize { get; init; } = 4096;
    public int SampleRate { get; init; } = 44100;
    public int Channels { get; init; } = 2;
    public int BitsPerSample { get; init; } = 16;
    // The sample count STREAMINFO declares, or null for the true count.
    public long? DeclaredSamples { get; init; }
    // Record the samples' MD5, or leave it all zeros as an encoder that does not compute it does.
    public bool Md5 { get; init; } = true;
    // XORs 1 into the samples' MD5 before it is written.
    public bool WrongMd5 { get; init; }
}

// Writes FLAC files (RFC 9639) for tests: a STREAMINFO and a PADDING block, then frames of 16-bit
// stereo samples. The frames cycle through independent, left/side and mid/side stereo, and each
// subframe is constant, verbatim, or a fixed predictor of order 1 or 2 with a Rice-coded residual,
// so a decoder meets the subframe types and stereo decorrelations an encoder writes.
internal static class SyntheticFlacWriter
{
    public static byte[] Write(byte[] samples, SyntheticFlacOptions? options = null)
    {
        options ??= new SyntheticFlacOptions();
        var count = samples.Length / 4;
        var left = new int[count];
        var right = new int[count];
        for (var index = 0; index < count; index++)
        {
            left[index] = BinaryPrimitives.ReadInt16LittleEndian(samples.AsSpan(4 * index));
            right[index] = BinaryPrimitives.ReadInt16LittleEndian(samples.AsSpan(4 * index + 2));
        }

        var output = new List<byte>("fLaC"u8.ToArray());
        var info = new BitWriter();
        info.Write(options.BlockSize, 16);
        info.Write(options.BlockSize, 16);
        info.Write(0, 24);
        info.Write(0, 24);
        info.Write(options.SampleRate, 20);
        info.Write(options.Channels - 1, 3);
        info.Write(options.BitsPerSample - 1, 5);
        info.Write(options.DeclaredSamples ?? count, 36);
        var md5 = options.Md5 ? MD5.HashData(samples.AsSpan(0, count * 4)) : new byte[16];
        if (options.WrongMd5) md5[0] ^= 1;
        foreach (var value in md5) info.Write(value, 8);
        output.AddRange([0x00, 0, 0, 34]);
        output.AddRange(info.ToArray());
        output.AddRange([0x81, 0, 0, 16]);
        output.AddRange(new byte[16]);

        for (int start = 0, number = 0; start < count; start += options.BlockSize, number++)
        {
            var size = Math.Min(options.BlockSize, count - start);
            output.AddRange(Frame(number, left.AsSpan(start, size), right.AsSpan(start, size)));
        }
        return [.. output];
    }

    private static byte[] Frame(int number, ReadOnlySpan<int> left, ReadOnlySpan<int> right)
    {
        var size = left.Length;
        var assignment = (number % 3) switch { 0 => 1, 1 => 8, _ => 10 };
        int[] first = new int[size], second = new int[size];
        for (var index = 0; index < size; index++)
        {
            int l = left[index], r = right[index];
            (first[index], second[index]) = assignment switch
            {
                1 => (l, r),
                8 => (l, l - r),
                _ => ((l + r) >> 1, l - r),
            };
        }

        var bits = new BitWriter();
        bits.Write(0xFFF8, 16);
        bits.Write(7, 4); // block size: 16 bits at the end of the header
        bits.Write(9, 4); // 44.1 kHz
        bits.Write(assignment, 4);
        bits.Write(4, 3); // 16 bits a sample
        bits.Write(0, 1);
        if (number < 0x80) bits.Write(number, 8);
        else
        {
            bits.Write(0xC0 | number >> 6, 8);
            bits.Write(0x80 | number & 0x3F, 8);
        }
        bits.Write(size - 1, 16);
        bits.Write(Crc8(bits.ToArray()), 8);
        Subframe(bits, first, 16, number);
        Subframe(bits, second, assignment == 1 ? 16 : 17, number + 1);
        bits.Align();
        var body = bits.ToArray();
        var crc = Crc16(body);
        return [.. body, (byte)(crc >> 8), (byte)crc];
    }

    private static void Subframe(BitWriter bits, int[] samples, int sampleBits, int choice)
    {
        bits.Write(0, 1);
        if (samples.All(sample => sample == samples[0]))
        {
            bits.Write(0, 6);
            bits.Write(0, 1);
            bits.Write(samples[0], sampleBits);
            return;
        }
        var order = samples.Length < 3 ? 0 : choice % 3;
        if (order == 0)
        {
            bits.Write(1, 6);
            bits.Write(0, 1);
            foreach (var sample in samples) bits.Write(sample, sampleBits);
            return;
        }
        bits.Write(8 + order, 6);
        bits.Write(0, 1);
        for (var index = 0; index < order; index++) bits.Write(samples[index], sampleBits);
        var residual = new long[samples.Length - order];
        for (var index = order; index < samples.Length; index++)
        {
            long prediction = order == 1 ? samples[index - 1] : 2L * samples[index - 1] - samples[index - 2];
            residual[index - order] = samples[index] - prediction;
        }
        // One partition with a Rice parameter near the log of the mean folded residual.
        var mean = residual.Select(value => (ulong)(value << 1 ^ value >> 63)).Aggregate(0UL, (sum, value) => sum + value) /
                   (ulong)residual.Length;
        var parameter = 0;
        while (parameter < 14 && 1UL << (parameter + 1) <= mean) parameter++;
        bits.Write(0, 2);
        bits.Write(0, 4);
        bits.Write(parameter, 4);
        foreach (var value in residual)
        {
            var folded = (ulong)(value << 1 ^ value >> 63);
            for (var zero = folded >> parameter; zero > 0; zero--) bits.Write(0, 1);
            bits.Write(1, 1);
            bits.Write((long)(folded & ((1UL << parameter) - 1)), parameter);
        }
    }

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

    private static int Crc16(ReadOnlySpan<byte> data)
    {
        var crc = 0;
        foreach (var value in data)
        {
            crc ^= value << 8;
            for (var bit = 0; bit < 8; bit++) crc = (crc & 0x8000) != 0 ? ((crc << 1) ^ 0x8005) & 0xFFFF : (crc << 1) & 0xFFFF;
        }
        return crc;
    }

    // Writes bits from the most significant end of each byte.
    private sealed class BitWriter
    {
        private readonly List<byte> bytes = [];
        private int current;
        private int used;

        // Writes the low count bits of value, most significant first.
        public void Write(long value, int count)
        {
            for (var bit = count - 1; bit >= 0; bit--)
            {
                current = current << 1 | (int)(value >> bit & 1);
                if (++used == 8)
                {
                    bytes.Add((byte)current);
                    current = used = 0;
                }
            }
        }

        public void Align()
        {
            while (used != 0) Write(0, 1);
        }

        // The whole bytes written so far.
        public byte[] ToArray() => [.. bytes];
    }
}
