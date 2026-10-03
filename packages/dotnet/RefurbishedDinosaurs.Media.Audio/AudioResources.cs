namespace RefurbishedDinosaurs.Media.Audio;

/// <summary>
/// Lazily creates and owns disposable audio resources. A failed factory is not cached. Use on the
/// backend's owning thread and dispose voices before resources. Capacity and eviction policy belong
/// to the host; call Remove only after voices using that resource have ended.
/// </summary>
/// <typeparam name="TKey">A game-defined resource identity.</typeparam>
/// <typeparam name="TResource">An audio backend resource.</typeparam>
public sealed class AudioResourceCache<TKey, TResource> : IDisposable where TKey : notnull where TResource : class, IDisposable
{
    private readonly Dictionary<TKey, TResource> _resources = new();
    private bool _disposed;

    /// <summary>Gets or creates a resource. Factories must return a non-null owned instance.</summary>
    public TResource GetOrCreate(TKey key, Func<TKey, TResource> create)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(create);
        if (_resources.TryGetValue(key, out var resource)) return resource;
        resource = create(key) ?? throw new InvalidOperationException("Audio factory returned null.");
        _resources.Add(key, resource);
        return resource;
    }

    /// <summary>Disposes and removes one resource, returning whether it existed.</summary>
    public bool Remove(TKey key)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (!_resources.Remove(key, out var resource)) return false;
        resource.Dispose();
        return true;
    }

    /// <summary>Disposes cached resources once. All disposal attempts run even if one fails.</summary>
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        List<Exception>? failures = null;
        foreach (var resource in _resources.Values)
        {
            try { resource.Dispose(); }
            catch (Exception error) { (failures ??= new()).Add(error); }
        }
        _resources.Clear();
        if (failures is not null) throw new AggregateException(failures);
    }
}

/// <summary>Owns active audio voices while the host supplies stopped-state and stop behavior.</summary>
/// <typeparam name="TVoice">A backend voice.</typeparam>
public sealed class AudioVoices<TVoice> : IDisposable where TVoice : class, IDisposable
{
    private readonly List<TVoice> _voices = new();
    private bool _disposed;

    /// <summary>The number of admitted voices; call Reap to remove finished voices.</summary>
    public int Count => _voices.Count;

    /// <summary>Admits an owned voice. Capacity, routing and stealing policy remain with the caller.</summary>
    public void Add(TVoice voice)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(voice);
        if (_voices.Contains(voice)) throw new ArgumentException("Voice is already owned.", nameof(voice));
        _voices.Add(voice);
    }

    /// <summary>Disposes finished voices. A voice is removed before disposal so it is attempted once.</summary>
    public void Reap(Func<TVoice, bool> isStopped)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(isStopped);
        for (var index = _voices.Count - 1; index >= 0; index--)
        {
            if (!isStopped(_voices[index])) continue;
            var voice = _voices[index];
            _voices.RemoveAt(index);
            voice.Dispose();
        }
    }

    /// <summary>Visits admitted voices for host volume, pause or other backend operations.</summary>
    public void ForEach(Action<TVoice> action)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        ArgumentNullException.ThrowIfNull(action);
        foreach (var voice in _voices) action(voice);
    }

    /// <summary>Disposes voices once; backend disposal must stop playback. Attempts all voices.</summary>
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        List<Exception>? failures = null;
        foreach (var voice in _voices)
        {
            try { voice.Dispose(); }
            catch (Exception error) { (failures ??= new()).Add(error); }
        }
        _voices.Clear();
        if (failures is not null) throw new AggregateException(failures);
    }
}
