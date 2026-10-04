using System.Buffers.Binary;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>An image decoded by <see cref="BmpDecoder"/>.</summary>
/// <param name="Width">Width in pixels.</param>
/// <param name="Height">Height in pixels.</param>
/// <param name="Rgba">Four bytes per pixel (red, green, blue, alpha), rows top to bottom, no padding.</param>
public sealed record BmpImage(int Width, int Height, byte[] Rgba);

/// <summary>
/// Decodes Windows BMP files to RGBA: 8-bit BI_RGB and BI_RLE8, and 24-bit and 32-bit BI_RGB, with a
/// BITMAPINFOHEADER or its V4 and V5 extensions.
/// </summary>
public static class BmpDecoder
{
    private const int FileHeaderSize = 14;
    private const int InfoHeaderSize = 40;
    private const int V4HeaderSize = 108;
    private const int V5HeaderSize = 124;
    private const int BiRgb = 0;
    private const int BiRle8 = 1;

    /// <summary>
    /// Decodes a whole BMP file. Bottom-up and top-down BI_RGB rows are both read and returned top to
    /// bottom. Every pixel is opaque: the fourth byte of a 32-bit BI_RGB pixel is unused by the format
    /// and is ignored. Pixels a BI_RLE8 stream skips take palette index 0.
    /// </summary>
    /// <param name="source">The file.</param>
    /// <param name="maximumPixels">The largest image accepted, in pixels. It is checked before any pixel buffer is allocated. Defaults to <see cref="ImageLimits.DefaultMaximumPixels"/>.</param>
    /// <param name="requireDeclaredFileSize">
    /// When true, the size in the file header must equal the length of <paramref name="source"/>. Pass
    /// false for files whose writer left that field zero or wrong; the pixel data must still fit.
    /// </param>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="maximumPixels"/> is negative.</exception>
    /// <exception cref="InvalidDataException">
    /// The file is malformed, is a BMP variant this decoder does not read (another header, bit depth or
    /// compression, or a top-down BI_RLE8 image), or exceeds the pixel limit.
    /// </exception>
    public static BmpImage Decode(
        ReadOnlySpan<byte> source, int maximumPixels = ImageLimits.DefaultMaximumPixels, bool requireDeclaredFileSize = true)
    {
        if (maximumPixels < 0) throw new ArgumentOutOfRangeException(nameof(maximumPixels));
        if (source.Length < FileHeaderSize + 4 || source[0] != (byte)'B' || source[1] != (byte)'M')
            throw new InvalidDataException("Input is not a BMP file.");

        var declaredSize = BinaryPrimitives.ReadUInt32LittleEndian(source[2..]);
        if (requireDeclaredFileSize && declaredSize != (uint)source.Length)
            throw new InvalidDataException(
                $"BMP declares {declaredSize} bytes but the file has {source.Length}.");
        var pixelOffset = BinaryPrimitives.ReadUInt32LittleEndian(source[10..]);
        var headerSize = BinaryPrimitives.ReadUInt32LittleEndian(source[FileHeaderSize..]);
        if (headerSize is not (InfoHeaderSize or V4HeaderSize or V5HeaderSize))
            throw new InvalidDataException(
                $"BMP header size {headerSize} is not supported; only BITMAPINFOHEADER and its V4 and V5 forms are read.");
        if (source.Length < FileHeaderSize + headerSize) throw new InvalidDataException("BMP header is truncated.");

        var width = BinaryPrimitives.ReadInt32LittleEndian(source[18..]);
        var signedHeight = BinaryPrimitives.ReadInt32LittleEndian(source[22..]);
        var planes = BinaryPrimitives.ReadUInt16LittleEndian(source[26..]);
        var bitsPerPixel = BinaryPrimitives.ReadUInt16LittleEndian(source[28..]);
        var compression = BinaryPrimitives.ReadUInt32LittleEndian(source[30..]);
        var colorsUsed = BinaryPrimitives.ReadUInt32LittleEndian(source[46..]);

        if (width <= 0) throw new InvalidDataException($"BMP width {width} is not positive.");
        if (signedHeight is 0 or int.MinValue) throw new InvalidDataException($"BMP height {signedHeight} is invalid.");
        if (planes != 1) throw new InvalidDataException($"BMP plane count {planes} is not 1.");
        if ((bitsPerPixel, compression) is not ((8, BiRgb) or (8, BiRle8) or (24, BiRgb) or (32, BiRgb)))
            throw new InvalidDataException(
                $"BMP must be 8-bit BI_RGB or BI_RLE8, or 24-bit or 32-bit BI_RGB; found bpp={bitsPerPixel}, compression={compression}.");
        var topDown = signedHeight < 0;
        var height = Math.Abs(signedHeight);
        if (topDown && compression == BiRle8)
            throw new InvalidDataException("BMP BI_RLE8 images must be stored bottom-up.");
        var pixelCount = (long)width * height;
        if (pixelCount > maximumPixels)
            throw new InvalidDataException(
                $"BMP dimensions {width}x{height} exceed the configured limit of {maximumPixels} pixels.");
        if (pixelCount * 4 > Array.MaxLength)
            throw new InvalidDataException($"BMP dimensions {width}x{height} are too large to decode.");

        var paletteEntries = 0L;
        if (bitsPerPixel == 8)
        {
            if (colorsUsed > 256) throw new InvalidDataException($"BMP palette of {colorsUsed} colours exceeds 256.");
            paletteEntries = colorsUsed == 0 ? 256 : colorsUsed;
        }
        var paletteOffset = FileHeaderSize + (long)headerSize;
        if (pixelOffset < paletteOffset + paletteEntries * 4)
            throw new InvalidDataException("BMP pixel data overlaps its header or palette.");
        if (pixelOffset > (uint)source.Length) throw new InvalidDataException("BMP pixel offset lies past the end of the file.");
        var palette = source.Slice((int)paletteOffset, (int)paletteEntries * 4);
        var pixels = source[(int)pixelOffset..];

        byte[] rgba;
        if (compression == BiRle8)
        {
            // The RGBA buffer is allocated only once the stream has decoded without error.
            var indices = Rle8BitmapDecoder.DecodeIndices(pixels, width, height);
            rgba = new byte[pixelCount * 4];
            for (var row = 0; row < height; row++)
                WriteIndexedRow(indices.AsSpan((height - 1 - row) * width, width), palette, rgba.AsSpan(row * width * 4, width * 4));
            return new BmpImage(width, height, rgba);
        }

        var stride = ((long)width * bitsPerPixel + 31) / 32 * 4;
        if (stride * height > pixels.Length)
            throw new InvalidDataException(
                $"BMP pixel data is truncated: {stride * height} bytes needed, {pixels.Length} present.");
        rgba = new byte[pixelCount * 4];
        for (var row = 0; row < height; row++)
        {
            var sourceRow = topDown ? row : height - 1 - row;
            var input = pixels.Slice((int)(sourceRow * stride), (int)stride);
            var output = rgba.AsSpan(row * width * 4, width * 4);
            switch (bitsPerPixel)
            {
                case 8:
                    WriteIndexedRow(input[..width], palette, output);
                    break;
                case 24:
                    WriteBgrRow(input, 3, output);
                    break;
                default:
                    WriteBgrRow(input, 4, output);
                    break;
            }
        }
        return new BmpImage(width, height, rgba);
    }

    private static void WriteIndexedRow(ReadOnlySpan<byte> indices, ReadOnlySpan<byte> palette, Span<byte> output)
    {
        for (var x = 0; x < indices.Length; x++)
        {
            var entry = indices[x] * 4;
            if (entry >= palette.Length)
                throw new InvalidDataException($"BMP pixel uses palette index {indices[x]} of a {palette.Length / 4}-colour palette.");
            output[x * 4] = palette[entry + 2];
            output[x * 4 + 1] = palette[entry + 1];
            output[x * 4 + 2] = palette[entry];
            output[x * 4 + 3] = 255;
        }
    }

    private static void WriteBgrRow(ReadOnlySpan<byte> input, int bytesPerPixel, Span<byte> output)
    {
        for (var x = 0; x < output.Length / 4; x++)
        {
            var pixel = x * bytesPerPixel;
            output[x * 4] = input[pixel + 2];
            output[x * 4 + 1] = input[pixel + 1];
            output[x * 4 + 2] = input[pixel];
            output[x * 4 + 3] = 255;
        }
    }
}
