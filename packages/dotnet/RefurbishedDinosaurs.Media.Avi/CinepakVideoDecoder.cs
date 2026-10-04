namespace RefurbishedDinosaurs.Media.Avi;

/// <summary>
/// A Cinepak (<c>cvid</c>) frame surface. Frames are cumulative: an inter frame
/// updates only some 4x4 blocks, and the per-strip vector codebooks persist
/// across frames, so every frame of a stream must be applied in order. The
/// output is straight RGB, top-down.
/// </summary>
public sealed class CinepakSurface
{
    /// <summary>Upper bound on either dimension, to fail fast on hostile headers.</summary>
    public const int MaxDimension = 4096;

    /// <summary>The number of strips a frame may carry.</summary>
    public const int MaxStrips = 32;

    private const int CodebookBytes = 256 * 12;

    private readonly int _width;
    private readonly int _height;
    private readonly byte[] _rgb;
    private readonly Strip[] _strips = new Strip[MaxStrips];

    /// <summary>Creates an all-black surface for <paramref name="width"/> x <paramref name="height"/>.</summary>
    public CinepakSurface(int width, int height)
    {
        if (width <= 0 || height <= 0 || width > MaxDimension || height > MaxDimension)
            throw new ArgumentOutOfRangeException(nameof(width), $"Cinepak size {width}x{height} is out of range.");
        DisplayWidth = width;
        DisplayHeight = height;
        _width = (width + 3) & ~3;
        _height = (height + 3) & ~3;
        if ((long)_width * _height > (long)MaxDimension * MaxDimension)
            throw new ArgumentOutOfRangeException(nameof(width), $"Cinepak size {width}x{height} exceeds the pixel limit.");
        _rgb = new byte[_width * _height * 3];
    }

    /// <summary>The display width in pixels, before 4-pixel padding.</summary>
    public int DisplayWidth { get; }

    /// <summary>The display height in pixels, before 4-pixel padding.</summary>
    public int DisplayHeight { get; }

    /// <summary>
    /// Applies one compressed Cinepak frame onto the surface. An empty payload
    /// (an AVI dropped frame) and a frame with no strips leave the surface
    /// unchanged.
    /// </summary>
    public void DecodeFrame(ReadOnlySpan<byte> frame)
    {
        if (frame.IsEmpty)
            return;
        if (frame.Length < 10)
            throw new InvalidDataException("Cinepak frame is shorter than its 10-byte header.");

        if (ReadU24(frame, 1) != frame.Length || ReadU16(frame, 4) != DisplayWidth || ReadU16(frame, 6) != DisplayHeight)
            throw new InvalidDataException("Cinepak frame length or dimensions disagree with the surface.");
        int frameFlags = frame[0];
        int stripCount = ReadU16(frame, 8);
        if (stripCount > MaxStrips)
            throw new InvalidDataException("Cinepak strip count is invalid.");

        int position = 10;
        int previousBottom = 0;
        for (int i = 0; i < stripCount; i++)
        {
            if (position + 12 > frame.Length)
                throw new InvalidDataException("Cinepak frame ends before its strip table.");

            int stripTop = ReadU16(frame, position + 4);
            int stripBottom = ReadU16(frame, position + 8);
            if (stripTop == 0)
            {
                stripTop = previousBottom;
                stripBottom = stripTop + stripBottom;
            }
            int stripLeft = ReadU16(frame, position + 6);
            int stripRight = ReadU16(frame, position + 10);

            int stripSize = ReadU24(frame, position + 1) - 12;
            if (stripSize < 0)
                throw new InvalidDataException("Cinepak strip header declares a size below its header length.");
            position += 12;
            if (position + stripSize > frame.Length)
                throw new InvalidDataException("Cinepak strip is truncated.");
            var body = frame.Slice(position, stripSize);
            position += stripSize;

            if (_strips[i] is null)
                _strips[i] = new Strip();
            if (i > 0 && (frameFlags & 1) == 0)
                _strips[i].CopyFrom(_strips[i - 1]);

            DecodeStrip(body, _strips[i], stripLeft, stripTop, stripRight, stripBottom);
            previousBottom = stripBottom;
        }
        if (position != frame.Length) throw new InvalidDataException("Cinepak frame has trailing bytes.");
    }

    /// <summary>
    /// Converts the surface into a straight RGBA buffer of
    /// <c>DisplayWidth * DisplayHeight * 4</c> bytes in display order, with an
    /// opaque alpha. Any 4-pixel padding is cropped away.
    /// </summary>
    public byte[] ToRgba()
    {
        var rgba = new byte[DisplayWidth * DisplayHeight * 4];
        for (int y = 0; y < DisplayHeight; y++)
        {
            int source = y * _width * 3;
            int destination = y * DisplayWidth * 4;
            for (int x = 0; x < DisplayWidth; x++)
            {
                rgba[destination] = _rgb[source];
                rgba[destination + 1] = _rgb[source + 1];
                rgba[destination + 2] = _rgb[source + 2];
                rgba[destination + 3] = 255;
                source += 3;
                destination += 4;
            }
        }
        return rgba;
    }

    private void DecodeStrip(ReadOnlySpan<byte> body, Strip strip, int x1, int y1, int x2, int y2)
    {
        // The far edges may be the unpadded display size; Put clips the 4x4 blocks to the padded surface.
        if (x2 > _width || y2 > _height || x1 >= x2 || y1 >= y2 || (x1 & 3) != 0 || (y1 & 3) != 0)
            throw new InvalidDataException($"Cinepak strip bounds ({x1},{y1})-({x2},{y2}) are outside the surface.");

        int position = 0;
        while (position + 4 <= body.Length)
        {
            int chunkId = body[position];
            int chunkSize = ReadU24(body, position + 1) - 4;
            if (chunkSize < 0)
                throw new InvalidDataException("Cinepak chunk declares a size below its header length.");
            position += 4;
            if (position + chunkSize > body.Length)
                throw new InvalidDataException("Cinepak chunk is truncated.");
            var chunk = body.Slice(position, chunkSize);

            switch (chunkId)
            {
                case 0x20 or 0x21 or 0x24 or 0x25:
                    DecodeCodebook(strip.V4, chunkId, chunk);
                    break;
                case 0x22 or 0x23 or 0x26 or 0x27:
                    DecodeCodebook(strip.V1, chunkId, chunk);
                    break;
                case 0x30 or 0x31 or 0x32:
                    DecodeVectors(strip, chunkId, chunk, x1, y1, x2, y2);
                    break;
                default:
                    throw new NotSupportedException($"Cinepak chunk type {chunkId} is unsupported.");
            }

            position += chunkSize;
        }
        if (position != body.Length) throw new InvalidDataException("Cinepak chunk header is truncated.");
    }

    private static void DecodeCodebook(byte[] codebook, int chunkId, ReadOnlySpan<byte> data)
    {
        int entrySize = (chunkId & 0x04) != 0 ? 4 : 6;
        uint flag = 0;
        uint mask = 0;
        int position = 0;
        for (int i = 0; i < 256; i++)
        {
            if ((chunkId & 0x01) != 0)
            {
                mask >>= 1;
                if (mask == 0)
                {
                    if (position == data.Length) return;
                    if (position + 4 > data.Length)
                        throw new InvalidDataException("Cinepak codebook flags are truncated.");
                    flag = ReadU32(data, position);
                    position += 4;
                    mask = 0x80000000;
                }
            }

            if ((chunkId & 0x01) == 0 || (flag & mask) != 0)
            {
                if (position == data.Length && (chunkId & 1) == 0) return;
                if (position + entrySize > data.Length)
                    throw new InvalidDataException("Cinepak codebook entry is truncated.");
                int entry = i * 12;
                if (entrySize == 4)
                {
                    for (int k = 0; k < 4; k++)
                    {
                        byte luma = data[position++];
                        codebook[entry + k * 3] = luma;
                        codebook[entry + k * 3 + 1] = luma;
                        codebook[entry + k * 3 + 2] = luma;
                    }
                }
                else
                {
                    int lumaStart = position;
                    position += 4;
                    int u = (sbyte)data[position++];
                    int v = (sbyte)data[position++];
                    for (int k = 0; k < 4; k++)
                    {
                        int luma = data[lumaStart + k];
                        codebook[entry + k * 3] = Clamp(luma + v * 2);
                        codebook[entry + k * 3 + 1] = Clamp(luma - u / 2 - v);
                        codebook[entry + k * 3 + 2] = Clamp(luma + u * 2);
                    }
                }
            }
        }
    }

    private void DecodeVectors(Strip strip, int chunkId, ReadOnlySpan<byte> data, int x1, int y1, int x2, int y2)
    {
        uint flag = 0;
        uint mask = 0;
        int position = 0;
        for (int y = y1; y < y2; y += 4)
        {
            for (int x = x1; x < x2; x += 4)
            {
                if ((chunkId & 0x01) != 0)
                {
                    mask >>= 1;
                    if (mask == 0)
                    {
                        if (position + 4 > data.Length)
                            throw new InvalidDataException("Cinepak vector payload is truncated.");
                        flag = ReadU32(data, position);
                        position += 4;
                        mask = 0x80000000;
                    }
                }

                if ((chunkId & 0x01) == 0 || (flag & mask) != 0)
                {
                    if ((chunkId & 0x02) == 0)
                    {
                        mask >>= 1;
                        if (mask == 0)
                        {
                            if (position + 4 > data.Length)
                                throw new InvalidDataException("Cinepak vector payload is truncated.");
                            flag = ReadU32(data, position);
                            position += 4;
                            mask = 0x80000000;
                        }
                    }

                    if ((chunkId & 0x02) != 0 || (~flag & mask) != 0)
                    {
                        if (position >= data.Length)
                            throw new InvalidDataException("Cinepak vector payload is truncated.");
                        PutSingle(strip.V1, data[position++], x, y);
                    }
                    else if ((flag & mask) != 0)
                    {
                        if (position + 4 > data.Length)
                            throw new InvalidDataException("Cinepak vector payload is truncated.");
                        PutQuad(strip.V4, data[position], data[position + 1], data[position + 2], data[position + 3], x, y);
                        position += 4;
                    }
                }
            }
        }
    }

    private void PutQuad(byte[] codebook, int i0, int i1, int i2, int i3, int x, int y)
    {
        int e0 = i0 * 12;
        int e1 = i1 * 12;
        int e2 = i2 * 12;
        int e3 = i3 * 12;
        Put(codebook, e0, 0, x, y);
        Put(codebook, e0, 3, x + 1, y);
        Put(codebook, e1, 0, x + 2, y);
        Put(codebook, e1, 3, x + 3, y);
        Put(codebook, e0, 6, x, y + 1);
        Put(codebook, e0, 9, x + 1, y + 1);
        Put(codebook, e1, 6, x + 2, y + 1);
        Put(codebook, e1, 9, x + 3, y + 1);
        Put(codebook, e2, 0, x, y + 2);
        Put(codebook, e2, 3, x + 1, y + 2);
        Put(codebook, e3, 0, x + 2, y + 2);
        Put(codebook, e3, 3, x + 3, y + 2);
        Put(codebook, e2, 6, x, y + 3);
        Put(codebook, e2, 9, x + 1, y + 3);
        Put(codebook, e3, 6, x + 2, y + 3);
        Put(codebook, e3, 9, x + 3, y + 3);
    }

    private void PutSingle(byte[] codebook, int index, int x, int y)
    {
        int e = index * 12;
        Put(codebook, e, 0, x, y);
        Put(codebook, e, 0, x + 1, y);
        Put(codebook, e, 3, x + 2, y);
        Put(codebook, e, 3, x + 3, y);
        Put(codebook, e, 0, x, y + 1);
        Put(codebook, e, 0, x + 1, y + 1);
        Put(codebook, e, 3, x + 2, y + 1);
        Put(codebook, e, 3, x + 3, y + 1);
        Put(codebook, e, 6, x, y + 2);
        Put(codebook, e, 6, x + 1, y + 2);
        Put(codebook, e, 9, x + 2, y + 2);
        Put(codebook, e, 9, x + 3, y + 2);
        Put(codebook, e, 6, x, y + 3);
        Put(codebook, e, 6, x + 1, y + 3);
        Put(codebook, e, 9, x + 2, y + 3);
        Put(codebook, e, 9, x + 3, y + 3);
    }

    private void Put(byte[] codebook, int entry, int offset, int x, int y)
    {
        if ((uint)x >= (uint)_width || (uint)y >= (uint)_height)
            return;
        int destination = (y * _width + x) * 3;
        _rgb[destination] = codebook[entry + offset];
        _rgb[destination + 1] = codebook[entry + offset + 1];
        _rgb[destination + 2] = codebook[entry + offset + 2];
    }

    private static byte Clamp(int value) => value < 0 ? (byte)0 : value > 255 ? (byte)255 : (byte)value;

    private static int ReadU16(ReadOnlySpan<byte> data, int offset) =>
        (data[offset] << 8) | data[offset + 1];

    private static int ReadU24(ReadOnlySpan<byte> data, int offset) =>
        (data[offset] << 16) | (data[offset + 1] << 8) | data[offset + 2];

    private static uint ReadU32(ReadOnlySpan<byte> data, int offset) =>
        ((uint)data[offset] << 24) | ((uint)data[offset + 1] << 16) | ((uint)data[offset + 2] << 8) | data[offset + 3];

    private sealed class Strip
    {
        public byte[] V4 { get; } = new byte[CodebookBytes];

        public byte[] V1 { get; } = new byte[CodebookBytes];

        public void CopyFrom(Strip other)
        {
            Array.Copy(other.V4, V4, CodebookBytes);
            Array.Copy(other.V1, V1, CodebookBytes);
        }
    }
}
