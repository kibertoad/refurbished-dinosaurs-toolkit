using RefurbishedDinosaurs.Media.Playback;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class MoviePlaybackTests
{
    [Fact]
    public void DecodesEveryInterveningFrameAndHoldsFinalInterval()
    {
        var movie = new MoviePlayback(4, TimeSpan.FromMilliseconds(10));
        var decoded = new List<int>();
        movie.Advance(TimeSpan.Zero, decoded.Add);
        Assert.Equal([0], decoded);
        movie.Advance(TimeSpan.FromMilliseconds(35), decoded.Add);
        Assert.Equal([0, 1, 2, 3], decoded);
        Assert.False(movie.IsComplete);
        movie.Advance(TimeSpan.FromMilliseconds(5), decoded.Add);
        Assert.True(movie.IsComplete);
        Assert.Equal(4, decoded.Count);
    }

    [Fact]
    public void DelayPauseAndSkipRemainHostControlled()
    {
        var movie = new MoviePlayback(2, TimeSpan.FromTicks(10), TimeSpan.FromTicks(5));
        var decoded = new List<int>();
        movie.Advance(TimeSpan.FromTicks(4), decoded.Add);
        Assert.Empty(decoded);
        movie.Pause();
        movie.Advance(TimeSpan.MaxValue, decoded.Add);
        Assert.Empty(decoded);
        movie.Resume();
        movie.Advance(TimeSpan.FromTicks(1), decoded.Add);
        Assert.Equal([0], decoded);
        movie.Skip();
        movie.Advance(TimeSpan.MaxValue, decoded.Add);
        Assert.Equal([0], decoded);
    }

    [Fact]
    public void HugeAdvanceSaturatesWithoutDroppingDependentFrames()
    {
        var movie = new MoviePlayback(3, TimeSpan.FromTicks(1));
        var decoded = new List<int>();
        movie.Advance(TimeSpan.MaxValue, decoded.Add);
        Assert.Equal([0, 1, 2], decoded);
        Assert.True(movie.IsComplete);
    }

    [Fact]
    public void DecodeFailureInvalidatesPlayback()
    {
        var movie = new MoviePlayback(3, TimeSpan.FromTicks(1));
        Assert.Throws<IOException>(() => movie.Advance(TimeSpan.FromTicks(1), _ => throw new IOException()));
        Assert.Throws<InvalidOperationException>(() => movie.Advance(TimeSpan.Zero, _ => { }));
        Assert.Equal(-1, movie.FrameIndex);
    }

    [Fact]
    public void RejectsInvalidTiming()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new MoviePlayback(0, TimeSpan.FromTicks(1)));
        Assert.Throws<ArgumentOutOfRangeException>(() => new MoviePlayback(1, TimeSpan.Zero));
        Assert.Throws<ArgumentOutOfRangeException>(() => new MoviePlayback(2, TimeSpan.MaxValue));
        Assert.Throws<ArgumentOutOfRangeException>(() => new MoviePlayback(1, TimeSpan.FromTicks(1), TimeSpan.FromTicks(-1)));
        var movie = new MoviePlayback(1, TimeSpan.FromTicks(1));
        Assert.Throws<ArgumentOutOfRangeException>(() => movie.Advance(TimeSpan.FromTicks(-1), _ => { }));
    }
}
