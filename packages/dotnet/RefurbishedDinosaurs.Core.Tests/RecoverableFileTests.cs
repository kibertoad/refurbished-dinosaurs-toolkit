using System.Text.Json;
using RefurbishedDinosaurs.Core.Persistence;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class RecoverableFileTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N"));
    private string PathName => Path.Combine(_root, "save.dat");
    public RecoverableFileTests() => Directory.CreateDirectory(_root);
    public void Dispose() => Directory.Delete(_root, true);
    private static int Read(string path) => int.Parse(File.ReadAllText(path));
    private void Write(string value) => RecoverableFile.Write(PathName,
        stream => stream.Write(System.Text.Encoding.UTF8.GetBytes(value)), path => Read(path),
        error => error is IOException or FormatException);

    [Fact]
    public void RejectsStagedCorruptionAndPreservesGoodBackupAcrossDamagedPrimary()
    {
        Write("1"); Write("2");
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Throws<FormatException>(() => Write("broken"));
        Assert.Equal("2", File.ReadAllText(PathName));
        File.WriteAllText(PathName, "broken");
        Write("3");
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void RecoveryReportsFailureWithoutRepairAndCanRejectIncompatibility()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "broken");
        var result = RecoverableFile.Read(PathName, Read, error => error is IOException or FormatException);
        Assert.Equal(1, result.Value);
        Assert.Equal(FileGeneration.Backup, result.Generation);
        Assert.IsType<FormatException>(result.PrimaryFailure);
        Assert.Equal("broken", File.ReadAllText(PathName));
        Assert.Throws<FormatException>(() => RecoverableFile.Read(PathName, Read));
        File.WriteAllText(PathName + ".bak", "also broken");
        var unreadable = Assert.Throws<FileGenerationsUnreadableException>(() => RecoverableFile.Read(PathName, Read,
            error => error is IOException or FormatException));
        Assert.Equal(2, unreadable.InnerExceptions.Count);
        Assert.IsType<FormatException>(unreadable.PrimaryFailure);
        Assert.Same(unreadable.InnerExceptions[1], unreadable.BackupFailure);
    }

    [Fact]
    public void WriterFailureLeavesBothGenerationsAndCleansTemporary()
    {
        Write("1"); Write("2");
        Assert.Throws<InvalidOperationException>(() => RecoverableFile.Write(PathName,
            _ => throw new InvalidOperationException("writer failure"), path => Read(path)));
        Assert.Equal("2", File.ReadAllText(PathName));
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void SettingsUseBoundedReadsAndReportBackupAndDefaults()
    {
        var store = new JsonSettingsStore<Settings>(PathName) { MaximumBytes = 64 };
        store.Save(new(1, true), value => value.Version == 1);
        store.Save(new(1, false), value => value.Version == 1);
        File.WriteAllText(PathName, new string('x', 65));
        var result = store.LoadResult(() => new(1, false), value => value.Version == 1);
        Assert.Equal(SettingsSource.Backup, result.Source);
        Assert.True(result.Value.Enabled);
        File.Delete(PathName + ".bak");
        Assert.Equal(SettingsSource.Default, store.LoadResult(() => new(1, false), value => value.Version == 1).Source);
        Assert.Throws<InvalidDataException>(() => RecoverableFile.ReadBounded(PathName, 64));
        Assert.Equal(SettingsSource.Backup, SettingsRecovery.Select<bool>(null, true, false, _ => true).Source);
    }

    [Fact]
    public void WriterLockTimesOutAndReleasesWithoutDeletingItsIdentity()
    {
        using (FileWriteLock.Acquire(PathName, TimeSpan.Zero, TestContext.Current.CancellationToken))
            Assert.Throws<TimeoutException>(() => FileWriteLock.Acquire(PathName, TimeSpan.FromMilliseconds(20), TestContext.Current.CancellationToken));
        using var next = FileWriteLock.Acquire(PathName, TimeSpan.Zero, TestContext.Current.CancellationToken);
        Assert.True(File.Exists(PathName + ".lock"));
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        Assert.Throws<OperationCanceledException>(() => FileWriteLock.Acquire(PathName, TimeSpan.FromSeconds(1), cancelled.Token));
    }

    [Fact]
    public async Task AsyncAtomicWriteDoesNotPromoteCancelledContents()
    {
        await RefurbishedDinosaurs.Core.IO.AtomicFile.WriteBytesAsync(PathName, new byte[] { 1 }, TestContext.Current.CancellationToken);
        using var cancelled = new CancellationTokenSource();
        cancelled.Cancel();
        await Assert.ThrowsAsync<OperationCanceledException>(() => RefurbishedDinosaurs.Core.IO.AtomicFile.WriteBytesAsync(
            PathName, new byte[] { 2 }, cancelled.Token));
        Assert.Equal(new byte[] { 1 }, File.ReadAllBytes(PathName));
    }

    [Fact]
    public void PreservesIncompatiblePrimaryAndAllowsAnExplicitTrustedGeneration()
    {
        File.WriteAllText(PathName, "newer-version");
        RecoverableFile.Write(PathName, stream => stream.Write(new byte[] { 49 }), candidate => Read(candidate),
            error => error is FormatException, error => error is FormatException, ".previous");
        Assert.Equal("newer-version", File.ReadAllText(PathName + ".previous"));
        var validations = 0;
        RecoverableFile.Write(PathName, stream => stream.Write(new byte[] { 50 }), candidate => { Read(candidate); validations++; },
            backupSuffix: ".previous", trustExistingPrimary: true);
        Assert.Equal(1, validations);
        Assert.Equal("1", File.ReadAllText(PathName + ".previous"));
    }

    [Fact]
    public void SettingsSavePreservesMigratablePrimary()
    {
        var store = new JsonSettingsStore<Settings>(PathName) { MaximumBytes = 64 };
        File.WriteAllText(PathName, JsonSerializer.Serialize(new Settings(0, true)));
        store.Save(new(1, false), value => value.Version == 1,
            value => value.Version == 0 ? value with { Version = 1 } : null);
        Assert.Equal(0, JsonSerializer.Deserialize<Settings>(File.ReadAllText(PathName + ".bak"))!.Version);
    }

    [Fact]
    public void SettingsLoadAPrimaryWithAByteOrderMarkAndRejectANonPositiveLimit()
    {
        var store = new JsonSettingsStore<Settings>(PathName) { MaximumBytes = 64 };
        File.WriteAllText(PathName, JsonSerializer.Serialize(new Settings(1, true)), new System.Text.UTF8Encoding(true));
        var result = store.LoadResult(() => new(1, false), value => value.Version == 1);
        Assert.Equal(SettingsSource.Primary, result.Source);
        Assert.True(result.Value.Enabled);
        Assert.Throws<ArgumentOutOfRangeException>(() => new JsonSettingsStore<Settings>(PathName) { MaximumBytes = 0 });
    }

    [Fact]
    public void RecoveryReadsTheBackupSuffixTheWriterUsed()
    {
        void WritePrevious(string value) => RecoverableFile.Write(PathName,
            stream => stream.Write(System.Text.Encoding.UTF8.GetBytes(value)), path => Read(path),
            error => error is IOException or FormatException, backupSuffix: ".previous");
        WritePrevious("1"); WritePrevious("2");
        File.WriteAllText(PathName, "broken");
        var result = RecoverableFile.Read(PathName, Read, error => error is IOException or FormatException, ".previous");
        Assert.Equal(1, result.Value);
        Assert.Equal(FileGeneration.Backup, result.Generation);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void WriteRenamesAKeptPrimaryToTheBackupInsteadOfCopyingIt(bool trusted)
    {
        Write("1");
        // A handle that shares delete still names the file it opened after a rename, so a byte
        // written through it shows up in the backup only when the backup is that same file.
        using (var held = new FileStream(PathName, FileMode.Open, FileAccess.ReadWrite,
                   FileShare.ReadWrite | FileShare.Delete))
        {
            RecoverableFile.Write(PathName, stream => stream.Write("2"u8), path => ReadShared(path),
                trustExistingPrimary: trusted);
            held.Position = 0;
            held.Write("7"u8);
        }
        Assert.Equal("2", File.ReadAllText(PathName));
        Assert.Equal("7", File.ReadAllText(PathName + ".bak"));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void WriteReplacesADamagedPrimaryThatAReaderHoldsOpen()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "broken");
        using (var held = new FileStream(PathName, FileMode.Open, FileAccess.Read,
                   FileShare.ReadWrite | FileShare.Delete))
        {
            Write("3");
            Assert.Equal("broken", new StreamReader(held).ReadToEnd());
        }
        Assert.Equal("3", File.ReadAllText(PathName));
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
    }

    private static int ReadShared(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        return int.Parse(new StreamReader(stream).ReadToEnd());
    }

    [Fact]
    public void WriteOverAPrimaryOpenWithoutDeleteSharingFailsAndChangesNothing()
    {
        Assert.SkipUnless(OperatingSystem.IsWindows(), "Only Windows enforces share modes on rename.");
        Write("1"); Write("2");
        using (new FileStream(PathName, FileMode.Open, FileAccess.Read, FileShare.Read))
            Assert.ThrowsAny<IOException>(() => Write("3"));
        Assert.Equal("2", File.ReadAllText(PathName));
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void AMissingBackupReportsThePrimaryFailureWhateverThePredicateAdmits()
    {
        File.WriteAllText(PathName, "broken");
        var unreadable = Assert.Throws<FileGenerationsUnreadableException>(() =>
            RecoverableFile.Read(PathName, Read, error => error is FormatException));
        Assert.IsType<FormatException>(unreadable.PrimaryFailure);
        Assert.Equal(PathName + ".bak",
            Assert.IsType<FileNotFoundException>(unreadable.BackupFailure).FileName);
        Assert.Equal([unreadable.PrimaryFailure, unreadable.BackupFailure], unreadable.InnerExceptions);
    }

    [Fact]
    public void ReadAndRepairRestoresTheBackupAndKeepsTheRejectedPrimary()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "broken");
        var result = RecoverableFile.ReadAndRepair(PathName, Read, error => error is IOException or FormatException);
        Assert.Equal(1, result.Value);
        Assert.Equal(FileGeneration.Backup, result.Generation);
        Assert.IsType<FormatException>(result.PrimaryFailure);
        Assert.True(result.PrimaryRepaired);
        Assert.Null(result.RepairFailure);
        Assert.Equal("1", File.ReadAllText(PathName));
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Equal("broken", File.ReadAllText(PathName + RecoverableFile.DefaultRejectedSuffix));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));

        var again = RecoverableFile.ReadAndRepair(PathName, Read, error => error is IOException or FormatException);
        Assert.Equal(FileGeneration.Primary, again.Generation);
        Assert.False(again.PrimaryRepaired);
    }

    [Fact]
    public void ReadAndRepairRestoresAMissingPrimaryWithoutARejectedFile()
    {
        Write("1"); Write("2");
        File.Delete(PathName);
        var result = RecoverableFile.ReadAndRepair(PathName, Read, backupSuffix: ".bak", rejectedSuffix: ".rejected");
        Assert.True(result.PrimaryRepaired);
        Assert.IsType<FileNotFoundException>(result.PrimaryFailure);
        Assert.Equal("1", File.ReadAllText(PathName));
        Assert.False(File.Exists(PathName + ".rejected"));
    }

    [Fact]
    public void AFailedRepairStillReturnsTheBackupAndLeavesThePrimary()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "broken");
        // A directory where the rejected primary would go makes the promotion fail on every system.
        Directory.CreateDirectory(PathName + RecoverableFile.DefaultRejectedSuffix);
        var result = RecoverableFile.ReadAndRepair(PathName, Read, error => error is IOException or FormatException);
        Assert.Equal(1, result.Value);
        Assert.False(result.PrimaryRepaired);
        Assert.NotNull(result.RepairFailure);
        Assert.Equal("broken", File.ReadAllText(PathName));
        Assert.Equal("1", File.ReadAllText(PathName + ".bak"));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void ReadAndRepairLeavesAPrimaryThePredicateDoesNotAdmit()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "newer-version");
        Assert.Throws<FormatException>(() => RecoverableFile.ReadAndRepair(PathName, Read));
        Assert.Equal("newer-version", File.ReadAllText(PathName));
        Assert.False(File.Exists(PathName + RecoverableFile.DefaultRejectedSuffix));
    }

    [Fact]
    public void RestoreValidatesTheCopyBeforePromotingIt()
    {
        Write("1"); Write("2");
        File.WriteAllText(PathName, "broken");
        File.WriteAllText(PathName + ".bak", "also broken");
        Assert.Throws<FormatException>(() => RecoverableFile.Restore(PathName, path => Read(path)));
        Assert.Equal("broken", File.ReadAllText(PathName));
        Assert.False(File.Exists(PathName + RecoverableFile.DefaultRejectedSuffix));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
        File.Delete(PathName + ".bak");
        Assert.Throws<FileNotFoundException>(() => RecoverableFile.Restore(PathName, path => Read(path)));
    }

    [Fact]
    public void RejectedSuffixMustNameItsOwnFile()
    {
        Write("1");
        Assert.Throws<ArgumentException>(() => RecoverableFile.ReadAndRepair(PathName, Read, rejectedSuffix: ".bak"));
        Assert.Throws<ArgumentException>(() => RecoverableFile.Restore(PathName, _ => { }, rejectedSuffix: "."));
    }

    private sealed record Settings(int Version, bool Enabled);
}
