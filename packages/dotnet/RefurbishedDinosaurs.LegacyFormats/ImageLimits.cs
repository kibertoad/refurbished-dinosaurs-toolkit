namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>Limits the image decoders in this package share.</summary>
public static class ImageLimits
{
    /// <summary>
    /// The largest image, in pixels, that <see cref="BmpDecoder.Decode"/>, both <see cref="PcxDecoder"/>
    /// overloads of <c>Decode</c> and <see cref="RawIndexedImageDecoder.Decode"/> accept when no
    /// <c>maximumPixels</c> is passed: 16,777,216, which is 4096 by 4096.
    /// </summary>
    public const int DefaultMaximumPixels = 16_777_216;
}
