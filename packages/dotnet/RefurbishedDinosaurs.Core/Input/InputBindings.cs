using System.Collections.ObjectModel;

namespace RefurbishedDinosaurs.Core.Input;

/// <summary>Immutable action bindings with caller-defined identities and duplicate-token admission.</summary>
/// <typeparam name="TAction">A game action.</typeparam>
/// <typeparam name="TButton">A physical key or button token.</typeparam>
public sealed class InputBindings<TAction, TButton> where TAction : notnull where TButton : notnull
{
    private readonly Dictionary<TAction, ReadOnlyCollection<TButton>> _bindings;
    private readonly IEqualityComparer<TButton> _buttonComparer;

    /// <summary>Copies action bindings. Repeated tokens within an action are removed.</summary>
    public InputBindings(IEnumerable<KeyValuePair<TAction, IEnumerable<TButton>>> bindings,
        IEqualityComparer<TAction>? actionComparer = null, IEqualityComparer<TButton>? buttonComparer = null)
    {
        ArgumentNullException.ThrowIfNull(bindings);
        _buttonComparer = buttonComparer ?? EqualityComparer<TButton>.Default;
        _bindings = new(actionComparer);
        foreach (var pair in bindings)
        {
            ArgumentNullException.ThrowIfNull(pair.Value);
            _bindings.Add(pair.Key, Copy(pair.Value));
        }
    }

    private InputBindings(Dictionary<TAction, ReadOnlyCollection<TButton>> bindings, IEqualityComparer<TButton> buttonComparer)
    {
        _bindings = bindings;
        _buttonComparer = buttonComparer;
    }

    /// <summary>A read-only view of the buttons bound to an action, or an empty list.</summary>
    public IReadOnlyList<TButton> ButtonsFor(TAction action) => _bindings.TryGetValue(action, out var buttons)
        ? buttons : ReadOnlyCollection<TButton>.Empty;

    /// <summary>
    /// Whether the action became held: one of its alternatives is held now and none was held in the
    /// previous snapshot.
    /// </summary>
    public bool IsPressed(TAction action, InputState<TButton> state)
    {
        ArgumentNullException.ThrowIfNull(state);
        return _bindings.TryGetValue(action, out var buttons) && state.AnyPressed(buttons);
    }

    /// <summary>
    /// Replaces an existing action binding. Optionally rejects tokens used by another action.
    /// The caller admits rebindable actions and valid device tokens before calling this method.
    /// </summary>
    public InputBindings<TAction, TButton> Rebind(TAction action, IEnumerable<TButton> buttons, bool rejectConflicts)
    {
        ArgumentNullException.ThrowIfNull(buttons);
        if (!_bindings.ContainsKey(action)) throw new ArgumentException("Action is not bound.", nameof(action));
        var replacement = Copy(buttons);
        if (rejectConflicts && _bindings.Where(pair => !_bindings.Comparer.Equals(pair.Key, action))
            .SelectMany(pair => pair.Value).Any(button => replacement.Contains(button, _buttonComparer)))
            throw new ArgumentException("A token is already bound to another action.", nameof(buttons));
        var rebound = new Dictionary<TAction, ReadOnlyCollection<TButton>>(_bindings, _bindings.Comparer);
        rebound[action] = replacement;
        return new(rebound, _buttonComparer);
    }

    /// <summary>
    /// Returns bindings with context-specific actions replacing base actions. Build contexts once,
    /// outside the input loop. Multiple alternatives remain OR bindings, not key chords.
    /// </summary>
    public InputBindings<TAction, TButton> Overlay(InputBindings<TAction, TButton> context)
    {
        ArgumentNullException.ThrowIfNull(context);
        var merged = new Dictionary<TAction, ReadOnlyCollection<TButton>>(_bindings, _bindings.Comparer);
        foreach (var pair in context._bindings) merged[pair.Key] = Copy(pair.Value);
        return new(merged, _buttonComparer);
    }

    private ReadOnlyCollection<TButton> Copy(IEnumerable<TButton> buttons) =>
        Array.AsReadOnly(buttons.Distinct(_buttonComparer).ToArray());
}
