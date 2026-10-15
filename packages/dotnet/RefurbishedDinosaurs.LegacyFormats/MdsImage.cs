using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.LegacyFormats;

// Reads an Alcohol 120% image: a version 1 .mds descriptor and the one .mdf it names. The structures
// are the ones in libmirage's image-mds/image-mds.h (cdemu) and Aaru's Alcohol120/Structs.cs: an
// 88-byte header (version at 16, medium type at 18, session count at 20, session blocks' offset at
// 80), a 24-byte block per session (end at 4, number at 8, block counts at 10 and 11, first and last
// track at 12 and 14, track blocks' offset at 20), an 80-byte block per track or lead-in entry (mode
// at 0, subchannel at 1, point at 4, extra block offset at 12, sector size at 16, INDEX 01 LBA at 36,
// .mdf offset at 40, file count at 48, footer offset at 52), an 8-byte extra block (pregap and
// length) per track and a 16-byte footer naming the data file. All values are little-endian.
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
        // MDS_Medium in image-mds.h: 0x00 CD-ROM, 0x01 CD-R, 0x02 CD-RW, 0x10 DVD-ROM, 0x12 DVD-R.
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
            // libmirage's mirage_parser_mds_convert_track_mode (image-mds/parser.c) reads only the low
            // nibble and treats a value and that value plus 8 alike, so the low three bits give the
            // mode: 1 audio, 2 Mode 1, 3 Mode 2, 4 Mode 2 Form 1, 5 Mode 2 Form 2 (0 and 7 Mode 2, 6
            // unknown). Aaru's Alcohol120/Enums.cs lists the bytes Alcohol writes: A9/E9 audio, AA/EA
            // Mode 1, AB Mode 2 formless, AC/EC Mode 2 Form 1, AD/ED Mode 2 Form 2. Aaru's writer and the
            // archiver type a CD-XA data track EC. libmirage and Aaru read AB as formless 2336-byte
            // sectors; this reader reads a raw AB track, like a raw EC track and a cue sheet's
            // MODE2/2352, as CD-XA Form 1: a raw track stores all 2352 bytes either way, and each sector
            // is checked as it is read (sync, mode byte, two equal subheader copies, Form 1 or empty
            // Form 2), so a sector that is not CD-XA fails. A 2048-byte track stores user data only, so
            // there AB is refused and EC read.
            var kind = mode & 0x07;
            if (kind is not (1 or 2 or 3 or 4))
                throw new NotSupportedException(
                    $"{name} gives track {point} mode 0x{mode:X2}; only audio, Mode 1 and Mode 2 (Form 1) tracks are read.");
            var subchannel = reader.U8(at + 1);
            if (subchannel is not (0 or 8))
                throw new NotSupportedException($"{name} gives track {point} subchannel mode 0x{subchannel:X2}; only none (0) and 96-byte P-W (8) are read.");
            var sectorSize = reader.U16(at + 16);
            var raw = sectorSize == CueBinSheet.RawSectorSize + (subchannel == 8 ? 96 : 0);
            // A 2048-byte sector holds Mode 1 or Mode 2 Form 1 user data (Aaru's
            // TrackModeToCookedBytesPerSector, Alcohol120/Helpers.cs).
            var cooked = sectorSize == 2048 && subchannel == 0 && kind is 2 or 4;
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

        // A track block gives the LBA and .mdf offset of the track's INDEX 01 and its extra block the
        // pregap and length. The references disagree on where a pregap is: libmirage's parser.c
        // ("MDS format doesn't seem to store pregap data in its data file") adds no stored sectors,
        // while Aaru's Alcohol120/Read.cs reads the pregap from the bytes before the track's offset and
        // its Write.cs stores it there. Each layout is read from the numbers it gives: a track's length
        // either ends where the next track's pregap begins or runs through it to the next INDEX 01, and
        // the pregap is stored when exactly its sectors lie between the two tracks' bytes in the .mdf
        // and not stored when no bytes do. Any other layout is refused.
        var list = tracks.Values.ToList();
        var lengths = list.Select(track => (long)track.Length).ToArray();
        var stored = new bool[list.Count];
        for (var at = 1; at < list.Count; at++)
        {
            var (previous, track) = (list[at - 1], list[at]);
            if ((long)track.Start == previous.Start + previous.Length + track.Pregap) { }
            else if (track.Pregap > 0 && (long)track.Start == previous.Start + previous.Length)
                lengths[at - 1] = previous.Length - track.Pregap;
            else
                throw new InvalidDataException(
                    $"{name} starts track {track.Number} at sector {track.Start}, which does not follow track {previous.Number} " +
                    $"(sector {previous.Start}, {previous.Length} sectors) and its own {track.Pregap}-sector pregap.");
            if (lengths[at - 1] <= 0)
                throw new InvalidDataException($"{name} leaves track {previous.Number} no sectors before track {track.Number}'s pregap.");
            var previousEnd = previous.Offset + lengths[at - 1] * previous.SectorSize;
            if (track.Offset == previousEnd + (long)track.Pregap * track.SectorSize) stored[at] = track.Pregap > 0;
            else if (track.Pregap == 0 || track.Offset != previousEnd)
                throw new InvalidDataException(
                    $"{name} puts track {track.Number}'s sectors at byte {track.Offset} of {mdfName}, which follows neither " +
                    $"track {previous.Number}'s sectors nor its own {track.Pregap}-sector pregap stored after them.");
        }

        var builder = new DiscLayoutBuilder();
        DiscDataTrack? data = null;
        for (var at = 0; at < list.Count; at++)
        {
            var track = list[at];
            if (track.Number == 1 && (track.Kind == 1 || track.Start != 0))
                throw new NotSupportedException($"{name} must begin with a data track at sector 0; only such discs are read.");
            if (track.Number > 1 && track.Kind != 1)
                throw new NotSupportedException($"{name} gives track {track.Number} data; only track 1 may hold data.");
            if (lengths[at] <= 0) throw new InvalidDataException($"{name} gives track {track.Number} no sectors.");
            var bytes = checked(lengths[at] * track.SectorSize);
            // Compared without adding, so an offset near the 64-bit limit cannot wrap past the check.
            if (track.Offset > mdf.Length - bytes)
                throw new InvalidDataException(
                    $"{name} puts track {track.Number}'s {bytes} bytes at byte {track.Offset} of {mdfName}, past the end of the file.");

            var mode = track.Kind == 2 ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1;
            builder.Track(track.Number, track.Number == 1 ? DiscImageOpener.TypeOf(mode, track.Cooked) : "AUDIO");
            // Track 1's pregap is the 150 sectors before LBA 0, which no image form here stores.
            if (at > 0 && track.Pregap > 0)
            {
                builder.Index(0, (long)track.Start - track.Pregap);
                if (stored[at]) builder.Stored(mdf, track.Offset - (long)track.Pregap * track.SectorSize, track.SectorSize, track.Pregap);
                else builder.Unavailable(track.Pregap, $"{name} does not store track {track.Number:D2}'s pregap.");
            }
            builder.Index(1, track.Start);
            if (track.Cooked)
            {
                builder.Unavailable(lengths[at], DiscImageOpener.CookedReason(mdfName));
                data = new CookedDiscDataTrack(mdf, track.Offset, lengths[at]);
            }
            else builder.Stored(mdf, track.Offset, track.SectorSize, lengths[at]);
        }
        if (sessionEnd != builder.Position)
            throw new InvalidDataException(
                $"{name} ends its session at sector {sessionEnd}, but its tracks end at sector {builder.Position}.");

        var first = tracks[1];
        data ??= new RawDiscDataTrack(first.Kind == 2 ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1,
            builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? builder.Position, $"the Alcohol .mds {name}");
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
