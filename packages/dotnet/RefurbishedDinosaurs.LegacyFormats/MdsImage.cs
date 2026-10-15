using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.LegacyFormats;

// Reads an Alcohol 120% image: a version 1 .mds descriptor and the one .mdf it names. The layout is
// the one libmirage's image-mds parser reads: an 88-byte header, a 24-byte block per session, an
// 80-byte block per track or lead-in entry, an 8-byte extra block (pregap and length) per track and
// a 16-byte footer naming the data file. All values are little-endian.
internal static class MdsImage
{
    private const int HeaderSize = 88;
    private const int SessionSize = 24;
    private const int TrackSize = 80;

    public static DiscImage Read(string mdsPath)
    {
        var name = Path.GetFileName(mdsPath);
        var mds = DiscImageOpener.ReadDescriptor(mdsPath, "Alcohol .mds");
        var reader = new Reader(mds, name);
        if (mds.Length < HeaderSize || !mds.AsSpan(0, 16).SequenceEqual("MEDIA DESCRIPTOR"u8))
            throw new InvalidDataException($"{name} is not an Alcohol .mds descriptor: it does not begin with MEDIA DESCRIPTOR.");
        if (mds[16] != 1)
            throw new NotSupportedException(
                $"{name} is an .mds of version {mds[16]}.{mds[17]}; only version 1, which Alcohol 120% 1.x writes, is read.");
        var medium = reader.U16(18);
        if (medium is not (0x00 or 0x01 or 0x02))
            throw new NotSupportedException(
                $"{name} describes medium type 0x{medium:X2}, not a CD; DVD images are not read.");
        var sessionCount = reader.U16(20);
        if (sessionCount != 1)
            throw new NotSupportedException(
                $"{name} holds {sessionCount} sessions; only single-session discs are read, so a CD-Extra or multisession copy is not.");

        var session = checked((int)reader.U32(80));
        var sessionEnd = reader.I32(session + 4);
        var allBlocks = reader.U8(session + 10);
        var leadInBlocks = reader.U8(session + 11);
        var firstTrack = reader.U16(session + 12);
        var lastTrack = reader.U16(session + 14);
        var trackBlocks = checked((int)reader.U32(session + 20));
        if (reader.U16(session + 8) != 1 || firstTrack != 1 || lastTrack is < 1 or > 99 ||
            allBlocks - leadInBlocks != lastTrack)
            throw new InvalidDataException(
                $"{name}'s session block must be session 1 with tracks 1 to {lastTrack} and a block for each.");

        var tracks = new SortedDictionary<int, Track>();
        var leadIns = 0;
        string? fileName = null;
        for (var block = 0; block < allBlocks; block++)
        {
            var at = checked(trackBlocks + block * TrackSize);
            reader.Check(at, TrackSize);
            var point = reader.U8(at + 4);
            if (point >= 0xA0)
            {
                leadIns++;
                continue;
            }
            if (point < 1 || point > lastTrack || tracks.ContainsKey(point))
                throw new InvalidDataException($"{name} has a track block for track {point}, outside 1 to {lastTrack} or given twice.");
            var mode = reader.U8(at);
            // The low three bits give the mode, as libmirage reads them: 1 audio, 2 Mode 1,
            // 3 Mode 2, 4 Mode 2 Form 1, 5 Mode 2 Form 2, 6 Mode 2 mixed.
            var kind = mode & 0x07;
            if (kind is not (1 or 2 or 4))
                throw new NotSupportedException(
                    $"{name} gives track {point} mode 0x{mode:X2}; only audio, Mode 1 and Mode 2 Form 1 tracks are read.");
            var subchannel = reader.U8(at + 1);
            if (subchannel is not (0 or 8))
                throw new NotSupportedException($"{name} gives track {point} subchannel mode 0x{subchannel:X2}; only none (0) and 96-byte P-W (8) are read.");
            var sectorSize = reader.U16(at + 16);
            var raw = sectorSize == CueBinSheet.RawSectorSize + (subchannel == 8 ? 96 : 0);
            var cooked = sectorSize == 2048 && subchannel == 0 && kind != 1;
            if (!raw && !cooked)
                throw new NotSupportedException(
                    $"{name} gives track {point} {sectorSize}-byte sectors; only raw 2352-byte sectors, with or without " +
                    "96 bytes of subchannel, and 2048-byte data sectors are read.");
            var extra = checked((int)reader.U32(at + 12));
            if (extra == 0) throw new InvalidDataException($"{name} gives track {point} no extra block with its pregap and length.");
            if (reader.U32(at + 48) != 1)
                throw new NotSupportedException($"{name} stores track {point} in {reader.U32(at + 48)} files; only one .mdf is read.");
            var footer = checked((int)reader.U32(at + 52));
            var trackFile = FileName(reader, footer, name);
            if (fileName is not null && !fileName.Equals(trackFile, StringComparison.Ordinal))
                throw new NotSupportedException($"{name} stores its tracks in different files; only one .mdf is read.");
            fileName = trackFile;
            tracks.Add(point, new(point, kind, cooked, sectorSize, reader.I32(at + 36), checked((long)reader.U64(at + 40)),
                reader.U32(extra), reader.U32(extra + 4)));
        }
        if (leadIns != leadInBlocks || tracks.Count != lastTrack)
            throw new InvalidDataException($"{name} does not hold one block for each of its tracks and lead-in entries.");

        var directory = Path.GetDirectoryName(mdsPath)!;
        var mdfName = fileName!.StartsWith('*') ? Path.GetFileNameWithoutExtension(mdsPath) + fileName[1..] : fileName;
        if (mdfName.Contains('/') || mdfName.Contains('\\') || mdfName.Length == 0)
            throw new InvalidDataException($"{name} names its data file {AssetVerifier.JsonString(fileName)}, which is not a file name beside it.");
        var mdfPath = CueBinSheet.ResolveReference(directory, mdfName)
            ?? throw new FileNotFoundException($"{name} reads its sectors from {mdfName}, which does not exist.", Path.Combine(directory, mdfName));
        var mdf = ImageSnapshot.Take(mdfPath);

        var builder = new DiscLayoutBuilder();
        DiscDataTrack? data = null;
        long fileEnd = 0;
        Track? previous = null;
        foreach (var track in tracks.Values)
        {
            if (track.Number == 1 && (track.Kind == 1 || track.Start != 0))
                throw new NotSupportedException($"{name} must begin with a data track at sector 0; only such discs are read.");
            if (track.Number > 1 && track.Kind != 1)
                throw new NotSupportedException($"{name} gives track {track.Number} data; only track 1 may hold data.");
            if (track.Length == 0) throw new InvalidDataException($"{name} gives track {track.Number} no sectors.");
            var bytes = checked((long)track.Length * track.SectorSize);
            if (track.Offset < fileEnd || track.Offset + bytes > mdf.Length)
                throw new InvalidDataException(
                    $"{name} puts track {track.Number} at bytes {track.Offset} to {track.Offset + bytes} of {mdfName}, " +
                    "overlapping the track before it or past the end of the file.");
            fileEnd = track.Offset + bytes;

            var mode = track.Kind == 2 ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1;
            builder.Track(track.Number, track.Number == 1 ? DiscImageOpener.TypeOf(mode, track.Cooked) : "AUDIO");
            // The pregap of track 1 lies before LBA 0. Another track's pregap is either the end of the
            // track before it, or stored in no file when the track before ends where the pregap begins.
            if (previous is not null && track.Pregap > 0)
            {
                var pregapStart = (long)track.Start - track.Pregap;
                if (pregapStart <= previous.Start)
                    throw new InvalidDataException(
                        $"{name} gives track {track.Number} a {track.Pregap}-sector pregap that begins before track {previous.Number} does.");
                builder.Index(0, pregapStart);
                if (builder.Position == pregapStart)
                    builder.Unavailable(track.Pregap, $"{name} does not store track {track.Number:D2}'s pregap.");
            }
            if (builder.Position != track.Start)
                throw new InvalidDataException(
                    $"{name} starts track {track.Number} at sector {track.Start}, but the tracks before it end at sector {builder.Position}.");
            builder.Index(1, track.Start);
            if (track.Cooked)
            {
                builder.Unavailable(track.Length, DiscImageOpener.CookedReason(mdfName));
                data = new CookedDiscDataTrack(mdf, track.Offset, track.Length);
            }
            else builder.Stored(mdf, track.Offset, track.SectorSize, track.Length);
            previous = track;
        }
        if (sessionEnd != builder.Position)
            throw new InvalidDataException(
                $"{name} ends its session at sector {sessionEnd}, but its tracks end at sector {builder.Position}.");

        var first = tracks[1];
        data ??= new RawDiscDataTrack(first.Kind == 2 ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1,
            builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? builder.Position);
        return new(DiscImageFormats.AlcoholMds, [mdsPath, mdfPath], null, null, builder.Sheet(Path.GetFileName(mdfPath), []),
            builder.Segments, builder.Position, data, new Dictionary<int, string>());
    }

    // The name a footer gives, in single-byte characters or, when its second field is set, UTF-16LE.
    private static string FileName(Reader reader, int footer, string name)
    {
        var offset = checked((int)reader.U32(footer));
        var wide = reader.U32(footer + 4) != 0;
        var characters = new StringBuilder();
        for (var at = offset; ; at += wide ? 2 : 1)
        {
            int character = wide ? reader.U16(at) : reader.U8(at);
            if (character == 0) break;
            if (characters.Length >= 1024) throw new InvalidDataException($"{name}'s data file name is not terminated.");
            characters.Append((char)character);
        }
        return characters.ToString();
    }

    private sealed record Track(int Number, int Kind, bool Cooked, int SectorSize, int Start, long Offset, uint Pregap, uint Length);

    // Bounds-checked little-endian reads of the descriptor.
    private sealed class Reader(byte[] bytes, string name)
    {
        public void Check(int offset, int length)
        {
            if (offset < 0 || length > bytes.Length - offset)
                throw new InvalidDataException($"{name} is truncated: it ends before byte {(long)offset + length}.");
        }

        public byte U8(int offset) { Check(offset, 1); return bytes[offset]; }
        public ushort U16(int offset) { Check(offset, 2); return BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(offset)); }
        public uint U32(int offset) { Check(offset, 4); return BinaryPrimitives.ReadUInt32LittleEndian(bytes.AsSpan(offset)); }
        public int I32(int offset) { Check(offset, 4); return BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(offset)); }
        public ulong U64(int offset) { Check(offset, 8); return BinaryPrimitives.ReadUInt64LittleEndian(bytes.AsSpan(offset)); }
    }
}
