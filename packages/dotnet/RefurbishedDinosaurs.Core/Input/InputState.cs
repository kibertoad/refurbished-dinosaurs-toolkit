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

    /// <summary>Whether a button is held in the current snapshot.</summary>
    public bool IsDown(TButton button) => _current.Contains(button);
    /// <summary>Whether a button became held in this snapshot.</summary>
    public bool IsPressed(TButton button) => _current.Contains(button) && !_previous.Contains(button);
    /// <summary>Whether a button became released in this snapshot.</summary>
    public bool IsReleased(TButton button) => !_current.Contains(button) && _previous.Contains(button);
    /// <summary>Whether any supplied button became held.</summary>
    public bool AnyPressed(IEnumerable<TButton> buttons)
    {
        ArgumentNullException.ThrowIfNull(buttons);
        return buttons.Any(IsPressed);
    }
    /// <summary>Returns negative, zero or positive one; opposing held buttons cancel.</summary>
    public int Axis(TButton negative, TButton positive) => (IsDown(positive) ? 1 : 0) - (IsDown(negative) ? 1 : 0);
}
