namespace RefurbishedDinosaurs.Core.Paths;

/// <summary>Directory names a restoration uses.</summary>
/// <param name="ApplicationDataDirectory">The game's directory under the local application data folder.</param>
/// <param name="ContentDirectory">The name of the imported-content directory.</param>
public sealed record RestorationPathOptions(
    string ApplicationDataDirectory,
    string ContentDirectory = "UserContent");

/// <summary>Resolves where a restoration keeps imported content and saved state.</summary>
public static class RestorationPaths
{
    /// <summary>
    /// Returns the first existing content directory next to the application, in its parent, or in the
    /// current directory (portable installs), and otherwise the one under local application data.
    /// </summary>
    /// <param name="options">Directory names.</param>
    /// <param name="applicationDirectory">The directory the game runs from.</param>
    /// <param name="currentDirectory">The process's current directory.</param>
    /// <param name="localApplicationData">The user's local application data folder.</param>
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

    /// <summary>Returns the game's directory under local application data, for settings and saves.</summary>
    /// <param name="options">Directory names.</param>
    /// <param name="localApplicationData">The user's local application data folder.</param>
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
