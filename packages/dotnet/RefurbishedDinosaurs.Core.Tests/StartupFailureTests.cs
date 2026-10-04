using RefurbishedDinosaurs.Core.Diagnostics;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

// Report writes to the process-wide standard error, which a test swaps for a buffer, so these tests
// do not run alongside others.
[CollectionDefinition(nameof(StandardErrorCollection), DisableParallelization = true)]
public sealed class StandardErrorCollection;

[Collection(nameof(StandardErrorCollection))]
public sealed class StartupFailureTests
{
    [Fact]
    public void ReportWithoutDialogWritesLogAndStandardError()
    {
        var logDirectory = Path.Combine(Path.GetTempPath(), $"startup-failure-{Guid.NewGuid():N}");
        var originalError = Console.Error;
        using var error = new StringWriter();
        try
        {
            var options = new StartupFailureOptions("Example", logDirectory, "Reinstall.");
            Console.SetError(error);
            StartupFailure.Report(options, new InvalidDataException("pack missing"), "content",
                showDialog: false);

            var logPath = Path.Combine(logDirectory, "startup-error.log");
            var log = File.ReadAllText(logPath);
            Assert.Contains($"Original-resource folder: {Path.GetFullPath("content")}", log);
            Assert.Contains("pack missing", log);
            var printed = error.ToString();
            Assert.Contains("Example could not start.", printed);
            Assert.Contains($"Original-resource folder: {Path.GetFullPath("content")}", printed);
            Assert.Contains("Reinstall.", printed);
            Assert.Contains($"Technical details: {logPath}", printed);
            Assert.Contains(typeof(InvalidDataException).FullName!, printed);
        }
        finally
        {
            Console.SetError(originalError);
            if (Directory.Exists(logDirectory)) Directory.Delete(logDirectory, true);
        }
    }

    [Fact]
    public void ReportKeepsAContentRootThePathApiRejects()
    {
        var logDirectory = Path.Combine(Path.GetTempPath(), $"startup-failure-{Guid.NewGuid():N}");
        var originalError = Console.Error;
        using var error = new StringWriter();
        const string contentRoot = "con\0tent";
        try
        {
            var options = new StartupFailureOptions("Example", logDirectory, "Reinstall.");
            Console.SetError(error);
            StartupFailure.Report(options, new InvalidDataException("pack missing"), contentRoot,
                showDialog: false);

            var log = File.ReadAllText(Path.Combine(logDirectory, "startup-error.log"));
            Assert.Contains($"Original-resource folder: {contentRoot}", log);
            var printed = error.ToString();
            Assert.Contains($"Original-resource folder: {contentRoot}", printed);
            Assert.Contains("pack missing", printed);
        }
        finally
        {
            Console.SetError(originalError);
            if (Directory.Exists(logDirectory)) Directory.Delete(logDirectory, true);
        }
    }

    [Fact]
    public void ReportAndTryWriteLogRejectMissingArguments()
    {
        var logDirectory = Path.Combine(Path.GetTempPath(), $"startup-failure-{Guid.NewGuid():N}");
        var options = new StartupFailureOptions("Example", logDirectory, "Reinstall.");
        var exception = new InvalidDataException("pack missing");

        Assert.Throws<ArgumentNullException>("options",
            () => StartupFailure.Report(null!, exception, null));
        Assert.Throws<ArgumentNullException>("exception",
            () => StartupFailure.Report(options, null!, null, showDialog: false));
        Assert.Throws<ArgumentNullException>("options",
            () => StartupFailure.TryWriteLog(null!, exception, null));
        Assert.Throws<ArgumentNullException>("exception",
            () => StartupFailure.TryWriteLog(options, null!, null));
        Assert.False(Directory.Exists(logDirectory));
    }
}
