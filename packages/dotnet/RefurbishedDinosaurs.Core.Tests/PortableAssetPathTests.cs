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
    [InlineData("folder/frame.dat.")]
    [InlineData("folder /frame.dat")]
    [InlineData("folder/.. /frame.dat")]
    [InlineData("folder/CON")]
    [InlineData("folder/nul.dat")]
    public void RelativeRejectsUnsafeReferencesOnEveryHost(string reference) =>
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));

    [Theory]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData(null)]
    public void BlankReferencesAreInvalidData(string? reference)
    {
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference!));
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.WithoutDriveRoot(reference!));
    }

    [Theory]
    [InlineData("folder\\frame.dat", "folder/frame.dat")]
    [InlineData("Folder/CONTROL.DAT", "Folder/CONTROL.DAT")]
    [InlineData("frame", "frame")]
    public void RelativeNormalizesSeparators(string reference, string expected) =>
        Assert.Equal(expected, PortableAssetPath.Relative(reference));

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
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(Path.Combine(root, "absent"), "frame.dat"));
            Assert.Throws<ArgumentException>(() => PortableAssetPath.ResolveFile(" ", "frame.dat"));
            if (!OperatingSystem.IsWindows() && !OperatingSystem.IsMacOS())
            {
                File.WriteAllText(Path.Combine(root, "Assets", "Frames", "frame.dat"), "ambiguous");
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "Assets/Frames/Frame.dat"));
                Directory.CreateSymbolicLink(Path.Combine(root, "linked"), Path.Combine(root, "Assets"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(root, "linked/Frames/Frame.dat"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(Path.Combine(root, "linked"), "Frames/Frame.dat"));
            }
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void ResolvesHiddenAndSystemEntries()
    {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        var directory = Directory.CreateDirectory(Path.Combine(root, ".Hidden"));
        try
        {
            var file = Path.Combine(directory.FullName, "Frame.dat");
            File.WriteAllText(file, "synthetic");
            if (OperatingSystem.IsWindows())
            {
                directory.Attributes |= FileAttributes.Hidden | FileAttributes.System;
                File.SetAttributes(file, FileAttributes.Hidden | FileAttributes.System);
            }
            Assert.Equal(".Hidden/Frame.dat", PortableAssetPath.ResolveFile(root, ".hidden/frame.dat"));
        }
        finally { Directory.Delete(root, true); }
    }
}
