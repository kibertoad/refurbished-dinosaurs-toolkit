using RefurbishedDinosaurs.LegacyFormats;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class BuildListingTests : IDisposable
{
    private const int RawSector = CueBinSheet.RawSectorSize;
    private static readonly DateOnly Day = new(2026, 10, 8);

    private readonly string _work = Directory.CreateTempSubdirectory("build-listing-tests-").FullName;

    public void Dispose() => Directory.Delete(_work, recursive: true);

    private static CancellationToken Token => TestContext.Current.CancellationToken;

    [Fact]
    public async Task ListsEveryFileOfTheInstallationSortedByteByByte()
    {
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "game.exe"), 5);
        await WriteAsync(Path.Combine(install, "Data", "MAP.BIN"), 3);
        await WriteAsync(Path.Combine(install, "Data", "deep", "UNIT.DAT"), 0);
        await WriteAsync(Path.Combine(install, "capture", "shot0001.png"), 2);
        var hidden = Path.Combine(install, "hidden.cfg");
        await WriteAsync(hidden, 1);
        File.SetAttributes(hidden, FileAttributes.Hidden);
        Directory.CreateDirectory(Path.Combine(install, "empty"));

        var record = BuildListing.Make(install, [], Day);

        Assert.Equal(new BuildListingMedium("", null, null), Assert.Single(record.Media));
        Assert.Equal(
            [
                new BuildListingItem("Data/MAP.BIN", 3, null, null),
                new BuildListingItem("Data/deep/UNIT.DAT", 0, null, null),
                new BuildListingItem("capture/shot0001.png", 2, null, null),
                new BuildListingItem("game.exe", 5, null, null),
                new BuildListingItem("hidden.cfg", 1, null, null)
            ],
            record.Items);
        Assert.StartsWith("RefurbishedDinosaurs.LegacyFormats ", record.Tool, StringComparison.Ordinal);
        Assert.Equal(
            $"tool: '{record.Tool}'\n" +
            "date: 2026-10-08\nlinks: listed\ncycles: null\n" +
            "media:\n  - prefix: ''\n    source: null\n    layout: null\n" +
            "archives: []\nitems:\n" +
            "  - path: 'Data/MAP.BIN'\n    size: 3\n" +
            "  - path: 'Data/deep/UNIT.DAT'\n    size: 0\n" +
            "  - path: 'capture/shot0001.png'\n    size: 2\n" +
            "  - path: 'game.exe'\n    size: 5\n" +
            "  - path: 'hidden.cfg'\n    size: 1\n",
            record.ToYaml());
    }

    [Fact]
    public async Task GivesALinkWithItsTargetAndDoesNotEnterIt()
    {
        var install = Path.Combine(_work, "install");
        var outside = Path.Combine(_work, "outside");
        await WriteAsync(Path.Combine(install, "game.exe"), 4);
        await WriteAsync(Path.Combine(outside, "SAVE1.SAV"), 9);
        var link = Path.Combine(install, "Saves");
        try
        {
            Directory.CreateSymbolicLink(link, outside);
        }
        catch (Exception exception) when (OperatingSystem.IsWindows() && exception is IOException or UnauthorizedAccessException)
        {
            // A symbolic link needs a privilege on Windows that a junction does not.
            using var mklink = System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(
                "cmd.exe", ["/c", "mklink", "/J", link, outside]) { RedirectStandardOutput = true })!;
            await mklink.WaitForExitAsync(Token);
            Assert.Equal(0, mklink.ExitCode);
        }

        BuildListingRecord record;
        try
        {
            record = BuildListing.Make(install, [], Day);
        }
        finally
        {
            // Removes the link alone, so deleting the work directory does not walk into it.
            Directory.Delete(link);
        }

        Assert.Equal(
            [new BuildListingItem("Saves", null, outside, null), new BuildListingItem("game.exe", 4, null, null)],
            record.Items);
        Assert.Contains($"  - path: 'Saves'\n    link: '{outside}'\n", record.ToYaml(), StringComparison.Ordinal);
        Assert.True(File.Exists(Path.Combine(outside, "SAVE1.SAV")));
    }

    [Fact]
    public async Task GivesAFileReparsePointThatIsNotALinkAsAFile()
    {
        if (!OperatingSystem.IsWindows()) Assert.Skip("The test sets an NTFS reparse point.");
        var install = Path.Combine(_work, "install");
        var placeholder = Path.Combine(install, "GAME.DAT");
        Directory.CreateDirectory(install);
        await File.WriteAllBytesAsync(placeholder, new byte[300], Token);
        // A reparse point of a Microsoft tag that is no link, as a cloud file's placeholder or a
        // deduplicated file carries (IO_REPARSE_TAG_HSM here), with no data.
        byte[] buffer = [0x04, 0x00, 0x00, 0xC0, 0, 0, 0, 0];
        using (var handle = File.OpenHandle(placeholder, FileMode.Open, FileAccess.ReadWrite))
        {
            if (!NativeReparse.DeviceIoControl(handle, NativeReparse.SetReparsePoint, buffer, buffer.Length,
                    IntPtr.Zero, 0, out _, IntPtr.Zero))
                Assert.Skip($"This volume refused the reparse point (error {System.Runtime.InteropServices.Marshal.GetLastPInvokeError()}).");
        }
        Assert.NotEqual(0, (int)(File.GetAttributes(placeholder) & FileAttributes.ReparsePoint));

        var record = BuildListing.Make(install, [], Day);
        Assert.Equal([new BuildListingItem("GAME.DAT", 300, null, null)], record.Items);
    }

    private static class NativeReparse
    {
        public const uint SetReparsePoint = 0x000900A4;

        [System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool DeviceIoControl(
            Microsoft.Win32.SafeHandles.SafeFileHandle device, uint code, byte[] input, int inputLength,
            IntPtr output, int outputLength, out int returned, IntPtr overlapped);
    }

    [Fact]
    public async Task GivesADirectoryItCannotEnterAsStopped()
    {
        if (OperatingSystem.IsWindows()) Assert.Skip("The test closes a directory with Unix permissions.");
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "game.exe"), 4);
        var locked = Path.Combine(install, "locked");
        await WriteAsync(Path.Combine(locked, "inside.dat"), 1);
        if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(locked, UnixFileMode.None);
        BuildListingRecord record;
        try
        {
            if (Directory.EnumerateFileSystemEntries(locked).Any()) Assert.Skip("This user reads every directory.");
        }
        catch (UnauthorizedAccessException) { }
        try
        {
            record = BuildListing.Make(install, [], Day);
        }
        finally
        {
            if (!OperatingSystem.IsWindows())
                File.SetUnixFileMode(locked, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        }

        Assert.Equal(
            [
                new BuildListingItem("game.exe", 4, null, null),
                new BuildListingItem("locked", null, null, "a directory it could not enter (UnauthorizedAccessException)")
            ],
            record.Items);
        // The reason names no path on this machine, so the record can be written.
        Assert.DoesNotContain(_work, record.ToYaml(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task RefusesANameAListingPathCannotHold()
    {
        if (OperatingSystem.IsWindows()) Assert.Skip("Windows refuses these names itself.");
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "a|b"), 1);
        Assert.Contains("marks an archive member", Assert.Throws<InvalidDataException>(() =>
            BuildListing.Make(install, [], Day)).Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ListsAnIsoImageUnderItsPrefix()
    {
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, ContentSourceExtractorTests.BuildTreeIso(new Dictionary<string, byte[]>
        {
            ["SETUP.EXE"] = new byte[7],
            ["DATA/INTRO.FLI"] = new byte[3000]
        }), Token);

        var record = BuildListing.Make(null, [new BuildListingDisc("CD:", image, "images/game.iso")], Day);

        Assert.Equal(new BuildListingMedium("CD:", "images/game.iso", "2048"), Assert.Single(record.Media));
        Assert.Equal(
            [new BuildListingItem("CD:DATA/INTRO.FLI", 3000, null, null), new BuildListingItem("CD:SETUP.EXE", 7, null, null)],
            record.Items);
        Assert.Contains("  - prefix: 'CD:'\n    source: 'images/game.iso'\n    layout: '2048'\n", record.ToYaml(),
            StringComparison.Ordinal);
    }

    [Fact]
    public async Task KeepsTheWholeIdentifierOfNamesThatWouldCollide()
    {
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, ContentSourceExtractorTests.BuildTreeIso(new Dictionary<string, byte[]>
        {
            ["README.;1"] = new byte[4],
            ["README.;2"] = new byte[6],
            ["NOTES.;1"] = new byte[2],
            ["SETUP.EXE;1"] = new byte[1]
        }, rawNames: true), Token);

        using (var source = OriginalContentSource.OpenIso9660(image))
            Assert.Equal(["NOTES", "README.;1", "README.;2", "SETUP.EXE"], source.Files.Select(file => file.Path));
        var record = BuildListing.Make(null, [new BuildListingDisc("CD2:", image, "game.iso")], Day);
        Assert.Equal(["CD2:NOTES", "CD2:README.;1", "CD2:README.;2", "CD2:SETUP.EXE"], record.Items.Select(item => item.Path));
    }

    [Fact]
    public async Task KeepsTheWholeIdentifierOfNamesThatCollideIgnoringCase()
    {
        // The source looks paths up ignoring case, so Readme and README would be one path.
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, ContentSourceExtractorTests.BuildTreeIso(new Dictionary<string, byte[]>
        {
            ["Readme.;1"] = new byte[4],
            ["README.;2"] = new byte[6]
        }, rawNames: true), Token);

        var record = BuildListing.Make(null, [new BuildListingDisc("CD:", image, "game.iso")], Day);
        Assert.Equal(["CD:README.;2", "CD:Readme.;1"], record.Items.Select(item => item.Path));
    }

    [Fact]
    public async Task RefusesAnInstallationNameThatStartsLikeADiscPath()
    {
        if (OperatingSystem.IsWindows()) Assert.Skip("Windows refuses ':' in a name itself.");
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "CD:notes.txt"), 1);
        Assert.Contains("starts like a disc path", Assert.Throws<InvalidDataException>(() =>
            BuildListing.Make(install, [], Day)).Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("MODE1/2352")]
    [InlineData("MODE2/2352")]
    public async Task ListsACueBinImageWithItsAudioTracks(string mode)
    {
        var iso = OriginalContentSourceTests.BuildIso([1, 2, 3]);
        var data = mode == "MODE1/2352" ? CueBinSourceTests.ToRaw(iso) : Mode2Form1SourceTests.ToMode2Form1(iso);
        // Track 02 runs from INDEX 01 to track 03's pregap; track 03 runs to the end of the image.
        var bin = new byte[data.Length + 2 * 75 * RawSector + 40 * RawSector];
        data.CopyTo(bin, 0);
        var dataSectors = data.Length / RawSector;
        var cue = $"FILE \"game.bin\" BINARY\nTRACK 01 {mode}\nINDEX 01 00:00:00\n" +
            $"TRACK 02 AUDIO\nINDEX 00 {Msf(dataSectors)}\nINDEX 01 {Msf(dataSectors + 10)}\n" +
            $"TRACK 03 AUDIO\nINDEX 00 {Msf(dataSectors + 75)}\nINDEX 01 {Msf(dataSectors + 75 + 5)}\n";
        await File.WriteAllBytesAsync(Path.Combine(_work, "game.bin"), bin, Token);
        await File.WriteAllTextAsync(Path.Combine(_work, "game.cue"), cue, Token);

        var record = BuildListing.Make(null,
            [new BuildListingDisc("CD:", Path.Combine(_work, "game.cue"), "game.cue")], Day);

        Assert.Equal(new BuildListingMedium("CD:", "game.cue", mode), Assert.Single(record.Media));
        Assert.Equal(
            [
                new BuildListingItem("CD:EI/TEST.BIN", 3, null, null),
                new BuildListingItem("CD:track02", 65L * RawSector, null, null),
                new BuildListingItem("CD:track03", (2 * 75 + 40 - 75 - 5L) * RawSector, null, null)
            ],
            record.Items);
    }

    [Fact]
    public async Task ListsTheInstallationAndADiscTogether()
    {
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "GAME.EXE"), 2);
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, OriginalContentSourceTests.BuildIso([5]), Token);

        var record = BuildListing.Make(install, [new BuildListingDisc("CD:", image, "game.iso")], Day);

        Assert.Equal(["", "CD:"], record.Media.Select(medium => medium.Prefix));
        Assert.Equal(["CD:EI/TEST.BIN", "GAME.EXE"], record.Items.Select(item => item.Path));
    }

    [Fact]
    public async Task RefusesWhatItCannotList()
    {
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, OriginalContentSourceTests.BuildIso([5]), Token);

        Assert.Throws<ArgumentException>(() => BuildListing.Make(null, [], Day));
        Assert.Throws<DirectoryNotFoundException>(() => BuildListing.Make(Path.Combine(_work, "missing"), [], Day));
        Assert.Contains("is not CD:", Assert.Throws<ArgumentException>(() =>
            BuildListing.Make(null, [new BuildListingDisc("DISC:", image, "game.iso")], Day)).Message, StringComparison.Ordinal);
        Assert.Contains("Two discs", Assert.Throws<ArgumentException>(() => BuildListing.Make(null,
            [new BuildListingDisc("CD:", image, "a.iso"), new BuildListingDisc("CD:", image, "b.iso")], Day)).Message,
            StringComparison.Ordinal);
        Assert.Contains("no source", Assert.Throws<ArgumentException>(() =>
            BuildListing.Make(null, [new BuildListingDisc("CD:", image, " ")], Day)).Message, StringComparison.Ordinal);
        Assert.Contains("mounted disc", Assert.Throws<ArgumentException>(() =>
            BuildListing.Make(null, [new BuildListingDisc("CD:", _work, "D:")], Day)).Message, StringComparison.Ordinal);
        Assert.Contains("forward slashes", Assert.Throws<ArgumentException>(() =>
            BuildListing.Make(null, [new BuildListingDisc("CD:", image, "images\\game.iso")], Day)).Message,
            StringComparison.Ordinal);
        // A disc refused for its image is refused before the installation is walked.
        Assert.Throws<ArgumentException>(() => BuildListing.Make(Path.Combine(_work, "missing"),
            [new BuildListingDisc("CD:", _work, "D:")], Day));
    }

    [Fact]
    public async Task WritesAValueWithASingleQuoteInDoubleQuotes()
    {
        var install = Path.Combine(_work, "install");
        await WriteAsync(Path.Combine(install, "Player's Guide.txt"), 1);
        Assert.Contains("  - path: \"Player's Guide.txt\"\n", BuildListing.Make(install, [], Day).ToYaml(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task WritesAValueWithBothQuotesInSingleQuotesWithTheQuoteDoubled()
    {
        var image = Path.Combine(_work, "game.iso");
        await File.WriteAllBytesAsync(image, ContentSourceExtractorTests.BuildTreeIso(new Dictionary<string, byte[]>
        {
            ["SETUP.EXE"] = new byte[7]
        }), Token);
        var record = BuildListing.Make(null, [new BuildListingDisc("CD:", image, "Bob's \"best\" disc.iso")], Day);
        Assert.Contains("    source: 'Bob''s \"best\" disc.iso'\n", record.ToYaml(), StringComparison.Ordinal);
    }

    private static string Msf(int sector) => $"{sector / 75 / 60:D2}:{sector / 75 % 60:D2}:{sector % 75:D2}";

    private static async Task WriteAsync(string path, int length)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        await File.WriteAllBytesAsync(path, new byte[length], Token);
    }
}
