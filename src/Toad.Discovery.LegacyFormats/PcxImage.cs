using System.Buffers.Binary;
using Toad.Discovery.Core.Imaging;

namespace Toad.Discovery.LegacyFormats;

public record IndexedImage(int Width, int Height, byte[] Indices, byte[] PaletteRgb)
{
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

public sealed record PcxImage(int Width, int Height, byte[] Indices, byte[] PaletteRgb)
    : IndexedImage(Width, Height, Indices, PaletteRgb);

public static class RawIndexedImageDecoder
{
    public static IndexedImage Decode(
        ReadOnlySpan<byte> indices, ReadOnlySpan<byte> paletteRgb,
        int width, int height, int maximumPixels = 16_777_216)
    {
        if (width <= 0) throw new ArgumentOutOfRangeException(nameof(width));
        if (height <= 0) throw new ArgumentOutOfRangeException(nameof(height));
        if (maximumPixels < 0) throw new ArgumentOutOfRangeException(nameof(maximumPixels));
        var pixelCount = checked(width * height);
        if (pixelCount > maximumPixels)
            throw new InvalidDataException("Raw indexed image dimensions exceed the configured pixel limit.");
        if (indices.Length != pixelCount)
            throw new InvalidDataException("Raw indexed image does not contain exactly width times height pixels.");
        if (paletteRgb.Length != IndexedPalette.ByteSize)
            throw new InvalidDataException("Raw indexed image palette does not contain 256 RGB triples.");
        return new IndexedImage(width, height, indices.ToArray(), paletteRgb.ToArray());
    }
}

public static class PcxDecoder
{
    private const int HeaderSize = 128;
    private const int PaletteSize = 768;

    public static PcxImage Decode(ReadOnlySpan<byte> source, int maximumPixels = 16_777_216)
    {
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
        var pixelCount = checked(width * height);
        if (maximumPixels < 0) throw new ArgumentOutOfRangeException(nameof(maximumPixels));
        if (pixelCount > maximumPixels) throw new InvalidDataException("PCX dimensions exceed the configured pixel limit.");

        var paletteOffset = source.Length - PaletteSize - 1;
        if (source[paletteOffset] != 0x0C) throw new InvalidDataException("PCX 256-color palette marker is missing.");
        var scanlines = new byte[checked(bytesPerLine * height)];
        var input = HeaderSize;
        var output = 0;
        while (output < scanlines.Length)
        {
            if (input >= paletteOffset) throw new InvalidDataException("PCX pixel stream ended early.");
            var token = source[input++];
            var count = 1;
            var value = token;
            if ((token & 0xC0) == 0xC0)
            {
                count = token & 0x3F;
                if (count == 0 || input >= paletteOffset) throw new InvalidDataException("PCX contains an invalid RLE run.");
                value = source[input++];
            }
            if (count > scanlines.Length - output) throw new InvalidDataException("PCX RLE run exceeds the scanline buffer.");
            scanlines.AsSpan(output, count).Fill(value);
            output += count;
        }
        if (input != paletteOffset) throw new InvalidDataException("PCX has unexpected bytes between pixels and palette.");

        var indices = new byte[pixelCount];
        for (var row = 0; row < height; row++)
            scanlines.AsSpan(row * bytesPerLine, width).CopyTo(indices.AsSpan(row * width, width));
        return new PcxImage(width, height, indices, source[(paletteOffset + 1)..].ToArray());
    }
}
