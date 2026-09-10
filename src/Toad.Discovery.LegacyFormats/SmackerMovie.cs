using System.Buffers.Binary;

namespace Toad.Discovery.LegacyFormats;

public sealed record SmackerAudioTrack(
    int Index, int SampleRate, int MaximumDecodedBytes, bool IsCompressed, bool Is16Bit, bool IsStereo);

public sealed record SmackerFrame(int Index, int Offset, int Length, byte Flags, bool IsKeyFrame);

public sealed record SmackerDataSegment(int Offset, int Length);

public sealed record SmackerAudioPacket(int TrackIndex, int DecodedLength, SmackerDataSegment Data);

public sealed record SmackerFrameLayout(
    bool PaletteChanged,
    byte[] Palette,
    IReadOnlyList<SmackerAudioPacket> AudioPackets,
    SmackerDataSegment Video);

public sealed record SmackerTreeSizes(int MMap, int MClr, int Full, int Type);

public sealed record SmackerMovie(
    int Version,
    int Width,
    int Height,
    TimeSpan FrameDuration,
    uint Flags,
    int TreeOffset,
    int TreeLength,
    SmackerTreeSizes TreeSizes,
    IReadOnlyList<SmackerAudioTrack> AudioTracks,
    IReadOnlyList<SmackerFrame> Frames);

public static class SmackerMovieDecoder
{
    private const uint Smk2Magic = 0x324B4D53;
    private const uint Smk4Magic = 0x344B4D53;
    private const int HeaderSize = 104;
    private const int AudioTrackCount = 7;
    private const int MaximumMovieBytes = 256 * 1024 * 1024;
    private const int MaximumDimension = 4096;
    private const int MaximumFrames = 1_000_000;

    public static SmackerFrameLayout DecodeFrameLayout(
        SmackerMovie movie, int frameIndex, ReadOnlySpan<byte> source, ReadOnlySpan<byte> previousPalette)
    {
        ArgumentNullException.ThrowIfNull(movie);
        if ((uint)frameIndex >= movie.Frames.Count)
            throw new ArgumentOutOfRangeException(nameof(frameIndex));
        if (previousPalette.Length != 256 * 3)
            throw new ArgumentException("Smacker palette must contain exactly 256 RGB entries.", nameof(previousPalette));

        var frame = movie.Frames[frameIndex];
        if (frame.Offset < 0 || frame.Length <= 0 || frame.Offset > source.Length - frame.Length)
            throw new InvalidDataException("Smacker frame lies outside the supplied movie.");
        return DecodeFramePayloadCore(movie, frame,
            source.Slice(frame.Offset, frame.Length), previousPalette, frame.Offset);
    }

    public static SmackerFrameLayout DecodeFramePayload(
        SmackerMovie movie, int frameIndex, ReadOnlySpan<byte> framePayload, ReadOnlySpan<byte> previousPalette)
    {
        ArgumentNullException.ThrowIfNull(movie);
        if ((uint)frameIndex >= movie.Frames.Count)
            throw new ArgumentOutOfRangeException(nameof(frameIndex));
        var frame = movie.Frames[frameIndex];
        if (framePayload.Length != frame.Length)
            throw new InvalidDataException("Smacker frame payload length is inconsistent with its index.");
        return DecodeFramePayloadCore(movie, frame, framePayload, previousPalette, 0);
    }

    private static SmackerFrameLayout DecodeFramePayloadCore(
        SmackerMovie movie, SmackerFrame frame, ReadOnlySpan<byte> source,
        ReadOnlySpan<byte> previousPalette, int segmentBase)
    {
        if (previousPalette.Length != 256 * 3)
            throw new ArgumentException("Smacker palette must contain exactly 256 RGB entries.", nameof(previousPalette));
        var palette = previousPalette.ToArray();
        var cursor = 0;
        var end = source.Length;
        var paletteChanged = (frame.Flags & 1) != 0;
        if (paletteChanged)
        {
            var paletteLength = checked(source[cursor] * 4);
            if (paletteLength == 0 || paletteLength > end - cursor)
                throw new InvalidDataException("Smacker palette packet is invalid.");
            DecodePalette(source.Slice(cursor + 1, paletteLength - 1), previousPalette, palette);
            cursor += paletteLength;
        }

        var audioPackets = new List<SmackerAudioPacket>();
        for (var trackIndex = 0; trackIndex < AudioTrackCount; trackIndex++)
        {
            if ((frame.Flags & (2 << trackIndex)) == 0) continue;
            var track = movie.AudioTracks.SingleOrDefault(candidate => candidate.Index == trackIndex)
                ?? throw new InvalidDataException("Smacker frame references an undeclared audio track.");
            if (end - cursor < 4)
                throw new InvalidDataException("Smacker audio packet header is truncated.");
            var packetLength = ReadUInt32(source, cursor);
            if (packetLength < 4 || packetLength > int.MaxValue || packetLength > end - cursor)
                throw new InvalidDataException("Smacker audio packet extent is invalid.");
            var dataOffset = checked(cursor + 4);
            var dataLength = checked((int)packetLength - 4);
            var decodedLength = dataLength;
            if (track.IsCompressed)
            {
                if (dataLength < 4)
                    throw new InvalidDataException("Smacker packed-audio packet is truncated.");
                var declaredLength = ReadUInt32(source, dataOffset);
                if (declaredLength == 0 || declaredLength > int.MaxValue
                    || track.MaximumDecodedBytes > 0 && declaredLength > track.MaximumDecodedBytes)
                    throw new InvalidDataException("Smacker packed-audio output length is invalid.");
                decodedLength = checked((int)declaredLength);
            }
            audioPackets.Add(new SmackerAudioPacket(trackIndex, decodedLength,
                new SmackerDataSegment(checked(segmentBase + dataOffset), dataLength)));
            cursor += checked((int)packetLength);
        }

        if (cursor >= end)
            throw new InvalidDataException("Smacker frame has no video payload.");
        return new SmackerFrameLayout(paletteChanged, palette, audioPackets,
            new SmackerDataSegment(checked(segmentBase + cursor), end - cursor));
    }

    public static SmackerMovie Decode(ReadOnlySpan<byte> source) => DecodeIndex(source, source.Length);

    internal static SmackerMovie DecodeIndex(ReadOnlySpan<byte> source, int sourceLength)
    {
        if (sourceLength < HeaderSize || sourceLength > MaximumMovieBytes || source.Length < HeaderSize
            || source.Length > sourceLength)
            throw new InvalidDataException("Smacker movie has an invalid length.");

        var magic = ReadUInt32(source, 0);
        var version = magic switch
        {
            Smk2Magic => 2,
            Smk4Magic => 4,
            _ => throw new InvalidDataException("Smacker movie magic is invalid.")
        };
        var width = ReadBoundedInt(source, 4, MaximumDimension, "width");
        var height = ReadBoundedInt(source, 8, MaximumDimension, "height");
        if ((width & 3) != 0 || (height & 3) != 0)
            throw new InvalidDataException("Smacker dimensions must align to four-pixel blocks.");

        var declaredFrames = ReadUInt32(source, 12);
        var rawFrameDuration = BinaryPrimitives.ReadInt32LittleEndian(source.Slice(16, 4));
        var flags = ReadUInt32(source, 20);
        var frameCount = checked((long)declaredFrames + ((flags & 1) != 0 ? 1 : 0));
        if (frameCount is <= 0 or > MaximumFrames || rawFrameDuration is 0 or int.MinValue)
            throw new InvalidDataException("Smacker timing or frame count is invalid.");

        var durationTicks = rawFrameDuration > 0
            ? checked((long)rawFrameDuration * TimeSpan.TicksPerMillisecond)
            : checked(-(long)rawFrameDuration * TimeSpan.TicksPerSecond / 100_000);
        if (durationTicks <= 0)
            throw new InvalidDataException("Smacker frame duration is invalid.");

        var audioTracks = new List<SmackerAudioTrack>();
        for (var index = 0; index < AudioTrackCount; index++)
        {
            var maximumBytes = ReadUInt32(source, 24 + index * 4);
            var rateAndFlags = ReadUInt32(source, 72 + index * 4);
            var sampleRate = checked((int)(rateAndFlags & 0x00FF_FFFF));
            var audioFlags = (byte)(rateAndFlags >> 24);
            if (maximumBytes > MaximumMovieBytes)
                throw new InvalidDataException("Smacker audio buffer is excessive.");
            if (sampleRate == 0) continue;
            if (sampleRate is < 1_000 or > 192_000)
                throw new InvalidDataException("Smacker audio sample rate is invalid.");
            audioTracks.Add(new SmackerAudioTrack(index, sampleRate, checked((int)maximumBytes),
                (audioFlags & 0x80) != 0, (audioFlags & 0x20) != 0, (audioFlags & 0x10) != 0));
        }

        var treeLength = ReadUInt32(source, 52);
        var treeSizes = new SmackerTreeSizes(
            ReadBoundedInt(source, 56, MaximumMovieBytes, "MMap tree size"),
            ReadBoundedInt(source, 60, MaximumMovieBytes, "MClr tree size"),
            ReadBoundedInt(source, 64, MaximumMovieBytes, "Full tree size"),
            ReadBoundedInt(source, 68, MaximumMovieBytes, "Type tree size"));
        var tableLength = checked(frameCount * 5);
        var treeOffset = checked((long)HeaderSize + tableLength);
        var frameDataOffset = checked(treeOffset + treeLength);
        if (frameDataOffset > source.Length)
            throw new InvalidDataException("Smacker frame tables or trees are truncated.");

        var frames = new List<SmackerFrame>(checked((int)frameCount));
        var offset = frameDataOffset;
        for (var index = 0; index < frameCount; index++)
        {
            var encodedLength = ReadUInt32(source, checked(HeaderSize + (int)index * 4));
            var length = encodedLength & 0xFFFF_FFFCu;
            if (length == 0 || length > int.MaxValue || offset + length > sourceLength)
                throw new InvalidDataException("Smacker frame extent is invalid.");
            var frameFlags = source[checked(HeaderSize + (int)(frameCount * 4) + (int)index)];
            frames.Add(new SmackerFrame(index, checked((int)offset), checked((int)length), frameFlags,
                index == 0 || (encodedLength & 1) != 0));
            offset += length;
        }
        if (offset != sourceLength)
            throw new InvalidDataException("Smacker frame extents do not consume the movie exactly.");

        return new SmackerMovie(version, width, height, new TimeSpan(durationTicks), flags,
            checked((int)treeOffset), checked((int)treeLength), treeSizes, audioTracks, frames);
    }

    private static int ReadBoundedInt(ReadOnlySpan<byte> source, int offset, int maximum, string field)
    {
        var value = ReadUInt32(source, offset);
        if (value is 0 || value > maximum)
            throw new InvalidDataException($"Smacker {field} is invalid.");
        return checked((int)value);
    }

    private static uint ReadUInt32(ReadOnlySpan<byte> source, int offset) =>
        BinaryPrimitives.ReadUInt32LittleEndian(source.Slice(offset, 4));

    private static void DecodePalette(ReadOnlySpan<byte> packet, ReadOnlySpan<byte> previous, Span<byte> output)
    {
        var sourceOffset = 0;
        var entry = 0;
        while (entry < 256)
        {
            if (sourceOffset >= packet.Length)
                throw new InvalidDataException("Smacker palette update is truncated.");
            var command = packet[sourceOffset++];
            if ((command & 0x80) != 0)
            {
                var count = (command & 0x7F) + 1;
                if (count > 256 - entry)
                    throw new InvalidDataException("Smacker palette skip exceeds the palette.");
                entry += count;
            }
            else if ((command & 0x40) != 0)
            {
                if (sourceOffset >= packet.Length)
                    throw new InvalidDataException("Smacker palette copy is truncated.");
                var count = (command & 0x3F) + 1;
                var oldEntry = packet[sourceOffset++];
                if (count > 256 - entry || count > 256 - oldEntry)
                    throw new InvalidDataException("Smacker palette copy exceeds the palette.");
                previous.Slice(oldEntry * 3, count * 3).CopyTo(output.Slice(entry * 3, count * 3));
                entry += count;
            }
            else
            {
                if (packet.Length - sourceOffset < 2)
                    throw new InvalidDataException("Smacker palette color is truncated.");
                output[entry * 3] = ExpandSixBit(command);
                output[entry * 3 + 1] = ExpandSixBit((byte)(packet[sourceOffset++] & 0x3F));
                output[entry * 3 + 2] = ExpandSixBit((byte)(packet[sourceOffset++] & 0x3F));
                entry++;
            }
        }
    }

    private static byte ExpandSixBit(byte value) => checked((byte)(value * 4 + value / 16));
}
