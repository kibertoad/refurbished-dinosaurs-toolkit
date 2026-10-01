using System.Text.Json;

namespace Toad.Discovery.Core.Validation;

/// <summary>How a value differs between expected and actual state.</summary>
public enum StateDifferenceKind
{
    /// <summary>The value exists only in the actual state.</summary>
    Added,
    /// <summary>The value exists only in the expected state.</summary>
    Removed,
    /// <summary>The value exists in both with different contents or JSON types.</summary>
    Changed
}

/// <summary>One difference between two JSON documents.</summary>
/// <param name="Path">JSONPath of the value, such as <c>$.actors[2].hp</c>.</param>
/// <param name="Label">A readable name from the labels given to <see cref="JsonStateDiffer.Compare"/>, or the path.</param>
/// <param name="Kind">Whether the value was added, removed or changed.</param>
/// <param name="Expected">Raw JSON of the expected value, or <see langword="null"/> when added.</param>
/// <param name="Actual">Raw JSON of the actual value, or <see langword="null"/> when removed.</param>
public sealed record StateDifference(
    string Path, string Label, StateDifferenceKind Kind, string? Expected, string? Actual);

/// <summary>Compares a reference capture of game state with a restoration's, value by value.</summary>
public static class JsonStateDiffer
{
    /// <summary>
    /// Lists every difference between <paramref name="expected"/> and <paramref name="actual"/>. Object
    /// members are compared by name in ordinal order, arrays by index, and other values by JSON equality.
    /// </summary>
    /// <param name="expected">The reference state.</param>
    /// <param name="actual">The state to check.</param>
    /// <param name="labels">
    /// Readable names keyed by JSONPath. A path without its own label uses its nearest labelled parent's
    /// name followed by the rest of the path.
    /// </param>
    public static IReadOnlyList<StateDifference> Compare(
        JsonElement expected, JsonElement actual,
        IReadOnlyDictionary<string, string>? labels = null)
    {
        var differences = new List<StateDifference>();
        CompareValue(expected, actual, "$", labels, differences);
        return differences;
    }

    private static void CompareValue(JsonElement expected, JsonElement actual, string path,
        IReadOnlyDictionary<string, string>? labels, List<StateDifference> differences)
    {
        if (expected.ValueKind != actual.ValueKind)
        { Add(StateDifferenceKind.Changed, expected.GetRawText(), actual.GetRawText()); return; }
        if (expected.ValueKind == JsonValueKind.Object)
        {
            var left = expected.EnumerateObject().ToDictionary(x => x.Name, StringComparer.Ordinal);
            var right = actual.EnumerateObject().ToDictionary(x => x.Name, StringComparer.Ordinal);
            foreach (var name in left.Keys.Union(right.Keys, StringComparer.Ordinal).Order(StringComparer.Ordinal))
            {
                var child = Append(path, name);
                if (!left.TryGetValue(name, out var l)) differences.Add(new(child, Label(child, labels), StateDifferenceKind.Added, null, right[name].Value.GetRawText()));
                else if (!right.TryGetValue(name, out var r)) differences.Add(new(child, Label(child, labels), StateDifferenceKind.Removed, l.Value.GetRawText(), null));
                else CompareValue(l.Value, r.Value, child, labels, differences);
            }
            return;
        }
        if (expected.ValueKind == JsonValueKind.Array)
        {
            var left = expected.EnumerateArray().ToArray(); var right = actual.EnumerateArray().ToArray();
            for (var index = 0; index < Math.Max(left.Length, right.Length); index++)
            {
                var child = $"{path}[{index}]";
                if (index >= left.Length) differences.Add(new(child, Label(child, labels), StateDifferenceKind.Added, null, right[index].GetRawText()));
                else if (index >= right.Length) differences.Add(new(child, Label(child, labels), StateDifferenceKind.Removed, left[index].GetRawText(), null));
                else CompareValue(left[index], right[index], child, labels, differences);
            }
            return;
        }
        if (!JsonElement.DeepEquals(expected, actual)) Add(StateDifferenceKind.Changed, expected.GetRawText(), actual.GetRawText());
        return;
        void Add(StateDifferenceKind kind, string? left, string? right) =>
            differences.Add(new(path, Label(path, labels), kind, left, right));
    }

    private static string Label(string path, IReadOnlyDictionary<string, string>? labels)
    {
        if (labels is null) return path;
        if (labels.TryGetValue(path, out var exact)) return exact;
        var parent = path;
        while ((parent = Parent(parent)).Length > 0)
            if (labels.TryGetValue(parent, out var label)) return label + path[parent.Length..];
        return path;
    }

    private static string Parent(string path)
    {
        var split = Math.Max(path.LastIndexOf('['), path.LastIndexOf('.'));
        return split <= 0 ? string.Empty : path[..split];
    }

    private static string Append(string path, string name) =>
        name.All(character => char.IsLetterOrDigit(character) || character == '_')
            ? $"{path}.{name}" : $"{path}['{name.Replace("'", "\\'")}']";
}
