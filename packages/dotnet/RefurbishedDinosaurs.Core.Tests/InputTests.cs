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
        var bindings = new InputBindings<string, string>(new Dictionary<string, IEnumerable<string>>
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
        var bindings = new InputBindings<string, string>(new Dictionary<string, IEnumerable<string>>
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
        var common = new InputBindings<string, int>(new Dictionary<string, IEnumerable<int>>
        { ["confirm"] = new[] { 1 }, ["cancel"] = new[] { 2 } });
        var context = new InputBindings<string, int>(new Dictionary<string, IEnumerable<int>>
        { ["confirm"] = new[] { 3 } });
        var merged = common.Overlay(context);
        Assert.Equal(new[] { 3 }, merged.ButtonsFor("confirm"));
        Assert.Equal(new[] { 2 }, merged.ButtonsFor("cancel"));
        Assert.Empty(merged.ButtonsFor("missing"));
    }
}
