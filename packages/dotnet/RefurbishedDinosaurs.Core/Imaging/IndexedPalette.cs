namespace RefurbishedDinosaurs.Core.Imaging;

/// <summary>A 256-colour palette.</summary>
/// <param name="Rgb">256 RGB triples, 8 bits per channel: <see cref="ByteSize"/> bytes.</param>
public sealed record IndexedPalette(byte[] Rgb)
{
    /// <summary>Number of colours.</summary>
    public const int ColorCount = 256;
    /// <summary>Size of <see cref="Rgb"/> in bytes.</summary>
    public const int ByteSize = ColorCount * 3;

    /// <summary>The colour at <paramref name="index"/>, 0..255.</summary>
    public (byte R, byte G, byte B) this[int index]
    {
        get
        {
            if ((uint)index >= ColorCount) throw new ArgumentOutOfRangeException(nameof(index));
            var offset = index * 3;
            return (Rgb[offset], Rgb[offset + 1], Rgb[offset + 2]);
        }
    }
}

/// <summary>Reads palettes stored as 768 bytes of RGB.</summary>
public static class IndexedPaletteDecoder
{
    /// <summary>
    /// Decodes 256 RGB triples. With <paramref name="channelBits"/> 6 (VGA DAC values 0..63), each channel
    /// is scaled to 8 bits by repeating its high bits.
    /// </summary>
    /// <exception cref="InvalidDataException"><paramref name="source"/> is not 768 bytes.</exception>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="channelBits"/> is not 6 or 8.</exception>
    public static IndexedPalette Decode(ReadOnlySpan<byte> source, int channelBits = 8)
    {
        if (source.Length != IndexedPalette.ByteSize)
            throw new InvalidDataException("An indexed RGB palette must contain exactly 256 RGB triples.");
        if (channelBits is not (6 or 8))
            throw new ArgumentOutOfRangeException(nameof(channelBits), "Only 6-bit and 8-bit channels are supported.");
        var rgb = source.ToArray();
        if (channelBits == 6)
            for (var index = 0; index < rgb.Length; index++)
                rgb[index] = (byte)((rgb[index] << 2) | (rgb[index] >> 4));
        return new(rgb);
    }
}
