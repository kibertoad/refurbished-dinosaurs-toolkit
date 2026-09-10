using Toad.Discovery.Core.Assets;
using Toad.Discovery.Core.Imaging;
using Toad.Discovery.Core.Paths;
using Xunit;

namespace Toad.Discovery.Core.Tests;

public sealed class CoreTests
{
    [Fact]
    public void ManifestRejectsTraversal()
    {
        var manifest = new AssetManifest("game", "edition", [new("../secret.dat", 1)]);
        Assert.Throws<InvalidDataException>(manifest.Validate);
    }

    [Fact]
    public async Task VerifierReportsWrongSizeBeforeHashing()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            await File.WriteAllTextAsync(
                Path.Combine(root, "GAME.DAT"), "abc", TestContext.Current.CancellationToken);
            var manifest = new AssetManifest("game", "edition", [new("GAME.DAT", 4, new string('0', 64))]);
            var result = await AssetVerifier.VerifyAsync(
                root, manifest, TestContext.Current.CancellationToken);
            Assert.Equal(AssetProblem.WrongSize, Assert.Single(result.Issues).Problem);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void StagedPackDoesNotReplaceDestinationUntilCommit()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var destination = Path.Combine(root, "UserContent");
            Directory.CreateDirectory(destination);
            File.WriteAllText(Path.Combine(destination, "old.txt"), "old");
            using var pack = StagedAssetPack.Create(destination);
            File.WriteAllText(Path.Combine(pack.StagingDirectory, "new.txt"), "new");
            Assert.True(File.Exists(Path.Combine(destination, "old.txt")));
            pack.Commit();
            Assert.True(File.Exists(Path.Combine(destination, "new.txt")));
            Assert.False(File.Exists(Path.Combine(destination, "old.txt")));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void PathsPreferContentBesideApplication()
    {
        var root = CreateTemporaryDirectory();
        try
        {
            var app = Path.Combine(root, "Game");
            var content = Path.Combine(app, "UserContent");
            Directory.CreateDirectory(content);
            var result = RestorationPaths.ResolveImportedContent(
                new("ExampleGame"), app, root, Path.Combine(root, "Local"));
            Assert.Equal(content, result);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void SixBitPaletteExpandsChannels()
    {
        var data = new byte[IndexedPalette.ByteSize];
        data[0] = 63;
        Assert.Equal((byte)255, IndexedPaletteDecoder.Decode(data, 6)[0].R);
    }

    private static string CreateTemporaryDirectory()
    {
        var path = Path.Combine(Path.GetTempPath(), "toad-discovery-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(path);
        return path;
    }
}
