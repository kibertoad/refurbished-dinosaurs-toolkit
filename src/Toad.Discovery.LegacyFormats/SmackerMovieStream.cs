using System.Buffers.Binary;

namespace Toad.Discovery.LegacyFormats;

/// <summary>Seekable, bounded access to one Smacker index/tree prefix and one frame at a time.</summary>
public sealed class SmackerMovieStream : IDisposable
{
    private const int HeaderSize = 104;
    private const int MaximumMovieBytes = 256 * 1024 * 1024;
    private const int MaximumFrames = 1_000_000;
    private readonly Stream _source;
    private readonly bool _leaveOpen;
    private bool _disposed;

    public SmackerMovie Movie { get; }
    public byte[] TreeData { get; }
    public int MaximumFrameLength { get; }

    public SmackerMovieStream(Stream source, bool leaveOpen = false)
    {
        ArgumentNullException.ThrowIfNull(source);
        if (!source.CanRead || !source.CanSeek)
            throw new ArgumentException("Smacker streaming requires a readable, seekable stream.", nameof(source));
        if (source.Length is < HeaderSize or > MaximumMovieBytes)
            throw new InvalidDataException("Smacker movie has an invalid length.");

        _source = source;
        _leaveOpen = leaveOpen;
        var header = new byte[HeaderSize];
        source.Position = 0;
        source.ReadExactly(header);
        var declaredFrames = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(12, 4));
        var flags = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(20, 4));
        var frameCount = checked((long)declaredFrames + ((flags & 1) != 0 ? 1 : 0));
        if (frameCount is <= 0 or > MaximumFrames)
            throw new InvalidDataException("Smacker frame count is invalid.");
        var treeLength = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(52, 4));
        var prefixLength = checked((long)HeaderSize + frameCount * 5 + treeLength);
        if (prefixLength > source.Length || prefixLength > int.MaxValue)
            throw new InvalidDataException("Smacker frame tables or trees are truncated.");

        var prefix = new byte[checked((int)prefixLength)];
        header.CopyTo(prefix, 0);
        source.ReadExactly(prefix.AsSpan(HeaderSize));
        Movie = SmackerMovieDecoder.DecodeIndex(prefix, checked((int)source.Length));
        TreeData = prefix.AsSpan(Movie.TreeOffset, Movie.TreeLength).ToArray();
        MaximumFrameLength = Movie.Frames.Max(frame => frame.Length);
    }

    public int ReadFrame(int frameIndex, Span<byte> destination)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if ((uint)frameIndex >= Movie.Frames.Count)
            throw new ArgumentOutOfRangeException(nameof(frameIndex));
        var frame = Movie.Frames[frameIndex];
        if (destination.Length < frame.Length)
            throw new ArgumentException("Smacker frame destination is too small.", nameof(destination));
        _source.Position = frame.Offset;
        _source.ReadExactly(destination[..frame.Length]);
        return frame.Length;
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (!_leaveOpen) _source.Dispose();
    }
}
