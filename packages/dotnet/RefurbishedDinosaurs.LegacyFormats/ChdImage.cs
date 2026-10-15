using System.Buffers.Binary;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace RefurbishedDinosaurs.LegacyFormats;

// The type a CHD track stores its sectors as, from its CHTR or CHT2 metadata.
internal enum ChdTrackType
{
    // MODE1_RAW or MODE1/2352: raw 2352-byte MODE1 sectors.
    Mode1Raw,
    // MODE2_RAW or MODE2/2352: raw 2352-byte MODE2 sectors.
    Mode2Raw,
    // AUDIO: 2352 bytes of 16-bit stereo samples, stored big-endian.
    Audio,
    // MODE1 or MODE1/2048: the 2048 bytes of user data alone, as chdman stores an .iso image.
    Mode1Cooked,
}

// One track of a CD CHD. Frames counts the sectors the CHD stores for it, a stored pregap included,
// and ChdFrame is where they start among the CHD's 2448-byte frames. chdman pads every track to a
// multiple of 4 frames, so a track's frames start after the padding of the tracks before it.
// UnstoredPregap counts the sectors of a pregap the CHD records but does not store, such as one a
// cue sheet declared with PREGAP.
internal sealed record ChdTrack(int Number, ChdTrackType Type, int Frames, int StoredPregap, long ChdFrame, int UnstoredPregap = 0);

// A MAME CHD version 5 CD image: its header, hunk map and track metadata, read and checked when it
// opens. The hunks are read through a ChdHunkReader, one per stream.
internal sealed partial class ChdImage
{
    public const int FrameSize = 2448;
    public const int SectorSize = 2352;
    public const int SubcodeSize = 96;
    private const int HeaderSize = 124;
    private const int TrackPadding = 4;
    private const int MaximumTracks = 99;
    private const int MaximumMetadataEntries = 1024;
    private const int MaximumHunkBytes = 1024 * FrameSize;
    // Far more frames than any CD holds, about 7 hours, which bounds what the map takes in memory.
    private const long MaximumFrames = 2_000_000;

    // Map entry types of a compressed v5 map, as MAME's chd.h numbers them.
    internal const byte CompressionNone = 4;
    internal const byte CompressionSelf = 5;
    internal const byte CompressionParent = 6;
    private const byte CompressionRleSmall = 7;
    private const byte CompressionRleLarge = 8;
    private const byte CompressionSelf0 = 9;
    private const byte CompressionSelf1 = 10;
    private const byte CompressionParentSelf = 11;
    private const byte CompressionParent0 = 12;
    private const byte CompressionParent1 = 13;

    internal const uint CodecCdLzma = 0x63646C7A; // cdlz
    internal const uint CodecCdZlib = 0x63647A6C; // cdzl
    internal const uint CodecCdFlac = 0x6364666C; // cdfl

    private const uint TagTrack = 0x43485452; // CHTR
    private const uint TagTrack2 = 0x43485432; // CHT2
    private const uint TagOldCd = 0x43484344; // CHCD
    private const uint TagGdRom = 0x43484744; // CHGD
    private const uint TagGdRomOld = 0x43484754; // CHGT
    private const uint TagDvd = 0x44564420; // "DVD "

    [GeneratedRegex(
        "^TRACK:(?<track>\\d+) TYPE:(?<type>\\S+) SUBTYPE:(?<subtype>\\S+) FRAMES:(?<frames>\\d+)" +
        "(?: PREGAP:(?<pregap>\\d+) PGTYPE:(?<pgtype>\\S+) PGSUB:(?<pgsub>\\S+) POSTGAP:(?<postgap>\\d+))?$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking)]
    private static partial Regex TrackMetadataPattern();

    private ChdImage(
        uint[] codecs, long logicalBytes, int hunkBytes, int hunkCount, byte[] rawSha1, MapEntry[] map,
        IReadOnlyList<ChdTrack> tracks)
    {
        Codecs = codecs;
        LogicalBytes = logicalBytes;
        HunkBytes = hunkBytes;
        HunkCount = hunkCount;
        RawSha1 = rawSha1;
        Map = map;
        Tracks = tracks;
    }

    public uint[] Codecs { get; }
    public long LogicalBytes { get; }
    public int HunkBytes { get; }
    public int HunkCount { get; }
    public byte[] RawSha1 { get; }
    public MapEntry[] Map { get; }
    public IReadOnlyList<ChdTrack> Tracks { get; }
    public int FramesPerHunk => HunkBytes / FrameSize;

    // Where a hunk's data is: its type, its compressed length, and its offset in the file (or, for
    // a self reference, the hunk it repeats), with the CRC-16 of its decompressed bytes.
    internal readonly record struct MapEntry(byte Type, int Length, long Offset, ushort Crc);

    // Whether the file at path starts with the CHD signature.
    public static bool HasSignature(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return HasSignature(stream);
    }

    // Whether the stream starts with the CHD signature.
    public static bool HasSignature(Stream stream)
    {
        Span<byte> tag = stackalloc byte[8];
        stream.Position = 0;
        return stream.ReadAtLeast(tag, 8, throwOnEndOfStream: false) == 8 && tag.SequenceEqual("MComprHD"u8);
    }

    // Reads and checks the header, metadata and map. Throws NotSupportedException for a valid CHD
    // this reader does not read, and InvalidDataException for one that is damaged.
    public static ChdImage Read(Stream file)
    {
        var fileLength = file.Length;
        if (fileLength < 16) throw new InvalidDataException("The file is too short to be a CHD.");
        var header = new byte[HeaderSize];
        file.Position = 0;
        var headerRead = file.ReadAtLeast(header, header.Length, throwOnEndOfStream: false);
        if (headerRead < 16 || !header.AsSpan(0, 8).SequenceEqual("MComprHD"u8))
            throw new InvalidDataException("The file does not start with the CHD signature \"MComprHD\".");
        var version = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(12));
        if (version is >= 1 and <= 4)
            throw new NotSupportedException(
                $"The CHD is version {version}; only version 5 is read. Run \"chdman copy\" on it to write a version 5 copy.");
        if (version != 5) throw new InvalidDataException($"The CHD declares unknown version {version}.");
        if (headerRead < HeaderSize || BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(8)) != HeaderSize)
            throw new InvalidDataException("The CHD version 5 header is truncated or declares another length.");

        var codecs = new uint[4];
        for (var slot = 0; slot < 4; slot++) codecs[slot] = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(16 + 4 * slot));
        var logicalBytes = BinaryPrimitives.ReadInt64BigEndian(header.AsSpan(32));
        var mapOffset = BinaryPrimitives.ReadInt64BigEndian(header.AsSpan(40));
        var metaOffset = BinaryPrimitives.ReadInt64BigEndian(header.AsSpan(48));
        var hunkBytes = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(56));
        var unitBytes = BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(60));
        var rawSha1 = header.AsSpan(64, 20).ToArray();
        if (header.AsSpan(104, 20).ContainsAnyExcept((byte)0))
            throw new NotSupportedException(
                "The CHD stores only its differences from a parent CHD, which this reader does not open. " +
                "Run \"chdman copy\" with the parent given as -ip to write a CHD that holds the whole disc.");

        // The track metadata says whether this is a CD at all, so it is read before the codecs.
        var tracks = ReadTracks(file, metaOffset, fileLength);
        if (unitBytes != FrameSize)
            throw new InvalidDataException($"The CD CHD's unit is {unitBytes} bytes, not the {FrameSize}-byte frame.");
        if (hunkBytes == 0 || hunkBytes % FrameSize != 0 || hunkBytes > MaximumHunkBytes)
            throw new InvalidDataException(
                $"The CD CHD's hunks are {hunkBytes} bytes, not a whole number of {FrameSize}-byte frames up to {MaximumHunkBytes} bytes.");
        var chdFrames = tracks.Count == 0 ? 0 : tracks[^1].ChdFrame + Padded(tracks[^1].Frames);
        if (logicalBytes != chdFrames * FrameSize)
            throw new InvalidDataException(
                $"The CHD holds {logicalBytes} bytes, but its tracks take {chdFrames} frames of {FrameSize} bytes.");
        var hunkCount = checked((int)((logicalBytes + hunkBytes - 1) / hunkBytes));

        var compressed = codecs[0] != 0;
        var seenEmpty = false;
        for (var slot = 0; slot < 4; slot++)
        {
            if (codecs[slot] == 0) { seenEmpty = true; continue; }
            if (seenEmpty) throw new InvalidDataException("The CHD names a codec after an empty codec slot.");
            if (codecs[slot] is not (CodecCdLzma or CodecCdZlib or CodecCdFlac))
                throw new NotSupportedException(
                    $"The CHD uses the codec {CodecName(codecs[slot])}, which this reader does not decode; it reads " +
                    "CD CHDs compressed with cdlz, cdzl and cdfl, the codecs chdman uses for CDs by default. Run " +
                    "\"chdman copy -c cdlz,cdzl,cdfl\" on it to write a copy this reader opens.");
        }

        var map = compressed
            ? ReadCompressedMap(file, mapOffset, fileLength, hunkCount, (int)hunkBytes)
            : ReadUncompressedMap(file, mapOffset, fileLength, hunkCount, (int)hunkBytes);
        for (var hunk = 0; hunk < hunkCount; hunk++)
        {
            var entry = map[hunk];
            switch (entry.Type)
            {
                case <= 3:
                    if (codecs[entry.Type] == 0)
                        throw new InvalidDataException($"CHD hunk {hunk} names codec slot {entry.Type}, which is empty.");
                    CheckInFile(hunk, entry.Offset, entry.Length, fileLength);
                    break;
                case CompressionNone:
                    // An uncompressed CHD maps a hunk of zeros to no bytes of the file.
                    if (entry.Length > 0) CheckInFile(hunk, entry.Offset, entry.Length, fileLength);
                    break;
                case CompressionSelf:
                    // chdman refers only to a hunk it wrote before, so a reference forward or to
                    // itself can only be damage, and following one could loop.
                    if (entry.Offset >= hunk)
                        throw new InvalidDataException($"CHD hunk {hunk} repeats hunk {entry.Offset}, which does not come before it.");
                    break;
                case CompressionParent:
                    throw new InvalidDataException(
                        $"CHD hunk {hunk} is stored in a parent CHD, but the header names no parent.");
                default:
                    throw new InvalidDataException($"CHD hunk {hunk} has the unknown map entry type {entry.Type}.");
            }
        }
        return new ChdImage(codecs, logicalBytes, (int)hunkBytes, hunkCount, rawSha1, map, tracks);
    }

    private static void CheckInFile(int hunk, long offset, long length, long fileLength)
    {
        if (offset < HeaderSize || length < 0 || offset > fileLength - length)
            throw new InvalidDataException(
                $"CHD hunk {hunk} lies at bytes {offset} to {offset + length}, past the end of the {fileLength}-byte file; the file is truncated or damaged.");
    }

    private static long Padded(int frames) => (frames + TrackPadding - 1) / TrackPadding * (long)TrackPadding;

    private static string CodecName(uint tag)
    {
        Span<byte> bytes = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(bytes, tag);
        return bytes.ContainsAnyExceptInRange((byte)0x20, (byte)0x7E)
            ? $"0x{tag:X8}" : $"'{Encoding.ASCII.GetString(bytes)}'";
    }

    // Walks the metadata chain and returns the tracks of the CHTR or CHT2 entries in chain order.
    private static List<ChdTrack> ReadTracks(Stream file, long offset, long fileLength)
    {
        var texts = new List<string>();
        var seen = new HashSet<long>();
        var oldCd = false;
        var header = new byte[16];
        while (offset != 0)
        {
            if (!seen.Add(offset) || seen.Count > MaximumMetadataEntries)
                throw new InvalidDataException("The CHD's metadata chain loops or is longer than the reader accepts.");
            if (offset < HeaderSize || offset > fileLength - header.Length)
                throw new InvalidDataException(
                    $"A CHD metadata entry lies at byte {offset}, outside the {fileLength}-byte file; the file is truncated or damaged.");
            file.Position = offset;
            file.ReadExactly(header);
            var tag = BinaryPrimitives.ReadUInt32BigEndian(header);
            var length = (header[5] << 16) | (header[6] << 8) | header[7];
            var next = BinaryPrimitives.ReadInt64BigEndian(header.AsSpan(8));
            if (length > fileLength - offset - header.Length)
                throw new InvalidDataException(
                    $"A CHD metadata entry at byte {offset} runs past the end of the file; the file is truncated or damaged.");
            switch (tag)
            {
                case TagGdRom or TagGdRomOld:
                    throw new NotSupportedException(
                        "The CHD holds a GD-ROM, a Dreamcast disc, not a CD; only CD images are read.");
                case TagDvd:
                    throw new NotSupportedException("The CHD holds a DVD, not a CD; only CD images are read.");
                case TagOldCd:
                    oldCd = true;
                    break;
                case TagTrack or TagTrack2:
                    var text = new byte[length];
                    file.ReadExactly(text);
                    texts.Add(Encoding.ASCII.GetString(text).TrimEnd('\0'));
                    break;
            }
            offset = next;
        }
        if (texts.Count == 0)
            throw oldCd
                ? new NotSupportedException(
                    "The CHD describes its tracks in the old binary CHCD metadata, which this reader does not read. " +
                    "Run \"chdman copy\" on it to write a copy with CHT2 track metadata.")
                : new NotSupportedException("The CHD holds no CD track metadata, so it is not a CD image.");
        if (texts.Count > MaximumTracks)
            throw new InvalidDataException($"The CHD describes {texts.Count} tracks; a CD holds at most {MaximumTracks}.");

        var tracks = new List<ChdTrack>(texts.Count);
        long chdFrame = 0;
        foreach (var text in texts)
        {
            var match = TrackMetadataPattern().Match(text);
            if (!match.Success)
                throw new InvalidDataException($"The CHD's track metadata {AssetVerifier.JsonString(Truncate(text))} cannot be read.");
            if (!int.TryParse(match.Groups["track"].Value, out var number) || number != tracks.Count + 1)
                throw new InvalidDataException(
                    $"The CHD's track metadata {AssetVerifier.JsonString(Truncate(text))} is not track {tracks.Count + 1}; tracks must be numbered from 1 in order.");
            if (!int.TryParse(match.Groups["frames"].Value, out var frames) || frames <= 0)
                throw new InvalidDataException($"CHD track {number} has an invalid frame count.");
            var typeName = match.Groups["type"].Value;
            var type = typeName switch
            {
                "MODE1_RAW" or "MODE1/2352" => ChdTrackType.Mode1Raw,
                "MODE2_RAW" or "MODE2/2352" => ChdTrackType.Mode2Raw,
                "AUDIO" => ChdTrackType.Audio,
                "MODE1" or "MODE1/2048" => ChdTrackType.Mode1Cooked,
                _ => throw new NotSupportedException(
                    $"CHD track {number} is of type {AssetVerifier.JsonString(Truncate(typeName))}; only MODE1_RAW, " +
                    "MODE2_RAW and AUDIO tracks, or a single MODE1 track of 2048-byte sectors, are read."),
            };
            // A pregap whose type starts with V is stored in the CHD as the track's first frames, as
            // chdman stores the INDEX 00 pregap of a cue sheet. Any other pregap was generated, such
            // as by a cue PREGAP command, and is not stored, so it is not in the image either.
            int storedPregap = 0, unstoredPregap = 0;
            if (match.Groups["pregap"].Success)
            {
                if (!int.TryParse(match.Groups["pregap"].Value, out var pregap) || pregap > MaximumFrames)
                    throw new InvalidDataException($"CHD track {number} has an invalid pregap.");
                if (!match.Groups["pgtype"].Value.StartsWith('V')) unstoredPregap = pregap;
                else if (pregap >= frames)
                    throw new InvalidDataException($"CHD track {number} has a stored pregap that does not fit in its frames.");
                else storedPregap = pregap;
                if (!int.TryParse(match.Groups["postgap"].Value, out var postgap) || postgap != 0)
                    throw new NotSupportedException(
                        $"CHD track {number} records a postgap, which no disc image form this library reads stores.");
            }
            tracks.Add(new ChdTrack(number, type, frames, storedPregap, chdFrame, unstoredPregap));
            chdFrame += Padded(frames);
            if (chdFrame > MaximumFrames)
                throw new InvalidDataException($"The CHD's tracks hold more than {MaximumFrames} frames, more than any CD.");
        }
        return tracks;
    }

    private static string Truncate(string value) => value.Length <= 80 ? value : value[..80] + "...";

    private static MapEntry[] ReadUncompressedMap(Stream file, long offset, long fileLength, int hunkCount, int hunkBytes)
    {
        var bytes = checked((long)hunkCount * 4);
        if (offset < HeaderSize || offset > fileLength - bytes)
            throw new InvalidDataException("The CHD's hunk map lies past the end of the file; the file is truncated or damaged.");
        var raw = new byte[bytes];
        file.Position = offset;
        file.ReadExactly(raw);
        var map = new MapEntry[hunkCount];
        for (var hunk = 0; hunk < hunkCount; hunk++)
        {
            var block = BinaryPrimitives.ReadUInt32BigEndian(raw.AsSpan(4 * hunk));
            // Block 0 is the header, so an entry of 0 marks a hunk of zeros the file does not store.
            map[hunk] = block == 0
                ? new MapEntry(CompressionNone, 0, 0, 0)
                : new MapEntry(CompressionNone, hunkBytes, (long)block * hunkBytes, 0);
        }
        return map;
    }

    private static MapEntry[] ReadCompressedMap(Stream file, long offset, long fileLength, int hunkCount, int hunkBytes)
    {
        if (offset < HeaderSize || offset > fileLength - 16)
            throw new InvalidDataException("The CHD's hunk map lies past the end of the file; the file is truncated or damaged.");
        var header = new byte[16];
        file.Position = offset;
        file.ReadExactly(header);
        var mapBytes = BinaryPrimitives.ReadUInt32BigEndian(header);
        var firstOffset = ((long)BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(4)) << 32) |
            BinaryPrimitives.ReadUInt32BigEndian(header.AsSpan(6));
        var mapCrc = BinaryPrimitives.ReadUInt16BigEndian(header.AsSpan(10));
        int lengthBits = header[12], selfBits = header[13], parentBits = header[14];
        if (mapBytes > fileLength - offset - 16)
            throw new InvalidDataException("The CHD's hunk map runs past the end of the file; the file is truncated or damaged.");
        if (lengthBits > 32 || selfBits > 32 || parentBits > 32)
            throw new InvalidDataException("The CHD's hunk map declares fields wider than 32 bits.");
        var compressed = new byte[mapBytes];
        file.ReadExactly(compressed);

        var bits = new ChdBitReader(compressed);
        var decoder = ChdHuffmanDecoder.ReadRleTree(ref bits, codes: 16, maximumBits: 8);
        var types = new byte[hunkCount];
        byte last = 0;
        for (var hunk = 0; hunk < hunkCount;)
        {
            var value = decoder.Decode(ref bits);
            var repeat = 1;
            if (value == CompressionRleSmall) repeat = 3 + decoder.Decode(ref bits);
            else if (value == CompressionRleLarge) repeat = 3 + 16 + (decoder.Decode(ref bits) << 4) + decoder.Decode(ref bits);
            else last = (byte)value;
            if (repeat > hunkCount - hunk) throw MapDamaged();
            types.AsSpan(hunk, repeat).Fill(last);
            hunk += repeat;
        }

        var map = new MapEntry[hunkCount];
        var raw = new byte[12];
        var crc = Crc16.Start;
        var current = firstOffset;
        long lastSelf = 0;
        long lastParent = 0;
        for (var hunk = 0; hunk < hunkCount; hunk++)
        {
            var type = types[hunk];
            long entryOffset = current;
            var length = 0;
            ushort entryCrc = 0;
            switch (type)
            {
                case <= 3:
                    length = (int)bits.Read(lengthBits);
                    current += length;
                    entryCrc = (ushort)bits.Read(16);
                    break;
                case CompressionNone:
                    length = hunkBytes;
                    current += length;
                    entryCrc = (ushort)bits.Read(16);
                    break;
                case CompressionSelf:
                    lastSelf = entryOffset = bits.Read(selfBits);
                    break;
                case CompressionParent:
                    lastParent = entryOffset = bits.Read(parentBits);
                    break;
                case CompressionSelf1 or CompressionSelf0:
                    if (type == CompressionSelf1) lastSelf++;
                    type = CompressionSelf;
                    entryOffset = lastSelf;
                    break;
                case CompressionParentSelf:
                    type = CompressionParent;
                    lastParent = entryOffset = (long)hunk * hunkBytes / FrameSize;
                    break;
                case CompressionParent1 or CompressionParent0:
                    if (type == CompressionParent1) lastParent += hunkBytes / FrameSize;
                    type = CompressionParent;
                    entryOffset = lastParent;
                    break;
                default:
                    throw new InvalidDataException($"CHD hunk {hunk} has the unknown map entry type {type}.");
            }
            if (bits.Overrun) throw MapDamaged();
            // The map's CRC covers each entry as MAME expands it: the type, the 24-bit length, the
            // 48-bit offset and the 16-bit CRC, all big-endian.
            raw[0] = type;
            raw[1] = (byte)(length >> 16); raw[2] = (byte)(length >> 8); raw[3] = (byte)length;
            for (var index = 0; index < 6; index++) raw[4 + index] = (byte)(entryOffset >> (40 - 8 * index));
            BinaryPrimitives.WriteUInt16BigEndian(raw.AsSpan(10), entryCrc);
            crc = Crc16.Append(crc, raw);
            map[hunk] = new MapEntry(type, length, entryOffset, entryCrc);
        }
        if (crc != mapCrc)
            throw new InvalidDataException("The CHD's hunk map fails its CRC-16 check; the file is damaged.");
        return map;
    }

    private static InvalidDataException MapDamaged() =>
        new("The CHD's hunk map cannot be decoded; the file is damaged.");
}

// Reads hunks of one CHD through one file stream, checking each hunk's CRC-16 and, when the hunks
// are read from the first to the last, the SHA-1 of the whole data against the header's.
internal sealed class ChdHunkReader : IDisposable
{
    private readonly ChdImage image;
    private readonly Stream file;
    private readonly byte[] compressed;
    private readonly byte[] scratch;
    private byte[]? padding;
    private readonly ChdLzmaDecoder lzma = new();
    private readonly FlacDecoder flac = new();
    private readonly bool[] content;
    private IncrementalHash? sha1 = IncrementalHash.CreateHash(HashAlgorithmName.SHA1);
    private int hashedHunks;
    // The SHA-1 mismatch, once found. The hash is not computed again, so every later read fails
    // with it instead of handing out data known to be damaged.
    private string? damaged;

    // content marks the hunks a caller reads for their frames. The others hold only padding, and
    // are read here only to carry the SHA-1 across them.
    public ChdHunkReader(ChdImage image, Stream file, bool[] content)
    {
        this.image = image;
        this.file = file;
        this.content = content;
        compressed = new byte[image.HunkBytes];
        scratch = new byte[image.HunkBytes];
    }

    // CD sectors whose sync pattern and ECC the codec rebuilt, for the tests.
    internal int RegeneratedSectors { get; private set; }

    public void ReadHunk(int hunk, Span<byte> destination)
    {
        if (damaged is not null) throw new InvalidDataException(damaged);
        // Hunks before this one that hold only padding are read for the SHA-1, so that reading
        // every content hunk in order checks the whole data.
        while (sha1 is not null && hashedHunks < hunk && !content[hashedHunks]) Hash(hashedHunks);
        Decode(hunk, destination);
        if (sha1 is not null && hunk == hashedHunks)
        {
            Append(destination);
            while (sha1 is not null && hashedHunks < image.HunkCount && !content[hashedHunks]) Hash(hashedHunks);
        }
    }

    private void Hash(int hunk)
    {
        padding ??= new byte[image.HunkBytes];
        Decode(hunk, padding);
        Append(padding);
    }

    private void Append(ReadOnlySpan<byte> hunkData)
    {
        var start = (long)hashedHunks * image.HunkBytes;
        var length = (int)Math.Min(image.HunkBytes, image.LogicalBytes - start);
        sha1!.AppendData(hunkData[..length]);
        hashedHunks++;
        if (hashedHunks < image.HunkCount) return;
        var actual = sha1.GetHashAndReset();
        sha1.Dispose();
        sha1 = null;
        if (actual.AsSpan().SequenceEqual(image.RawSha1)) return;
        damaged = $"The CHD's data has SHA-1 {Convert.ToHexStringLower(actual)}, but its header records " +
                  $"{Convert.ToHexStringLower(image.RawSha1)}; the file is damaged.";
        throw new InvalidDataException(damaged);
    }

    private void Decode(int hunk, Span<byte> destination)
    {
        // A self reference names an earlier hunk, which ChdImage.Read checked, so this ends.
        while (image.Map[hunk].Type == ChdImage.CompressionSelf) hunk = (int)image.Map[hunk].Offset;
        var entry = image.Map[hunk];
        switch (entry.Type)
        {
            case ChdImage.CompressionNone when entry.Length == 0:
                destination[..image.HunkBytes].Clear();
                return;
            case ChdImage.CompressionNone:
                ReadFile(entry.Offset, destination[..image.HunkBytes]);
                // An uncompressed CHD records no CRC for its hunks; the SHA-1 covers them.
                if (image.Codecs[0] != 0) CheckCrc(hunk, destination, entry.Crc);
                return;
        }
        if (entry.Length > compressed.Length)
            throw new InvalidDataException($"CHD hunk {hunk} is longer compressed than uncompressed; the file is damaged.");
        var source = compressed.AsSpan(0, entry.Length);
        ReadFile(entry.Offset, source);
        try
        {
            DecodeCd(image.Codecs[entry.Type], source, destination[..image.HunkBytes]);
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException($"CHD hunk {hunk} cannot be decompressed: {exception.Message}", exception);
        }
        CheckCrc(hunk, destination, entry.Crc);
    }

    private void CheckCrc(int hunk, ReadOnlySpan<byte> data, ushort expected)
    {
        var actual = Crc16.Append(Crc16.Start, data[..image.HunkBytes]);
        if (actual != expected)
            throw new InvalidDataException(
                $"CHD hunk {hunk} fails its CRC-16 check (expected {expected:x4}, found {actual:x4}); the file is damaged.");
    }

    private void ReadFile(long offset, Span<byte> destination)
    {
        file.Position = offset;
        if (file.ReadAtLeast(destination, destination.Length, throwOnEndOfStream: false) != destination.Length)
            throw new InvalidDataException("The CHD ended inside a hunk; the file is truncated.");
    }

    // The CD codecs store a hunk's 2352-byte sectors and its 96-byte subcode blocks apart. cdlz and
    // cdzl begin with a bitmap of the sectors whose sync pattern and ECC were removed, and the
    // length of the compressed sector data; cdfl codes the sectors as FLAC and needs neither.
    private void DecodeCd(uint codec, ReadOnlySpan<byte> source, Span<byte> destination)
    {
        var frames = destination.Length / ChdImage.FrameSize;
        var sectorBytes = frames * ChdImage.SectorSize;
        var sectors = scratch.AsSpan(0, sectorBytes);
        var subcode = scratch.AsSpan(sectorBytes, frames * ChdImage.SubcodeSize);
        var eccBytes = 0;
        if (codec == ChdImage.CodecCdFlac)
        {
            var consumed = flac.Decode(source, sectors);
            Inflate(source[consumed..], subcode, "subcode");
        }
        else
        {
            eccBytes = (frames + 7) / 8;
            var lengthBytes = destination.Length < 65536 ? 2 : 3;
            if (source.Length < eccBytes + lengthBytes) throw new InvalidDataException("the hunk is too short for its header.");
            var baseLength = (int)source[eccBytes] << 8 | source[eccBytes + 1];
            if (lengthBytes == 3) baseLength = baseLength << 8 | source[eccBytes + 2];
            var start = eccBytes + lengthBytes;
            if (baseLength > source.Length - start) throw new InvalidDataException("the sector data runs past the hunk.");
            var data = source.Slice(start, baseLength);
            if (codec == ChdImage.CodecCdLzma) lzma.Decode(data, sectors);
            else Inflate(data, sectors, "sector");
            Inflate(source[(start + baseLength)..], subcode, "subcode");
        }
        for (var frame = 0; frame < frames; frame++)
        {
            var target = destination.Slice(frame * ChdImage.FrameSize, ChdImage.FrameSize);
            sectors.Slice(frame * ChdImage.SectorSize, ChdImage.SectorSize).CopyTo(target);
            subcode.Slice(frame * ChdImage.SubcodeSize, ChdImage.SubcodeSize).CopyTo(target[ChdImage.SectorSize..]);
            if (eccBytes > 0 && (source[frame / 8] & (1 << (frame % 8))) != 0)
            {
                CdSectorEcc.SyncPattern.CopyTo(target);
                CdSectorEcc.Generate(target[..ChdImage.SectorSize]);
                RegeneratedSectors++;
            }
        }
    }

    // Raw deflate, as MAME's zlib codec writes it, which must fill destination exactly.
    private static void Inflate(ReadOnlySpan<byte> source, Span<byte> destination, string part)
    {
        using var inflater = new DeflateStream(new MemoryStream(source.ToArray(), writable: false), CompressionMode.Decompress);
        try
        {
            inflater.ReadExactly(destination);
            Span<byte> extra = stackalloc byte[1];
            if (inflater.Read(extra) != 0)
                throw new InvalidDataException($"the {part} data holds more than the hunk.");
        }
        catch (EndOfStreamException exception)
        {
            throw new InvalidDataException($"the {part} data holds less than the hunk.", exception);
        }
    }

    public void Dispose()
    {
        sha1?.Dispose();
        file.Dispose();
    }
}

// CRC-16/CCITT-FALSE, as MAME's crc16_creator computes it: polynomial 0x1021, starting at 0xFFFF.
internal static class Crc16
{
    public const ushort Start = 0xFFFF;
    private static readonly ushort[] Table = BuildTable();

    public static ushort Append(ushort crc, ReadOnlySpan<byte> data)
    {
        foreach (var value in data) crc = (ushort)((crc << 8) ^ Table[(crc >> 8) ^ value]);
        return crc;
    }

    private static ushort[] BuildTable()
    {
        var table = new ushort[256];
        for (var index = 0; index < 256; index++)
        {
            var value = (ushort)(index << 8);
            for (var bit = 0; bit < 8; bit++) value = (ushort)((value & 0x8000) != 0 ? (value << 1) ^ 0x1021 : value << 1);
            table[index] = value;
        }
        return table;
    }
}

// Reads bits from the most significant end of each byte, as MAME's bitstream_in does. Bits past the
// end read as zeros and set Overrun.
internal ref struct ChdBitReader(ReadOnlySpan<byte> data)
{
    private readonly ReadOnlySpan<byte> data = data;
    private long position;

    public readonly bool Overrun => position > (long)data.Length * 8;

    public readonly uint Peek(int count)
    {
        uint value = 0;
        for (var index = 0; index < count; index++)
        {
            var bit = position + index;
            var byteIndex = bit >> 3;
            var set = byteIndex < data.Length && (data[(int)byteIndex] & (0x80 >> (int)(bit & 7))) != 0;
            value = (value << 1) | (set ? 1u : 0u);
        }
        return value;
    }

    public void Skip(int count) => position += count;

    public uint Read(int count)
    {
        var value = Peek(count);
        position += count;
        return value;
    }
}

// The canonical Huffman decoder of MAME's huffman.cpp, for the map's compression types.
internal sealed class ChdHuffmanDecoder
{
    private readonly int maximumBits;
    // For each maximumBits-bit prefix, the code it starts with and its length, or -1 for none.
    private readonly (int Code, int Bits)[] lookup;

    private ChdHuffmanDecoder(int maximumBits, (int, int)[] lookup)
    {
        this.maximumBits = maximumBits;
        this.lookup = lookup;
    }

    public static ChdHuffmanDecoder ReadRleTree(ref ChdBitReader bits, int codes, int maximumBits)
    {
        var fieldBits = maximumBits >= 16 ? 5 : maximumBits >= 8 ? 4 : 3;
        var lengths = new int[codes];
        for (var code = 0; code < codes;)
        {
            var length = (int)bits.Read(fieldBits);
            if (length != 1)
            {
                lengths[code++] = length;
                continue;
            }
            length = (int)bits.Read(fieldBits);
            if (length == 1)
            {
                lengths[code++] = 1;
                continue;
            }
            var repeat = (int)bits.Read(fieldBits) + 3;
            if (repeat > codes - code) throw Damaged();
            lengths.AsSpan(code, repeat).Fill(length);
            code += repeat;
        }
        if (bits.Overrun) throw Damaged();

        // MAME's canonical codes: the longest codes take the lowest values.
        var histogram = new uint[33];
        foreach (var length in lengths)
        {
            if (length > maximumBits) throw Damaged();
            histogram[length]++;
        }
        uint start = 0;
        for (var length = 32; length > 0; length--)
        {
            var next = (start + histogram[length]) >> 1;
            if (length != 1 && next * 2 != start + histogram[length]) throw Damaged();
            histogram[length] = start;
            start = next;
        }
        var lookup = new (int, int)[1 << maximumBits];
        lookup.AsSpan().Fill((-1, 0));
        for (var code = 0; code < codes; code++)
        {
            var length = lengths[code];
            if (length == 0) continue;
            var value = (int)histogram[length]++;
            var shift = maximumBits - length;
            var first = value << shift;
            var last = ((value + 1) << shift) - 1;
            if (last >= lookup.Length) throw Damaged();
            lookup.AsSpan(first, last - first + 1).Fill((code, length));
        }
        return new ChdHuffmanDecoder(maximumBits, lookup);
    }

    public int Decode(ref ChdBitReader bits)
    {
        var (code, length) = lookup[bits.Peek(maximumBits)];
        if (code < 0) throw Damaged();
        bits.Skip(length);
        if (bits.Overrun) throw Damaged();
        return code;
    }

    private static InvalidDataException Damaged() =>
        new("The CHD's hunk map cannot be decoded; the file is damaged.");
}

// The P and Q parity of a CD-ROM sector (ECMA-130 annex A), which chdman's CD codecs remove from a
// sector when they can rebuild it, and rebuild when the hunk is read.
internal static class CdSectorEcc
{
    private const int POffset = 2076;
    private const int QOffset = 2248;
    private static readonly byte[] Forward = new byte[256];
    private static readonly byte[] Backward = new byte[256];

    public static ReadOnlySpan<byte> SyncPattern => [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00];

    static CdSectorEcc()
    {
        for (var index = 0; index < 256; index++)
        {
            var value = (index << 1) ^ ((index & 0x80) != 0 ? 0x11D : 0);
            Forward[index] = (byte)value;
            Backward[index ^ value] = (byte)index;
        }
    }

    // Writes the 172 P bytes and then the 104 Q bytes of a 2352-byte sector from its header and
    // data. A MODE2 sector's ECC is computed with its 4 header bytes taken as zero.
    public static void Generate(Span<byte> sector)
    {
        var mode2 = sector[15] == 2;
        for (var major = 0; major < 86; major++)
        {
            byte a = 0, b = 0;
            for (var minor = 0; minor < 24; minor++)
            {
                var value = Source(sector, major + 86 * minor, mode2);
                a ^= value;
                b ^= value;
                a = Forward[a];
            }
            a = Backward[Forward[a] ^ b];
            sector[POffset + major] = a;
            sector[POffset + 86 + major] = (byte)(a ^ b);
        }
        for (var major = 0; major < 52; major++)
        {
            byte a = 0, b = 0;
            for (var minor = 0; minor < 43; minor++)
            {
                var word = ((major >> 1) * 43 + minor * 44) % 1118;
                var value = Source(sector, 2 * word + (major & 1), mode2);
                a ^= value;
                b ^= value;
                a = Forward[a];
            }
            a = Backward[Forward[a] ^ b];
            sector[QOffset + major] = a;
            sector[QOffset + 52 + major] = (byte)(a ^ b);
        }
    }

    private static byte Source(ReadOnlySpan<byte> sector, int offset, bool mode2) =>
        mode2 && offset < 4 ? (byte)0 : sector[12 + offset];
}
