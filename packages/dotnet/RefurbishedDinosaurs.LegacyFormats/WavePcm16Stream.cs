using System.Buffers.Binary;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Indexes PCM16 WAVE chunks and reads frame-aligned buffers without loading the track.</summary>
public sealed class WavePcm16Stream : IDisposable
{
    private readonly Stream _stream;
    private readonly bool _leaveOpen;
    private long _dataStart;
    private bool _disposed;
    internal long ContainerEnd { get; private set; }
    /// <summary>Frames per second.</summary>
    public int SampleRate { get; private set; }
    /// <summary>One or two channels.</summary>
    public int ChannelCount { get; private set; }
    /// <summary>Admitted PCM byte length.</summary>
    public long Length { get; private set; }
    /// <summary>Current byte offset within PCM data.</summary>
    public long Position { get; private set; }

    /// <summary>
    /// Indexes from the current stream position. Takes ownership unless leaveOpen is true,
    /// including when indexing fails. The caller sets the maximum admitted track size.
    /// </summary>
    public WavePcm16Stream(Stream stream, bool leaveOpen = false, long maximumSampleBytes = uint.MaxValue)
    {
        ArgumentNullException.ThrowIfNull(stream);
        _stream = stream;
        _leaveOpen = leaveOpen;
        try
        {
            ArgumentOutOfRangeException.ThrowIfNegativeOrZero(maximumSampleBytes);
            Index(stream, maximumSampleBytes);
        }
        catch { if (!leaveOpen) stream.Dispose(); throw; }
    }

    /// <summary>Reads complete frames, returning zero at the end; requires a frame-aligned buffer.</summary>
    public int Read(Span<byte> destination)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (destination.Length % (ChannelCount * 2) != 0) throw new ArgumentException("Buffer must hold complete frames.", nameof(destination));
        var count = (int)Math.Min(destination.Length, Length - Position);
        _stream.Position = _dataStart + Position;
        _stream.ReadExactly(destination[..count]);
        Position += count;
        return count;
    }

    /// <summary>Fills a frame-aligned buffer, wrapping to the first frame at track end.</summary>
    public void ReadLooped(Span<byte> destination)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (destination.Length % (ChannelCount * 2) != 0) throw new ArgumentException("Buffer must hold complete frames.", nameof(destination));
        while (!destination.IsEmpty)
        {
            if (Position == Length) Position = 0;
            var count = Read(destination);
            destination = destination[count..];
        }
    }

    /// <summary>Seeks to a frame-aligned byte offset within the track, including its end.</summary>
    public void Seek(long position)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (position < 0 || position > Length || position % (ChannelCount * 2) != 0)
            throw new ArgumentOutOfRangeException(nameof(position));
        Position = position;
    }

    /// <summary>Disposes the owned stream once.</summary>
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (!_leaveOpen) _stream.Dispose();
    }
    private const uint Riff = 0x46464952;
    private const uint Wave = 0x45564157;
    private const uint Format = 0x20746d66;
    private const uint Data = 0x61746164;

    private void Index(Stream stream, long maximumSampleBytes)
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
        long? dataStart = null;
        long dataLength = 0;
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
                if (dataStart is not null)
                    throw new InvalidDataException("WAVE contains duplicate data chunks.");
                if (size == 0 || size > maximumSampleBytes)
                    throw new InvalidDataException("WAVE sample data size is invalid.");
                dataStart = stream.Position;
                dataLength = size;
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
        if (dataStart is null) throw new InvalidDataException("WAVE data chunk is missing.");
        if (dataLength % format.BlockAlign != 0)
            throw new InvalidDataException("WAVE sample data is not frame-aligned.");
        ContainerEnd = riffEnd;
        SampleRate = format.SampleRate;
        ChannelCount = format.Channels;
        Length = dataLength;
        _dataStart = dataStart.Value;
        stream.Position = _dataStart;
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
