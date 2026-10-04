using System.Text;

namespace RefurbishedDinosaurs.Core.Presentation;

/// <summary>An integer rectangle.</summary>
/// <param name="X">Left edge.</param>
/// <param name="Y">Top edge.</param>
/// <param name="Width">Width.</param>
/// <param name="Height">Height.</param>
public readonly record struct IntRectangle(int X, int Y, int Width, int Height)
{
    /// <summary>Whether the point lies inside; the right and bottom edges are excluded.</summary>
    public bool Contains(int x, int y) => x >= X && y >= Y && x < X + Width && y < Y + Height;
}

/// <summary>Fits a fixed logical resolution, such as 640 × 480, into a window while keeping its aspect ratio.</summary>
public static class ViewportScaler
{
    /// <summary>
    /// The centred rectangle the logical screen fills in the viewport. With
    /// <paramref name="integerScaling"/> and room for at least 1×, the scale is rounded down to a whole number.
    /// </summary>
    /// <param name="viewportWidth">Window width in pixels.</param>
    /// <param name="viewportHeight">Window height in pixels.</param>
    /// <param name="logicalWidth">The game's logical width.</param>
    /// <param name="logicalHeight">The game's logical height.</param>
    /// <param name="integerScaling">Whether to use whole-number scales only.</param>
    public static IntRectangle Destination(
        int viewportWidth, int viewportHeight, int logicalWidth, int logicalHeight,
        bool integerScaling)
    {
        if (viewportWidth <= 0 || viewportHeight <= 0 || logicalWidth <= 0 || logicalHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(viewportWidth), "Viewport and logical dimensions must be positive.");
        var scale = Math.Min(viewportWidth / (double)logicalWidth, viewportHeight / (double)logicalHeight);
        if (integerScaling && scale >= 1) scale = Math.Floor(scale);
        var width = Math.Max(1, (int)Math.Round(logicalWidth * scale));
        var height = Math.Max(1, (int)Math.Round(logicalHeight * scale));
        return new((viewportWidth - width) / 2, (viewportHeight - height) / 2, width, height);
    }

    /// <summary>Converts a window position, such as the mouse, to logical coordinates. Positions outside the destination map outside the logical screen.</summary>
    /// <param name="x">Window x.</param>
    /// <param name="y">Window y.</param>
    /// <param name="destination">The rectangle from <see cref="Destination"/>.</param>
    /// <param name="logicalWidth">The game's logical width.</param>
    /// <param name="logicalHeight">The game's logical height.</param>
    public static (int X, int Y) ToLogical(
        int x, int y, IntRectangle destination, int logicalWidth, int logicalHeight)
    {
        if (destination.Width <= 0 || destination.Height <= 0 || logicalWidth <= 0 || logicalHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(destination));
        return ((int)Math.Floor((x - destination.X) * logicalWidth / (double)destination.Width),
            (int)Math.Floor((y - destination.Y) * logicalHeight / (double)destination.Height));
    }
}

/// <summary>Word wrapping for fixed-width text displays.</summary>
public static class FixedWidthText
{
    /// <summary>
    /// Wraps each paragraph at word boundaries to at most <paramref name="maximumColumns"/> characters per
    /// line, with <c>\n</c> line breaks. Runs of whitespace collapse to one space, and a word longer than
    /// the limit gets a line of its own.
    /// </summary>
    public static string Wrap(string text, int maximumColumns)
    {
        ArgumentNullException.ThrowIfNull(text);
        if (maximumColumns <= 0) throw new ArgumentOutOfRangeException(nameof(maximumColumns));
        var result = new StringBuilder(text.Length + text.Length / maximumColumns);
        var paragraphs = text.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n').Split('\n');
        for (var paragraphIndex = 0; paragraphIndex < paragraphs.Length; paragraphIndex++)
        {
            if (paragraphIndex > 0) result.Append('\n');
            var lineLength = 0;
            foreach (var word in paragraphs[paragraphIndex].Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries))
            {
                if (lineLength == 0) { result.Append(word); lineLength = word.Length; }
                else if (lineLength + 1 + word.Length <= maximumColumns)
                { result.Append(' ').Append(word); lineLength += word.Length + 1; }
                else { result.Append('\n').Append(word); lineLength = word.Length; }
            }
        }
        return result.ToString();
    }
}
