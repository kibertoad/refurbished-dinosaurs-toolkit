namespace Toad.Discovery.Core.Determinism;

public interface IRandomSource
{
    int NextInt(int exclusiveMaximum);
}

/// <summary>The rand() sequence used by statically linked legacy Microsoft C runtimes.</summary>
public sealed class MsvcRandom : IRandomSource
{
    private const uint Multiplier = 0x343fd;
    private const uint Addend = 0x269ec3;
    private uint _state;

    public MsvcRandom(int seed) => _state = unchecked((uint)seed);

    public MsvcRandom(uint state, long consumptionCount)
    {
        if (consumptionCount < 0) throw new ArgumentOutOfRangeException(nameof(consumptionCount));
        _state = state;
        ConsumptionCount = consumptionCount;
    }

    public uint State => _state;
    public long ConsumptionCount { get; private set; }

    public int NextRaw()
    {
        _state = unchecked(_state * Multiplier + Addend);
        ConsumptionCount++;
        return (int)((_state >> 16) & 0x7fff);
    }

    public int NextInt(int exclusiveMaximum)
    {
        if (exclusiveMaximum <= 0) throw new ArgumentOutOfRangeException(nameof(exclusiveMaximum));
        return NextRaw() % exclusiveMaximum;
    }
}
