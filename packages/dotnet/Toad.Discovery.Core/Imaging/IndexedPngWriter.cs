using System.Buffers.Binary;
using System.IO.Compression;

namespace Toad.Discovery.Core.Imaging;

/// <summary>
/// Writes 8-bit palette images as PNG. The output depends only on the inputs: the same pixels and
/// palette always produce the same bytes, so written images can be compared by hash.
/// </summary>
public static class IndexedPngWriter
{
    private static ReadOnlySpan<byte> Signature => [137, 80, 78, 71, 13, 10, 26, 10];

    /// <summary>
    /// Writes one PNG with colour type 3 (indexed), bit depth 8, a 256-entry <c>PLTE</c> chunk and
    /// unfiltered scanlines compressed at the smallest zlib size.
    /// </summary>
    /// <param name="output">A writable stream; the PNG is written from its current position.</param>
    /// <param name="width">Image width in pixels, at least 1.</param>
    /// <param name="height">Image height in pixels, at least 1.</param>
    /// <param name="pixels">Palette indexes, row by row, exactly <paramref name="width"/> × <paramref name="height"/> bytes.</param>
    /// <param name="palette">The 256-colour palette the indexes refer to.</param>
    /// <exception cref="ArgumentException">The stream is not writable, the dimensions do not match the pixels, or the palette is not 256 RGB triples.</exception>
    public static void Write(
        Stream output, ushort width, ushort height, ReadOnlySpan<byte> pixels, IndexedPalette palette)
    {
        ArgumentNullException.ThrowIfNull(output);
        ArgumentNullException.ThrowIfNull(palette);
        if (!output.CanWrite) throw new ArgumentException("PNG output must be writable.", nameof(output));
        if (width == 0 || height == 0 || pixels.Length != (long)width * height)
            throw new ArgumentException("Indexed image dimensions do not match its pixels.", nameof(pixels));
        if (palette.Rgb is null || palette.Rgb.Length != IndexedPalette.ByteSize)
            throw new ArgumentException("Indexed PNG output requires exactly 256 colors.", nameof(palette));

        output.Write(Signature);
        Span<byte> header = stackalloc byte[13];
        BinaryPrimitives.WriteUInt32BigEndian(header, width);
        BinaryPrimitives.WriteUInt32BigEndian(header[4..], height);
        header[8] = 8;
        header[9] = 3;
        WriteChunk(output, "IHDR"u8, header);
        WriteChunk(output, "PLTE"u8, palette.Rgb);

        using var compressed = new MemoryStream();
        using (var zlib = new ZLibStream(compressed, CompressionLevel.SmallestSize, leaveOpen: true))
        {
            ReadOnlySpan<byte> filter = [0];
            for (var row = 0; row < height; row++)
            {
                zlib.Write(filter);
                zlib.Write(pixels.Slice(checked(row * width), width));
            }
        }
        WriteChunk(output, "IDAT"u8, compressed.GetBuffer().AsSpan(0, checked((int)compressed.Length)));
        WriteChunk(output, "IEND"u8, []);
    }

    private static void WriteChunk(Stream output, ReadOnlySpan<byte> type, ReadOnlySpan<byte> data)
    {
        Span<byte> value = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(value, checked((uint)data.Length));
        output.Write(value);
        output.Write(type);
        output.Write(data);
        var crc = UpdateCrc(UpdateCrc(uint.MaxValue, type), data) ^ uint.MaxValue;
        BinaryPrimitives.WriteUInt32BigEndian(value, crc);
        output.Write(value);
    }

    private static uint UpdateCrc(uint crc, ReadOnlySpan<byte> bytes)
    {
        foreach (var value in bytes)
        {
            crc ^= value;
            for (var bit = 0; bit < 8; bit++)
                crc = (crc >> 1) ^ ((crc & 1) == 0 ? 0 : 0xedb88320u);
        }
        return crc;
    }
}
