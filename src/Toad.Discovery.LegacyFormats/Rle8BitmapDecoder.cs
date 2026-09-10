namespace Toad.Discovery.LegacyFormats;

public static class Rle8BitmapDecoder
{
    private const int BitmapHeaderSize = 54;
    private const int PaletteEntries = 256;
    private const int PaletteBytes = PaletteEntries * 4;

    public static byte[] Decode(byte[] source, int width, int height)
    {
        ArgumentNullException.ThrowIfNull(source);
        if (width <= 0 || height <= 0) throw new ArgumentOutOfRangeException(nameof(width));
        if (source.Length < BitmapHeaderSize || source[0] != (byte)'B' || source[1] != (byte)'M')
            throw new InvalidDataException("Input is not a BMP container.");

        var pixelOffset = ReadInt32(source, 10);
        var bitsPerPixel = ReadInt16(source, 28);
        var compression = ReadInt32(source, 30);
        if (pixelOffset < BitmapHeaderSize + PaletteBytes || pixelOffset > source.Length)
            throw new InvalidDataException("BMP palette or pixel offset is invalid.");
        if (bitsPerPixel != 8 || compression is not (0 or 1))
            throw new InvalidDataException($"BMP must use 8-bit BI_RGB or BI_RLE8 encoding; found bpp={bitsPerPixel}, compression={compression}.");

        var stride = checked((width + 3) & ~3);
        var imageBytes = checked(stride * height);
        var output = new byte[checked(pixelOffset + imageBytes)];
        source.AsSpan(0, pixelOffset).CopyTo(output);
        if (compression == 0)
        {
            if (source.Length < output.Length)
                throw new InvalidDataException("Uncompressed PX08 pixel data is truncated.");
            source.AsSpan(pixelOffset, imageBytes).CopyTo(output.AsSpan(pixelOffset));
        }
        else
        {
            var indices = DecodeIndices(source.AsSpan(pixelOffset), width, height);
            for (var y = 0; y < height; y++)
                indices.AsSpan(y * width, width).CopyTo(output.AsSpan(pixelOffset + y * stride, width));
        }
        WriteInt32(output, 2, output.Length);
        WriteInt32(output, 18, width);
        WriteInt32(output, 22, height);
        WriteInt16(output, 26, 1);
        WriteInt16(output, 28, 8);
        WriteInt32(output, 30, 0);
        WriteInt32(output, 34, imageBytes);
        WriteInt32(output, 46, PaletteEntries);
        return output;
    }

    private static byte[] DecodeIndices(ReadOnlySpan<byte> encoded, int width, int height)
    {
        var pixels = new byte[checked(width * height)];
        var sourceIndex = 0;
        var x = 0;
        var y = 0;
        var ended = false;
        while (sourceIndex < encoded.Length && !ended)
        {
            Require(encoded, sourceIndex, 2);
            var count = encoded[sourceIndex++];
            var value = encoded[sourceIndex++];
            if (count != 0)
            {
                RequireDestination(x, y, count, width, height);
                pixels.AsSpan(y * width + x, count).Fill(value);
                x += count;
                continue;
            }

            switch (value)
            {
                case 0: // End of line.
                    x = 0;
                    y++;
                    if (y > height) throw new InvalidDataException("BMP RLE8 stream has too many rows.");
                    break;
                case 1: // End of bitmap.
                    ended = true;
                    break;
                case 2: // Delta.
                    Require(encoded, sourceIndex, 2);
                    x += encoded[sourceIndex++];
                    y += encoded[sourceIndex++];
                    if (x > width || y >= height)
                        throw new InvalidDataException("BMP RLE8 delta leaves the image bounds.");
                    break;
                default: // Absolute run.
                    var literalCount = value;
                    Require(encoded, sourceIndex, literalCount + (literalCount & 1));
                    RequireDestination(x, y, literalCount, width, height);
                    encoded.Slice(sourceIndex, literalCount).CopyTo(pixels.AsSpan(y * width + x, literalCount));
                    sourceIndex += literalCount + (literalCount & 1);
                    x += literalCount;
                    break;
            }
        }
        if (!ended) throw new InvalidDataException("BMP RLE8 stream has no end-of-bitmap marker.");
        return pixels;
    }

    private static void Require(ReadOnlySpan<byte> source, int offset, int count)
    {
        if (offset < 0 || count < 0 || offset > source.Length - count)
            throw new InvalidDataException("BMP RLE8 stream is truncated.");
    }

    private static void RequireDestination(int x, int y, int count, int width, int height)
    {
        if (x < 0 || y < 0 || y >= height || count > width - x)
            throw new InvalidDataException(
                $"BMP RLE8 run leaves the image bounds (x={x}, y={y}, count={count}, size={width}x{height}).");
    }

    private static short ReadInt16(byte[] bytes, int offset) => BitConverter.ToInt16(bytes, offset);
    private static int ReadInt32(byte[] bytes, int offset) => BitConverter.ToInt32(bytes, offset);
    private static void WriteInt16(byte[] bytes, int offset, short value) =>
        BitConverter.TryWriteBytes(bytes.AsSpan(offset, sizeof(short)), value);
    private static void WriteInt32(byte[] bytes, int offset, int value) =>
        BitConverter.TryWriteBytes(bytes.AsSpan(offset, sizeof(int)), value);
}
