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

    /// <summary>Whether <paramref name="value"/> is a fingerprint: 32 lower-case hex digits.</summary>
    public static bool IsXxh3(string? value) =>
        value is { Length: Xxh3Length } && value.All(static c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f');

    // The canonical form is the 128-bit value written big-endian, which is how UInt128 formats.
    internal static string Format(UInt128 hash) => hash.ToString("x32");

    private static FileStream OpenSequential(string path, FileOptions options) =>
        new(path, FileMode.Open, FileAccess.Read, FileShare.Read, bufferSize: 0, options);
}
