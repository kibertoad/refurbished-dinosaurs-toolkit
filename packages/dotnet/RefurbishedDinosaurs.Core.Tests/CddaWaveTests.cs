using System.Buffers.Binary;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class CddaWaveTests
{
    private const int Sector = CddaWave.BytesPerSector;

    [Fact]
    public async Task WriteAndWriteAsyncCopyEachTrackExtentOfACueSheet()
    {
        // Track 3 declares a pregap with INDEX 00, so track 2 ends there; track 4 has none, so track 3
        // ends at track 4's INDEX 01; track 4 runs to the end of the image.
        var sheet = CueBinSheet.Parse("""
            FILE "disc.bin" BINARY
              TRACK 01 MODE1/2352
                INDEX 01 00:00:00
              TRACK 02 AUDIO
                INDEX 01 00:00:04
              TRACK 03 AUDIO
                INDEX 00 00:00:10
                INDEX 01 00:00:12
              TRACK 04 AUDIO
                INDEX 01 00:00:20
            """);
        var image = Patterned(30 * Sector);
        using var source = new MemoryStream(image);
        foreach (var (track, start, end) in new[] { (2, 4L, 10L), (3, 12L, 20L), (4, 20L, 30L) })
        {
            var extent = sheet.TrackExtent(track, image.Length / Sector);
            Assert.Equal((start, end), (extent.StartSector, extent.EndSector));
            var expected = image.AsSpan((int)(start * Sector), (int)((end - start) * Sector)).ToArray();

            using var output = new MemoryStream();
            CddaWave.Write(source, output, extent.StartSector, extent.Sectors);
            using var outputAsync = new MemoryStream();
            await CddaWave.WriteAsync(source, outputAsync, extent.StartSector, extent.Sectors, TestContext.Current.CancellationToken);

            Assert.Equal(output.ToArray(), outputAsync.ToArray());
            Assert.Equal(expected, output.ToArray()[44..]);
            output.Position = 0;
            using var reader = new WavePcm16Stream(output, leaveOpen: true);
            Assert.Equal((44100, 2, (long)expected.Length), (reader.SampleRate, reader.ChannelCount, reader.Length));
        }
    }

    [Fact]
    public async Task CancellingWriteAsyncMidTrackStopsTheCopy()
    {
        using var cancellation = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        const int sectors = 200;
        using var source = new CancelOnReadStream(Patterned(sectors * Sector), cancellation, cancelOnRead: 2);
        using var output = new MemoryStream();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(
            () => CddaWave.WriteAsync(source, output, 0, sectors, cancellation.Token));
        Assert.InRange(output.Length, 45, 44 + (long)sectors * Sector - 1);
    }

    [Fact]
    public async Task ARangeTooLongForOneWaveFileIsRejectedBeforeAnythingIsWritten()
    {
        Assert.True((CddaWave.MaximumSectors + 1) * Sector + 36 > uint.MaxValue);
        using var source = new ZeroStream((CddaWave.MaximumSectors + 1) * Sector);
        using var output = new MemoryStream();

        Assert.Throws<ArgumentOutOfRangeException>(() => CddaWave.Write(source, output, 0, CddaWave.MaximumSectors + 1));
        await Assert.ThrowsAsync<ArgumentOutOfRangeException>(
            () => CddaWave.WriteAsync(source, output, 0, CddaWave.MaximumSectors + 1, TestContext.Current.CancellationToken));
        Assert.Equal(0, output.Length);
    }

    [Fact]
    public void TheLongestRangeFitsTheWaveHeader()
    {
        using var source = new ZeroStream(CddaWave.MaximumSectors * Sector);
        using var output = new HeaderOnlyStream();

        Assert.Throws<HeaderWritten>(() => CddaWave.Write(source, output, 0, CddaWave.MaximumSectors));
        var dataLength = (uint)(CddaWave.MaximumSectors * Sector);
        Assert.Equal(dataLength + 36, BinaryPrimitives.ReadUInt32LittleEndian(output.Header.AsSpan(4)));
        Assert.Equal(dataLength, BinaryPrimitives.ReadUInt32LittleEndian(output.Header.AsSpan(40)));
    }

    [Fact]
    public async Task ARangePastTheImageOrANegativeOneIsRejectedBeforeAnythingIsWritten()
    {
        using var source = new MemoryStream(new byte[3 * Sector]);
        using var output = new MemoryStream();

        Assert.Throws<EndOfStreamException>(() => CddaWave.Write(source, output, 2, 2));
        await Assert.ThrowsAsync<EndOfStreamException>(
            () => CddaWave.WriteAsync(source, output, 4, 0, TestContext.Current.CancellationToken));
        Assert.Throws<ArgumentOutOfRangeException>(() => CddaWave.Write(source, output, -1, 1));
        await Assert.ThrowsAsync<ArgumentOutOfRangeException>(
            () => CddaWave.WriteAsync(source, output, 0, -1, TestContext.Current.CancellationToken));
        Assert.Equal(0, output.Length);
    }

    private static byte[] Patterned(int length)
    {
        var bytes = new byte[length];
        for (var i = 0; i < bytes.Length; i++) bytes[i] = (byte)(i * 7 + i / Sector);
        return bytes;
    }

    // Cancels the token when the given read starts, so the copy is cancelled with audio left to copy.
    private sealed class CancelOnReadStream(byte[] bytes, CancellationTokenSource cancellation, int cancelOnRead)
        : MemoryStream(bytes)
    {
        private int reads;

        public override ValueTask<int> ReadAsync(Memory<byte> buffer, CancellationToken cancellationToken = default)
        {
            if (++reads == cancelOnRead) cancellation.Cancel();
            return base.ReadAsync(buffer, cancellationToken);
        }
    }

    // A readable, seekable stream of zeros, long enough to stand for an image larger than a WAVE file.
    private sealed class ZeroStream(long length) : Stream
    {
        public override bool CanRead => true;
        public override bool CanSeek => true;
        public override bool CanWrite => false;
        public override long Length => length;
        public override long Position { get; set; }
        public override int Read(byte[] buffer, int offset, int count)
        {
            var read = (int)Math.Clamp(length - Position, 0, count);
            Array.Clear(buffer, offset, read);
            Position += read;
            return read;
        }
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }

    private sealed class HeaderWritten : Exception;

    // Keeps the 44-byte header and stops the copy at the first write after it.
    private sealed class HeaderOnlyStream : Stream
    {
        public byte[] Header { get; private set; } = [];
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => throw new NotSupportedException();
        public override long Position { get => throw new NotSupportedException(); set => throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) => Write(buffer.AsSpan(offset, count));
        public override void Write(ReadOnlySpan<byte> buffer)
        {
            if (Header.Length > 0) throw new HeaderWritten();
            Header = buffer.ToArray();
        }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void Flush() { }
        public override void SetLength(long value) => throw new NotSupportedException();
    }
}
