using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class WavePcm16ReaderTests
{
    [Fact]
    public void ReadsPcmWithUnknownPaddedChunk()
    {
        using var stream = Wave(
            channels: 2,
            sampleRate: 22_050,
            samples: [1, 2, 3, 4, 5, 6, 7, 8],
            addPaddedChunk: true);

        var wave = WavePcm16Reader.Read(stream);

        Assert.Equal((22_050, 2), (wave.SampleRate, wave.ChannelCount));
        Assert.Equal([1, 2, 3, 4, 5, 6, 7, 8], wave.Samples);
        Assert.Equal(TimeSpan.FromSeconds(2d / 22_050), wave.Duration);
    }

    [Theory]
    [InlineData("signature")]
    [InlineData("container-size")]
    [InlineData("encoding")]
    [InlineData("channels")]
    [InlineData("sample-rate")]
    [InlineData("byte-rate")]
    [InlineData("bits")]
    [InlineData("data-alignment")]
    [InlineData("chunk-size")]
    public void RejectsMalformedWave(string mutation)
    {
        using var stream = Wave(2, 22_050, [1, 2, 3, 4]);
        var bytes = stream.ToArray();
        switch (mutation)
        {
            case "signature": bytes[0] = 0; break;
            case "container-size": Write32(bytes, 4, uint.MaxValue); break;
            case "encoding": Write16(bytes, 20, 3); break;
            case "channels": Write16(bytes, 22, 3); break;
            case "sample-rate": Write32(bytes, 24, 1); break;
            case "byte-rate": Write32(bytes, 28, 1); break;
            case "bits": Write16(bytes, 34, 8); break;
            case "data-alignment": Write32(bytes, 40, 3); break;
            case "chunk-size": Write32(bytes, 16, uint.MaxValue); break;
        }

        Assert.Throws<InvalidDataException>(() =>
            WavePcm16Reader.Read(new MemoryStream(bytes, writable: false)));
    }

    private static MemoryStream Wave(
        ushort channels,
        uint sampleRate,
        byte[] samples,
        bool addPaddedChunk = false)
    {
        var extra = addPaddedChunk ? 12 : 0;
        var bytes = new byte[44 + extra + samples.Length];
        "RIFF"u8.CopyTo(bytes);
        Write32(bytes, 4, checked((uint)(bytes.Length - 8)));
        "WAVEfmt "u8.CopyTo(bytes.AsSpan(8));
        Write32(bytes, 16, 16);
        Write16(bytes, 20, 1);
        Write16(bytes, 22, channels);
        Write32(bytes, 24, sampleRate);
        var blockAlign = checked((ushort)(channels * sizeof(short)));
        Write32(bytes, 28, checked(sampleRate * blockAlign));
        Write16(bytes, 32, blockAlign);
        Write16(bytes, 34, 16);
        var dataOffset = 36;
        if (addPaddedChunk)
        {
            "JUNK"u8.CopyTo(bytes.AsSpan(dataOffset));
            Write32(bytes, dataOffset + 4, 3);
            bytes[dataOffset + 8] = 1;
            bytes[dataOffset + 9] = 2;
            bytes[dataOffset + 10] = 3;
            dataOffset += 12;
        }
        "data"u8.CopyTo(bytes.AsSpan(dataOffset));
        Write32(bytes, dataOffset + 4, checked((uint)samples.Length));
        samples.CopyTo(bytes, dataOffset + 8);
        return new(bytes, writable: false);
    }

    private static void Write16(byte[] bytes, int offset, ushort value) =>
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(offset), value);

    private static void Write32(byte[] bytes, int offset, uint value) =>
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(offset), value);
}
