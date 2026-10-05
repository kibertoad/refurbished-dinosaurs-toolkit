using System.Buffers.Binary;
using RefurbishedDinosaurs.Core.Imaging;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>An 8-bit indexed image with its palette.</summary>
/// <param name="Width">Width in pixels.</param>
/// <param name="Height">Height in pixels.</param>
/// <param name="Indices">One palette index per pixel, rows top to bottom, no padding.</param>
/// <param name="PaletteRgb">256 RGB triples, 8 bits per channel.</param>
public record IndexedImage(int Width, int Height, byte[] Indices, byte[] PaletteRgb)
{
    /// <summary>Expands the image to 8-bit RGBA, fully opaque, in the same pixel order.</summary>
    /// <exception cref="InvalidDataException">The index or palette size does not match.</exception>
    public byte[] ToRgba()
    {
        if (Width <= 0 || Height <= 0 || (long)Width * Height != Indices.Length)
            throw new InvalidDataException("Indexed pixel buffer does not match its dimensions.");
        if (PaletteRgb.Length != 256 * 3)
            throw new InvalidDataException("Indexed palette does not contain 256 RGB triples.");
        var rgba = new byte[checked(Indices.Length * 4)];
        for (var pixel = 0; pixel < Indices.Length; pixel++)
        {
            var palette = Indices[pixel] * 3;
            var target = pixel * 4;
            rgba[target] = PaletteRgb[palette];
            rgba[target + 1] = PaletteRgb[palette + 1];
            rgba[target + 2] = PaletteRgb[palette + 2];
            rgba[target + 3] = 255;
        }
        return rgba;
    }
}

/// <summary>An image decoded by <see cref="PcxDecoder"/>.</summary>
/// <param name="Width">Width in pixels.</param>
/// <param name="Height">Height in pixels.</param>
/// <param name="Indices">One palette index per pixel, rows top to bottom, no padding.</param>
/// <param name="PaletteRgb">The file's 256-colour palette.</param>
public sealed record PcxImage(int Width, int Height, byte[] Indices, byte[] PaletteRgb)
    : IndexedImage(Width, Height, Indices, PaletteRgb)
{
    /// <summary>
    /// How far the RLE stream fell short of the declared scanlines, when a
    /// <see cref="PcxShortStreamRepair"/> filled the rest. Null when the stream covered every scanline.
    /// </summary>
    public PcxStreamShortfall? Shortfall { get; init; }
}

/// <summary>
/// Lets <see cref="PcxDecoder.Decode(ReadOnlySpan{byte}, PcxShortStreamRepair, int)"/> finish an image
/// whose RLE stream ends, at a token boundary, before the declared scanlines are full.
/// </summary>
/// <param name="FillIndex">The palette index written into every pixel the stream does not supply.</param>
public sealed record PcxShortStreamRepair(byte FillIndex);

/// <summary>What a <see cref="PcxShortStreamRepair"/> filled in.</summary>
/// <param name="MissingScanlineBytes">
/// Decoded scanline bytes, scanline padding included, that the stream did not supply. Always positive.
/// </param>
/// <param name="FilledPixels">
/// Pixels set to <see cref="PcxShortStreamRepair.FillIndex"/>. They are the last <paramref name="FilledPixels"/>
/// entries of <see cref="IndexedImage.Indices"/>. Zero when only the padding of the last scanline was missing.
/// </param>
public sealed record PcxStreamShortfall(long MissingScanlineBytes, int FilledPixels);

/// <summary>Wraps headerless pixel and palette data whose dimensions come from elsewhere.</summary>
public static class RawIndexedImageDecoder
{
    /// <summary>Copies <paramref name="indices"/> and <paramref name="paletteRgb"/> into an image after checking their sizes.</summary>
    /// <param name="indices">Exactly <paramref name="width"/> × <paramref name="height"/> palette indices.</param>
    /// <param name="paletteRgb">768 bytes of 8-bit RGB.</param>
    /// <param name="width">Width in pixels.</param>
    /// <param name="height">Height in pixels.</param>
    /// <param name="maximumPixels">The largest image accepted, in pixels. Defaults to <see cref="ImageLimits.DefaultMaximumPixels"/>.</param>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="width"/> or <paramref name="height"/> is not positive, or <paramref name="maximumPixels"/> is negative.</exception>
    /// <exception cref="InvalidDataException">The image exceeds the pixel limit, or the index or palette size does not match.</exception>
    public static IndexedImage Decode(
        ReadOnlySpan<byte> indices, ReadOnlySpan<byte> paletteRgb,
        int width, int height, int maximumPixels = ImageLimits.DefaultMaximumPixels)
    {
        if (width <= 0) throw new ArgumentOutOfRangeException(nameof(width));
        if (height <= 0) throw new ArgumentOutOfRangeException(nameof(height));
        if (maximumPixels < 0) throw new ArgumentOutOfRangeException(nameof(maximumPixels));
        var pixelCount = (long)width * height;
        if (pixelCount > maximumPixels)
            throw new InvalidDataException("Raw indexed image dimensions exceed the configured pixel limit.");
        if (indices.Length != pixelCount)
            throw new InvalidDataException("Raw indexed image does not contain exactly width times height pixels.");
        if (paletteRgb.Length != IndexedPalette.ByteSize)
            throw new InvalidDataException("Raw indexed image palette does not contain 256 RGB triples.");
        return new IndexedImage(width, height, indices.ToArray(), paletteRgb.ToArray());
    }
}

/// <summary>Decodes 8-bit single-plane RLE PCX images with a trailing 256-colour palette.</summary>
public static class PcxDecoder
{
    private const int HeaderSize = 128;
    private const int PaletteSize = 768;

    /// <summary>
    /// Decodes a whole PCX file. Scanline padding is removed. An RLE stream that ends before the
    /// declared scanlines are full throws; <see cref="Decode(ReadOnlySpan{byte}, PcxShortStreamRepair, int)"/>
    /// fills such a stream instead and reports the shortfall.
    /// </summary>
    /// <param name="source">The file.</param>
    /// <param name="maximumPixels">The largest image accepted, in pixels. Defaults to <see cref="ImageLimits.DefaultMaximumPixels"/>.</param>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="maximumPixels"/> is negative.</exception>
    /// <exception cref="InvalidDataException">The file is another PCX variant or malformed, its stream ends early, or it exceeds the pixel limit.</exception>
    public static PcxImage Decode(ReadOnlySpan<byte> source, int maximumPixels = ImageLimits.DefaultMaximumPixels) =>
        DecodeCore(source, null, maximumPixels);

    /// <summary>
    /// Decodes a whole PCX file whose RLE stream may end before the declared scanlines are full. The
    /// missing pixels take <see cref="PcxShortStreamRepair.FillIndex"/>, and
    /// <see cref="PcxImage.Shortfall"/> reports how much was missing. A file whose stream is complete
    /// decodes as <see cref="Decode(ReadOnlySpan{byte}, int)"/> would, with a null shortfall.
    /// </summary>
    /// <param name="source">The file.</param>
    /// <param name="shortStreamRepair">The fill to use for the pixels a short stream does not supply.</param>
    /// <param name="maximumPixels">The largest image accepted, in pixels. Defaults to <see cref="ImageLimits.DefaultMaximumPixels"/>.</param>
    /// <exception cref="ArgumentNullException"><paramref name="shortStreamRepair"/> is null.</exception>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="maximumPixels"/> is negative.</exception>
    /// <exception cref="InvalidDataException">
    /// The file is another PCX variant or malformed, or exceeds the pixel limit. A run token directly
    /// before the palette marker, with no value byte, is still malformed: the stream claims pixels it
    /// does not give.
    /// </exception>
    public static PcxImage Decode(
        ReadOnlySpan<byte> source, PcxShortStreamRepair shortStreamRepair,
        int maximumPixels = ImageLimits.DefaultMaximumPixels)
    {
        ArgumentNullException.ThrowIfNull(shortStreamRepair);
        return DecodeCore(source, shortStreamRepair, maximumPixels);
    }

    private static PcxImage DecodeCore(ReadOnlySpan<byte> source, PcxShortStreamRepair? repair, int maximumPixels)
    {
        if (maximumPixels < 0) throw new ArgumentOutOfRangeException(nameof(maximumPixels));
        if (source.Length < HeaderSize + 1 + PaletteSize) throw new InvalidDataException("PCX resource is too short.");
        if (source[0] != 0x0A || source[2] != 1 || source[3] != 8)
            throw new InvalidDataException("Only 8-bit RLE PCX resources are supported.");
        if (source[65] != 1) throw new InvalidDataException("Only single-plane indexed PCX resources are supported.");

        var xMin = BinaryPrimitives.ReadUInt16LittleEndian(source[4..]);
        var yMin = BinaryPrimitives.ReadUInt16LittleEndian(source[6..]);
        var xMax = BinaryPrimitives.ReadUInt16LittleEndian(source[8..]);
        var yMax = BinaryPrimitives.ReadUInt16LittleEndian(source[10..]);
        if (xMax < xMin || yMax < yMin) throw new InvalidDataException("PCX dimensions are inverted.");
        var width = xMax - xMin + 1;
        var height = yMax - yMin + 1;
        var bytesPerLine = BinaryPrimitives.ReadUInt16LittleEndian(source[66..]);
        if (bytesPerLine < width) throw new InvalidDataException("PCX scanline is narrower than its image.");
        if ((long)width * height > maximumPixels) throw new InvalidDataException("PCX dimensions exceed the configured pixel limit.");
        var pixelCount = width * height;

        var paletteOffset = source.Length - PaletteSize - 1;
        if (source[paletteOffset] != 0x0C) throw new InvalidDataException("PCX 256-color palette marker is missing.");
        // Runs are written straight into the unpadded pixels, so a wide bytesPerLine cannot allocate
        // past the pixel limit. A run may cross the end of a scanline.
        var indices = new byte[pixelCount];
        var scanlineBytes = (long)bytesPerLine * height;
        var output = 0L;
        var input = HeaderSize;
        while (output < scanlineBytes)
        {
            if (input >= paletteOffset)
            {
                if (repair is null) throw new InvalidDataException("PCX pixel stream ended early.");
                break;
            }
            var token = source[input++];
            var count = 1;
            var value = token;
            if ((token & 0xC0) == 0xC0)
            {
                count = token & 0x3F;
                if (count == 0 || input >= paletteOffset) throw new InvalidDataException("PCX contains an invalid RLE run.");
                value = source[input++];
            }
            if (count > scanlineBytes - output) throw new InvalidDataException("PCX RLE run exceeds the scanline buffer.");
            while (count > 0)
            {
                var row = (int)(output / bytesPerLine);
                var column = (int)(output % bytesPerLine);
                var span = Math.Min(count, bytesPerLine - column);
                if (column < width)
                    indices.AsSpan(row * width + column, Math.Min(span, width - column)).Fill(value);
                output += span;
                count -= span;
            }
        }
        if (input != paletteOffset) throw new InvalidDataException("PCX has unexpected bytes between pixels and palette.");

        PcxStreamShortfall? shortfall = null;
        if (output < scanlineBytes)
        {
            // Every scanline byte from output on is missing, so the unfilled pixels are a suffix of indices.
            var row = (int)(output / bytesPerLine);
            var column = (int)(output % bytesPerLine);
            var firstFilled = row * width + Math.Min(column, width);
            indices.AsSpan(firstFilled).Fill(repair!.FillIndex);
            shortfall = new PcxStreamShortfall(scanlineBytes - output, pixelCount - firstFilled);
        }
        return new PcxImage(width, height, indices, source[(paletteOffset + 1)..].ToArray()) { Shortfall = shortfall };
    }
}
