namespace RefurbishedDinosaurs.Media.Avi;

/// <summary>
/// A Microsoft RLE8 (<c>BI_RLE8</c>) frame surface. Frames are cumulative: an
/// inter frame draws only the pixels that changed, so every frame of a stream
/// must be applied in order. Rows are stored bottom-up, matching the AVI
/// bitmap convention; <see cref="ToRgba"/> flips them into display order.
/// </summary>
public sealed class RleVideoSurface
{
    /// <summary>Upper bound on either dimension, to fail fast on hostile headers.</summary>
    public const int MaxDimension = 4096;

    /// <summary>Upper bound on the pixel count, to fail fast on hostile headers.</summary>
    public const long MaxPixels = (long)MaxDimension * MaxDimension;

    /// <summary>Creates an all-zero surface for <paramref name="width"/> x <paramref name="height"/>.</summary>
    public RleVideoSurface(int width, int height)
    {
        if (width <= 0 || height <= 0 || width > MaxDimension || height > MaxDimension)
            throw new ArgumentOutOfRangeException(nameof(width), $"RLE surface size {width}x{height} is out of range.");
        if ((long)width * height > MaxPixels)
            throw new ArgumentOutOfRangeException(nameof(width), $"RLE surface {width}x{height} exceeds the pixel limit.");
        Width = width;
        Height = height;
        Indices = new byte[width * height];
    }

    /// <summary>The surface width in pixels.</summary>
    public int Width { get; }

    /// <summary>The surface height in pixels.</summary>
    public int Height { get; }

    /// <summary>The palette indices, row-major and bottom-up.</summary>
    public byte[] Indices { get; }

    /// <summary>
    /// Applies one compressed frame onto the surface. The stream is a sequence
    /// of <c>(count, value)</c> runs and the four escape forms
    /// <c>00 00</c> (end of line), <c>00 01</c> (end of bitmap),
    /// <c>00 02 dx dy</c> (delta), and <c>00 nn</c> followed by <c>nn</c>
    /// literal indices padded to an even count. An empty payload (an AVI
    /// dropped frame) leaves the surface unchanged.
    /// </summary>
    public void DecodeFrame(ReadOnlySpan<byte> frame)
    {
        if (frame.IsEmpty)
            return;
        int x = 0;
        int y = 0;
        int i = 0;
        while (i + 1 < frame.Length)
        {
            byte count = frame[i];
            byte value = frame[i + 1];
            i += 2;

            if (count != 0)
            {
                Write(value, count, ref x, y);
                continue;
            }

            switch (value)
            {
                case 0: // end of line
                    x = 0;
                    y++;
                    break;
                case 1: // end of bitmap
                    return;
                case 2: // delta
                    if (i + 1 >= frame.Length)
                        throw new InvalidDataException("RLE8 delta is truncated.");
                    x += frame[i];
                    y += frame[i + 1];
                    i += 2;
                    if (x > Width || y >= Height) throw new InvalidDataException("RLE8 delta leaves the surface.");
                    break;
                default:
                    int run = value;
                    if (i + run > frame.Length)
                        throw new InvalidDataException("RLE8 literal run is truncated.");
                    for (int k = 0; k < run; k++)
                        Write(frame[i + k], 1, ref x, y);
                    if (i + run + (run & 1) > frame.Length)
                        throw new InvalidDataException("RLE8 literal padding is truncated.");
                    i += run + (run & 1);
                    break;
            }
        }
        throw new InvalidDataException("RLE8 frame has no end marker.");
    }

    /// <summary>
    /// Converts the surface into a straight RGBA buffer of
    /// <c>Width * Height * 4</c> bytes in display order (top row first), with
    /// an opaque alpha. The palette must hold 256 RGB entries; an out-of-range
    /// index fails instead of reading past the palette.
    /// </summary>
    public byte[] ToRgba(ReadOnlySpan<byte> palette)
    {
        if (palette.Length < 256 * 3)
            throw new InvalidDataException($"RLE palette is {palette.Length} bytes, shorter than the 768-byte table.");
        var rgba = new byte[Width * Height * 4];
        for (int y = 0; y < Height; y++)
        {
            int source = (Height - 1 - y) * Width;
            int destination = y * Width * 4;
            for (int x = 0; x < Width; x++)
            {
                int entry = Indices[source + x] * 3;
                rgba[destination] = palette[entry];
                rgba[destination + 1] = palette[entry + 1];
                rgba[destination + 2] = palette[entry + 2];
                rgba[destination + 3] = 255;
                destination += 4;
            }
        }
        return rgba;
    }

    private void Write(byte value, int count, ref int x, int y)
    {
        if ((uint)y >= (uint)Height || x < 0 || count > Width - x)
            throw new InvalidDataException("RLE8 run leaves the surface.");
        Indices.AsSpan(y * Width + x, count).Fill(value);
        x += count;
    }
}
