using RefurbishedDinosaurs.Media.Smacker;
using System.Buffers.Binary;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class SmackerPcm16Tests
{
    [Fact]
    public void PackedMono8KeepsExistingSamples()
    {
        byte[] packet = [3, 0, 0, 0, 0x29, 0xA0, 0x02];
        var track = new SmackerAudioTrack(0, 22050, 4096, true, false, false);
        Assert.Equal(SmackerAudioDecoder.Decode(packet, track).ToPcm16LittleEndian(),
            SmackerAudioDecoder.DecodePcm16(packet, track).Samples);
    }

    [Theory]
    [InlineData(false, true)]
    [InlineData(true, false)]
    [InlineData(true, true)]
    public void DecodesSeedOrderResidualsAndWrapping(bool sixteen, bool stereo)
    {
        var packet = Packet(sixteen, stereo);
        var result = SmackerAudioDecoder.DecodePcm16(packet, new(0, 22050, 64, true, sixteen, stereo));
        Assert.Equal(stereo ? 2 : 1, result.Channels);
        // All trees emit delta 1 for the low byte and 0 for the high byte.
        short[] expected = sixteen
            ? stereo ? [(short)-1, (short)100, (short)0, (short)101] : [(short)-1, (short)0]
            : stereo ? [(short)32512, (short)-32768, (short)-32768, (short)-32512] : [(short)32512, (short)-32768];
        var bytes = new byte[expected.Length * 2];
        for (int i = 0; i < expected.Length; i++) BinaryPrimitives.WriteInt16LittleEndian(bytes.AsSpan(i * 2), expected[i]);
        Assert.Equal(bytes, result.Samples);
    }

    [Fact]
    public void EmptyTruncatedMismatchedAndExcessivePacketsAreExplicit()
    {
        var track = new SmackerAudioTrack(0, 22050, 64, true, true, false);
        Assert.Empty(SmackerAudioDecoder.DecodePcm16([0, 0, 0, 0, 0], track).Samples);
        Assert.Throws<InvalidDataException>(() => SmackerAudioDecoder.DecodePcm16([1, 0, 0, 0, 1], track));
        Assert.Throws<InvalidDataException>(() => SmackerAudioDecoder.DecodePcm16(Packet(false, false), track));
        Assert.Throws<InvalidDataException>(() => SmackerAudioDecoder.DecodePcm16([0, 0, 0, 2, 1], track));
        var packet = Packet(true, true);
        Assert.Throws<InvalidDataException>(() => SmackerAudioDecoder.DecodePcm16(packet[..^1], track with { IsStereo = true }));
        Assert.Throws<NotSupportedException>(() => SmackerAudioDecoder.DecodePcm16(packet, track with { IsCompressed = false }));
    }

    private static byte[] Packet(bool sixteen, bool stereo)
    {
        var bits = new List<int>();
        void Write(int value, int count)
        {
            for (int i = 0; i < count; i++) bits.Add((value >> i) & 1);
        }
        Write(1, 1); Write(stereo ? 1 : 0, 1); Write(sixteen ? 1 : 0, 1);
        int channels = stereo ? 2 : 1;
        int width = sixteen ? 2 : 1;
        for (int i = 0; i < channels * width; i++)
        {
            Write(0, 1); Write(0, 1); Write(sixteen && (i & 1) != 0 ? 0 : 1, 8); Write(0, 1);
        }
        if (stereo) Write(sixteen ? 0x6400 : 0, width * 8);
        Write(sixteen ? 0xFFFF : 255, width * 8);
        var packet = new byte[4 + (bits.Count + 7) / 8];
        BinaryPrimitives.WriteUInt32LittleEndian(packet, (uint)(2 * channels * width));
        for (int i = 0; i < bits.Count; i++) packet[4 + i / 8] |= (byte)(bits[i] << (i & 7));
        return packet;
    }
}
