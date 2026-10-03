using RefurbishedDinosaurs.Core.IO;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class PortableAssetPathTests
{
    [Theory]
    [InlineData("C:\\assets\\frame.dat")]
    [InlineData("C:frame.dat")]
    [InlineData("\\\\server\\share\\frame.dat")]
    [InlineData("/frame.dat")]
    [InlineData("folder\\..\\frame.dat")]
    [InlineData("folder//frame.dat")]
    [InlineData("folder/./frame.dat")]
    [InlineData("folder/\0frame.dat")]
    public void RelativeRejectsUnsafeReferencesOnEveryHost(string reference) =>
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));

    [Fact]
    public void DriveRemovalIsExplicitAndStillRejectsTraversal()
    {
        Assert.Equal("ASSETS/frame.dat", PortableAssetPath.WithoutDriveRoot("C:\\ASSETS\\frame.dat"));
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.WithoutDriveRoot("C:\\..\\frame.dat"));
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.WithoutDriveRoot("C:\\"));
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.WithoutDriveRoot("C:frame.dat"));
    }

    [Fact]
    public void ResolvesEveryComponentAndRejectsMissingDirectoriesAndNonFiles()
    {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "Assets", "Frames"));
        try
        {
            File.WriteAllText(Path.Combine(root, "Assets", "Frames", "Frame.dat"), "synthetic");
            Assert.Equal("Assets/Frames/Frame.dat", PortableAssetPath.ResolveFile(root, "ASSETS\\frames\\FRAME.DAT"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "Assets/Frames"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "missing/frame.dat"));
            if (!OperatingSystem.IsWindows() && !OperatingSystem.IsMacOS())
            {
                File.WriteAllText(Path.Combine(root, "Assets", "Frames", "frame.dat"), "ambiguous");
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "Assets/Frames/Frame.dat"));
                Directory.CreateSymbolicLink(Path.Combine(root, "linked"), Path.Combine(root, "Assets"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "linked/Frames/Frame.dat"));
            }
        }
        finally { Directory.Delete(root, true); }
    }
}
