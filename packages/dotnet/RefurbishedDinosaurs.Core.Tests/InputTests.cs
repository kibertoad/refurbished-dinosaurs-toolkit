using RefurbishedDinosaurs.Core.Input;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class InputTests
{
    [Fact]
    public void SnapshotCopiesStateAndDistinguishesHeldPressedReleasedAndOppositeAxes()
    {
        var current = new List<string> { "left", "right", "held" };
        var state = new InputState<string>(current, new[] { "held", "released" });
        current.Clear();
        Assert.True(state.IsDown("held"));
        Assert.False(state.IsPressed("held"));
        Assert.True(state.IsPressed("left"));
        Assert.True(state.IsReleased("released"));
        Assert.Equal(0, state.Axis("left", "right"));
        Assert.False(state.IsReleased("missing"));
        Assert.Equal(-1, state.Axis("left", "missing"));
        Assert.Equal(1, state.Axis("missing", "left"));
    }

    [Fact]
    public void AlternativesShareOnePressedEdge()
    {
        var bindings = InputBindings<string, string>.Create(new Dictionary<string, string[]>
        { ["fire"] = new[] { "ctrl", "mouse" } });
        Assert.True(bindings.IsPressed("fire", new(new[] { "ctrl" }, Array.Empty<string>())));
        Assert.False(bindings.IsPressed("fire", new(new[] { "ctrl", "mouse" }, new[] { "ctrl" })));
        Assert.False(bindings.IsPressed("fire", new(new[] { "mouse" }, new[] { "ctrl" })));
        Assert.True(bindings.IsPressed("fire", new(new[] { "ctrl", "mouse" }, Array.Empty<string>())));
        Assert.False(bindings.IsPressed("missing", new(new[] { "ctrl" }, Array.Empty<string>())));
    }

    [Fact]
    public void RebindingIsImmutableAndConflictPolicyIsExplicit()
    {
        var bindings = InputBindings<string, string>.Create(new Dictionary<string, string[]>
        { ["jump"] = new[] { "space" }, ["fire"] = new[] { "ctrl", "mouse" } });
        Assert.Throws<ArgumentException>(() => bindings.Rebind("jump", new[] { "ctrl" }, true));
        var changed = bindings.Rebind("jump", new[] { "j", "j" }, true);
        Assert.Equal(new[] { "space" }, bindings.ButtonsFor("jump"));
        Assert.Equal(new[] { "j" }, changed.ButtonsFor("jump"));
        Assert.True(bindings.IsPressed("fire", new(new[] { "mouse" }, Array.Empty<string>())));
        Assert.Equal(new[] { "ctrl" }, bindings.Rebind("jump", new[] { "ctrl" }, false).ButtonsFor("jump"));
    }

    [Fact]
    public void ContextOverlayReplacesOnlyItsActions()
    {
        var common = InputBindings<string, int>.Create(new Dictionary<string, int[]>
        { ["confirm"] = new[] { 1 }, ["cancel"] = new[] { 2 } });
        var context = InputBindings<string, int>.Create(new Dictionary<string, int[]>
        { ["confirm"] = new[] { 3 } });
        var merged = common.Overlay(context);
        Assert.Equal(new[] { 3 }, merged.ButtonsFor("confirm"));
        Assert.Equal(new[] { 2 }, merged.ButtonsFor("cancel"));
        Assert.Empty(merged.ButtonsFor("missing"));
    }

    [Fact]
    public void BindingsAcceptAnyButtonCollectionType()
    {
        var bindings = InputBindings<string, string>.Create(new Dictionary<string, List<string>>
        { ["jump"] = ["space", "space", "w"] });
        Assert.Equal(new[] { "space", "w" }, bindings.ButtonsFor("jump"));
    }

    [Fact]
    public void SnapshotsMustUseTheBindingsButtonComparer()
    {
        var bindings = InputBindings<string, string>.Create(new Dictionary<string, string[]>
        { ["jump"] = ["Space"] }, buttonComparer: StringComparer.OrdinalIgnoreCase);
        Assert.Throws<ArgumentException>(() => bindings.IsPressed("jump", new(["space"], [])));
        Assert.True(bindings.IsPressed("jump", new(["space"], [], StringComparer.OrdinalIgnoreCase)));
        var ordinal = InputBindings<string, string>.Create(new Dictionary<string, string[]>
        { ["jump"] = ["space"] }, buttonComparer: StringComparer.Ordinal);
        Assert.True(ordinal.IsPressed("jump", new(["space"], [], StringComparer.Ordinal)));
    }

    [Fact]
    public void OverlayRejectsContextsWithOtherComparers()
    {
        var common = InputBindings<string, int>.Create(new Dictionary<string, int[]>
        { ["confirm"] = [1] }, StringComparer.OrdinalIgnoreCase);
        var ordinal = InputBindings<string, int>.Create(new Dictionary<string, int[]>
        { ["Confirm"] = [2], ["confirm"] = [3] }, StringComparer.Ordinal);
        Assert.Throws<ArgumentException>(() => common.Overlay(ordinal));
        var buttons = InputBindings<string, int>.Create(new Dictionary<string, int[]>
        { ["confirm"] = [2] }, StringComparer.OrdinalIgnoreCase, new ParityComparer());
        Assert.Throws<ArgumentException>(() => common.Overlay(buttons));
    }

    [Fact]
    public void PressedQueriesDoNotAllocate()
    {
        var bindings = InputBindings<string, string>.Create(new Dictionary<string, string[]>
        { ["fire"] = ["ctrl", "mouse"] });
        var state = new InputState<string>(["mouse"], []);
        Assert.True(bindings.IsPressed("fire", state));
        var before = GC.GetAllocatedBytesForCurrentThread();
        for (var i = 0; i < 100; i++) bindings.IsPressed("fire", state);
        Assert.Equal(before, GC.GetAllocatedBytesForCurrentThread());
    }

    private sealed class ParityComparer : IEqualityComparer<int>
    {
        public bool Equals(int x, int y) => x % 2 == y % 2;
        public int GetHashCode(int obj) => obj % 2;
    }
}
