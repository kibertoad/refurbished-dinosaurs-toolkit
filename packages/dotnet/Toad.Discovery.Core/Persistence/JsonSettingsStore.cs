using System.Text.Json;
using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Persistence;

/// <summary>
/// Keeps settings in a JSON file with a backup copy, so a damaged or unsupported file falls back to the
/// last good one and then to defaults.
/// </summary>
/// <typeparam name="T">The settings type.</typeparam>
/// <param name="path">The settings file.</param>
/// <param name="jsonOptions">Serializer options; indented output by default.</param>
public sealed class JsonSettingsStore<T>(string path, JsonSerializerOptions? jsonOptions = null)
    where T : class
{
    private readonly string _path = Path.GetFullPath(path);
    private readonly JsonSerializerOptions _jsonOptions = jsonOptions ?? new() { WriteIndented = true };

    /// <summary>The backup file: the settings path with <c>.bak</c> appended.</summary>
    public string BackupPath => _path + ".bak";

    /// <summary>
    /// Loads the settings file, or the backup if the file is missing, unreadable or unsupported, or else
    /// <paramref name="createDefault"/>.
    /// </summary>
    /// <param name="createDefault">Builds settings when no file is usable.</param>
    /// <param name="isSupported">Whether loaded settings can be used as they are.</param>
    /// <param name="migrate">Converts unsupported settings; the result must pass <paramref name="isSupported"/>.</param>
    public T Load(Func<T> createDefault, Func<T, bool> isSupported, Func<T, T?>? migrate = null)
    {
        ArgumentNullException.ThrowIfNull(createDefault);
        ArgumentNullException.ThrowIfNull(isSupported);
        return TryLoad(_path, isSupported, migrate) ??
               TryLoad(BackupPath, isSupported, migrate) ?? createDefault();
    }

    /// <summary>
    /// Copies the current file to the backup if it is usable, then writes <paramref name="settings"/>
    /// atomically.
    /// </summary>
    /// <exception cref="InvalidDataException"><paramref name="settings"/> fails <paramref name="isSupported"/>.</exception>
    public void Save(T settings, Func<T, bool> isSupported)
    {
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(isSupported);
        if (!isSupported(settings)) throw new InvalidDataException("Cannot save unsupported settings.");
        if (TryLoad(_path, isSupported, migrate: null) is not null)
            AtomicFile.Copy(_path, BackupPath);
        AtomicFile.WriteAllText(_path, JsonSerializer.Serialize(settings, _jsonOptions));
    }

    private T? TryLoad(string candidate, Func<T, bool> isSupported, Func<T, T?>? migrate)
    {
        if (!File.Exists(candidate)) return null;
        try
        {
            var value = JsonSerializer.Deserialize<T>(File.ReadAllText(candidate), _jsonOptions);
            if (value is null) return null;
            if (isSupported(value)) return value;
            var migrated = migrate?.Invoke(value);
            return migrated is not null && isSupported(migrated) ? migrated : null;
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException or
                                      JsonException or NotSupportedException or ArgumentException)
        {
            return null;
        }
    }
}
