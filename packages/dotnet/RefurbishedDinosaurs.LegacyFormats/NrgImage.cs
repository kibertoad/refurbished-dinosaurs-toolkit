using System.Buffers.Binary;
using System.Text;

namespace RefurbishedDinosaurs.LegacyFormats;

// Reads a Nero image: one .nrg holding the tracks' sectors, then a list of chunks, then a footer
// giving the list's offset. The layout is the one libmirage's image-nrg parser reads. Version 2 ends
// in "NER5" and a 64-bit offset and uses the CUEX and DAOX chunks; version 1 ends in "NERO" and a
// 32-bit offset and uses CUES and DAOI. All values are big-endian. Only one session of disc-at-once
// tracks is read.
internal static class NrgImage
{
    private const int MaximumChunkBytes = 16 * 1024 * 1024;
    private const int DaoHeaderSize = 22;
    // Chunks that carry nothing the sector layout depends on.
    private static readonly string[] Ignored = ["CDTX", "MTYP", "DINF", "TOCT", "RELO", "AFNM"];

    public static DiscImage Read(string nrgPath)
    {
        var name = Path.GetFileName(nrgPath);
        var file = ImageSnapshot.Take(nrgPath);
        byte[] list;
        bool version2;
        long listOffset;
        using (var stream = file.OpenBuffered())
        {
            var footer = new byte[12];
            if (file.Length < 12) throw NotNrg(name);
            stream.Position = file.Length - 12;
            stream.ReadExactly(footer);
            if (footer.AsSpan(0, 4).SequenceEqual("NER5"u8))
            {
                version2 = true;
                listOffset = checked((long)BinaryPrimitives.ReadUInt64BigEndian(footer.AsSpan(4)));
            }
            else if (footer.AsSpan(4, 4).SequenceEqual("NERO"u8))
            {
                version2 = false;
                listOffset = BinaryPrimitives.ReadUInt32BigEndian(footer.AsSpan(8));
            }
            else throw NotNrg(name);
            var listEnd = file.Length - (version2 ? 12 : 8);
            if (listOffset < 0 || listOffset >= listEnd || listEnd - listOffset > MaximumChunkBytes)
                throw new InvalidDataException($"{name}'s footer puts its chunk list at byte {listOffset}, outside the file.");
            list = new byte[listEnd - listOffset];
            stream.Position = listOffset;
            stream.ReadExactly(list);
        }

        var chunks = Chunks(list, name);
        var (cueId, daoId) = version2 ? ("CUEX", "DAOX") : ("CUES", "DAOI");
        foreach (var (id, _) in chunks)
        {
            if (id is "ETNF" or "ETN2")
                throw new NotSupportedException($"{name} holds track-at-once tracks ({id}); only disc-at-once images are read.");
            if (id is "CUEX" or "CUES" or "DAOX" or "DAOI" && id != cueId && id != daoId)
                throw new InvalidDataException($"{name} is a version {(version2 ? 2 : 1)} image but holds a {id} chunk.");
            if (id is not ("CUEX" or "CUES" or "DAOX" or "DAOI" or "SINF") && !Ignored.Contains(id))
                throw new InvalidDataException($"{name} holds a {AssetVerifier.JsonString(id)} chunk, which is not read.");
        }
        var cues = chunks.Where(chunk => chunk.Id == cueId).ToArray();
        var daos = chunks.Where(chunk => chunk.Id == daoId).ToArray();
        var sinfs = chunks.Where(chunk => chunk.Id == "SINF").ToArray();
        if (cues.Length > 1 || daos.Length > 1 || sinfs.Length > 1)
            throw new NotSupportedException(
                $"{name} holds several sessions; only single-session discs are read, so a CD-Extra or multisession copy is not.");
        if (cues.Length == 0 || daos.Length == 0)
            throw new InvalidDataException($"{name} holds no {cueId} and {daoId} chunks describing its tracks.");

        var (indices, leadOut) = Cue(cues[0].Data, version2, name);
        var tracks = Dao(daos[0].Data, version2, name);
        if (tracks.Count != indices.Count)
            throw new InvalidDataException($"{name}'s {daoId} chunk describes {tracks.Count} tracks and its {cueId} chunk {indices.Count}.");
        if (sinfs.Length == 1 && (sinfs[0].Data.Length != 4 || BinaryPrimitives.ReadUInt32BigEndian(sinfs[0].Data) != tracks.Count))
            throw new InvalidDataException($"{name}'s SINF chunk does not give its session's {tracks.Count} tracks.");

        var builder = new DiscLayoutBuilder();
        DiscDataTrack? data = null;
        long fileEnd = 0;
        for (var position = 0; position < tracks.Count; position++)
        {
            var number = position + 1;
            var track = tracks[position];
            var (data1, indexZero, indexOne, more) = indices[position];
            if (data1 != track.Data)
                throw new InvalidDataException($"{name}'s {cueId} and {daoId} chunks disagree on whether track {number} holds data.");
            if (number == 1 && !track.Data)
                throw new NotSupportedException($"{name} begins with an audio track; only discs whose first track holds the data are read.");
            if (number > 1 && track.Data)
                throw new NotSupportedException($"{name} gives track {number} data; only track 1 may hold data.");
            if (number == 1 && indexOne != 0)
                throw new InvalidDataException($"{name} must start track 1 at sector 0.");
            var next = position + 1 < tracks.Count ? indices[position + 1].IndexZero : leadOut;
            if (track.PregapOffset < fileEnd || track.PregapOffset > track.StartOffset || track.StartOffset > track.EndOffset ||
                track.EndOffset > listOffset)
                throw new InvalidDataException($"{name} puts track {number}'s sectors out of order or past its chunk list.");
            fileEnd = track.EndOffset;
            var stored = Sectors(track.StartOffset - track.PregapOffset, track.SectorSize, name, number);
            var length = Sectors(track.EndOffset - track.StartOffset, track.SectorSize, name, number);
            // Track 1's pregap lies before LBA 0; Nero may store it, and it is skipped.
            if (stored != indexOne - indexZero && !(number == 1 && stored == 0))
                throw new InvalidDataException(
                    $"{name} stores {stored} pregap sectors for track {number}, but its {cueId} chunk gives {indexOne - indexZero}.");
            if (length != next - indexOne || length == 0)
                throw new InvalidDataException(
                    $"{name} stores {length} sectors for track {number}, but its {cueId} chunk gives {next - indexOne}.");

            var mode = track.Mode2 ? RawDataTrackMode.Mode2Form1 : RawDataTrackMode.Mode1;
            builder.Track(number, number == 1 ? DiscImageOpener.TypeOf(mode, track.Cooked) : "AUDIO");
            if (number > 1)
            {
                if (builder.Position != indexZero)
                    throw new InvalidDataException($"{name} starts track {number} at sector {indexZero}, not where track {number - 1} ends.");
                if (stored > 0)
                {
                    builder.Index(0, indexZero);
                    builder.Stored(file, track.PregapOffset, track.SectorSize, stored);
                }
            }
            builder.Index(1, indexOne);
            foreach (var (index, sector) in more)
            {
                if (sector < indexOne || sector >= next)
                    throw new InvalidDataException($"{name} puts track {number} index {index} outside the track.");
                builder.Index(index, sector);
            }
            if (track.Cooked)
            {
                builder.Unavailable(length, DiscImageOpener.CookedReason(name));
                data = new CookedDiscDataTrack(file, track.StartOffset, length);
            }
            else builder.Stored(file, track.StartOffset, track.SectorSize, length);
        }

        data ??= new RawDiscDataTrack(tracks[0].Mode2 ? RawDataTrackMode.Mode2Form1 : RawDataTrackMode.Mode1,
            builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? builder.Position);
        return new(DiscImageFormats.NeroNrg, [nrgPath], null, null, builder.Sheet(name, []), builder.Segments,
            builder.Position, data, new Dictionary<int, string>());
    }

    private static InvalidDataException NotNrg(string name) =>
        new($"{name} is not a Nero image: it ends in neither the NER5 nor the NERO footer.");

    private static long Sectors(long bytes, int sectorSize, string name, int number) => bytes % sectorSize == 0
        ? bytes / sectorSize
        : throw new InvalidDataException($"{name} stores track {number} in a part of the file that is not a whole number of {sectorSize}-byte sectors.");

    // The chunks up to END!, which must end the list.
    private static List<(string Id, byte[] Data)> Chunks(byte[] list, string name)
    {
        var chunks = new List<(string, byte[])>();
        for (var at = 0; ;)
        {
            if (list.Length - at < 8) throw new InvalidDataException($"{name}'s chunk list ends without an END! chunk.");
            var id = Encoding.Latin1.GetString(list, at, 4);
            var length = BinaryPrimitives.ReadUInt32BigEndian(list.AsSpan(at + 4));
            at += 8;
            if (length > list.Length - at) throw new InvalidDataException($"{name}'s {AssetVerifier.JsonString(id)} chunk runs past its chunk list.");
            if (id == "END!")
            {
                if (length != 0 || at != list.Length)
                    throw new InvalidDataException($"{name}'s END! chunk is not empty or does not end its chunk list.");
                return chunks;
            }
            chunks.Add((id, list.AsSpan(at, (int)length).ToArray()));
            at += (int)length;
        }
    }

    // Each track's data bit and indices from the CUEX or CUES chunk, and the lead-out sector.
    private static (List<(bool Data, long IndexZero, long IndexOne, List<(int Index, long Sector)> More)> Tracks, long LeadOut) Cue(
        byte[] chunk, bool version2, string name)
    {
        var id = version2 ? "CUEX" : "CUES";
        if (chunk.Length % 8 != 0 || chunk.Length < 16)
            throw new InvalidDataException($"{name}'s {id} chunk is not a list of 8-byte entries.");
        var tracks = new List<(bool Data, long IndexZero, long IndexOne, List<(int, long)> More)>();
        long? leadOut = null;
        long previous = long.MinValue;
        (bool Data, long? Zero, long? One, List<(int, long)> More, int Number)? current = null;
        for (var at = 0; at < chunk.Length; at += 8)
        {
            var control = chunk[at];
            var track = Bcd(chunk[at + 1], name, id);
            var index = Bcd(chunk[at + 2], name, id);
            long sector = version2
                ? BinaryPrimitives.ReadInt32BigEndian(chunk.AsSpan(at + 4))
                : (Bcd(chunk[at + 5], name, id) * 60L + Bcd(chunk[at + 6], name, id)) * 75 + Bcd(chunk[at + 7], name, id) - 150;
            if (at == 0)
            {
                if (track != 0 || index != 0)
                    throw new InvalidDataException($"{name}'s {id} chunk does not begin with the lead-in entry.");
                continue;
            }
            if (sector < previous) throw new InvalidDataException($"{name}'s {id} chunk gives its entries out of order.");
            previous = sector;
            if (chunk[at + 1] == 0xAA)
            {
                if (index != 1 || at + 8 != chunk.Length)
                    throw new InvalidDataException($"{name}'s {id} chunk must end with the lead-out entry.");
                leadOut = sector;
                break;
            }
            if (current is null || current.Value.Number != track)
            {
                if (current is { } done) tracks.Add(Finish(done, name, id));
                if (track != tracks.Count + 1)
                    throw new InvalidDataException($"{name}'s {id} chunk numbers its tracks out of order.");
                current = ((control & 0x40) != 0, null, null, [], track);
            }
            var value = current.Value;
            if (index == 0 && value.Zero is null && value.One is null) current = value with { Zero = sector };
            else if (index == 1 && value.One is null) current = value with { One = sector };
            else if (index > 1 && value.One is not null && !value.More.Any(entry => entry.Item1 == index)) value.More.Add((index, sector));
            else throw new InvalidDataException($"{name}'s {id} chunk gives track {track} index {index} out of order or twice.");
        }
        if (leadOut is null || current is null) throw new InvalidDataException($"{name}'s {id} chunk gives no tracks and lead-out.");
        tracks.Add(Finish(current.Value, name, id));
        return (tracks, leadOut.Value);
    }

    private static (bool, long, long, List<(int, long)>) Finish(
        (bool Data, long? Zero, long? One, List<(int, long)> More, int Number) track, string name, string id)
    {
        if (track.One is not { } one) throw new InvalidDataException($"{name}'s {id} chunk gives track {track.Number} no index 1.");
        return (track.Data, track.Zero ?? one, one, track.More);
    }

    private static int Bcd(byte value, string name, string id) => (value >> 4) < 10 && (value & 0x0F) < 10
        ? (value >> 4) * 10 + (value & 0x0F)
        : value == 0xAA ? 0xAA : throw new InvalidDataException($"{name}'s {id} chunk holds 0x{value:X2}, which is not a BCD number.");

    private sealed record DaoTrack(bool Data, bool Cooked, bool Mode2, int SectorSize, long PregapOffset, long StartOffset, long EndOffset);

    // Each track's sector layout and offsets from the DAOX or DAOI chunk.
    private static List<DaoTrack> Dao(byte[] chunk, bool version2, string name)
    {
        var id = version2 ? "DAOX" : "DAOI";
        var blockSize = version2 ? 42 : 30;
        if (chunk.Length < DaoHeaderSize) throw new InvalidDataException($"{name}'s {id} chunk is truncated.");
        int first = chunk[20], last = chunk[21];
        if (first != 1 || last < first || last > 99 || chunk.Length != DaoHeaderSize + (last - first + 1) * blockSize)
            throw new InvalidDataException($"{name}'s {id} chunk must describe tracks 1 to {last}, one {blockSize}-byte block each.");
        var tracks = new List<DaoTrack>();
        for (var at = DaoHeaderSize; at < chunk.Length; at += blockSize)
        {
            var number = tracks.Count + 1;
            var sectorSize = BinaryPrimitives.ReadUInt16BigEndian(chunk.AsSpan(at + 12));
            var mode = chunk[at + 14];
            // The mode codes libmirage's image-nrg parser reads, with the sector size each implies.
            var (data, cooked, mode2, expected) = mode switch
            {
                0x00 => (true, true, false, 2048),
                0x02 => (true, true, true, 2048),
                0x05 => (true, false, false, 2352),
                0x06 => (true, false, true, 2352),
                0x07 => (false, false, false, 2352),
                0x0F => (true, false, false, 2448),
                0x10 => (false, false, false, 2448),
                0x11 => (true, false, true, 2448),
                0x03 => throw new NotSupportedException(
                    $"{name} stores track {number} as 2336-byte Mode 2 sectors (mode 0x03), which are not read."),
                _ => throw new NotSupportedException($"{name} gives track {number} mode 0x{mode:X2}, which is not read."),
            };
            if (sectorSize != expected)
                throw new InvalidDataException($"{name} gives track {number} mode 0x{mode:X2} with {sectorSize}-byte sectors, not {expected}.");
            long Offset(int index) => version2
                ? checked((long)BinaryPrimitives.ReadUInt64BigEndian(chunk.AsSpan(at + 18 + index * 8)))
                : BinaryPrimitives.ReadUInt32BigEndian(chunk.AsSpan(at + 18 + index * 4));
            tracks.Add(new(data, cooked, mode2, sectorSize, Offset(0), Offset(1), Offset(2)));
        }
        return tracks;
    }
}
