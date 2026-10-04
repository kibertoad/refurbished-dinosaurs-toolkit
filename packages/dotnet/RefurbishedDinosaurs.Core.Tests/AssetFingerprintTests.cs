using System.Text;
using RefurbishedDinosaurs.Core.Assets;
using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class AssetFingerprintTests
{
    // Reference values from the xxHash library's XXH3_128bits, as xxhsum -H2 prints them.
    public static TheoryData<byte[], string> ReferenceHashes => new()
    {
        { [], "99aa06d3014798d86001c324468d497f" },
        { "a"u8.ToArray(), "a96faf705af16834e6c632b61e964e1f" },
        { "abc"u8.ToArray(), "06b05ab6733a618578af5f94892f3950" },
        { Enumerable.Range(0, 1280).Select(i => (byte)i).ToArray(), "d92ab3a1cb0542a74844b009e164352e" },
    };

    [Theory]
    [MemberData(nameof(ReferenceHashes))]
    public async Task FingerprintsMatchTheReferenceImplementation(byte[] data, string expected)
    {
        Assert.Equal(expected, FileFingerprint.Xxh3(data));
        var path = Path.GetTempFileName();
        try
        {
            await File.WriteAllBytesAsync(path, data, TestContext.Current.CancellationToken);
            Assert.Equal(expected, FileFingerprint.Xxh3(path));
            Assert.Equal(expected, await FileFingerprint.Xxh3Async(path, TestContext.Current.CancellationToken));
        }
        finally { File.Delete(path); }
    }

    [Theory]
    [InlineData("99aa06d3014798d86001c324468d497f", true)]
    [InlineData("99AA06D3014798D86001C324468D497F", false)]
    [InlineData("99aa06d3014798d86001c324468d497", false)]
    [InlineData("0000000000000000000000000000000000000000000000000000000000000000", false)]
    [InlineData(null, false)]
    public void OnlyLowerCaseThirtyTwoDigitHexIsAFingerprint(string? value, bool expected) =>
        Assert.Equal(expected, FileFingerprint.IsXxh3(value));

    [Fact]
    public void ManifestReadsXxh3AndSourceKind()
    {
        const string json = """
            { "gameId": "game", "sourceEdition": "retail", "sourceKind": "iso9660",
              "files": [ { "path": "GAME.DAT", "size": 3, "xxh3": "06b05ab6733a618578af5f94892f3950" } ] }
            """;
        var manifest = AssetManifest.Load(new MemoryStream(Encoding.UTF8.GetBytes(json)));
        Assert.Equal("iso9660", manifest.SourceKind);
        Assert.Equal("06b05ab6733a618578af5f94892f3950", Assert.Single(manifest.Files).Xxh3);
    }

    [Theory]
    [InlineData("""{ "gameId": "game", "sourceEdition": "retail", "files": [ { "path": "A", "size": 1, "xxh3": "0000000000000000000000000000000000000000000000000000000000000000" } ] }""")]
    [InlineData("""{ "gameId": "game", "sourceEdition": "retail", "files": [ { "path": "A", "size": 1, "xxh3": "06B05AB6733A618578AF5F94892F3950" } ] }""")]
    [InlineData("""{ "gameId": "game", "sourceEdition": "retail", "sourceKind": " ", "files": [] }""")]
    public void ManifestRejectsOtherHashesAndABlankSourceKind(string json) =>
        Assert.Throws<InvalidDataException>(() => AssetManifest.Load(new MemoryStream(Encoding.UTF8.GetBytes(json))));

    [Fact]
    public void ManifestRejectsAnOversizedStream()
    {
        var stream = new MemoryStream(new byte[AssetManifest.MaximumBytes + 1]);
        Assert.Throws<InvalidDataException>(() => AssetManifest.Load(stream));
    }

    [Fact]
    public void EditionFingerprintIgnoresOrderSeparatorAndSourceKind()
    {
        const string hash = "06b05ab6733a618578af5f94892f3950";
        var first = new AssetManifest("game", "retail",
            [new(@"DATA\A0", 3, hash), new(@"DATA\A\B", 3, hash), new("GAME.EXE", 9, null, Required: false)]);
        var second = new AssetManifest("game", "retail",
            [new("GAME.EXE", 9), new("DATA/A/B", 3, hash), new("DATA/A0", 3, hash)], "iso9660");
        Assert.True(FileFingerprint.IsXxh3(first.Fingerprint()));
        Assert.Equal(first.Fingerprint(), second.Fingerprint());
        Assert.NotEqual(first.Fingerprint(), (second with { Files = [new("GAME.EXE", 10)] }).Fingerprint());
    }

    [Fact]
    public async Task VerifierReportsWrongSizeBeforeHashing()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "GAME.DAT"), "abc", TestContext.Current.CancellationToken);
            var manifest = new AssetManifest("game", "edition", [new("GAME.DAT", 4, new string('0', 32))]);
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal(AssetProblem.WrongSize, Assert.Single(result.Issues).Problem);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierChecksHashesAndSkipsAbsentOptionalFiles()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "GAME.DAT"), "abc", TestContext.Current.CancellationToken);
            var manifest = new AssetManifest("game", "edition", [
                new("GAME.DAT", 3, new string('0', 32)),
                new("MISSING.DAT", 1),
                new("OPTIONAL.DAT", 1, Required: false)]);
            var result = await AssetVerifier.VerifyAsync(root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal([AssetProblem.WrongHash, AssetProblem.Missing], result.Issues.Select(issue => issue.Problem));
            Assert.Contains("06b05ab6733a618578af5f94892f3950", result.Issues[0].Detail);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task VerifierReadsAnIsoImageAndReportsAnUnreadableSource()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var payload = "abc"u8.ToArray();
            var image = Path.Combine(root, "game.iso");
            await File.WriteAllBytesAsync(image, OriginalContentSourceTests.BuildIso(payload),
                TestContext.Current.CancellationToken);
            var manifest = new AssetManifest("game", "edition",
                [new("EI/TEST.BIN", 3, FileFingerprint.Xxh3(payload))], ContentSourceKinds.Iso9660);

            Assert.True((await AssetVerifier.VerifyAsync(image, manifest, TestContext.Current.CancellationToken)).IsValid);
            var unreadable = await AssetVerifier.VerifyAsync(Path.Combine(root, "absent.iso"), manifest,
                TestContext.Current.CancellationToken);
            var issue = Assert.Single(unreadable.Issues);
            Assert.Equal(AssetProblem.Unreadable, issue.Problem);
            Assert.Null(issue.Path);
            await Assert.ThrowsAsync<InvalidDataException>(() => AssetVerifier.VerifyAsync(
                image, manifest with { SourceKind = "zip" }, TestContext.Current.CancellationToken));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task IdentifyReturnsTheMatchingEditionAndWhyOthersFailed()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "GAME.DAT"), "abc", TestContext.Current.CancellationToken);
            var budget = new AssetManifest("game", "a-budget", [new("GAME.DAT", 4)]);
            var retail = new AssetManifest("game", "b-retail", [new("GAME.DAT", 3, "06b05ab6733a618578af5f94892f3950")]);

            var found = await AssetVerifier.IdentifyAsync(root, [retail, budget], TestContext.Current.CancellationToken);
            Assert.Same(retail, found.Edition);
            var mismatch = Assert.Single(found.Mismatches);
            Assert.Same(budget, mismatch.Edition);
            Assert.Equal(AssetProblem.WrongSize, Assert.Single(mismatch.Issues).Problem);

            var none = await AssetVerifier.IdentifyAsync(root, [budget], TestContext.Current.CancellationToken);
            Assert.False(none.IsSupported);
            Assert.Single(none.Mismatches);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledVerifierAcceptsMatchingContent()
    {
        var root = CreateInstalledContent();
        try
        {
            var result = await InstalledAssetVerifier.VerifyDirectoryAsync(root, Expectations(rejectUnlisted: true),
                TestContext.Current.CancellationToken);
            Assert.True(result.IsValid, string.Join("; ", result.Issues));
            Assert.Equal(1, result.VerifiedFiles);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void InstalledManifestIsWrittenInTheSchemaFieldNames()
    {
        var root = CreateInstalledContent();
        try
        {
            var json = File.ReadAllText(Path.Combine(root, "manifest.json"));
            foreach (var field in new[] { "\"formatVersion\"", "\"sourceFingerprint\"", "\"xxh3\"", "\"sourcePath\"" })
                Assert.Contains(field, json);
            Assert.DoesNotContain("\"FormatVersion\"", json);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledVerifierReportsUnlistedFilesOnlyWhenAsked()
    {
        var root = CreateInstalledContent();
        try
        {
            await File.WriteAllTextAsync(Path.Combine(root, "Decoded", "stale.bin"), "x", TestContext.Current.CancellationToken);
            Assert.True((await InstalledAssetVerifier.VerifyDirectoryAsync(root, Expectations(rejectUnlisted: false),
                TestContext.Current.CancellationToken)).IsValid);
            var issue = Assert.Single((await InstalledAssetVerifier.VerifyDirectoryAsync(root,
                Expectations(rejectUnlisted: true), TestContext.Current.CancellationToken)).Issues);
            Assert.Equal((InstalledAssetProblem.Unlisted, "Decoded/stale.bin"), (issue.Problem, issue.Path));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledVerifierReportsAnotherProductAndAChangedFile()
    {
        var root = CreateInstalledContent();
        try
        {
            await File.WriteAllBytesAsync(Path.Combine(root, "Decoded", "asset.bin"), [9, 9, 9],
                TestContext.Current.CancellationToken);
            var result = await InstalledAssetVerifier.VerifyDirectoryAsync(root,
                Expectations(rejectUnlisted: false) with { Product = "other" }, TestContext.Current.CancellationToken);
            Assert.Equal([InstalledAssetProblem.ProductMismatch, InstalledAssetProblem.WrongHash],
                result.Issues.Select(issue => issue.Problem));
            Assert.Equal(0, result.VerifiedFiles);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledVerifierReportsBadRecords()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var hash = FileFingerprint.Xxh3("abc"u8);
            var manifest = new InstalledAssetManifest(1, "game", "retail", new string('0', 64), DateTimeOffset.UtcNow,
                [new("../escape.bin", 3, hash, "A"), new("a.bin", 3, hash, "A"), new("A.BIN", 3, hash, "A"),
                 new("b.bin", 3, hash.ToUpperInvariant(), "A")], "1.0.0");
            var result = await InstalledAssetVerifier.VerifyAsync(root, manifest, Expectations(rejectUnlisted: false),
                TestContext.Current.CancellationToken);
            Assert.Equal([
                    InstalledAssetProblem.SourceFingerprintInvalid, InstalledAssetProblem.UnsafePath,
                    InstalledAssetProblem.Missing, InstalledAssetProblem.DuplicatePath,
                    InstalledAssetProblem.InvalidRecord],
                result.Issues.Select(issue => issue.Problem));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public async Task InstalledVerifierReportsAMissingOrUnreadableManifest()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var expected = Expectations(rejectUnlisted: false);
            Assert.Equal(InstalledAssetProblem.ManifestMissing, Assert.Single((await InstalledAssetVerifier
                .VerifyDirectoryAsync(root, expected, TestContext.Current.CancellationToken)).Issues).Problem);

            await File.WriteAllTextAsync(Path.Combine(root, "manifest.json"), "not json", TestContext.Current.CancellationToken);
            Assert.Equal(InstalledAssetProblem.ManifestUnreadable, Assert.Single((await InstalledAssetVerifier
                .VerifyDirectoryAsync(root, expected, TestContext.Current.CancellationToken)).Issues).Problem);

            await File.WriteAllBytesAsync(Path.Combine(root, "manifest.json"),
                new byte[InstalledAssetManifest.DefaultMaximumBytes + 1], TestContext.Current.CancellationToken);
            var tooLarge = Assert.Single((await InstalledAssetVerifier
                .VerifyDirectoryAsync(root, expected, TestContext.Current.CancellationToken)).Issues);
            Assert.Equal(InstalledAssetProblem.ManifestUnreadable, tooLarge.Problem);
            Assert.Contains("limit", tooLarge.Detail);
        }
        finally { Directory.Delete(root, true); }
    }

    private static InstalledAssetExpectations Expectations(bool rejectUnlisted) =>
        new(1, "game", RejectUnlistedFiles: rejectUnlisted);

    private static string CreateInstalledContent()
    {
        var root = CreateTemporaryDirectory();
        var written = InstalledContentWriter.WriteBytes(root, "Decoded/asset.bin", [1, 2, 3]);
        var edition = new AssetManifest("game", "retail", [new("GAME.DAT", 3)]);
        new InstalledAssetManifest(1, "game", "retail", edition.Fingerprint(), DateTimeOffset.UtcNow,
            [new("Decoded/asset.bin", written.Bytes, written.Xxh3, "GAME.DAT")], "1.0.0")
            .Write(Path.Combine(root, "manifest.json"));
        return root;
    }

    private static string CreateTemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "scientific-method-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
