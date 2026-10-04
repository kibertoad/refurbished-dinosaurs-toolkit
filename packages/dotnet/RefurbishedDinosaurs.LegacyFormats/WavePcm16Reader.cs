namespace RefurbishedDinosaurs.LegacyFormats;

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
    /// <summary>
    /// Reads one RIFF/WAVE file from the stream's current position. Unknown chunks are skipped, the
    /// format and data chunks must each appear once, and the data must be a whole number of frames.
    /// </summary>
    /// <exception cref="ArgumentException">The stream is not readable and seekable.</exception>
    /// <exception cref="InvalidDataException">The file is malformed or is not 16-bit mono or stereo PCM.</exception>
    public static WavePcm16 Read(Stream stream)
    {
        using var reader = new WavePcm16Stream(stream, leaveOpen: true, MaximumSampleBytes);
        var samples = new byte[(int)reader.Length];
        reader.Read(samples);
        stream.Position = reader.ContainerEnd;
        return new(reader.SampleRate, reader.ChannelCount, samples);
    }
}
