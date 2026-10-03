using System.Collections.ObjectModel;

namespace RefurbishedDinosaurs.Core.Input;

/// <summary>Immutable action bindings with caller-defined identities and duplicate-token admission.</summary>
/// <typeparam name="TAction">A game action.</typeparam>
/// <typeparam name="TButton">A physical key or button token.</typeparam>
public sealed class InputBindings<TAction, TButton> where TAction : notnull where TButton : notnull
{
    private readonly Dictionary<TAction, Binding> _bindings;
    private readonly IEqualityComparer<TButton> _buttonComparer;

    private InputBindings(Dictionary<TAction, Binding> bindings, IEqualityComparer<TButton> buttonComparer)
    {
        _bindings = bindings;
        _buttonComparer = buttonComparer;
    }

    /// <summary>
    /// Copies action bindings from any map whose values are button collections, such as
    /// <c>Dictionary&lt;TAction, TButton[]&gt;</c>. Repeated tokens within an action are removed.
    /// </summary>
    /// <typeparam name="TButtons">The collection type holding one action's alternatives.</typeparam>
    public static InputBindings<TAction, TButton> Create<TButtons>(IEnumerable<KeyValuePair<TAction, TButtons>> bindings,
        IEqualityComparer<TAction>? actionComparer = null, IEqualityComparer<TButton>? buttonComparer = null)
        where TButtons : IEnumerable<TButton>
    {
        ArgumentNullException.ThrowIfNull(bindings);
        buttonComparer ??= EqualityComparer<TButton>.Default;
        var copied = new Dictionary<TAction, Binding>(actionComparer);
        foreach (var pair in bindings)
        {
            ArgumentNullException.ThrowIfNull(pair.Value);
            copied.Add(pair.Key, Binding.Copy(pair.Value, buttonComparer));
        }
        return new(copied, buttonComparer);
    }

    /// <summary>The comparer that identifies actions.</summary>
    public IEqualityComparer<TAction> ActionComparer => _bindings.Comparer;

    /// <summary>The comparer that identifies button tokens. Snapshots queried by these bindings must use it.</summary>
    public IEqualityComparer<TButton> ButtonComparer => _buttonComparer;

    /// <summary>A read-only view of the buttons bound to an action, or an empty list.</summary>
    public IReadOnlyList<TButton> ButtonsFor(TAction action) => _bindings.TryGetValue(action, out var binding)
        ? binding.View : ReadOnlyCollection<TButton>.Empty;

    /// <summary>
    /// Whether the action became held: one of its alternatives is held now and none was held in the
    /// previous snapshot. Does not allocate.
    /// </summary>
    /// <exception cref="ArgumentException">The snapshot uses a different button comparer.</exception>
    public bool IsPressed(TAction action, InputState<TButton> state)
    {
        ArgumentNullException.ThrowIfNull(state);
        if (!Equals(state.Comparer, _buttonComparer))
            throw new ArgumentException("The snapshot uses a different button comparer than the bindings.", nameof(state));
        return _bindings.TryGetValue(action, out var binding) && state.AnyPressed(binding.Buttons);
    }

    /// <summary>
    /// Replaces an existing action binding. Optionally rejects tokens used by another action.
    /// The caller admits rebindable actions and valid device tokens before calling this method.
    /// </summary>
    public InputBindings<TAction, TButton> Rebind(TAction action, IEnumerable<TButton> buttons, bool rejectConflicts)
    {
        ArgumentNullException.ThrowIfNull(buttons);
        if (!_bindings.ContainsKey(action)) throw new ArgumentException("Action is not bound.", nameof(action));
        var replacement = Binding.Copy(buttons, _buttonComparer);
        if (rejectConflicts && _bindings.Where(pair => !_bindings.Comparer.Equals(pair.Key, action))
            .SelectMany(pair => pair.Value.Buttons).Any(button => replacement.Buttons.Contains(button, _buttonComparer)))
            throw new ArgumentException("A token is already bound to another action.", nameof(buttons));
        var rebound = new Dictionary<TAction, Binding>(_bindings, _bindings.Comparer);
        rebound[action] = replacement;
        return new(rebound, _buttonComparer);
    }

    /// <summary>
    /// Returns bindings with context-specific actions replacing base actions. Build contexts once,
    /// outside the input loop. Multiple alternatives remain OR bindings, not key chords.
    /// </summary>
    /// <exception cref="ArgumentException">The context uses a different action or button comparer.</exception>
    public InputBindings<TAction, TButton> Overlay(InputBindings<TAction, TButton> context)
    {
        ArgumentNullException.ThrowIfNull(context);
        if (!Equals(context._bindings.Comparer, _bindings.Comparer) || !Equals(context._buttonComparer, _buttonComparer))
            throw new ArgumentException("The context uses different comparers than the base bindings.", nameof(context));
        var merged = new Dictionary<TAction, Binding>(_bindings, _bindings.Comparer);
        foreach (var pair in context._bindings) merged[pair.Key] = pair.Value;
        return new(merged, _buttonComparer);
    }

    /// <summary>One action's distinct alternatives, with a read-only view made once.</summary>
    private sealed class Binding(TButton[] buttons)
    {
        public TButton[] Buttons { get; } = buttons;
        public ReadOnlyCollection<TButton> View { get; } = Array.AsReadOnly(buttons);

        public static Binding Copy(IEnumerable<TButton> buttons, IEqualityComparer<TButton> comparer) =>
            new(buttons.Distinct(comparer).ToArray());
    }
}
