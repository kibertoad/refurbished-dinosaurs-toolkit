using System.Buffers.Binary;

namespace ScientificMethod.LegacyFormats;

/// <summary>Decoded 16-bit PCM audio.</summary>
/// <param name="SampleRate">Frames per second.</param>
/// <param name="ChannelCount">1 or 2.</param>
/// <param name="Samples">Little-endian 16-bit samples, channels interleaved, as stored in the file.</param>
public sealed record WavePcm16(
    int SampleRate,
    int ChannelCount,
    byte[] Samples)
{
    /// <summary>The playing time of <see cref="Samples"/>.</summary>
    public TimeSpan Duration => TimeSpan.FromSeconds(
        Samples.Length / (double)(SampleRate * ChannelCount * sizeof(short)));
}

/// <summary>Reads RIFF/WAVE files holding 16-bit mono or stereo PCM.</summary>
public static class WavePcm16Reader
{
    /// <summary>The largest data chunk accepted.</summary>
    public const int MaximumSampleBytes = 256 * 1024 * 1024;
    private const uint Riff = 0x46464952;
    private const uint Wave = 0x45564157;
    private const uint Format = 0x20746d66;
    private const uint Data = 0x61746164;

    /// <summary>
    /// Reads one RIFF/WAVE file from the stream's current position. Unknown chunks are skipped, the
    /// format and data chunks must each appear once, and the data must be a whole number of frames.
    /// </summary>
    /// <exception cref="ArgumentException">The stream is not readable and seekable.</exception>
    /// <exception cref="InvalidDataException">The file is malformed or is not 16-bit mono or stereo PCM.</exception>
    public static WavePcm16 Read(Stream stream)
    {
        ArgumentNullException.ThrowIfNull(stream);
        if (!stream.CanRead || !stream.CanSeek)
            throw new ArgumentException("WAVE input must be readable and seekable.", nameof(stream));
        var start = stream.Position;
        if (stream.Length - start < 12)
            throw new InvalidDataException("WAVE header is truncated.");
        Span<byte> header = stackalloc byte[12];
        stream.ReadExactly(header);
        if (BinaryPrimitives.ReadUInt32LittleEndian(header) != Riff ||
            BinaryPrimitives.ReadUInt32LittleEndian(header[8..]) != Wave)
            throw new InvalidDataException("Input is not a RIFF/WAVE stream.");
        var riffSize = BinaryPrimitives.ReadUInt32LittleEndian(header[4..]);
        var riffEnd = checked(start + 8L + riffSize);
        if (riffEnd > stream.Length || riffEnd < stream.Position)
            throw new InvalidDataException("RIFF size lies outside the input stream.");

        WaveFormat? format = null;
        byte[]? samples = null;
        Span<byte> chunkHeader = stackalloc byte[8];
        while (stream.Position < riffEnd)
        {
            if (riffEnd - stream.Position < chunkHeader.Length)
                throw new InvalidDataException("WAVE chunk header is truncated.");
            stream.ReadExactly(chunkHeader);
            var id = BinaryPrimitives.ReadUInt32LittleEndian(chunkHeader);
            var size = BinaryPrimitives.ReadUInt32LittleEndian(chunkHeader[4..]);
            var chunkEnd = checked(stream.Position + size);
            if (chunkEnd > riffEnd)
                throw new InvalidDataException("WAVE chunk lies outside the RIFF container.");
            if (id == Format)
            {
                if (format is not null)
                    throw new InvalidDataException("WAVE contains duplicate format chunks.");
                format = ReadFormat(stream, size);
            }
            else if (id == Data)
            {
                if (samples is not null)
                    throw new InvalidDataException("WAVE contains duplicate data chunks.");
                if (size is 0 or > MaximumSampleBytes)
                    throw new InvalidDataException("WAVE sample data size is invalid.");
                samples = new byte[checked((int)size)];
                stream.ReadExactly(samples);
            }
            stream.Position = chunkEnd;
            if ((size & 1) != 0)
            {
                if (stream.Position >= riffEnd)
                    throw new InvalidDataException("WAVE chunk padding is truncated.");
                stream.Position++;
            }
        }
        if (format is null) throw new InvalidDataException("WAVE format chunk is missing.");
        if (samples is null) throw new InvalidDataException("WAVE data chunk is missing.");
        if (samples.Length % format.BlockAlign != 0)
            throw new InvalidDataException("WAVE sample data is not frame-aligned.");
        return new(format.SampleRate, format.Channels, samples);
    }

    private static WaveFormat ReadFormat(Stream stream, uint size)
    {
        if (size < 16) throw new InvalidDataException("WAVE format chunk is truncated.");
        Span<byte> data = stackalloc byte[16];
        stream.ReadExactly(data);
        var encoding = BinaryPrimitives.ReadUInt16LittleEndian(data);
        var channels = BinaryPrimitives.ReadUInt16LittleEndian(data[2..]);
        var sampleRate = BinaryPrimitives.ReadUInt32LittleEndian(data[4..]);
        var byteRate = BinaryPrimitives.ReadUInt32LittleEndian(data[8..]);
        var blockAlign = BinaryPrimitives.ReadUInt16LittleEndian(data[12..]);
        var bitsPerSample = BinaryPrimitives.ReadUInt16LittleEndian(data[14..]);
        if (encoding != 1 || channels is < 1 or > 2 ||
            sampleRate is < 8_000 or > 48_000 || bitsPerSample != 16)
            throw new InvalidDataException("Only 16-bit mono or stereo PCM WAVE audio is supported.");
        var expectedBlockAlign = checked((ushort)(channels * sizeof(short)));
        var expectedByteRate = checked(sampleRate * expectedBlockAlign);
        if (blockAlign != expectedBlockAlign || byteRate != expectedByteRate)
            throw new InvalidDataException("WAVE format rates are inconsistent.");
        return new(checked((int)sampleRate), channels, blockAlign);
    }

    private sealed record WaveFormat(int SampleRate, ushort Channels, ushort BlockAlign);
}
