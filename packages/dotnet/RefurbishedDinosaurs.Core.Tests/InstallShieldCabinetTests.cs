using System.Buffers.Binary;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class InstallShieldCabinetTests
{
    private static readonly byte[] Noise = Bytes(150_000, 7);
    private static readonly byte[] Text = "plain stored member\r\n"u8.ToArray();

    [Theory]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ListsAndExpandsNestedStoredCompressedAndObfuscatedMembers(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var repeated = Enumerable.Repeat((byte)'A', 90_000).ToArray();
            CabinetFile[] files =
            [
                new(@"Data\Maps", "noise.bin", Noise),
                new(@"Data", "readme.txt", Text, Compressed: false),
                new("", "obfuscated.dat", repeated, Obfuscated: true),
                new(@"Data\Maps", "stored-obfuscated.bin", Bytes(3000, 9), Compressed: false, Obfuscated: true),
                new("", "empty.dat", [])
            ];
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major, files));

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(ContentSourceKinds.InstallShieldCabinet, source.Kind);
            Assert.Equal(major, source.MajorVersion);
            Assert.Null(source.Label);
            Assert.Empty(source.SkippedFiles);
            Assert.Equal(
                ["Data/Maps/noise.bin", "Data/Maps/stored-obfuscated.bin", "Data/readme.txt", "empty.dat", "obfuscated.dat"],
                source.Files.Select(entry => entry.Path));
            foreach (var file in files)
            {
                var path = file.Directory.Length == 0 ? file.Name : $"{file.Directory.Replace('\\', '/')}/{file.Name}";
                Assert.True(source.TryGetFile(path.ToUpperInvariant(), out var entry));
                Assert.Equal(file.Data.Length, entry!.Size);
                await using var stream = source.OpenRead(path);
                Assert.Equal(FileFingerprint.Xxh3(file.Data), await FileFingerprint.Xxh3Async(stream, TestContext.Current.CancellationToken));
            }
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(5, true)]
    [InlineData(5, false)]
    [InlineData(6, true)]
    [InlineData(6, false)]
    public async Task ReadsMembersSplitAcrossVolumes(int major, bool compressed)
    {
        var root = TemporaryDirectory();
        try
        {
            var second = Bytes(40_000, 3);
            CabinetFile[] files =
            [
                new("", "first.bin", Bytes(1000, 1), compressed),
                new("", "split.bin", second, compressed),
                new("Later", "after.bin", Bytes(500, 5), compressed)
            ];
            var set = SyntheticInstallShieldCabinet.Build(major, files, volumeCapacity: 20_000);
            Assert.Contains("data2.cab", set.Keys);
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            foreach (var file in files)
            {
                var path = file.Directory.Length == 0 ? file.Name : $"{file.Directory}/{file.Name}";
                await using var stream = source.OpenRead(path);
                Assert.Equal(file.Data, await ReadAll(stream));
            }

            File.Delete(Path.Combine(root, "data2.cab"));
            Assert.Throws<FileNotFoundException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr")));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task OpensASetInsideAnotherSourceAndThroughTheGenericOpeners()
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6, [new("Bin", "game.dat", Noise)], volumeCapacity: 60_000);
            var disc = Path.Combine(root, "DISC", "SETUP");
            Directory.CreateDirectory(disc);
            foreach (var (name, bytes) in set) File.WriteAllBytes(Path.Combine(disc, name.ToUpperInvariant()), bytes);

            using var container = OriginalContentSource.OpenDirectory(Path.Combine(root, "DISC"));
            using var nested = OriginalContentSource.OpenInstallShieldCabinet(container, "setup/data1.hdr");
            await using (var stream = nested.OpenRead("bin/game.dat"))
                Assert.Equal(Noise, await ReadAll(stream));

            using var byExtension = OriginalContentSource.Open(Path.Combine(disc, "DATA1.HDR"));
            Assert.IsType<InstallShieldCabinetSource>(byExtension);
            using var byKind = OriginalContentSource.Open(Path.Combine(disc, "DATA1.HDR"), ContentSourceKinds.InstallShieldCabinet);
            Assert.Equal("Bin/game.dat", Assert.Single(byKind.Files).Path);
            Assert.True(ContentSourceKinds.IsSupported("installshield-cabinet"));

            var manifest = new AssetManifest("game", "retail",
                [new("Bin/game.dat", Noise.Length, FileFingerprint.Xxh3(Noise))], ContentSourceKinds.InstallShieldCabinet);
            var result = await AssetVerifier.VerifyAsync(Path.Combine(disc, "DATA1.HDR"), manifest, TestContext.Current.CancellationToken);
            Assert.True(result.IsValid);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(@"..\Escape", "x.bin")]
    [InlineData("", @"..\x.bin")]
    [InlineData(@"C:\Windows", "x.bin")]
    [InlineData(@"\Rooted", "x.bin")]
    [InlineData("Data", "CON")]
    public void RejectsAMemberPathThatIsNotRelativeWhenOpened(string directory, string name)
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6,
                [new("Data", "fine.bin", Text), new(directory, name, Text)]));
            var exception = Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr")));
            Assert.Contains("InstallShield file 1", exception.Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void RejectsASetOverItsLimitsWhenOpened()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(5,
                [new("", "a.bin", Text), new("", "b.bin", Noise)]));
            var header = Path.Combine(root, "data1.hdr");
            Assert.Contains("limit", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                header, new InstallShieldCabinetLimits(MaximumFiles: 1))).Message);
            Assert.Contains("limit", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                header, new InstallShieldCabinetLimits(MaximumExpandedBytes: Noise.Length))).Message);
            // A .hdr header is read whole, so its header region is the file.
            Assert.Contains($"header is {new FileInfo(header).Length} bytes, more than the limit", Assert.Throws<InvalidDataException>(
                () => OriginalContentSource.OpenInstallShieldCabinet(header, new InstallShieldCabinetLimits(MaximumHeaderBytes: 64))).Message);
            using var exact = OriginalContentSource.OpenInstallShieldCabinet(
                header, new InstallShieldCabinetLimits(MaximumFiles: 2, MaximumExpandedBytes: Noise.Length + Text.Length));
            Assert.Equal(2, exact.Files.Count);
        }
        finally
        {
            Directory.Delete(root, true);
        }

        // Two declared sizes whose sum does not fit in a long still exceed the largest limit.
        root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6, [new("", "a.bin", Noise), new("", "b.bin", Noise)]);
            var bytes = set["data1.hdr"];
            var table = SyntheticInstallShieldCabinet.DescriptorOffset +
                        BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x0c));
            var descriptors = table + BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x2c));
            foreach (var index in new[] { 0, 1 })
                BinaryPrimitives.WriteUInt64LittleEndian(bytes.AsSpan(descriptors + index * 0x57 + 2), (ulong)(long.MaxValue / 2 + 1));
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            Assert.Contains("limit", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                Path.Combine(root, "data1.hdr"), new InstallShieldCabinetLimits(MaximumExpandedBytes: long.MaxValue))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(5)]
    [InlineData(6)]
    public void RejectsATruncatedHeaderOrVolume(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(major, [new("Data", "a.bin", Noise), new("Data", "b.bin", Text)]);
            var header = Path.Combine(root, "data1.hdr");
            var cabinet = Path.Combine(root, "data1.cab");
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            foreach (var length in new[] { 0, 10, 40, SyntheticInstallShieldCabinet.DescriptorOffset + 0x20, set["data1.hdr"].Length - 3 })
            {
                File.WriteAllBytes(header, set["data1.hdr"][..length]);
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(header));
            }

            File.WriteAllBytes(header, set["data1.hdr"]);
            File.WriteAllBytes(cabinet, set["data1.cab"][..^1]);
            Assert.Contains("shorter than the header claims",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(header)).Message);
            File.WriteAllBytes(cabinet, set["data1.cab"][..30]);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(header));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ReportsAVersion6MemberWhoseBytesDoNotMatchItsMd5()
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6, [new("", "stored.bin", Noise, Compressed: false)]);
            set["data1.cab"][SyntheticInstallShieldCabinet.VolumeDataOffset + 100_000] ^= 0xff;
            SyntheticInstallShieldCabinet.WriteTo(root, set);

            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            await using var stream = source.OpenRead("stored.bin");
            var first = new byte[65_536];
            stream.ReadExactly(first);
            Assert.Contains("MD5", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);

            var manifest = new AssetManifest("game", "retail", [new("stored.bin", Noise.Length, FileFingerprint.Xxh3(Noise))],
                ContentSourceKinds.InstallShieldCabinet);
            var issue = Assert.Single((await AssetVerifier.VerifyAsync(source, manifest, TestContext.Current.CancellationToken)).Issues);
            Assert.Equal(AssetProblem.Unreadable, issue.Problem);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(5)]
    [InlineData(6)]
    public async Task ReportsAMemberThatExpandsToAnotherSizeThanDeclared(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(major, [new("", "packed.bin", Noise)]);
            SetExpandedSize(set["data1.hdr"], major, Noise.Length + 1);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr")))
            await using (var stream = source.OpenRead("packed.bin"))
                Assert.Contains("ends after", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);

            SetExpandedSize(set["data1.hdr"], major, Noise.Length - 1);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr")))
            await using (var stream = source.OpenRead("packed.bin"))
            {
                Assert.Contains("past its declared size", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
                // A retry gets the same failure, not the bytes of the chunk that overran.
                Assert.Contains("past its declared size", Assert.Throws<InvalidDataException>(() => stream.Read(new byte[16])).Message);
                stream.Position = 0;
                Assert.Contains("past its declared size", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
            }
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SeeksBackwardAndForward()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6, [new("", "noise.bin", Noise)]));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            await using var stream = source.OpenRead("noise.bin");
            Assert.True(stream.CanSeek);
            var buffer = new byte[16];
            stream.Position = 120_000;
            stream.ReadExactly(buffer);
            Assert.Equal(Noise.AsSpan(120_000, 16).ToArray(), buffer);
            stream.Seek(-100_000, SeekOrigin.Current);
            stream.ReadExactly(buffer);
            Assert.Equal(Noise.AsSpan(20_016, 16).ToArray(), buffer);
            stream.Seek(0, SeekOrigin.End);
            Assert.Equal(0, stream.Read(buffer));
            Assert.Throws<IOException>(() => stream.Seek(1, SeekOrigin.End));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task FollowsLinksAndReportsSkippedAndDuplicateEntries()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6,
            [
                new("", "original.bin", Noise),
                new("Copy", "linked.bin", [], LinkTo: 0),
                new("", "gone.bin", Text, Invalid: true),
                new("", "original.bin", [], LinkTo: 0)
            ]));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(["Copy/linked.bin", "original.bin"], source.Files.Select(entry => entry.Path));
            Assert.Equal([(2, "gone.bin"), (3, "original.bin")], source.SkippedFiles.Select(file => (file.Index, file.Path)));
            Assert.Equal("The file shares the data of file 0 at 'original.bin', which is listed.", source.SkippedFiles[1].Reason);
            await using var stream = source.OpenRead("copy/linked.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }

        root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6,
                [new("Data", "same.bin", Noise), new("data", "SAME.BIN", Text)]));
            Assert.Contains("two different files",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(5)]
    [InlineData(6)]
    public async Task OpensASetWhoseHeaderIsHeldInADataCabLargerThanTheHeaderLimit(int major)
    {
        var root = TemporaryDirectory();
        try
        {
            CabinetFile[] files = [new("Bin", "noise.bin", Noise), new("", "readme.txt", Text, Compressed: false)];
            var set = SyntheticInstallShieldCabinet.Build(major, files, headerInCabinet: true);
            Assert.Equal(["data1.cab"], set.Keys);
            var cabinet = set["data1.cab"];
            var region = SyntheticInstallShieldCabinet.DescriptorOffset + BinaryPrimitives.ReadInt32LittleEndian(cabinet.AsSpan(16));
            Assert.True(cabinet.Length > 10 * region);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            var path = Path.Combine(root, "data1.cab");

            // The limit bounds the header region, which is all that is read of the file to open it.
            var limits = new InstallShieldCabinetLimits(MaximumHeaderBytes: region);
            using (var source = OriginalContentSource.OpenInstallShieldCabinet(path, limits))
            {
                Assert.Equal(["Bin/noise.bin", "readme.txt"], source.Files.Select(entry => entry.Path));
                await using var stream = source.OpenRead("bin/noise.bin");
                Assert.Equal(Noise, await ReadAll(stream));
            }
            using (var container = OriginalContentSource.OpenDirectory(root))
            using (var nested = OriginalContentSource.OpenInstallShieldCabinet(container, "DATA1.CAB", limits))
            await using (var stream = nested.OpenRead("readme.txt"))
                Assert.Equal(Text, await ReadAll(stream));

            Assert.Contains("header region", Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(
                path, new InstallShieldCabinetLimits(MaximumHeaderBytes: region - 1))).Message);
            // A descriptor size that stops short of the file table leaves the table outside the region
            // read, though the file holds it; the message names the region's size.
            var shortened = cabinet.ToArray();
            BinaryPrimitives.WriteInt32LittleEndian(shortened.AsSpan(16), 0x30);
            File.WriteAllBytes(path, shortened);
            Assert.Contains($"past the end of the {SyntheticInstallShieldCabinet.DescriptorOffset + 0x30}-byte header region",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(path)).Message);
            File.WriteAllBytes(path, cabinet[..(region - 10)]);
            Assert.Contains("truncated",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(path)).Message);
            File.WriteAllBytes(path, cabinet[..10]);
            Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(path));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SkipsALinkToAnEntryWithNoDataAndRefusesADamagedLink()
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6,
            [
                new("", "original.bin", Noise),
                new("", "gone.bin", Text, Invalid: true),
                new("Copy", "to-gone.bin", [], LinkTo: 1),
                new("", "empty-offset.bin", Text),
                new("Copy", "to-empty-offset.bin", [], LinkTo: 3)
            ]);
            BinaryPrimitives.WriteUInt64LittleEndian(set["data1.hdr"].AsSpan(Version6Descriptor(set["data1.hdr"], 3) + 0x12), 0);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(["original.bin"], source.Files.Select(entry => entry.Path));
            Assert.Equal([1, 2, 3, 4], source.SkippedFiles.Select(file => file.Index));
            Assert.Equal("Copy/to-gone.bin", source.SkippedFiles[1].Path);
            Assert.Equal("The file links to file 1, which the cabinet marks invalid.", source.SkippedFiles[1].Reason);
            Assert.Equal("Copy/to-empty-offset.bin", source.SkippedFiles[3].Path);
            Assert.Equal("The file links to file 3, which has no data offset.", source.SkippedFiles[3].Reason);
            await using var stream = source.OpenRead("original.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }

        // A link outside the table or a link cycle means the header is damaged, and the open fails.
        root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6,
                [new("", "a.bin", Noise), new("", "b.bin", [], LinkTo: 0), new("", "c.bin", [], LinkTo: 1)]);
            var header = set["data1.hdr"];
            var path = Path.Combine(root, "data1.hdr");

            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(Version6Descriptor(header, 1) + 0x4c), 7);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            Assert.Contains("file 7, which does not exist",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(path)).Message);

            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(Version6Descriptor(header, 1) + 0x4c), 0);
            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(Version6Descriptor(header, 0) + 0x4c), 2);
            header[Version6Descriptor(header, 0) + 0x54] = 1;
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            Assert.Contains("link cycle",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(path)).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ListsOneOfTwoIdenticalVersion6EntriesAtOnePath()
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6,
                [new("Data", "same.bin", Noise), new("data", "SAME.BIN", Noise), new("Data", "other.bin", Text)]));
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(["Data/other.bin", "Data/same.bin"], source.Files.Select(entry => entry.Path));
            var skipped = Assert.Single(source.SkippedFiles);
            Assert.Equal((1, "data/SAME.BIN"), (skipped.Index, skipped.Path));
            Assert.Equal("The file duplicates file 0 at 'Data/same.bin': same expanded size and MD5.", skipped.Reason);
            await using var stream = source.OpenRead("data/same.bin");
            Assert.Equal(Noise, await ReadAll(stream));
        }
        finally
        {
            Directory.Delete(root, true);
        }

        // The duplicate's extent is not followed, so one whose data offset lies past its volume is skipped.
        root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(6, [new("Data", "same.bin", Noise), new("Data", "same.bin", Noise)]);
            BinaryPrimitives.WriteUInt64LittleEndian(set["data1.hdr"].AsSpan(Version6Descriptor(set["data1.hdr"], 1) + 0x12), 0x7fff_ffff);
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            Assert.Equal(["Data/same.bin"], source.Files.Select(entry => entry.Path));
            Assert.Equal(1, Assert.Single(source.SkippedFiles).Index);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(5, 7)]
    [InlineData(6, 8)]
    public void RefusesDuplicateEntriesAtOnePathThatAreNotShownIdentical(int major, int secondSeed)
    {
        // Version 5 records no MD5, so identical bytes still fail; in version 6 the MD5s differ.
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(major,
                [new("Data", "same.bin", Noise), new("Data", "same.bin", Bytes(Noise.Length, secondSeed))]));
            Assert.Contains("two different files",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Theory]
    [InlineData(0x01003000u)]
    [InlineData(0x02000000u | 700)]
    [InlineData(0x09000000u)]
    public async Task RefusesVersionsOtherThanFiveAndSix(uint versionWord)
    {
        var root = TemporaryDirectory();
        try
        {
            SyntheticInstallShieldCabinet.WriteTo(root, SyntheticInstallShieldCabinet.Build(6, [new("", "a.bin", Text)], versionWord: versionWord));
            Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr")));
            var manifest = new AssetManifest("game", "retail", [new("a.bin", Text.Length, FileFingerprint.Xxh3(Text))],
                ContentSourceKinds.InstallShieldCabinet);
            var issue = Assert.Single((await AssetVerifier.VerifyAsync(
                Path.Combine(root, "data1.hdr"), manifest, TestContext.Current.CancellationToken)).Issues);
            Assert.Equal(AssetProblem.Unreadable, issue.Problem);

            File.WriteAllBytes(Path.Combine(root, "data1.hdr"), "MSCF\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0\0"u8.ToArray());
            Assert.Contains("Microsoft cabinet",
                Assert.Throws<InvalidDataException>(() => OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ReportsMarkerDelimitedChunksAsUnsupported()
    {
        var root = TemporaryDirectory();
        try
        {
            var set = SyntheticInstallShieldCabinet.Build(5, [new("", "packed.bin", Noise)]);
            set["data1.cab"][SyntheticInstallShieldCabinet.VolumeDataOffset] = 0;
            set["data1.cab"][SyntheticInstallShieldCabinet.VolumeDataOffset + 1] = 0;
            SyntheticInstallShieldCabinet.WriteTo(root, set);
            using var source = OriginalContentSource.OpenInstallShieldCabinet(Path.Combine(root, "data1.hdr"));
            await using var stream = source.OpenRead("packed.bin");
            Assert.Contains("00 00 FF FF", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    // The offset of a version 6 file descriptor in a header.
    private static int Version6Descriptor(byte[] header, int index)
    {
        var table = SyntheticInstallShieldCabinet.DescriptorOffset +
                    BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x0c));
        return table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x2c)) +
               index * 0x57;
    }

    // Points the only member's expanded size at another value.
    private static void SetExpandedSize(byte[] header, int major, int size)
    {
        var table = SyntheticInstallShieldCabinet.DescriptorOffset +
                    BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x0c));
        if (major == 5)
        {
            var descriptor = table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(table + 4));
            BinaryPrimitives.WriteUInt32LittleEndian(header.AsSpan(descriptor + 10), (uint)size);
        }
        else
        {
            var descriptors = table + BinaryPrimitives.ReadInt32LittleEndian(header.AsSpan(SyntheticInstallShieldCabinet.DescriptorOffset + 0x2c));
            BinaryPrimitives.WriteUInt64LittleEndian(header.AsSpan(descriptors + 2), (ulong)size);
        }
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
