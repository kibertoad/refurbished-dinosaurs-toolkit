using RefurbishedDinosaurs.Core.Diagnostics;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

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
            Console.SetError(originalError);

            var logPath = Path.Combine(logDirectory, "startup-error.log");
            var log = File.ReadAllText(logPath);
            Assert.Contains("Original-resource folder: content", log);
            Assert.Contains("pack missing", log);
            var printed = error.ToString();
            Assert.Contains("Example could not start.", printed);
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
