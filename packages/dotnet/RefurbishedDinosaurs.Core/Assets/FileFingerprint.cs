using System.Buffers;
using System.IO.Hashing;

namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>
/// XXH3-128 fingerprints, the hash the documentation standard uses for every file it names: the
/// 128-bit form <c>xxhsum -H2</c> prints, as 32 lower-case hex digits in canonical byte order.
/// </summary>
public static class FileFingerprint
{
    /// <summary>The length of a fingerprint in hex digits.</summary>
    public const int Xxh3Length = 32;

    private const int BufferSize = 1024 * 1024;

    /// <summary>Hashes <paramref name="data"/>.</summary>
    public static string Xxh3(ReadOnlySpan<byte> data) => Format(XxHash128.HashToUInt128(data));

    /// <summary>Hashes the file at <paramref name="path"/>.</summary>
    public static string Xxh3(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        using var stream = OpenSequential(path, FileOptions.SequentialScan);
        var hash = new XxHash128();
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            int read;
            while ((read = stream.Read(buffer, 0, BufferSize)) > 0) hash.Append(buffer.AsSpan(0, read));
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        return Format(hash.GetCurrentHashAsUInt128());
    }

    /// <summary>Hashes the file at <paramref name="path"/> asynchronously.</summary>
    /// <param name="path">The file to hash.</param>
    /// <param name="cancellationToken">Cancels the read.</param>
    public static async Task<string> Xxh3Async(string path, CancellationToken cancellationToken = default)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        await using var stream = OpenSequential(path, FileOptions.Asynchronous | FileOptions.SequentialScan);
        return await Xxh3Async(stream, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>Hashes <paramref name="stream"/> from its current position to its end, without disposing it.</summary>
    /// <param name="stream">A readable stream.</param>
    /// <param name="cancellationToken">Cancels the read.</param>
    public static async Task<string> Xxh3Async(Stream stream, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(stream);
        var hash = new XxHash128();
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            int read;
            while ((read = await stream.ReadAsync(buffer.AsMemory(0, BufferSize), cancellationToken)
                       .ConfigureAwait(false)) > 0)
                hash.Append(buffer.AsSpan(0, read));
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        return Format(hash.GetCurrentHashAsUInt128());
    }

    /// <summary>
    /// Copies <paramref name="input"/> from its current position to <paramref name="output"/> and hashes
    /// what it copies, without disposing or flushing either stream. It stops before writing a read
    /// that would take the copy past <paramref name="maximumBytes"/>, and then reports
    /// <see cref="FingerprintedCopy.Exceeded"/>.
    /// </summary>
    /// <param name="input">A readable stream.</param>
    /// <param name="output">A writable stream.</param>
    /// <param name="maximumBytes">The most bytes to copy.</param>
    /// <param name="cancellationToken">Cancels the copy.</param>
    /// <returns>The bytes written, their fingerprint, and whether the input held more.</returns>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="maximumBytes"/> is negative.</exception>
    public static async Task<FingerprintedCopy> CopyXxh3Async(
        Stream input, Stream output, long maximumBytes, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(input);
        ArgumentNullException.ThrowIfNull(output);
        ArgumentOutOfRangeException.ThrowIfNegative(maximumBytes);
        var hash = new XxHash128();
        long total = 0;
        var exceeded = false;
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            int read;
            while ((read = await input.ReadAsync(buffer.AsMemory(0, BufferSize), cancellationToken)
                       .ConfigureAwait(false)) > 0)
            {
                if (read > maximumBytes - total)
                {
                    exceeded = true;
                    break;
                }
                total += read;
                hash.Append(buffer.AsSpan(0, read));
                await output.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            }
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        return new(total, Format(hash.GetCurrentHashAsUInt128()), exceeded);
    }

    /// <summary>Whether <paramref name="value"/> is a fingerprint: 32 lower-case hex digits.</summary>
    public static bool IsXxh3(string? value) =>
        value is { Length: Xxh3Length } && value.All(static c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f');

    // The canonical form is the 128-bit value written big-endian, which is how UInt128 formats.
    internal static string Format(UInt128 hash) => hash.ToString("x32");

    private static FileStream OpenSequential(string path, FileOptions options) =>
        new(path, FileMode.Open, FileAccess.Read, FileShare.Read, bufferSize: 0, options);
}

/// <summary>What <see cref="FileFingerprint.CopyXxh3Async"/> copied.</summary>
/// <param name="Bytes">The bytes written to the output.</param>
/// <param name="Xxh3">The XXH3-128 fingerprint of the bytes written.</param>
/// <param name="Exceeded">
/// Whether the input held more than the maximum. The copy then stopped early, so
/// <paramref name="Bytes"/> and <paramref name="Xxh3"/> cover only part of the input.
/// </param>
public readonly record struct FingerprintedCopy(long Bytes, string Xxh3, bool Exceeded);
