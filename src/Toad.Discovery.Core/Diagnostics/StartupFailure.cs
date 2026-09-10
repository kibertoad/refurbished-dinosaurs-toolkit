using System.Runtime.InteropServices;
using System.Text;

namespace Toad.Discovery.Core.Diagnostics;

public sealed record StartupFailureOptions(
    string ApplicationTitle,
    string LogDirectory,
    string RecoveryInstruction,
    string ContentLabel = "Original-resource folder");

public static class StartupFailure
{
    public static string BuildMessage(
        StartupFailureOptions options,
        Exception exception,
        string? contentRoot,
        string? logPath = null)
    {
        ArgumentNullException.ThrowIfNull(options);
        ArgumentNullException.ThrowIfNull(exception);
        var builder = new StringBuilder()
            .AppendLine($"{options.ApplicationTitle} could not start.")
            .AppendLine()
            .AppendLine(exception.Message);
        if (!string.IsNullOrWhiteSpace(contentRoot))
            builder.AppendLine().AppendLine($"{options.ContentLabel}: {Path.GetFullPath(contentRoot)}");
        builder.AppendLine().AppendLine(options.RecoveryInstruction);
        if (!string.IsNullOrWhiteSpace(logPath))
            builder.AppendLine().AppendLine($"Technical details: {logPath}");
        return builder.ToString().TrimEnd();
    }

    public static string? TryWriteLog(
        StartupFailureOptions options,
        Exception exception,
        string? contentRoot)
    {
        try
        {
            Directory.CreateDirectory(options.LogDirectory);
            var path = Path.Combine(options.LogDirectory, "startup-error.log");
            File.WriteAllText(path,
                $"{DateTimeOffset.UtcNow:O}{Environment.NewLine}" +
                $"{options.ContentLabel}: {contentRoot ?? "(not resolved)"}{Environment.NewLine}" +
                exception);
            return path;
        }
        catch
        {
            return null;
        }
    }

    public static void Report(StartupFailureOptions options, Exception exception, string? contentRoot)
    {
        var logPath = TryWriteLog(options, exception, contentRoot);
        var message = BuildMessage(options, exception, contentRoot, logPath);
        Console.Error.WriteLine(message);
        Console.Error.WriteLine(exception);
        if (OperatingSystem.IsWindows())
            _ = MessageBoxW(IntPtr.Zero, message, options.ApplicationTitle, 0x10);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);
}
