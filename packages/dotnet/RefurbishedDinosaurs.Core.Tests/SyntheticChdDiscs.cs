using RefurbishedDinosaurs.LegacyFormats;

namespace RefurbishedDinosaurs.Core.Tests;

// Deterministic synthetic discs for the CHD tests: the BIN images and cue sheets the committed CHD
// fixtures were made from with chdman (see Fixtures/Chd/README.md), rebuilt here so each test can
// compare a CHD with its source. Changing anything here changes the images, and the fixtures must
// then be made again. Integer arithmetic only, so every platform builds the same bytes.
internal static class SyntheticChdDiscs
{
    private const int RawSector = CueBinSheet.RawSectorSize;
    private const int CookedSector = 2048;

    // A MODE1/2352 data track of 30 sectors holding BuildIso's 23-sector volume, then two audio
    // tracks: track 02 with a 3-sector pregap stored as INDEX 00 and 10 sectors of audio, and track
    // 03 with 7. No track is a multiple of 4 sectors, so chdman pads each one. Every data sector
    // carries a correct EDC and ECC, so chdman's CD codecs remove and rebuild them.
    public const int MixedDataSectors = 30;
    public const int MixedTrack2Pregap = 3;
    public const int MixedTrack2Sectors = 10;
    public const int MixedTrack3Sectors = 7;

    public const string MixedCue =
        "FILE \"mixed.bin\" BINARY\n" +
        "  TRACK 01 MODE1/2352\n" +
        "    INDEX 01 00:00:00\n" +
        "  TRACK 02 AUDIO\n" +
        "    INDEX 00 00:00:30\n" +
        "    INDEX 01 00:00:33\n" +
        "  TRACK 03 AUDIO\n" +
        "    INDEX 01 00:00:43\n";

    // A MODE2/2352 data track of 25 CD-XA Form 1 sectors holding BuildIso's volume, with sector 19,
    // which the volume does not use, an empty Form 2 sector as a master leaves for padding.
    public const int DataMode2Sectors = 25;
    public const int DataMode2EmptyForm2Sector = 19;

    public const string DataMode2Cue =
        "FILE \"data-mode2.bin\" BINARY\n" +
        "  TRACK 01 MODE2/2352\n" +
        "    INDEX 01 00:00:00\n";

    // The file the volumes hold, 1500 bytes of a fixed pseudo-random sequence.
    public static byte[] Payload()
    {
        var payload = new byte[1500];
        var state = 0x1234567u;
        for (var index = 0; index < payload.Length; index++)
        {
            state = state * 1103515245 + 12345;
            payload[index] = (byte)(state >> 16);
        }
        return payload;
    }

    public static byte[] Iso() => OriginalContentSourceTests.BuildIso(Payload());

    public static byte[] MixedBin()
    {
        var iso = Iso();
        var image = new byte[(MixedDataSectors + MixedTrack2Pregap + MixedTrack2Sectors + MixedTrack3Sectors) * RawSector];
        for (var lba = 0; lba < MixedDataSectors; lba++)
        {
            var sector = image.AsSpan(lba * RawSector, RawSector);
            var data = lba * CookedSector < iso.Length ? iso.AsSpan(lba * CookedSector, CookedSector) : default;
            WriteMode1(sector, lba, data);
        }
        // The pregap is digital silence; the tracks hold different tones over low-level noise.
        WriteAudio(image.AsSpan((MixedDataSectors + MixedTrack2Pregap) * RawSector, MixedTrack2Sectors * RawSector), 97, 61, 1);
        WriteAudio(image.AsSpan((MixedDataSectors + MixedTrack2Pregap + MixedTrack2Sectors) * RawSector), 53, 141, 2);
        return image;
    }

    public static byte[] DataMode2Bin()
    {
        var iso = Iso();
        var image = new byte[DataMode2Sectors * RawSector];
        for (var lba = 0; lba < DataMode2Sectors; lba++)
        {
            var sector = image.AsSpan(lba * RawSector, RawSector);
            if (lba == DataMode2EmptyForm2Sector)
            {
                WriteHeader(sector, lba, 2);
                sector[18] = 0x20;
                sector[22] = 0x20;
                WriteEdc(sector, 16, 2332, 2348);
                continue;
            }
            var data = lba * CookedSector < iso.Length ? iso.AsSpan(lba * CookedSector, CookedSector) : default;
            WriteMode2Form1(sector, lba, data);
        }
        return image;
    }

    // Writes the BIN images, cue sheets and the .iso the fixtures are made from into directory.
    public static void WriteSources(string directory)
    {
        Directory.CreateDirectory(directory);
        File.WriteAllBytes(Path.Combine(directory, "mixed.bin"), MixedBin());
        File.WriteAllText(Path.Combine(directory, "mixed.cue"), MixedCue);
        File.WriteAllBytes(Path.Combine(directory, "data-mode2.bin"), DataMode2Bin());
        File.WriteAllText(Path.Combine(directory, "data-mode2.cue"), DataMode2Cue);
        File.WriteAllBytes(Path.Combine(directory, "data.iso"), Iso());
    }

    private static void WriteAudio(Span<byte> audio, int leftPeriod, int rightPeriod, uint seed)
    {
        var noise = seed * 2654435761u;
        for (var sample = 0; sample < audio.Length / 4; sample++)
        {
            noise = noise * 1664525 + 1013904223;
            var left = Triangle(sample, leftPeriod, 9000) + Triangle(sample, 7 * leftPeriod + 3, 4000) + (int)(noise >> 28) - 8;
            var right = Triangle(sample, rightPeriod, 7000) + (int)((noise >> 20) & 15) - 8;
            BitConverterLittleEndian(audio[(4 * sample)..], (short)left);
            BitConverterLittleEndian(audio[(4 * sample + 2)..], (short)right);
        }
    }

    private static void BitConverterLittleEndian(Span<byte> destination, short value)
    {
        destination[0] = (byte)value;
        destination[1] = (byte)(value >> 8);
    }

    private static int Triangle(int sample, int period, int amplitude)
    {
        var phase = sample % period;
        var half = period / 2;
        var rising = phase < half ? phase : period - phase;
        return (rising * 2 * amplitude / half) - amplitude;
    }

    private static void WriteHeader(Span<byte> sector, int lba, byte mode)
    {
        sector[1..11].Fill(0xFF);
        var address = lba + 150;
        sector[12] = Bcd(address / (60 * 75));
        sector[13] = Bcd(address / 75 % 60);
        sector[14] = Bcd(address % 75);
        sector[15] = mode;
    }

    private static void WriteMode1(Span<byte> sector, int lba, ReadOnlySpan<byte> data)
    {
        WriteHeader(sector, lba, 1);
        data.CopyTo(sector[16..]);
        WriteEdc(sector, 0, 2064, 2064);
        WriteEcc(sector);
    }

    private static void WriteMode2Form1(Span<byte> sector, int lba, ReadOnlySpan<byte> data)
    {
        WriteHeader(sector, lba, 2);
        byte[] subheader = [0, 0, 0x08, 0];
        subheader.CopyTo(sector[16..]);
        subheader.CopyTo(sector[20..]);
        data.CopyTo(sector[24..]);
        WriteEdc(sector, 16, 2056, 2072);
        WriteEcc(sector);
    }

    private static byte Bcd(int value) => checked((byte)(((value / 10) << 4) | (value % 10)));

    // The CD-ROM EDC, a CRC-32 over the given bytes, stored little-endian.
    private static void WriteEdc(Span<byte> sector, int start, int length, int at)
    {
        uint edc = 0;
        foreach (var value in sector.Slice(start, length))
        {
            edc ^= value;
            for (var bit = 0; bit < 8; bit++) edc = (edc & 1) != 0 ? (edc >> 1) ^ 0xD8018001 : edc >> 1;
        }
        sector[at] = (byte)edc;
        sector[at + 1] = (byte)(edc >> 8);
        sector[at + 2] = (byte)(edc >> 16);
        sector[at + 3] = (byte)(edc >> 24);
    }

    // The P and Q parity of ECMA-130 annex A, written out the long way: each parity pair solves
    // the Reed-Solomon code over GF(2^8) with the polynomial x^8+x^4+x^3+x^2+1. A MODE2 sector's
    // parity is computed with its address bytes taken as zero.
    private static void WriteEcc(Span<byte> sector)
    {
        // The header and data from byte 12, then the P parity once it is written.
        var symbols = new byte[2236];
        sector.Slice(12, 2064).CopyTo(symbols);
        if (sector[15] == 2) symbols.AsSpan(0, 4).Clear();
        var vector = new byte[43];
        for (var column = 0; column < 86; column++)
        {
            for (var row = 0; row < 24; row++) vector[row] = symbols[column + 86 * row];
            var (p0, p1) = Parity(vector.AsSpan(0, 24));
            sector[2076 + column] = symbols[2064 + column] = p0;
            sector[2076 + 86 + column] = symbols[2064 + 86 + column] = p1;
        }
        for (var diagonal = 0; diagonal < 52; diagonal++)
        {
            for (var row = 0; row < 43; row++)
                vector[row] = symbols[2 * (((diagonal >> 1) * 43 + row * 44) % 1118) + (diagonal & 1)];
            var (q0, q1) = Parity(vector);
            sector[2248 + diagonal] = q0;
            sector[2248 + 52 + diagonal] = q1;
        }
    }

    // The two parity bytes that make sum(v_i) + p0 + p1 = 0 and sum(v_i * a^(n+1-i)) + p0 * a + p1 = 0,
    // where v_i is the i-th of n symbols and a the field's generator.
    private static (byte, byte) Parity(ReadOnlySpan<byte> symbols)
    {
        var count = symbols.Length;
        int sum = 0, weighted = 0;
        for (var index = 0; index < count; index++)
        {
            var value = symbols[index];
            sum ^= value;
            weighted ^= Multiply(value, Power(count + 1 - index));
        }
        // p0 + p1 = sum and a*p0 + p1 = weighted, so (a + 1) * p0 = sum + weighted.
        var p0 = Multiply(sum ^ weighted, Inverse(Power(1) ^ 1));
        return ((byte)p0, (byte)(p0 ^ sum));
    }

    private static int Multiply(int a, int b)
    {
        var result = 0;
        while (b != 0)
        {
            if ((b & 1) != 0) result ^= a;
            a <<= 1;
            if ((a & 0x100) != 0) a ^= 0x11D;
            b >>= 1;
        }
        return result;
    }

    private static int Power(int exponent)
    {
        var result = 1;
        for (var index = 0; index < exponent; index++) result = Multiply(result, 2);
        return result;
    }

    private static int Inverse(int value)
    {
        for (var candidate = 1; candidate < 256; candidate++)
            if (Multiply(value, candidate) == 1) return candidate;
        throw new ArgumentOutOfRangeException(nameof(value));
    }
}
