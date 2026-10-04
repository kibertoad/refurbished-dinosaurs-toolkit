using RefurbishedDinosaurs.Core.Diagnostics;
using Xunit;

namespace RefurbishedDinosaurs.Core.Tests;

public sealed class StartupFailureTests
{
    [Fact]
    public void ReportWithoutDialogWritesLog()
    {
        var logDirectory = Path.Combine(Path.GetTempPath(), $"startup-failure-{Guid.NewGuid():N}");
        try
        {
            var options = new StartupFailureOptions("Example", logDirectory, "Reinstall.");
            StartupFailure.Report(options, new InvalidDataException("pack missing"), "content",
                showDialog: false);
            var log = File.ReadAllText(Path.Combine(logDirectory, "startup-error.log"));
            Assert.Contains("Original-resource folder: content", log);
            Assert.Contains("pack missing", log);
        }
        finally
        {
            if (Directory.Exists(logDirectory)) Directory.Delete(logDirectory, true);
        }
    }
}
