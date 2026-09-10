namespace Toad.Discovery.Core.Paths;

public sealed record RestorationPathOptions(
    string ApplicationDataDirectory,
    string ContentDirectory = "UserContent");

public static class RestorationPaths
{
    public static string ResolveImportedContent(
        RestorationPathOptions options,
        string applicationDirectory,
        string currentDirectory,
        string localApplicationData)
    {
        ArgumentNullException.ThrowIfNull(options);
        Validate(options.ApplicationDataDirectory, nameof(options.ApplicationDataDirectory));
        Validate(options.ContentDirectory, nameof(options.ContentDirectory));
        Validate(applicationDirectory, nameof(applicationDirectory));
        Validate(currentDirectory, nameof(currentDirectory));
        Validate(localApplicationData, nameof(localApplicationData));

        var parent = Directory.GetParent(applicationDirectory)?.FullName ?? applicationDirectory;
        foreach (var root in new[] { applicationDirectory, parent, currentDirectory })
        {
            var candidate = Path.Combine(root, options.ContentDirectory);
            if (Directory.Exists(candidate)) return candidate;
        }

        return Path.Combine(localApplicationData,
            options.ApplicationDataDirectory, options.ContentDirectory);
    }

    public static string ResolveStateRoot(
        RestorationPathOptions options,
        string localApplicationData)
    {
        ArgumentNullException.ThrowIfNull(options);
        Validate(options.ApplicationDataDirectory, nameof(options.ApplicationDataDirectory));
        Validate(localApplicationData, nameof(localApplicationData));
        return Path.Combine(localApplicationData, options.ApplicationDataDirectory);
    }

    private static void Validate(string value, string parameter) =>
        ArgumentException.ThrowIfNullOrWhiteSpace(value, parameter);
}
