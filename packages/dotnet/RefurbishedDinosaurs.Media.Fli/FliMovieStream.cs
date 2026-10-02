using System.Buffers.Binary;

namespace RefurbishedDinosaurs.Media.Fli;

/// <summary>Seekable access to 8-bit Autodesk FLI (AF11) animations, one frame at a time.</summary>
/// <remarks>FLC/AF12 is unsupported. An optional trailing ring frame is indexed but excluded from playback.</remarks>
public sealed class FliMovieStream : IDisposable
{
    private readonly Stream _source;
    private readonly bool _leaveOpen;
    private readonly List<(long Offset, int Length)> _frames = [];
    private bool _disposed;

    /// <summary>Maximum source length, 256 MiB.</summary>
    public const int MaximumSourceBytes = 256 * 1024 * 1024;
    /// <summary>Maximum encoded frame length, 64 MiB.</summary>
    public const int MaximumFrameBytes = 64 * 1024 * 1024;
    /// <summary>Width in indexed pixels.</summary>
    public int Width { get; }
    /// <summary>Height in indexed pixels.</summary>
    public int Height { get; }
    /// <summary>Declared playback frame count, excluding the ring frame.</summary>
    public int FrameCount { get; }
    /// <summary>Whether a trailing ring frame is present.</summary>
    public bool HasRingFrame => _frames.Count == FrameCount + 1;
    /// <summary>Header duration in 1/70-second units, or null when zero. A game may override it.</summary>
    public TimeSpan? HeaderFrameDuration { get; }
    /// <summary>Largest indexed record, for sizing a reusable buffer.</summary>
    public int MaximumFrameLength { get; }

    /// <summary>Reads the header and validates record extents without decoding their chunks.</summary>
    public FliMovieStream(Stream source, bool leaveOpen = false)
    {
        ArgumentNullException.ThrowIfNull(source);
        if (!source.CanRead || !source.CanSeek)
            throw new ArgumentException("FLI requires a readable, seekable stream.", nameof(source));
        if (source.Length is < 128 or > MaximumSourceBytes)
            throw new InvalidDataException("FLI source length is invalid.");
        _source = source;
        _leaveOpen = leaveOpen;
        source.Position = 0;
        Span<byte> header = stackalloc byte[128];
        source.ReadExactly(header);
        if (BinaryPrimitives.ReadUInt16LittleEndian(header[4..]) != 0xAF11)
            throw new NotSupportedException("Only AF11 FLI animations are supported.");
        if (BinaryPrimitives.ReadUInt32LittleEndian(header) != source.Length)
            throw new InvalidDataException("FLI declared file length is inconsistent.");
        FrameCount = BinaryPrimitives.ReadUInt16LittleEndian(header[6..]);
        Width = BinaryPrimitives.ReadUInt16LittleEndian(header[8..]);
        Height = BinaryPrimitives.ReadUInt16LittleEndian(header[10..]);
        if (FrameCount == 0 || Width is <= 0 or > 4096 || Height is <= 0 or > 4096
            || BinaryPrimitives.ReadUInt16LittleEndian(header[12..]) != 8)
            throw new InvalidDataException("FLI geometry, depth or frame count is invalid.");
        var speed = BinaryPrimitives.ReadUInt16LittleEndian(header[16..]);
        HeaderFrameDuration = speed == 0 ? null : TimeSpan.FromTicks((long)speed * TimeSpan.TicksPerSecond / 70);
        Span<byte> record = stackalloc byte[16];
        while (source.Position < source.Length)
        {
            var offset = source.Position;
            if (_frames.Count >= FrameCount + 1 || source.Length - offset < 16)
                throw new InvalidDataException("FLI record count or header is invalid.");
            source.ReadExactly(record);
            var length = BinaryPrimitives.ReadUInt32LittleEndian(record);
            if (length is < 16 or > MaximumFrameBytes || length > source.Length - offset
                || BinaryPrimitives.ReadUInt16LittleEndian(record[4..]) != 0xF1FA)
                throw new InvalidDataException("FLI record extent or type is invalid.");
            _frames.Add((offset, (int)length));
            source.Position = offset + length;
        }
        if (_frames.Count < FrameCount) throw new InvalidDataException("FLI frame records are missing.");
        MaximumFrameLength = _frames.Max(frame => frame.Length);
    }

    /// <summary>Reads a complete record, including its header. Index FrameCount accesses an optional ring frame.</summary>
    public int ReadFrame(int index, Span<byte> destination)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if ((uint)index >= _frames.Count) throw new ArgumentOutOfRangeException(nameof(index));
        var frame = _frames[index];
        if (destination.Length < frame.Length) throw new ArgumentException("FLI destination is too small.", nameof(destination));
        _source.Position = frame.Offset;
        _source.ReadExactly(destination[..frame.Length]);
        return frame.Length;
    }

    /// <summary>Closes the source unless leaveOpen was selected.</summary>
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (!_leaveOpen) _source.Dispose();
    }
}
