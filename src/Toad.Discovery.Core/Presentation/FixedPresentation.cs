using System.Text;

namespace Toad.Discovery.Core.Presentation;

public readonly record struct IntRectangle(int X, int Y, int Width, int Height)
{
    public bool Contains(int x, int y) => x >= X && y >= Y && x < X + Width && y < Y + Height;
}

public static class ViewportScaler
{
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

    public static (int X, int Y) ToLogical(
        int x, int y, IntRectangle destination, int logicalWidth, int logicalHeight)
    {
        if (destination.Width <= 0 || destination.Height <= 0 || logicalWidth <= 0 || logicalHeight <= 0)
            throw new ArgumentOutOfRangeException(nameof(destination));
        return ((int)Math.Floor((x - destination.X) * logicalWidth / (double)destination.Width),
            (int)Math.Floor((y - destination.Y) * logicalHeight / (double)destination.Height));
    }
}

public static class FixedWidthText
{
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
