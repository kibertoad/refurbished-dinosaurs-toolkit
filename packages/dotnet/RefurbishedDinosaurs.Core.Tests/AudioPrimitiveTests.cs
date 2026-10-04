using RefurbishedDinosaurs.LegacyFormats;
using RefurbishedDinosaurs.Media.Audio;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class AudioPrimitiveTests
{
    [Fact]
    public void ConversionHasExactExtremaAndRejectsAliasing()
    {
        Assert.Equal(new byte[] { 0, 128, 0, 0, 0, 127 }, Pcm16.FromUnsigned8(new byte[] { 0, 128, 255 }));
        Assert.Equal(new byte[] { 0, 128, 255, 127 }, Pcm16.Encode(new short[] { short.MinValue, short.MaxValue }));
        var bytes = new byte[8];
        Assert.Throws<ArgumentException>(() => Pcm16.FromUnsigned8(bytes.AsSpan(0, 4), bytes));
    }

    [Fact]
    public void WaveRoundTripsAndStreamsLoopsAndSeeksWithoutOwningBorrowedStream()
    {
        using var bytes = new MemoryStream();
        WavePcm16Writer.Write(bytes, new byte[] { 1, 2, 3, 4 }, 1, 8000);
        bytes.Position = 0;
        using (var reader = new WavePcm16Stream(bytes, leaveOpen: true))
        {
            var buffer = new byte[6];
            reader.ReadLooped(buffer);
            Assert.Equal(new byte[] { 1, 2, 3, 4, 1, 2 }, buffer);
            reader.Seek(4);
            Assert.Equal(0, reader.Read(buffer));
            Assert.Throws<ArgumentOutOfRangeException>(() => reader.Seek(1));
            Assert.Throws<ArgumentException>(() => reader.Read(new byte[3]));
        }
        Assert.True(bytes.CanRead);
        bytes.Position = 0;
        Assert.Equal(new byte[] { 1, 2, 3, 4 }, WavePcm16Reader.Read(bytes).Samples);
        bytes.Position = 0;
        Assert.Throws<InvalidDataException>(() => new WavePcm16Stream(bytes, true, 2));
    }

    [Fact]
    public void WriterAcceptsEmptyPcmAndPositiveRatesBeyondTheReaderProfile()
    {
        using var bytes = new MemoryStream();
        WavePcm16Writer.Write(bytes, Array.Empty<byte>(), 2, 96000);
        Assert.Equal(44, bytes.Length);
        Assert.Equal(36U, System.Buffers.Binary.BinaryPrimitives.ReadUInt32LittleEndian(bytes.ToArray().AsSpan(4)));
    }

    [Fact]
    public void IndexFailureDisposesOwnedStream()
    {
        var invalid = new MemoryStream(new byte[3]);
        Assert.Throws<InvalidDataException>(() => new WavePcm16Stream(invalid));
        Assert.False(invalid.CanRead);
    }

    [Fact]
    public void CacheRetriesFactoryFailuresAndDisposesOnlyOnce()
    {
        var cache = new AudioResourceCache<int, Resource>();
        Assert.Throws<IOException>(() => cache.GetOrCreate(1, _ => throw new IOException()));
        var resource = cache.GetOrCreate(1, _ => new Resource());
        Assert.Same(resource, cache.GetOrCreate(1, _ => throw new Exception()));
        cache.Dispose(); cache.Dispose();
        Assert.Equal(1, resource.Disposals);
        Assert.Throws<ObjectDisposedException>(() => cache.GetOrCreate(2, _ => new Resource()));
    }

    [Fact]
    public void VoicesDisposeStoppedInstancesBeforeShutdown()
    {
        var active = new Resource(); var stopped = new Resource();
        using var voices = new AudioVoices<Resource>();
        voices.Add(active); voices.Add(stopped);
        voices.Reap(value => ReferenceEquals(value, stopped));
        Assert.Equal(1, voices.Count);
        Assert.Equal(1, stopped.Disposals);
        voices.Dispose();
        Assert.Equal(1, active.Disposals);
        Assert.Equal(1, stopped.Disposals);
    }

    [Fact]
    public void EagerReaderLeavesInputAfterContainer()
    {
        using var bytes = new MemoryStream();
        WavePcm16Writer.Write(bytes, new byte[] { 1, 2, 3, 4 }, 2, 22050);
        bytes.WriteByte(0xAA);
        bytes.Position = 0;
        WavePcm16Reader.Read(bytes);
        Assert.Equal(48, bytes.Position);
    }

    [Fact]
    public void CddaWaveIsReadableAsCanonicalStereoPcm16()
    {
        using var source = new MemoryStream(new byte[CddaWave.BytesPerSector]);
        using var output = new MemoryStream();
        CddaWave.Write(source, output, 0, 1);
        output.Position = 0;
        using var reader = new WavePcm16Stream(output, leaveOpen: true);
        Assert.Equal((44100, 2, (long)CddaWave.BytesPerSector), (reader.SampleRate, reader.ChannelCount, reader.Length));
    }

    [Fact]
    public void CacheDisposesResourceCreatedAgainstADisposedOrReenteredCache()
    {
        var cache = new AudioResourceCache<int, Resource>();
        var orphan = new Resource();
        Assert.Throws<ObjectDisposedException>(() => cache.GetOrCreate(1, _ => { cache.Dispose(); return orphan; }));
        Assert.Equal(1, orphan.Disposals);

        var reentered = new AudioResourceCache<int, Resource>();
        var outer = new Resource();
        Assert.Throws<InvalidOperationException>(() => reentered.GetOrCreate(1, key =>
        {
            reentered.GetOrCreate(key, _ => new Resource());
            return outer;
        }));
        Assert.Equal(1, outer.Disposals);
        reentered.Dispose();
    }

    [Fact]
    public void ReapAttemptsEveryStoppedVoiceWhenOneDisposalFails()
    {
        var failing = new FailingResource(); var stopped = new Resource();
        using var voices = new AudioVoices<IDisposable>();
        voices.Add(stopped); voices.Add(failing);
        Assert.Throws<AggregateException>(() => voices.Reap(_ => true));
        Assert.Equal(0, voices.Count);
        Assert.Equal(1, stopped.Disposals);
    }

    [Fact]
    public void ReapKeepsDisposalFailuresWhenThePredicateFails()
    {
        var failing = new FailingResource(); var pending = new Resource();
        using var voices = new AudioVoices<IDisposable>();
        voices.Add(pending); voices.Add(failing);
        var error = Assert.Throws<AggregateException>(() =>
            voices.Reap(voice => ReferenceEquals(voice, failing) ? true : throw new TimeoutException()));
        Assert.Contains(error.InnerExceptions, inner => inner is IOException);
        Assert.Contains(error.InnerExceptions, inner => inner is TimeoutException);
    }

    [Fact]
    public void VoicesAdmitDistinctInstancesThatCompareEqual()
    {
        using var voices = new AudioVoices<EqualVoice>();
        var voice = new EqualVoice();
        voices.Add(voice);
        voices.Add(new EqualVoice());
        Assert.Equal(2, voices.Count);
        Assert.Throws<ArgumentException>(() => voices.Add(voice));
    }

    [Fact]
    public void WriterRejectsRatesWhoseByteRateIsNotRepresentable()
    {
        using var bytes = new MemoryStream();
        Assert.Throws<ArgumentOutOfRangeException>(() => WavePcm16Writer.Write(bytes, Array.Empty<byte>(), 2, int.MaxValue));
        Assert.Equal(0, bytes.Length);
    }

    [Fact]
    public void CddaWaveFlushesItsOutput()
    {
        using var source = new MemoryStream(new byte[CddaWave.BytesPerSector]);
        using var output = new FlushCountingStream();
        CddaWave.Write(source, output, 0, 1);
        Assert.Equal(1, output.Flushes);
    }

    private sealed class FlushCountingStream : MemoryStream
    {
        public int Flushes { get; private set; }
        public override void Flush() { Flushes++; base.Flush(); }
    }

    private sealed class EqualVoice : IDisposable
    {
        public override bool Equals(object? obj) => obj is EqualVoice;
        public override int GetHashCode() => 0;
        public void Dispose() { }
    }

    private sealed class FailingResource : IDisposable
    {
        public void Dispose() => throw new IOException();
    }

    private sealed class Resource : IDisposable
    {
        public int Disposals { get; private set; }
        public void Dispose() => Disposals++;
    }
}
