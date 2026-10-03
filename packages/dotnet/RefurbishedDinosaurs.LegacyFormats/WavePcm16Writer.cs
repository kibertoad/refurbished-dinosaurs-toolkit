using System.Buffers.Binary;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Writes canonical mono or stereo PCM16 RIFF/WAVE without host-endian assumptions.</summary>
public static class WavePcm16Writer
{
    /// <summary>Writes a header and frame-aligned little-endian PCM bytes, leaving the destination open.</summary>
    public static void Write(Stream destination, ReadOnlySpan<byte> samples, int channels, int sampleRate)
    {
        ArgumentNullException.ThrowIfNull(destination);
        if (!destination.CanWrite) throw new ArgumentException("Destination must be writable.", nameof(destination));
        if (channels is < 1 or > 2) throw new ArgumentOutOfRangeException(nameof(channels));
        if (sampleRate <= 0) throw new ArgumentOutOfRangeException(nameof(sampleRate));
        if (samples.Length % (channels * 2) != 0) throw new ArgumentException("Samples must contain complete frames.", nameof(samples));
        WriteHeader(destination, (uint)samples.Length, channels, sampleRate);
        destination.Write(samples);
    }

    /// <summary>Writes the 44-byte canonical header for <paramref name="dataLength"/> PCM bytes.</summary>
    internal static void WriteHeader(Stream destination, uint dataLength, int channels, int sampleRate)
    {
        Span<byte> header = stackalloc byte[44];
        "RIFF"u8.CopyTo(header);
        BinaryPrimitives.WriteUInt32LittleEndian(header[4..], checked(dataLength + 36));
        "WAVEfmt "u8.CopyTo(header[8..]);
        BinaryPrimitives.WriteUInt32LittleEndian(header[16..], 16);
        BinaryPrimitives.WriteUInt16LittleEndian(header[20..], 1);
        BinaryPrimitives.WriteUInt16LittleEndian(header[22..], (ushort)channels);
        BinaryPrimitives.WriteUInt32LittleEndian(header[24..], (uint)sampleRate);
        BinaryPrimitives.WriteUInt32LittleEndian(header[28..], checked((uint)((long)sampleRate * channels * 2)));
        BinaryPrimitives.WriteUInt16LittleEndian(header[32..], (ushort)(channels * 2));
        BinaryPrimitives.WriteUInt16LittleEndian(header[34..], 16);
        "data"u8.CopyTo(header[36..]);
        BinaryPrimitives.WriteUInt32LittleEndian(header[40..], dataLength);
        destination.Write(header);
    }
}
