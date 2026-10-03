namespace RefurbishedDinosaurs.Core.Input;

/// <summary>A copied input snapshot with deterministic button transition queries.</summary>
/// <typeparam name="TButton">The host's key, button or token identity.</typeparam>
public sealed class InputState<TButton> where TButton : notnull
{
    private readonly HashSet<TButton> _current;
    private readonly HashSet<TButton> _previous;

    /// <summary>Copies current and previous down buttons with an optional equality comparer.</summary>
    public InputState(IEnumerable<TButton> current, IEnumerable<TButton> previous,
        IEqualityComparer<TButton>? comparer = null)
    {
        ArgumentNullException.ThrowIfNull(current);
        ArgumentNullException.ThrowIfNull(previous);
        _current = new(current, comparer);
        _previous = new(previous, comparer);
    }

    /// <summary>The comparer that identifies buttons in both snapshots.</summary>
    public IEqualityComparer<TButton> Comparer => _current.Comparer;

    /// <summary>Whether a button is held in the current snapshot.</summary>
    public bool IsDown(TButton button) => _current.Contains(button);
    /// <summary>Whether a button became held in this snapshot.</summary>
    public bool IsPressed(TButton button) => _current.Contains(button) && !_previous.Contains(button);
    /// <summary>Whether a button became released in this snapshot.</summary>
    public bool IsReleased(TButton button) => !_current.Contains(button) && _previous.Contains(button);

    /// <summary>
    /// Whether the supplied alternatives, read as one OR binding, became held: at least one is held
    /// now and none was held in the previous snapshot. Pressing a second alternative while another
    /// is still held is not a new press. Does not allocate.
    /// </summary>
    public bool AnyPressed(ReadOnlySpan<TButton> buttons)
    {
        var down = false;
        foreach (var button in buttons)
        {
            if (_previous.Contains(button)) return false;
            down |= _current.Contains(button);
        }
        return down;
    }

    /// <summary>Returns negative, zero or positive one; opposing held buttons cancel.</summary>
    public int Axis(TButton negative, TButton positive) => (IsDown(positive) ? 1 : 0) - (IsDown(negative) ? 1 : 0);
}
