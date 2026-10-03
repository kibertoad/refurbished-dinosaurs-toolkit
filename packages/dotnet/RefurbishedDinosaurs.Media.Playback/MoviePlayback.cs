namespace RefurbishedDinosaurs.Media.Playback;

/// <summary>A fixed-cadence presentation clock that decodes dependent frames in ascending order.</summary>
/// <remarks>Owns no stream, texture, audio device or wall clock. Audio synchronization is the host's responsibility.</remarks>
public sealed class MoviePlayback
{
    private readonly int _frameCount;
    private readonly long _frameTicks;
    private readonly long _delayTicks;
    private readonly long _endTicks;
    private long _elapsed;
    private bool _faulted;

    /// <summary>Creates a clock with an optional delay before displaying frame zero.</summary>
    /// <remarks>The final frame remains visible for one complete frame interval. Ring frames must be excluded by the caller.</remarks>
    public MoviePlayback(int frameCount, TimeSpan frameDuration, TimeSpan initialDelay = default)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(frameCount);
        if (frameDuration <= TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(frameDuration));
        if (initialDelay < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(initialDelay));
        if (frameDuration.Ticks > (long.MaxValue - initialDelay.Ticks) / frameCount)
            throw new ArgumentOutOfRangeException(nameof(frameDuration), "Movie duration exceeds TimeSpan.");
        _frameCount = frameCount;
        _frameTicks = frameDuration.Ticks;
        _delayTicks = initialDelay.Ticks;
        _endTicks = _delayTicks + _frameTicks * frameCount;
    }

    /// <summary>Last decoded frame, or -1 before the first frame.</summary>
    public int FrameIndex { get; private set; } = -1;
    /// <summary>Whether the final interval elapsed or the host skipped playback.</summary>
    public bool IsComplete { get; private set; }
    /// <summary>Whether elapsed presentation time is paused.</summary>
    public bool IsPaused { get; private set; }

    /// <summary>Advances time and calls <paramref name="decodeFrame"/> for every intervening frame.</summary>
    /// <remarks>Upload only the last decoded frame after returning. A failed callback invalidates this clock;
    /// discard it and the decoder together. A large advance decodes through the final frame before completing.</remarks>
    public void Advance(TimeSpan elapsed, Action<int> decodeFrame)
    {
        ArgumentNullException.ThrowIfNull(decodeFrame);
        if (elapsed < TimeSpan.Zero) throw new ArgumentOutOfRangeException(nameof(elapsed));
        if (_faulted) throw new InvalidOperationException("Playback decoder failed; create a new clock and decoder.");
        if (IsComplete || IsPaused) return;
        _elapsed += Math.Min(elapsed.Ticks, _endTicks - _elapsed);
        if (_elapsed < _delayTicks) return;
        var target = (int)Math.Min(_frameCount - 1L, (_elapsed - _delayTicks) / _frameTicks);
        try
        {
            while (FrameIndex < target)
            {
                decodeFrame(FrameIndex + 1);
                FrameIndex++;
            }
        }
        catch
        {
            _faulted = true;
            throw;
        }
        IsComplete = _elapsed == _endTicks;
    }

    /// <summary>Stops advancing time; the host must pause audio separately.</summary>
    public void Pause() => IsPaused = true;
    /// <summary>Resumes time; the host must resume audio separately.</summary>
    public void Resume() => IsPaused = false;
    /// <summary>Completes playback without decoding any further frames; the host stops audio.</summary>
    public void Skip() => IsComplete = true;
}
