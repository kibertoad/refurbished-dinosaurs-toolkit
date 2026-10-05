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
    public void ResolvesDirectoriesWithTheFileRules()
    {
        var root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path.Combine(root, "A", "b", "C"));
        try
        {
            File.WriteAllText(Path.Combine(root, "A", "b", "C", "Tile.dat"), "synthetic");
            Assert.Equal("A/b/C", PortableAssetPath.ResolveDirectory(root, "a/B/c"));
            Assert.Equal("A/b/C", PortableAssetPath.ResolveDirectory(root, "a\\B\\c"));
            Assert.Equal("A", PortableAssetPath.ResolveDirectory(root, "a"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/B/c/tile.dat"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/missing"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/missing/c"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/B/c/tile.dat/d"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/../A"));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, " "));
            Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(Path.Combine(root, "absent"), "a"));
            Assert.Throws<ArgumentException>(() => PortableAssetPath.ResolveDirectory(" ", "a"));
            if (!OperatingSystem.IsWindows() && !OperatingSystem.IsMacOS())
            {
                Directory.CreateDirectory(Path.Combine(root, "A", "B"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "A/b"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "a/b/C"));
                Directory.CreateSymbolicLink(Path.Combine(root, "linked"), Path.Combine(root, "A", "b"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "linked"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(root, "Linked/C"));
                Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveDirectory(Path.Combine(root, "linked"), "C"));
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

    [Fact]
    public void ALayoutSpellsEachDirectoryLikeTheFirstPathUnderIt()
    {
        var layout = new PortablePathLayout();
        Assert.Equal("Data/A.DAT", layout.Add("Data\\A.DAT"));
        Assert.Equal("Data/Sub/b.dat", layout.Add("DATA/Sub/b.dat"));
        Assert.Equal("Data/Sub/c.dat", layout.Add("data/SUB/c.dat"));
        Assert.Equal("top.dat", layout.Add("top.dat"));
        Assert.Equal(4, layout.Count);
    }

    [Theory]
    [InlineData("data/a.dat", "DATA/A.DAT", "twice")]
    [InlineData("data/a.dat", "DATA/A.DAT/b.dat", "both a file and a directory")]
    [InlineData("data/a.dat/b.dat", "DATA/A.DAT", "both a file and a directory")]
    [InlineData("data/a.dat", "../a.dat", "is not a portable relative path")]
    [InlineData("data/a.dat", "data/a|b.dat", "is not a portable relative path")]
    public void ALayoutRejectsAPathThatClashesIgnoringCase(string first, string second, string message)
    {
        var layout = new PortablePathLayout();
        layout.Add(first);
        var exception = Assert.Throws<InvalidDataException>(() => layout.Add(second));
        Assert.Contains(message, exception.Message);
        Assert.Equal(1, layout.Count);
    }

    [Theory]
    [InlineData("bad<name.dat", "'<'")]
    [InlineData("bad>name.dat", "'>'")]
    [InlineData("bad\"name.dat", "'\"'")]
    [InlineData("bad|name.dat", "'|'")]
    [InlineData("data/bad?name.dat", "'?'")]
    [InlineData("bad*/name.dat", "'*'")]
    [InlineData("data:stream", "':'")]
    public void RelativeRejectsEveryCharacterWindowsRefusesInAName(string reference, string character)
    {
        var exception = Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));
        Assert.Contains($"it contains {character}, which Windows does not allow in a file name", exception.Message);
        Assert.Throws<InvalidDataException>(() => PortableAssetPath.WithoutDriveRoot($"C:\\{reference}"));
    }

    [Fact]
    public void RelativeRejectsEveryC0ControlCharacterAndDel()
    {
        for (var code = 0; code <= 0x7f; code = code == 0x1f ? 0x7f : code + 1)
        {
            var reference = $"data/bad{(char)code}name.dat";
            var exception = Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));
            Assert.Contains($"it contains the control character U+{code:X4}", exception.Message);
            // The reference is shown as a JSON string, so a control character stays visible on one line.
            Assert.Contains($"\"data/bad\\u{code:X4}name.dat\"", exception.Message);
        }
    }

    [Fact]
    public void RelativeRejectsAnUnpairedSurrogate()
    {
        // Built in code: theory data with a lone surrogate does not survive serialization.
        (string Reference, string CodePoint)[] cases =
        [
            ("bad\uD800name.dat", "U+D800"), ("bad\uDC00name.dat", "U+DC00"), ("bad\uD800", "U+D800"),
            ("\uDC00\uD800.dat", "U+DC00")
        ];
        foreach (var (reference, codePoint) in cases)
        {
            var exception = Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));
            Assert.Contains($"it contains the unpaired surrogate {codePoint}", exception.Message);
            Assert.Contains($"\\u{codePoint[2..]}", exception.Message);
        }
    }

    [Theory]
    [InlineData("My Game/Save Files/slot 1.sav")]
    [InlineData("DATA/~TEMP~1.DAT")]
    [InlineData("#1 & 2 + 3!/it's (final) [v2] @home.dat")]
    [InlineData("a{b}c=d,e;f%g$h^i`j.dat")]
    [InlineData("Donn\u00E9es/\u00C9CRAN \u00DC \u00DF \u00F1 \u00FF.PCX")]
    // C1 controls are what a Latin-1 reading gives for bytes 0x80 to 0x9F of a DOS or Shift-JIS name.
    [InlineData("MUSIC/\u0082T\u0085\u009F.WAV")]
    [InlineData("\u00A0nbsp/pair \uD83E\uDD95.dat")]
    public void RelativeAdmitsOrdinaryLegacyNames(string reference) =>
        Assert.Equal(reference, PortableAssetPath.Relative(reference));

    [Theory]
    [InlineData("/data/a.dat", "it is rooted")]
    [InlineData("data//a.dat", "it has an empty component")]
    [InlineData("data/../a.dat", "it has a '..' component")]
    [InlineData("data/./a.dat", "it has a '.' component")]
    [InlineData("data /a.dat", "component \"data \" ends with a dot or a space")]
    [InlineData("data/Aux.txt", "component \"Aux.txt\" is the reserved Windows device name Aux")]
    public void TheMessageNamesTheRuleAReferenceBreaks(string reference, string rule)
    {
        var exception = Assert.Throws<InvalidDataException>(() => PortableAssetPath.Relative(reference));
        Assert.Contains("is not a portable relative path", exception.Message);
        Assert.Contains(rule, exception.Message);
    }

    [Fact]
    public void ResolveFileRejectsAReservedCharacterBeforeReadingTheTree() =>
        Assert.Contains("'?'", Assert.Throws<InvalidDataException>(() => PortableAssetPath.ResolveFile(
            Path.Combine(Path.GetTempPath(), $"absent-{Guid.NewGuid():N}"), "a?.dat")).Message);
}
