using System.Runtime.InteropServices;
using System.Text;

namespace RefurbishedDinosaurs.Core.Diagnostics;

/// <summary>Text and locations for reporting a failed start.</summary>
/// <param name="ApplicationTitle">The game's window title, used in the message and dialog caption.</param>
/// <param name="LogDirectory">Where <c>startup-error.log</c> is written.</param>
/// <param name="RecoveryInstruction">What the player should do, such as rerunning the importer.</param>
/// <param name="ContentLabel">How the message names the content directory.</param>
public sealed record StartupFailureOptions(
    string ApplicationTitle,
    string LogDirectory,
    string RecoveryInstruction,
    string ContentLabel = "Original-resource folder");

/// <summary>Reports an exception that stopped the game from starting, in terms a player can act on.</summary>
public static class StartupFailure
{
    /// <summary>
    /// Builds the player-facing message: the title, the exception message, the content directory if
    /// known, the recovery instruction, and the log path if one was written.
    /// </summary>
    /// <param name="options">Text and locations to use.</param>
    /// <param name="exception">The failure.</param>
    /// <param name="contentRoot">The content directory the game tried, or <see langword="null"/>.</param>
    /// <param name="logPath">The log written by <see cref="TryWriteLog"/>, or <see langword="null"/>.</param>
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

    /// <summary>
    /// Writes the time, content directory and full exception to <c>startup-error.log</c> in the log
    /// directory, and returns its path, or <see langword="null"/> when it cannot be written.
    /// </summary>
    /// <param name="options">Text and locations to use.</param>
    /// <param name="exception">The failure.</param>
    /// <param name="contentRoot">The content directory the game tried, or <see langword="null"/>.</param>
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

    /// <summary>
    /// Writes the log, prints the message and exception to standard error, and on Windows shows the
    /// message in an error dialog.
    /// </summary>
    /// <param name="options">Text and locations to use.</param>
    /// <param name="exception">The failure.</param>
    /// <param name="contentRoot">The content directory the game tried, or <see langword="null"/>.</param>
    public static void Report(StartupFailureOptions options, Exception exception, string? contentRoot) =>
        Report(options, exception, contentRoot, showDialog: true);

    /// <summary>
    /// Writes the log and prints the message and exception to standard error. When
    /// <paramref name="showDialog"/> is <see langword="true"/>, also shows the message in an error
    /// dialog on Windows.
    /// </summary>
    /// <param name="options">Text and locations to use.</param>
    /// <param name="exception">The failure.</param>
    /// <param name="contentRoot">The content directory the game tried, or <see langword="null"/>.</param>
    /// <param name="showDialog">
    /// <see langword="false"/> for an unattended run, such as a smoke test or CI, where a modal dialog
    /// nobody can dismiss would turn a failed start into a hang.
    /// </param>
    public static void Report(
        StartupFailureOptions options,
        Exception exception,
        string? contentRoot,
        bool showDialog)
    {
        ArgumentNullException.ThrowIfNull(options);
        ArgumentNullException.ThrowIfNull(exception);
        var logPath = TryWriteLog(options, exception, contentRoot);
        var message = BuildMessage(options, exception, contentRoot, logPath);
        Console.Error.WriteLine(message);
        Console.Error.WriteLine(exception);
        if (showDialog && OperatingSystem.IsWindows())
            _ = MessageBoxW(IntPtr.Zero, message, options.ApplicationTitle, 0x10);
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr window, string text, string caption, uint type);
}
