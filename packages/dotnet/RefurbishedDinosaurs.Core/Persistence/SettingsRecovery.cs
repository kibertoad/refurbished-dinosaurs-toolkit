namespace RefurbishedDinosaurs.Core.Persistence;

/// <summary>The source of a recovered setting or settings document.</summary>
public enum SettingsSource
{
    /// <summary>The primary document supplied the value.</summary>
    Primary,
    /// <summary>The backup document supplied the value.</summary>
    Backup,
    /// <summary>The caller's default supplied the value.</summary>
    Default
}

/// <summary>A recovered value and its provenance.</summary>
/// <typeparam name="T">The setting or document type.</typeparam>
/// <param name="Value">The usable value.</param>
/// <param name="Source">The successful source.</param>
public sealed record SettingsResult<T>(T Value, SettingsSource Source);

/// <summary>Supports caller-controlled field admission without imposing settings schemas.</summary>
public static class SettingsRecovery
{
    /// <summary>
    /// Selects the first present, admitted field. Call separately per value-type field for partial
    /// recovery; whole-document fallback is <see cref="JsonSettingsStore{T}.LoadResult"/>. Invalid
    /// values do not become defaults until both generations have been tried.
    /// </summary>
    public static SettingsResult<T> Select<T>(T? primary, T? backup, T fallback, Func<T, bool> isValid)
        where T : struct
    {
        ArgumentNullException.ThrowIfNull(isValid);
        if (primary is { } first && isValid(first)) return new(first, SettingsSource.Primary);
        if (backup is { } second && isValid(second)) return new(second, SettingsSource.Backup);
        return new(fallback, SettingsSource.Default);
    }
}
