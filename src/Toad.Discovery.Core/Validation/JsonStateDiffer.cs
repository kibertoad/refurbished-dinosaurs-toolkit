using System.Text.Json;

namespace Toad.Discovery.Core.Validation;

public enum StateDifferenceKind { Added, Removed, Changed }

public sealed record StateDifference(
    string Path, string Label, StateDifferenceKind Kind, string? Expected, string? Actual);

public static class JsonStateDiffer
{
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
