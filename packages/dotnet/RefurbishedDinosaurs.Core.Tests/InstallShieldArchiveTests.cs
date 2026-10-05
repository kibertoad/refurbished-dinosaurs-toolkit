using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class InstallShieldArchiveTests
{
    private static readonly byte[] Prose = Encoding.ASCII.GetBytes(string.Concat(
        Enumerable.Range(0, 400).Select(index => $"line {index % 37}: the quick brown fox {index % 11} jumps\r\n")));
    private static readonly byte[] Text = "plain stored member\r\n"u8.ToArray();

    [Fact]
    public void ExplodesTheFormatDescriptionsExample()
    {
        // The example of the format description, as zlib's contrib/blast corrects it.
        Assert.Equal("AIAIAIAIAIAIA"u8.ToArray(), Explode([0x00, 0x04, 0x82, 0x24, 0x25, 0x8f, 0x80, 0x7f]));
    }

    [Fact]
    public void ExplodesCodedLiteralsCheckedAgainstAnIndependentDecoder()
    {
        // Written by SyntheticPkwareDcl with coded literals and a 1 KiB dictionary, and decoded to the
        // same text by pwexplode (GPL-3.0), which shares no code with this reader or the writer.
        byte[] imploded =
        [
            0x01, 0x04, 0x50, 0x6c, 0xd3, 0xd4, 0xf1, 0x3d, 0x25, 0xb9, 0x81, 0xd2, 0x8b, 0xbd, 0x4a, 0x76,
            0xb6, 0xf2, 0x2c, 0x97, 0xcd, 0xf2, 0x3a, 0xcd, 0x33, 0x74, 0xba, 0x4d, 0xf7, 0xa6, 0xa5, 0xd8,
            0xda, 0x46, 0x55, 0xbc, 0x80, 0x7f
        ];
        Assert.Equal("Hello, hello, hello! A small TEST of coded literals."u8.ToArray(), Explode(imploded));
    }

    [Fact]
    public void BuildsCompleteCodeTables()
    {
        var tables = PkwareDclExploder.Tables().ToArray();
        Assert.Equal([256, 16, 64], tables.Select(table => table.Counts.Skip(1).Sum()));
        Assert.All(tables, table => Assert.True(table.Complete));
    }

    [Theory]
    [InlineData(true, 4)]
    [InlineData(true, 5)]
    [InlineData(true, 6)]
    [InlineData(false, 4)]
    [InlineData(false, 6)]
    public void ExplodesWhatTheTestWriterImplodes(bool codedLiterals, int dictionaryBits)
    {
        // A run longer than the window, two-byte matches and every byte value.
        var data = Prose.Concat(Enumerable.Repeat((byte)'x', 9000)).Concat(Enumerable.Range(0, 256).Select(value => (byte)value))
            .Concat("abab-ab-cdcd"u8.ToArray()).Concat(Prose).ToArray();
        Assert.Equal(data, Explode(SyntheticPkwareDcl.Implode(data, codedLiterals, dictionaryBits)));
        Assert.Equal(data, Explode(SyntheticPkwareDcl.Implode(data, codedLiterals, dictionaryBits, matches: false)));
    }

    [Theory]
    [InlineData(new byte[] { 0x02, 0x04 }, "literal mode 2")]
    [InlineData(new byte[] { 0x00, 0x07 }, "dictionary of 7 bits")]
    [InlineData(new byte[] { 0x00, 0x04, 0x82 }, "ends before its end code")]
    public void RefusesMalformedCompressedData(byte[] imploded, string problem)
    {
        Assert.Contains(problem, Assert.Throws<InvalidDataException>(() => Explode(imploded)).Message);
    }

    [Fact]
    public void RefusesADistanceBeforeTheFirstByte()
    {
        // The example with its first item's flag set: it starts with a copy, which reaches back past the start.
        Assert.Contains("after only 0 bytes",
            Assert.Throws<InvalidDataException>(() => Explode([0x00, 0x04, 0x83, 0x24, 0x25, 0x8f, 0x80, 0x7f])).Message);
    }

    [Fact]
    public async Task ListsAndExpandsStoredAndCompressedMembersInNestedDirectories()
    {
        var root = TemporaryDirectory();
        try
        {
            ArchiveFile[] files =
            [
                new("", "readme.txt", Text, Stored: true),
                new("", "empty.dat", []),
                new(@"Program\Fonts", "large.fnt", Prose, DictionaryBits: 4),
                new(@"Program\Fonts", "small.fnt", Prose[..300], CodedLiterals: false, DictionaryBits: 5),
                new("Data", "nodes.dat", Bytes(5000, 3)),
                new("Data", "empty-stored.dat", [], Stored: true)
            ];
            var path = Path.Combine(root, "_SETUP.1");
            File.WriteAllBytes(path, SyntheticInstallShieldArchive.Build(files));

            using var source = OriginalContentSource.OpenInstallShieldArchive(path);
            Assert.Equal(ContentSourceKinds.InstallShieldArchive, source.Kind);
            Assert.Null(source.Label);
            Assert.Empty(source.SkippedFiles);
            Assert.Equal(
                ["Data/empty-stored.dat", "Data/nodes.dat", "empty.dat", "Program/Fonts/large.fnt", "Program/Fonts/small.fnt", "readme.txt"],
                source.Files.Select(entry => entry.Path));
            foreach (var file in files)
            {
                var member = file.Directory.Length == 0 ? file.Name : $"{file.Directory.Replace('\\', '/')}/{file.Name}";
                Assert.True(source.TryGetFile(member.ToUpperInvariant(), out var entry));
                Assert.Equal(file.Data.Length, entry!.Size);
                await using var stream = source.OpenRead(member);
                Assert.Equal(FileFingerprint.Xxh3(file.Data), await FileFingerprint.Xxh3Async(stream, TestContext.Current.CancellationToken));
            }
            Assert.Throws<FileNotFoundException>(() => source.OpenRead("missing.dat"));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task OpensAnArchiveInsideAnotherSourceFromAStreamAndThroughTheGenericOpeners()
    {
        var root = TemporaryDirectory();
        try
        {
            var archive = SyntheticInstallShieldArchive.Build([new("Bin", "game.exe", Prose)]);
            var setup = Path.Combine(root, "DISC", "SETUP");
            Directory.CreateDirectory(setup);
            var path = Path.Combine(setup, "_SETUP.1");
            File.WriteAllBytes(path, archive);

            using var container = OriginalContentSource.OpenDirectory(Path.Combine(root, "DISC"));
            using (var nested = OriginalContentSource.OpenInstallShieldArchive(container, "setup/_setup.1"))
            await using (var stream = nested.OpenRead("bin/game.exe"))
                Assert.Equal(Prose, await ReadAll(stream));
            Assert.Throws<FileNotFoundException>(() => OriginalContentSource.OpenInstallShieldArchive(container, "setup/_setup.2"));

            // An archive carved out of a larger file, such as a self-extractor, opens from a stream.
            using (var carved = new MemoryStream(archive))
            using (var fromStream = OriginalContentSource.OpenInstallShieldArchive(carved))
            await using (var stream = fromStream.OpenRead("Bin/game.exe"))
                Assert.Equal(Prose, await ReadAll(stream));

            using var bySignature = OriginalContentSource.Open(path);
            Assert.IsType<InstallShieldArchiveSource>(bySignature);
            using var byKind = OriginalContentSource.Open(path, ContentSourceKinds.InstallShieldArchive);
            Assert.Equal("Bin/game.exe", Assert.Single(byKind.Files).Path);
            Assert.True(ContentSourceKinds.IsSupported("installshield3-archive"));

            var manifest = new AssetManifest("game", "retail",
                [new("Bin/game.exe", Prose.Length, FileFingerprint.Xxh3(Prose))], ContentSourceKinds.InstallShieldArchive);
            var result = await AssetVerifier.VerifyAsync(path, manifest, TestContext.Current.CancellationToken);
            Assert.True(result.IsValid);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task SeeksForwardAndBackwardWithinAMember()
    {
        using var archive = new MemoryStream(SyntheticInstallShieldArchive.Build([new("", "prose.txt", Prose)]));
        using var source = OriginalContentSource.OpenInstallShieldArchive(archive);
        await using var stream = source.OpenRead("prose.txt");
        var buffer = new byte[100];
        stream.Position = 5000;
        stream.ReadExactly(buffer);
        Assert.Equal(Prose[5000..5100], buffer);
        stream.Seek(-4000, SeekOrigin.Current);
        stream.ReadExactly(buffer);
        Assert.Equal(Prose[1100..1200], buffer);
        stream.Seek(0, SeekOrigin.End);
        Assert.Equal(0, stream.Read(buffer));
        Assert.Throws<IOException>(() => stream.Seek(1, SeekOrigin.End));
        stream.Position = 0;
        Assert.Equal(Prose, await ReadAll(stream));
    }

    [Theory]
    [InlineData((ushort)0x0001)]
    [InlineData((ushort)0x0002)]
    [InlineData((ushort)0x0003)]
    public async Task ReadsASplitArchiveOfOnePart(ushort archiveFlags)
    {
        // A header that sets a split flag but declares itself part 1 of 1 holds every member's data,
        // as each entry confirms by naming part 1. The members read as they do in an unsplit archive.
        ArchiveFile[] files =
        [
            new("", "readme.txt", Text, Stored: true),
            new("Data", "prose.txt", Prose),
            new("Data", "random.bin", Bytes(3000, 7), CodedLiterals: false, DictionaryBits: 4),
            new("Data", "gone.bin", Text, Invalid: true)
        ];
        using var source = Open(SyntheticInstallShieldArchive.Build(files, archiveFlags, totalParts: 1, partNumber: 1));
        Assert.Equal(["Data/prose.txt", "Data/random.bin", "readme.txt"], source.Files.Select(entry => entry.Path));
        Assert.Equal("Data/gone.bin", Assert.Single(source.SkippedFiles).Path);
        foreach (var file in files.Where(file => !file.Invalid))
        {
            await using var stream = source.OpenRead(file.Directory.Length == 0 ? file.Name : $"{file.Directory}/{file.Name}");
            Assert.Equal(file.Data, await ReadAll(stream));
        }
    }

    [Theory]
    [InlineData((ushort)1, (byte)2, (byte)1, "flags 0x0001, part 1, 2 parts")]
    [InlineData((ushort)2, (byte)3, (byte)1, "flags 0x0002, part 1, 3 parts")]
    [InlineData((ushort)1, (byte)0, (byte)1, "flags 0x0001, part 1, 0 parts")]
    [InlineData((ushort)1, (byte)0, (byte)2, "flags 0x0001, part 2, 0 parts")]
    [InlineData((ushort)3, (byte)1, (byte)2, "flags 0x0003, part 2, 1 parts")]
    [InlineData((ushort)1, (byte)1, (byte)0, "flags 0x0001, part 0, 1 parts")]
    [InlineData((ushort)0, (byte)3, (byte)1, "flags 0x0000, part 1, 3 parts")]
    public void RefusesASplitArchiveThatIsNotWholeInOneFile(ushort archiveFlags, byte totalParts, byte partNumber, string header)
    {
        // A first part of several, a later part (which declares 0 parts), a part number past the count,
        // and an unsplit header that declares several parts all need data from files not given.
        using var archive = new MemoryStream(SyntheticInstallShieldArchive.Build([new("", "a.bin", Text)], archiveFlags, totalParts, partNumber));
        var message = Assert.Throws<NotSupportedException>(() => OriginalContentSource.OpenInstallShieldArchive(archive)).Message;
        Assert.Contains("one part of a split archive", message);
        Assert.Contains(header, message);
    }

    [Theory]
    [InlineData((byte)1, (byte)2)]
    [InlineData((byte)2, (byte)2)]
    [InlineData((byte)0, (byte)1)]
    [InlineData((byte)0, (byte)0)]
    public void RejectsAnEntryInAnotherPartOfASplitArchiveOfOnePart(byte firstPart, byte lastPart)
    {
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text), new("Data", "b.bin", Prose)], archiveFlags: 1);
        var entry = SyntheticInstallShieldArchive.FileEntry(bytes, 1);
        bytes[entry] = lastPart;
        bytes[entry + 28] = firstPart;
        Assert.Contains($"file 1 at 'Data/b.bin' lies in parts {firstPart} to {lastPart}, but the archive is a split archive of one part",
            Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // An unsplit archive's entries do not name their parts, so the same bytes there are not read.
        bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text), new("Data", "b.bin", Prose)]);
        entry = SyntheticInstallShieldArchive.FileEntry(bytes, 1);
        bytes[entry] = lastPart;
        bytes[entry + 28] = firstPart;
        Assert.Equal(2, Open(bytes).Files.Count);
    }

    [Theory]
    [InlineData((ushort)0)]
    [InlineData((ushort)1)]
    public void RefusesAnEntryThatSpansParts(ushort archiveFlags)
    {
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text), new("", "b.bin", Text)], archiveFlags);
        var flags = SyntheticInstallShieldArchive.FileEntry(bytes, 1) + 25;
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(flags), 0x100);
        Assert.Contains("file 1 at 'b.bin' spans archive parts", Assert.Throws<NotSupportedException>(() => Open(bytes)).Message);
    }

    [Fact]
    public async Task ChecksTheExtentsAndDataOfASplitArchiveOfOnePart()
    {
        // A member whose stored bytes run past the end of the file.
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text, Stored: true), new("", "b.bin", Prose)], archiveFlags: 1);
        var entry = SyntheticInstallShieldArchive.FileEntry(bytes, 1);
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(entry + 7), (uint)bytes.Length);
        Assert.Contains($"file 1 at 'b.bin' lies outside the {bytes.Length}-byte archive",
            Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // A compressed member whose stored bytes stop before its end code.
        bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text, Stored: true), new("", "b.bin", Prose)], archiveFlags: 1);
        entry = SyntheticInstallShieldArchive.FileEntry(bytes, 1);
        var storedSize = BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(entry + 7));
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(entry + 7), storedSize - 2);
        using var source = Open(bytes);
        await using var stream = source.OpenRead("b.bin");
        await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream));
    }

    [Fact]
    public void RefusesAnotherHeaderSizeAndOtherFormats()
    {
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Text)]);
        bytes[4] = 0x40;
        Assert.Contains("64 bytes of fields", Assert.Throws<NotSupportedException>(() => Open(bytes)).Message);
        bytes[0] = 0;
        Assert.Contains("not an InstallShield 3 archive", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);
        Assert.Contains("too short", Assert.Throws<InvalidDataException>(() => Open(new byte[20])).Message);
    }

    [Fact]
    public void ListsEntriesTheArchiveMarksInvalidAsSkipped()
    {
        var bytes = SyntheticInstallShieldArchive.Build(
        [
            new("Data", "kept.bin", Text), new("Data", "gone.bin", Text, Invalid: true),
            new("", @"..\escape.bin", Text, Invalid: true)
        ]);
        using var source = Open(bytes);
        Assert.Equal(["Data/kept.bin"], source.Files.Select(entry => entry.Path));
        Assert.Equal([(1, (string?)"Data/gone.bin"), (2, null)], source.SkippedFiles.Select(file => (file.Index, file.Path)));
        Assert.All(source.SkippedFiles, file => Assert.Equal("The archive marks the file invalid.", file.Reason));
    }

    [Theory]
    [InlineData(@"..\Escape", "x.bin")]
    [InlineData("", @"..\x.bin")]
    [InlineData(@"C:\Windows", "x.bin")]
    [InlineData(@"\Rooted", "x.bin")]
    [InlineData("Data", "CON")]
    public void RejectsAMemberPathThatIsNotRelativeWhenOpened(string directory, string name)
    {
        var bytes = SyntheticInstallShieldArchive.Build([new("Data", "fine.bin", Text), new(directory, name, Text)]);
        Assert.Contains("file 1 has a path that is not relative", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);
    }

    [Fact]
    public void RejectsTablesThatDisagree()
    {
        ArchiveFile[] files = [new("A", "one.bin", Text), new("B", "two.bin", Text)];

        // The file table names directory 0 for the second file; the directory counts place it in 1.
        var bytes = SyntheticInstallShieldArchive.Build(files);
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(SyntheticInstallShieldArchive.FileEntry(bytes, 1) + 1), 0);
        Assert.Contains("file 1 names directory 0, but the directory table's file counts place it in directory 1",
            Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // The directory counts add up to more files than the header declares.
        bytes = SyntheticInstallShieldArchive.Build(files);
        var directories = (int)BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(41));
        BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(directories), 2);
        Assert.Contains("directories hold 3 files, but the header declares 2", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // The file table is declared one byte longer than its entries.
        bytes = SyntheticInstallShieldArchive.Build(files);
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(55), BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(55)) - 1);
        Assert.Contains("file table is truncated", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // A name without its terminator.
        bytes = SyntheticInstallShieldArchive.Build(files);
        var entry = SyntheticInstallShieldArchive.FileEntry(bytes, 0);
        bytes[entry + 30 + "one.bin".Length] = (byte)'!';
        Assert.Contains("no terminator", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        // The tables lie past the end of the archive.
        bytes = SyntheticInstallShieldArchive.Build(files);
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(51), (uint)bytes.Length);
        Assert.Contains("file table lies outside", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);
    }

    [Fact]
    public void RejectsMembersWhoseDataOrSizesDoNotFit()
    {
        ArchiveFile[] files = [new("", "stored.bin", Text, Stored: true), new("", "packed.bin", Prose)];

        var bytes = SyntheticInstallShieldArchive.Build(files);
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(SyntheticInstallShieldArchive.FileEntry(bytes, 1) + 11), (uint)bytes.Length - 2);
        Assert.Contains("file 1 at 'packed.bin' lies outside", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        bytes = SyntheticInstallShieldArchive.Build(files);
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(SyntheticInstallShieldArchive.FileEntry(bytes, 0) + 3), 5);
        Assert.Contains("stored uncompressed, but declares 21 stored and 5 expanded bytes",
            Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);

        bytes = SyntheticInstallShieldArchive.Build([new("Data", "same.bin", Text), new("Data", "SAME.BIN", Text)]);
        Assert.Contains("two files at 'Data/SAME.BIN' (file 0 and file 1)", Assert.Throws<InvalidDataException>(() => Open(bytes)).Message);
    }

    [Fact]
    public void RejectsAnArchiveOverItsLimitsWhenOpened()
    {
        using var archive = new MemoryStream(SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose), new("", "b.bin", Text)]));
        Assert.Contains("declares 2 files, more than the limit of 1", Assert.Throws<InvalidDataException>(
            () => OriginalContentSource.OpenInstallShieldArchive(archive, new InstallShieldArchiveLimits(MaximumFiles: 1))).Message);
        Assert.Contains("expands to more than the limit", Assert.Throws<InvalidDataException>(
            () => OriginalContentSource.OpenInstallShieldArchive(archive, new InstallShieldArchiveLimits(MaximumExpandedBytes: Prose.Length))).Message);
        Assert.Contains("more than the limit of 10", Assert.Throws<InvalidDataException>(
            () => OriginalContentSource.OpenInstallShieldArchive(archive, new InstallShieldArchiveLimits(MaximumTableBytes: 10))).Message);
        Assert.Throws<ArgumentOutOfRangeException>(
            () => OriginalContentSource.OpenInstallShieldArchive(archive, new InstallShieldArchiveLimits(MaximumTableBytes: 0)));
    }

    [Theory]
    [InlineData(1, "ends after 12000 of its 12001 bytes")]
    [InlineData(-1, "expands past its declared size of 11999 bytes")]
    public async Task ReportsAMemberThatDoesNotExpandToItsDeclaredSize(int change, string problem)
    {
        var data = Prose[..12000];
        var bytes = SyntheticInstallShieldArchive.Build([new("", "packed.bin", data)]);
        var size = SyntheticInstallShieldArchive.FileEntry(bytes, 0) + 3;
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(size), (uint)(data.Length + change));
        using var source = Open(bytes);
        await using var stream = source.OpenRead("packed.bin");
        var exception = await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream));
        Assert.Contains($"member 'packed.bin' {problem}", exception.Message);
        // The failure repeats until a seek decodes again from the start.
        Assert.Throws<InvalidDataException>(() => stream.ReadByte());
        stream.Position = 0;
        Assert.Equal(data[0], stream.ReadByte());
    }

    [Fact]
    public async Task ReportsDataLeftAfterTheEndCodeAndDataThatDoesNotDecode()
    {
        // The stored size takes in the first byte of the next member's data.
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose), new("", "b.bin", Text)]);
        var stored = SyntheticInstallShieldArchive.FileEntry(bytes, 0) + 7;
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(stored), BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(stored)) + 1);
        using (var source = Open(bytes))
        await using (var stream = source.OpenRead("a.bin"))
            Assert.Contains("member 'a.bin' has stored data left after its end code",
                (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);

        bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose)]);
        bytes[SyntheticInstallShieldArchive.DataStart + 1] = 9;
        using (var source = Open(bytes))
        await using (var stream = source.OpenRead("a.bin"))
            Assert.Contains("member 'a.bin' does not decode after 0 bytes: PKWARE DCL data declares a dictionary of 9 bits",
                (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
    }

    [Fact]
    public async Task ReportsHowFarAMemberDecodedBeforeItsDataRanOut()
    {
        // The stored size is cut to half, so the data runs out partway through the one read that
        // asks for the whole member.
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose)]);
        var stored = SyntheticInstallShieldArchive.FileEntry(bytes, 0) + 7;
        BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(stored), BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(stored)) / 2);
        using var source = Open(bytes);
        using var stream = source.OpenRead("a.bin");
        var message = Assert.Throws<InvalidDataException>(() => stream.Read(new byte[Prose.Length])).Message;
        var match = System.Text.RegularExpressions.Regex.Match(message, @"does not decode after (\d+) bytes: PKWARE DCL data ends before its end code");
        Assert.True(match.Success, message);
        Assert.InRange(long.Parse(match.Groups[1].Value), 1, Prose.Length - 1);
    }

    [Fact]
    public void PassesOnAFailureOfTheArchiveStreamUnwrapped()
    {
        var bytes = SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose)]);
        using var archive = new FailingStream(bytes);
        using var source = OriginalContentSource.OpenInstallShieldArchive(archive);
        using var stream = source.OpenRead("a.bin");
        archive.Fail = true;
        Assert.Equal("the container failed", Assert.Throws<InvalidDataException>(() => stream.ReadByte()).Message);
    }

    [Fact]
    public void RefusesToReadOrSeekAMemberStreamOnceDisposed()
    {
        using var source = Open(SyntheticInstallShieldArchive.Build([new("", "a.bin", Prose)]));
        var stream = source.OpenRead("a.bin");
        Assert.Equal(Prose[0], stream.ReadByte());
        stream.Dispose();
        Assert.False(stream.CanRead);
        Assert.Throws<ObjectDisposedException>(() => stream.ReadByte());
        Assert.Throws<ObjectDisposedException>(() => stream.Seek(0, SeekOrigin.Begin));
    }

    [Fact]
    public async Task ReportsAnArchiveThatShrankAfterItWasOpened()
    {
        var root = TemporaryDirectory();
        try
        {
            var path = Path.Combine(root, "_SETUP.1");
            var bytes = SyntheticInstallShieldArchive.Build([new("", "stored.bin", Prose, Stored: true)]);
            File.WriteAllBytes(path, bytes);
            using var source = OriginalContentSource.OpenInstallShieldArchive(path);
            File.WriteAllBytes(path, bytes[..(SyntheticInstallShieldArchive.DataStart + 100)]);
            await using var stream = source.OpenRead("stored.bin");
            Assert.Contains("ends early", (await Assert.ThrowsAsync<InvalidDataException>(() => ReadAll(stream))).Message);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public async Task ReportsAnUnreadableMemberToTheAssetVerifier()
    {
        var root = TemporaryDirectory();
        try
        {
            var bytes = SyntheticInstallShieldArchive.Build([new("", "packed.bin", Prose)]);
            BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(SyntheticInstallShieldArchive.FileEntry(bytes, 0) + 3), (uint)Prose.Length + 1);
            var path = Path.Combine(root, "_SETUP.1");
            File.WriteAllBytes(path, bytes);
            var manifest = new AssetManifest("game", "retail",
                [new("packed.bin", Prose.Length + 1, FileFingerprint.Xxh3(Prose))], ContentSourceKinds.InstallShieldArchive);
            var result = await AssetVerifier.VerifyAsync(path, manifest, TestContext.Current.CancellationToken);
            Assert.Equal(AssetProblem.Unreadable, Assert.Single(result.Issues).Problem);

            // A split archive whose other parts are not in this file is reported as unreadable, with the reason.
            File.WriteAllBytes(path, SyntheticInstallShieldArchive.Build([new("", "packed.bin", Prose)], archiveFlags: 1, totalParts: 2));
            result = await AssetVerifier.VerifyAsync(path, manifest, TestContext.Current.CancellationToken);
            Assert.Contains("split archive", Assert.Single(result.Issues).Detail);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    private static InstallShieldArchiveSource Open(byte[] archive) =>
        OriginalContentSource.OpenInstallShieldArchive(new MemoryStream(archive));

    private static byte[] Explode(byte[] imploded)
    {
        var exploder = new PkwareDclExploder(Reader(imploded));
        using var output = new MemoryStream();
        var buffer = new byte[1000];
        int read;
        while ((read = exploder.Read(buffer)) > 0) output.Write(buffer, 0, read);
        Assert.True(exploder.Ended);
        Assert.False(exploder.HasTrailingBytes());
        return output.ToArray();
    }

    private static Func<int> Reader(byte[] bytes)
    {
        var position = 0;
        return () => position < bytes.Length ? bytes[position++] : -1;
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

    // A stream over an archive that throws InvalidDataException from every read once Fail is set, as
    // a container's stream does when its own check fails.
    private sealed class FailingStream(byte[] bytes) : MemoryStream(bytes)
    {
        public bool Fail { get; set; }

        public override int Read(Span<byte> buffer) =>
            Fail ? throw new InvalidDataException("the container failed") : base.Read(buffer);
    }

    private static string TemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "installshield3-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
