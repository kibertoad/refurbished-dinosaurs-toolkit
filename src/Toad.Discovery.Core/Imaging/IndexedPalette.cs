namespace Toad.Discovery.Core.Imaging;

public sealed record IndexedPalette(byte[] Rgb)
{
    public const int ColorCount = 256;
    public const int ByteSize = ColorCount * 3;

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

public static class IndexedPaletteDecoder
{
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
