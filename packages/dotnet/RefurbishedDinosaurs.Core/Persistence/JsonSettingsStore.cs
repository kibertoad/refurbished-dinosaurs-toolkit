using System.Text.Json;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.Core.Persistence;

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
    private readonly int _maximumBytes = int.MaxValue;

    /// <summary>Maximum admitted UTF-8 file size; defaults to Int32.MaxValue for compatibility. Applies to reads and writes.</summary>
    /// <exception cref="ArgumentOutOfRangeException">The value is zero or negative.</exception>
    public int MaximumBytes
    {
        get => _maximumBytes;
        init
        {
            ArgumentOutOfRangeException.ThrowIfNegativeOrZero(value);
            _maximumBytes = value;
        }
    }

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
        => LoadResult(createDefault, isSupported, migrate).Value;

    /// <summary>Loads settings with explicit primary, backup or default provenance.</summary>
    public SettingsResult<T> LoadResult(Func<T> createDefault, Func<T, bool> isSupported,
        Func<T, T?>? migrate = null)
    {
        ArgumentNullException.ThrowIfNull(createDefault);
        ArgumentNullException.ThrowIfNull(isSupported);
        var primary = TryLoad(_path, isSupported, migrate);
        if (primary is not null) return new(primary, SettingsSource.Primary);
        var backup = TryLoad(BackupPath, isSupported, migrate);
        return backup is not null ? new(backup, SettingsSource.Backup) : new(createDefault(), SettingsSource.Default);
    }

    /// <summary>
    /// Copies the current file to the backup if it is usable, then writes <paramref name="settings"/>
    /// atomically.
    /// </summary>
    /// <exception cref="InvalidDataException"><paramref name="settings"/> fails <paramref name="isSupported"/>.</exception>
    public void Save(T settings, Func<T, bool> isSupported) => Save(settings, isSupported, null);

    /// <summary>Saves settings, also preserving a primary admitted by the supplied migration.</summary>
    public void Save(T settings, Func<T, bool> isSupported, Func<T, T?>? migrate)
    {
        ArgumentNullException.ThrowIfNull(settings);
        ArgumentNullException.ThrowIfNull(isSupported);
        if (!isSupported(settings)) throw new InvalidDataException("Cannot save unsupported settings.");
        var bytes = JsonSerializer.SerializeToUtf8Bytes(settings, _jsonOptions);
        if (bytes.Length > MaximumBytes) throw new InvalidDataException("Settings exceed their size limit.");
        if (TryLoad(_path, isSupported, migrate) is not null)
            AtomicFile.Copy(_path, BackupPath);
        AtomicFile.WriteBytes(_path, bytes);
    }

    private static ReadOnlySpan<byte> Utf8Bom => [0xEF, 0xBB, 0xBF];

    private T? TryLoad(string candidate, Func<T, bool> isSupported, Func<T, T?>? migrate)
    {
        if (!File.Exists(candidate)) return null;
        try
        {
            // File.ReadAllText dropped a UTF-8 byte-order mark; the byte overloads reject one.
            ReadOnlySpan<byte> json = RecoverableFile.ReadBounded(candidate, MaximumBytes);
            if (json.StartsWith(Utf8Bom)) json = json[Utf8Bom.Length..];
            var value = JsonSerializer.Deserialize<T>(json, _jsonOptions);
            if (value is null) return null;
            if (isSupported(value)) return value;
            var migrated = migrate?.Invoke(value);
            return migrated is not null && isSupported(migrated) ? migrated : null;
        }
        catch (Exception error) when (error is IOException or InvalidDataException or UnauthorizedAccessException or
                                      JsonException or NotSupportedException or ArgumentException)
        {
            return null;
        }
    }
}
