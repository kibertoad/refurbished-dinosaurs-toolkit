using System.Buffers.Binary;

namespace RefurbishedDinosaurs.Media.Audio;

/// <summary>Writes interleaved signed PCM16 in explicit little-endian byte order.</summary>
public static class Pcm16
{
    /// <summary>Converts unsigned eight-bit samples without changing channel order.</summary>
    public static byte[] FromUnsigned8(ReadOnlySpan<byte> samples)
    {
        var bytes = new byte[checked(samples.Length * 2)];
        FromUnsigned8(samples, bytes);
        return bytes;
    }

    /// <summary>Converts into a caller buffer. Input and output must not overlap.</summary>
    public static void FromUnsigned8(ReadOnlySpan<byte> samples, Span<byte> destination)
    {
        if (destination.Length < checked(samples.Length * 2)) throw new ArgumentException("Destination is too short.", nameof(destination));
        if (samples.Overlaps(destination)) throw new ArgumentException("Buffers must not overlap.", nameof(destination));
        for (var index = 0; index < samples.Length; index++)
            BinaryPrimitives.WriteInt16LittleEndian(destination.Slice(index * 2, 2), (short)((samples[index] - 128) << 8));
    }

    /// <summary>Encodes native signed samples without assuming the host's byte order.</summary>
    public static byte[] Encode(ReadOnlySpan<short> samples)
    {
        var bytes = new byte[checked(samples.Length * 2)];
        for (var index = 0; index < samples.Length; index++)
            BinaryPrimitives.WriteInt16LittleEndian(bytes.AsSpan(index * 2, 2), samples[index]);
        return bytes;
    }
}
