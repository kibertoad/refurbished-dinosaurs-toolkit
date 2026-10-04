using System.IO.Compression;
using System.Text.Json;
using RefurbishedDinosaurs.Core.Assets;

namespace RefurbishedDinosaurs.Core.Tests;

// Synthetic overlay manifests, payloads and zip archives shared by the overlay tests.
internal static class OverlayFixtures
{
    public static readonly byte[] Base = "base bytes"u8.ToArray();
    public static readonly byte[] Patched = "patched bytes, longer"u8.ToArray();
    public static readonly byte[] Added = "added"u8.ToArray();

    public static object Record(string path, byte[] payload, string? baseXxh3, string? xxh3 = null, long? bytes = null) => new
    {
        path,
        bytes = bytes ?? payload.Length,
        baseXxh3,
        xxh3 = xxh3 ?? FileFingerprint.Xxh3(payload)
    };

    public static object Manifest(params object[] files) => new
    {
        formatVersion = 1,
        name = "synthetic-overlay-1",
        gameId = "synthetic-game",
        fromVersion = "1.0",
        toVersion = "1.1",
        files
    };

    // Replaces DATA/MAIN.BIN (Base) with Patched and adds extra/new/added.dat.
    public static object StandardManifest(string? patchedXxh3 = null) => Manifest(
        Record("data/main.bin", Patched, FileFingerprint.Xxh3(Base), patchedXxh3),
        Record("extra/new/added.dat", Added, null));

    public static (string Path, byte[] Payload)[] StandardPayloads =>
        [("data/main.bin", Patched), ("extra/new/added.dat", Added)];

    public static byte[] ZipBytes(object manifest, params (string Path, byte[] Payload)[] payloads)
    {
        using var buffer = new MemoryStream();
        using (var archive = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            using (var stream = archive.CreateEntry(ContentOverlay.ManifestFileName).Open())
                JsonSerializer.Serialize(stream, manifest);
            foreach (var (name, payload) in payloads)
            {
                using var stream = archive.CreateEntry($"{ContentOverlay.PayloadDirectory}/{name}").Open();
                stream.Write(payload);
            }
        }
        return buffer.ToArray();
    }
}
