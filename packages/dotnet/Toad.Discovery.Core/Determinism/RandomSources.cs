namespace Toad.Discovery.Core.Determinism;

/// <summary>A source of random integers, so game rules can take the original's generator or a test double.</summary>
public interface IRandomSource
{
    /// <summary>Returns a value in <c>0..exclusiveMaximum-1</c>.</summary>
    int NextInt(int exclusiveMaximum);
}

/// <summary>The rand() sequence used by statically linked legacy Microsoft C runtimes.</summary>
public sealed class MsvcRandom : IRandomSource
{
    private const uint Multiplier = 0x343fd;
    private const uint Addend = 0x269ec3;
    private uint _state;

    /// <summary>Starts the sequence as <c>srand(seed)</c> does.</summary>
    public MsvcRandom(int seed) => _state = unchecked((uint)seed);

    /// <summary>Resumes a sequence from a saved <see cref="State"/> and <see cref="ConsumptionCount"/>.</summary>
    public MsvcRandom(uint state, long consumptionCount)
    {
        if (consumptionCount < 0) throw new ArgumentOutOfRangeException(nameof(consumptionCount));
        _state = state;
        ConsumptionCount = consumptionCount;
    }

    /// <summary>The full 32-bit generator state, for saving and replay.</summary>
    public uint State => _state;
    /// <summary>How many values have been drawn since seeding.</summary>
    public long ConsumptionCount { get; private set; }

    /// <summary>Advances the state and returns the next <c>rand()</c> value, <c>0..32767</c>.</summary>
    public int NextRaw()
    {
        _state = unchecked(_state * Multiplier + Addend);
        ConsumptionCount++;
        return (int)((_state >> 16) & 0x7fff);
    }

    /// <summary>Returns <c>rand() % exclusiveMaximum</c>, as the original code computes it.</summary>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="exclusiveMaximum"/> is not positive.</exception>
    public int NextInt(int exclusiveMaximum)
    {
        if (exclusiveMaximum <= 0) throw new ArgumentOutOfRangeException(nameof(exclusiveMaximum));
        return NextRaw() % exclusiveMaximum;
    }
}
