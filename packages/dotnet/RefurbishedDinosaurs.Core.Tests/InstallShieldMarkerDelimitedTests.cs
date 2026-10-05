using System.Buffers.Binary;
using System.IO.Compression;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// Compressed data with no chunk lengths, where each chunk ends with the empty stored block
// 00 00 FF FF: what Unshield reads with -O. The caller selects it when opening the set.
public sealed class InstallShieldMarkerDelimitedTests
{
    private const InstallShieldCompressedFormat Marker = InstallShieldCompressedFormat.MarkerDelimitedChunks;
    private static readonly byte[] Noise = Bytes(150_000, 7);

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReadsMarkerDelimitedMembersWhenTheCallerSelectsThem(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            // Stored data whose bytes hold the marker followed by a byte with its low bit clear, which
            // splitting at every 00 00 FF FF would cut in the middle of a stored block.
            var markerInData = Enumerable.Range(0, 20_000)
                .SelectMany(index => new byte[] { 0, 0, 0xff, 0xff, (byte)(index * 2) }).ToArray();
            CabinetFile[] files =
            [
                new("Data", "noise.bin", Noise),
                new("Data", "repeated.bin", Enumerable.Repeat((byte)'A', 200_000).ToArray()),
                new("", "obfuscated.bin", Bytes(70_000, 3), Obfuscated: true),
                new("", "stored.txt", "plain stored member\r\n"u8.ToArray(), Compressed: false),
                new("", "marker-in-data.bin", markerInData,
                    Stored: SyntheticInstallShieldCabinet.MarkerChunks(markerInData, level: CompressionLevel.NoCompression)),
                new("", "continuous.bin", Noise.Concat(Noise).ToArray(),
                    Stored: SyntheticInstallShieldCabinet.MarkerChunks(Noise.Concat(Noise).ToArray(), continuous: true)),
                new("", "empty.bin", [])
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major, files, markerDelimited: true));

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker);
            Assert.Equal(Marker, source.CompressedFormat);
            Assert.Empty(source.SkippedFiles);
            foreach (var file in files)
            {
                var path = file.Directory.Length == 0 ? file.Name : $"{file.Directory}/{file.Name}";
                await using var stream = source.OpenRead(path);
                Assert.Equal(file.Data, await ReadAll(stream));
            }

            var manifest = new AssetManifest("game", "retail",
                [new("Data/noise.bin", Noise.Length, FileFingerprint.Xxh3(Noise))], ContentSourceKinds.InstallShieldCabinet);
            Assert.Empty((await AssetVerifier.VerifyAsync(source, manifest, TestContext.Current.CancellationToken)).Issues);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReadsAMarkerDelimitedMemberSplitAcrossVolumesAndInsideAnotherSource(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("", "first.bin", Bytes(1000, 1)), new("", "split.bin", Noise), new("", "after.bin", Bytes(500, 5))];
            var set = SyntheticInstallShieldCabinet.Build(major, files, volumeCapacity: 60_000, markerDelimited: true);
            Assert.Contains("data3.cab", set.Keys);
            SyntheticInstallShieldCabinet.WriteTo(Path.Combine(root, "SETUP"), set);

            using var container = OriginalContentSource.OpenDirectory(root);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(container, "SETUP/data1.hdr", null, Marker);
            foreach (var file in files)
            {
                await using var stream = source.OpenRead(file.Name);
                Assert.Equal(file.Data, await ReadAll(stream));
            }
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SeeksWithinAMarkerDelimitedMember()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root,
                SyntheticInstallShieldCabinet.Build(5, [new("", "noise.bin", Noise)], markerDelimited: true));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker);
            await using var stream = source.OpenRead("noise.bin");
            var part = new byte[1000];
            stream.Position = 120_000;
            stream.ReadExactly(part);
            Assert.Equal(Noise.AsSpan(120_000, 1000).ToArray(), part);
            stream.Position = 10;
            stream.ReadExactly(part);
            Assert.Equal(Noise.AsSpan(10, 1000).ToArray(), part);
            stream.Position = Noise.Length;
            Assert.Equal(0, stream.Read(part));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReadsEachFormOnlyAsTheCallerSelectedIt(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var marked = Path.Combine(root, "marked");
            var prefixed = Path.Combine(root, "prefixed");
            SyntheticInstallShieldCabinet.WriteTo(marked,
                SyntheticInstallShieldCabinet.Build(major, [new("", "packed.bin", Noise)], markerDelimited: true));
            SyntheticInstallShieldCabinet.WriteTo(prefixed, SyntheticInstallShieldCabinet.Build(major, [new("", "packed.bin", Noise)]));

            // The default reads length-prefixed chunks and names the other form when a chunk does not
            // read; it does not fall back to it.
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(marked, "data1.hdr")))
            {
                Assert.Equal(InstallShieldCompressedFormat.LengthPrefixedChunks, source.CompressedFormat);
                await using var stream = source.OpenRead("packed.bin");
                var message = (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message;
                Assert.Contains("InstallShieldCompressedFormat.MarkerDelimitedChunks", message);
                Assert.Contains("-O", message);
            }

            // Length-prefixed data read as marker-delimited fails too.
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(prefixed, "data1.hdr"), compressedFormat: Marker))
            await using (var stream = source.OpenRead("packed.bin"))
                await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsMalformedMarkerDelimitedData(int major)
    {
        var chunks = SyntheticInstallShieldCabinet.MarkerChunks(Noise);
        // Each case stores other bytes under the header of a member that expands to Noise.
        (byte[] Stored, string Problem)[] cases =
        [
            // Cut inside a chunk: the decoder runs out before the declared size.
            (chunks[..(chunks.Length / 2)], "ends after"),
            // Every expanded byte is there, but the last chunk's marker is cut short.
            (chunks[..^1], "does not end with the 00 00 FF FF marker"),
            // Bytes after the last marker that expand to nothing still leave the marker short of the end.
            ([.. chunks, 1, 2, 3], "does not end with the 00 00 FF FF marker"),
            // Another chunk after the last expands past the declared size.
            ([.. chunks, .. SyntheticInstallShieldCabinet.MarkerChunks(Bytes(10, 4))], "past its declared size"),
            // A reserved block type is not deflate data.
            ([0x07, .. chunks[1..]], "does not inflate"),
            // A final block ends the stream before the declared size.
            (FinalBlock(Noise[..1000]), "ends after"),
            // Length-prefixed chunks hold no marker.
            (SyntheticInstallShieldCabinet.Chunks(Noise), "")
        ];
        foreach (var (stored, problem) in cases)
        {
            var root = TemporaryDirectory();
            try
            {
                SyntheticInstallShieldCabinet.WriteTo(root,
                    SyntheticInstallShieldCabinet.Build(major, [new("", "packed.bin", Noise, Stored: stored)]));
                using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker);
                await using var stream = source.OpenRead("packed.bin");
                var message = (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message;
                Assert.Contains(problem, message);
                // A retry reports the same failure.
                Assert.Equal(message, Assert.Throws<InvalidDataException>(() => stream.Read(new byte[16])).Message);
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }

    [Theory]
    [InlineData(0)]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ChecksTheDeclaredSizeAndTheStoredExtent(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(major, [new("", "packed.bin", Noise)], markerDelimited: true);
            var header = set["data1.hdr"];
            var descriptor = FileDescriptor(header, major, 0);
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            SetExpandedSize(header, major, descriptor, Noise.Length - 1);
            File.WriteAllBytes(Path.Combine(root, "data1.hdr"), header);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker))
            await using (var stream = source.OpenRead("packed.bin"))
                Assert.Contains("past its declared size", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);

            SetExpandedSize(header, major, descriptor, Noise.Length + 1);
            File.WriteAllBytes(Path.Combine(root, "data1.hdr"), header);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker))
            await using (var stream = source.OpenRead("packed.bin"))
                Assert.Contains("ends after", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);

            // Stored bytes that run past the volume fail the open, as for length-prefixed data.
            SetExpandedSize(header, major, descriptor, Noise.Length);
            File.WriteAllBytes(Path.Combine(root, "data1.hdr"), header);
            File.WriteAllBytes(Path.Combine(root, "data1.cab"), set["data1.cab"][..^10]);
            Assert.Contains("lies past the end of volume 1", Assert.Throws<InvalidDataException>(() =>
                OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker)).Message);

            // A volume that shrinks after the open is reported as such, not as bad deflate data.
            File.WriteAllBytes(Path.Combine(root, "data1.cab"), set["data1.cab"]);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker))
            {
                File.WriteAllBytes(Path.Combine(root, "data1.cab"), set["data1.cab"][..^5000]);
                await using var stream = source.OpenRead("packed.bin");
                Assert.Contains("shorter than when it was opened",
                    (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
            }
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ChecksTheMd5OfAVersion6MarkerDelimitedMember()
    {
        var root = TemporaryDirectory();
        try
        {
            var changed = Noise.ToArray();
            changed[^1] ^= 0xff;
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6,
                [new("", "packed.bin", Noise, Stored: SyntheticInstallShieldCabinet.MarkerChunks(changed))]));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"), compressedFormat: Marker);
            await using var stream = source.OpenRead("packed.bin");
            Assert.Contains("MD5", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void RefusesACompressedFormatThatIsNotDefined()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(5, [new("", "packed.bin", Noise)]));
            using var container = OriginalContentSource.OpenDirectory(root);
            Assert.Throws<ArgumentOutOfRangeException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                Path.Combine(root, "data1.hdr"), compressedFormat: (InstallShieldCompressedFormat)2));
            Assert.Throws<ArgumentOutOfRangeException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                container, "data1.hdr", null, (InstallShieldCompressedFormat)(-1)));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // Raw deflate data of data that ends with a final block.
    private static byte[] FinalBlock(byte[] data)
    {
        using var output = new MemoryStream();
        using (var deflate = new DeflateStream(output, CompressionLevel.Optimal, leaveOpen: true))
            deflate.Write(data);
        return output.ToArray();
    }

    // The offset of a file descriptor in a header of major version 0, 5 or 6.
    private static int FileDescriptor(byte[] header, int major, int index)
    {
        var descriptor = SyntheticInstallShieldCabinet.DescriptorOffset;
        var table = descriptor + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x0c));
        if (major == 6)
            return table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x2c)) + index * 0x57;
        var directories = BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(descriptor + 0x1c));
        return table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(table + 4 * (directories + index)));
    }

    private static void SetExpandedSize(byte[] header, int major, int descriptor, int size)
    {
        if (major == 6)
            BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(descriptor + 2), (ulong)size);
        else
            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(descriptor + 10), (uint)size);
    }

    private static async Task<byte[]> ReadAll(Stream stream)
    {
        using var copy = new MemoryStream();
        await stream.CopyToAsync(copy, TestContext.Current.CancellationToken);
        return copy.ToArray();
    }

    private static byte[] Bytes(int length, int seed)
    {
        var bytes = new byte[length];
        new Random(seed).NextBytes(bytes);
        return bytes;
    }

    private static string TemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "installshield-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
