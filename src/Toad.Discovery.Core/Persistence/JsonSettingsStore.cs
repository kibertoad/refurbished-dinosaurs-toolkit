using System.Text.Json;
using Toad.Discovery.Core.IO;

namespace Toad.Discovery.Core.Persistence;

public sealed class JsonSettingsStore<T>(string path, JsonSerializerOptions? jsonOptions = null)
    where T : class
{
    private readonly string _path = Path.GetFullPath(path);
    private readonly JsonSerializerOptions _jsonOptions = jsonOptions ?? new() { WriteIndented = true };

    public string BackupPath => _path + ".bak";

    public T Load(Func<T> createDefault, Func<T, bool> isSupported, Func<T, T?>? migrate = null)
    {
        ArgumentNullException.ThrowIfNull(createDefault);
        ArgumentNullException.ThrowIfNull(isSupported);
        return TryLoad(_path, isSupported, migrate) ??
               TryLoad(BackupPath, isSupported, migrate) ?? createDefault();
    }

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
