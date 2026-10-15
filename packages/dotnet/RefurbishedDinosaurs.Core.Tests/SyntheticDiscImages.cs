using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.LegacyFormats;

namespace RefurbishedDinosaurs.Core.Tests;

// One synthetic mixed-mode disc written in each image form a disc-image source reads: a MODE1 data
// track holding the BuildIso volume, track 02 after a 150-sector pregap of silence, and track 03
// straight after it, each a run of seeded noise.
internal static class SyntheticDiscImages
{
    public const int RawSector = CueBinSheet.RawSectorSize;
    public const int CookedSector = 2048;
    public const int DataSectors = OriginalContentSourceTests.IsoSectors;
    public const int PregapSectors = 150;
    public const int TrackSectors = 20;
    public const int Track2Start = DataSectors + PregapSectors;
    public const int Track3Start = Track2Start + TrackSectors;
    public const int LeadOut = Track3Start + TrackSectors;
    public static readonly byte[] Payload = [9, 8, 7, 6, 5];
    public static readonly byte[] Iso = OriginalContentSourceTests.BuildIso(Payload);
    public static readonly byte[] RawData = CueBinSourceTests.ToRaw(Iso);
    public static readonly byte[] Track2 = Noise(TrackSectors * RawSector, 2);
    public static readonly byte[] Track3 = Noise(TrackSectors * RawSector, 3);
    private static readonly byte[] Silence = new byte[PregapSectors * RawSector];

    // The whole disc from LBA 0 to the lead-out, as a one-file BIN holds it.
    public static byte[] Bin => [.. RawData, .. Silence, .. Track2, .. Track3];

    public static string CueBin(string root)
    {
        File.WriteAllBytes(Path.Combine(root, "game.bin"), Bin);
        return Text(root, "game.cue",
            "FILE \"game.bin\" BINARY", "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00",
            "  TRACK 02 AUDIO", $"    INDEX 00 {Msf(DataSectors)}", $"    INDEX 01 {Msf(Track2Start)}",
            "  TRACK 03 AUDIO", $"    INDEX 01 {Msf(Track3Start)}");
    }

    // One BINARY file per track, as Redump lays a disc out.
    public static string SplitBin(string root)
    {
        File.WriteAllBytes(Path.Combine(root, "game (Track 1).bin"), RawData);
        File.WriteAllBytes(Path.Combine(root, "game (Track 2).bin"), [.. Silence, .. Track2]);
        File.WriteAllBytes(Path.Combine(root, "game (Track 3).bin"), Track3);
        return Text(root, "game.cue",
            "FILE \"game (Track 1).bin\" BINARY", "  TRACK 01 MODE1/2352", "    INDEX 01 00:00:00",
            "FILE \"game (Track 2).bin\" BINARY", "  TRACK 02 AUDIO", "    INDEX 00 00:00:00", $"    INDEX 01 {Msf(PregapSectors)}",
            "FILE \"game (Track 3).bin\" BINARY", "  TRACK 03 AUDIO", "    INDEX 01 00:00:00");
    }

    // The data track as an ISO and each audio track as a WAVE file from its INDEX 01, with the pregap
    // declared as PREGAP, as the archiver's iso-wav form and DOSBox sheets write them.
    public static string IsoWave(string root, string track2 = "game (Track 02).wav", byte[]? track2Bytes = null)
    {
        File.WriteAllBytes(Path.Combine(root, "game.iso"), Iso);
        File.WriteAllBytes(Path.Combine(root, track2), track2Bytes ?? Wave(Track2));
        File.WriteAllBytes(Path.Combine(root, "game (Track 03).wav"), Wave(Track3));
        return Text(root, "game.cue",
            "FILE \"game.iso\" BINARY", "  TRACK 01 MODE1/2048", "    INDEX 01 00:00:00",
            $"FILE \"{track2}\" WAVE", "  TRACK 02 AUDIO", "    PREGAP 00:02:00", "    INDEX 01 00:00:00",
            "FILE \"game (Track 03).wav\" WAVE", "  TRACK 03 AUDIO", "    INDEX 01 00:00:00");
    }

    // The data track as an ISO and each audio track as a FLAC file from its INDEX 01, given the WAVE
    // file type, as the archiver's iso-flac form writes them.
    public static string IsoFlac(string root, SyntheticFlacOptions? track2 = null)
    {
        File.WriteAllBytes(Path.Combine(root, "game.iso"), Iso);
        File.WriteAllBytes(Path.Combine(root, "game (Track 02).flac"), SyntheticFlacWriter.Write(Track2, track2));
        // Track 03 in blocks of another size, ending in a short block.
        File.WriteAllBytes(Path.Combine(root, "game (Track 03).flac"),
            SyntheticFlacWriter.Write(Track3, new() { BlockSize = 1152 }));
        return Text(root, "game.cue",
            "FILE \"game.iso\" BINARY", "  TRACK 01 MODE1/2048", "    INDEX 01 00:00:00",
            "FILE \"game (Track 02).flac\" WAVE", "  TRACK 02 AUDIO", "    PREGAP 00:02:00", "    INDEX 01 00:00:00",
            "FILE \"game (Track 03).flac\" WAVE", "  TRACK 03 AUDIO", "    INDEX 01 00:00:00");
    }

    // A CHD of the disc as chdman writes one from the cue/bin, with track 02's pregap stored, or
    // with the pregap recorded but not stored, as chdman writes one from a sheet that declares it
    // with PREGAP.
    public static string Chd(string root, bool unstoredPregap = false)
    {
        var path = Path.Combine(root, "game.chd");
        File.WriteAllBytes(path, unstoredPregap
            ? SyntheticChdWriter.Write([.. RawData, .. Track2, .. Track3],
                [new("MODE1_RAW", DataSectors), new("AUDIO", TrackSectors, UnstoredPregap: PregapSectors), new("AUDIO", TrackSectors)])
            : SyntheticChdWriter.Write(Bin,
                [new("MODE1_RAW", DataSectors), new("AUDIO", PregapSectors + TrackSectors, PregapSectors), new("AUDIO", TrackSectors)]));
        return path;
    }

    public static string CloneCd(string root, Func<string, string>? edit = null, byte[]? image = null)
    {
        File.WriteAllBytes(Path.Combine(root, "game.img"), image ?? Bin);
        File.WriteAllBytes(Path.Combine(root, "game.sub"), new byte[LeadOut * 96]);
        var lines = new List<string>
        {
            "[CloneCD]", "Version=3", "[Disc]", "TocEntries=6", "Sessions=1", "DataTracksScrambled=0", "CDTextLength=0",
            "[Session 1]", "PreGapMode=1", "PreGapSubC=0",
        };
        Entry(lines, 0, 0xA0, 0x04, 1 * 4500 - 150);
        Entry(lines, 1, 0xA1, 0x00, 3 * 4500 - 150);
        Entry(lines, 2, 0xA2, 0x00, LeadOut);
        Entry(lines, 3, 1, 0x04, 0);
        Entry(lines, 4, 2, 0x00, Track2Start);
        Entry(lines, 5, 3, 0x00, Track3Start);
        lines.AddRange(["[TRACK 1]", "MODE=1", "INDEX 1=0",
            "[TRACK 2]", "MODE=0", $"INDEX 0={DataSectors}", $"INDEX 1={Track2Start}",
            "[TRACK 3]", "MODE=0", $"INDEX 1={Track3Start}"]);
        var text = string.Join("\r\n", lines) + "\r\n";
        var path = Path.Combine(root, "game.ccd");
        File.WriteAllText(path, edit is null ? text : edit(text), Encoding.ASCII);
        return path;
    }

    private static void Entry(List<string> lines, int number, int point, int control, int lba)
    {
        var frames = lba + 150;
        lines.AddRange([$"[Entry {number}]", "Session=1", $"Point=0x{point:x2}", "ADR=0x01", $"Control=0x{control:x2}",
            "TrackNo=0", "AMin=0", "ASec=0", "AFrame=0", "ALBA=-150", "Zero=0",
            $"PMin={frames / 4500}", $"PSec={frames / 75 % 60}", $"PFrame={frames % 75}", $"PLBA={lba}"]);
    }

    // An Alcohol 120% version 1 image. With cookedData, track 1 is 2048-byte sectors and track 02's
    // pregap is stored in no file. Otherwise every sector is raw, followed by 96 bytes of subchannel
    // when subchannel is set, and the pregap ends track 1's data, counted in track 1's length or,
    // with pregapApart, stored after it outside that length, as Aaru's Alcohol120 writer and the
    // archiver lay it out. With mode2, track 1 is a raw Mode 2 track of CD-XA Form 1 sectors, typed with
    // that mode byte: 0xAB (Mode 2 formless) or 0xEC (Mode 2 Form 1, as Aaru's writer and the archiver type it).
    public static string Mds(string root, bool cookedData = false, bool subchannel = false, bool pregapApart = false,
        byte? mode2 = null, Action<byte[]>? edit = null)
    {
        var sectorSize = RawSector + (subchannel ? 96 : 0);
        byte[] Sectors(byte[] raw) => subchannel ? WithSubchannel(raw) : raw;
        var mdf = new MemoryStream();
        var tracks = new List<(int Point, byte Mode, int Size, int Start, long Offset, int Pregap, int Length)>();
        if (cookedData)
        {
            tracks.Add((1, 0xAA, CookedSector, 0, 0, 150, DataSectors));
            mdf.Write(Iso);
            tracks.Add((2, 0xA9, sectorSize, Track2Start, mdf.Length, PregapSectors, TrackSectors));
            mdf.Write(Sectors(Track2));
        }
        else if (pregapApart || mode2 is not null)
        {
            tracks.Add((1, mode2 ?? 0xAA, sectorSize, 0, 0, 150, DataSectors));
            mdf.Write(Sectors([.. mode2 is not null ? Mode2Form1SourceTests.ToMode2Form1(Iso) : RawData, .. Silence]));
            tracks.Add((2, 0xA9, sectorSize, Track2Start, mdf.Length, PregapSectors, TrackSectors));
            mdf.Write(Sectors(Track2));
        }
        else
        {
            tracks.Add((1, 0xAA, sectorSize, 0, 0, 150, Track2Start));
            mdf.Write(Sectors([.. RawData, .. Silence]));
            tracks.Add((2, 0xA9, sectorSize, Track2Start, mdf.Length, PregapSectors, TrackSectors));
            mdf.Write(Sectors(Track2));
        }
        tracks.Add((3, 0xA9, sectorSize, Track3Start, mdf.Length, 0, TrackSectors));
        mdf.Write(Sectors(Track3));
        File.WriteAllBytes(Path.Combine(root, "game.mdf"), mdf.ToArray());

        const int sessionAt = 88, tracksAt = sessionAt + 24, blocks = 6, extraAt = tracksAt + blocks * 80;
        const int footerAt = extraAt + 3 * 8, nameAt = footerAt + 16;
        var mds = new byte[nameAt + 6];
        "MEDIA DESCRIPTOR"u8.CopyTo(mds);
        mds[16] = 1;
        mds[17] = 3;
        BinaryPrimitives.WriteUInt16LittleEndian(mds.AsSpan(20), 1);
        BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(80), sessionAt);
        BinaryPrimitives.WriteInt32LittleEndian(mds.AsSpan(sessionAt), -150);
        BinaryPrimitives.WriteInt32LittleEndian(mds.AsSpan(sessionAt + 4), LeadOut);
        BinaryPrimitives.WriteUInt16LittleEndian(mds.AsSpan(sessionAt + 8), 1);
        mds[sessionAt + 10] = blocks;
        mds[sessionAt + 11] = 3;
        BinaryPrimitives.WriteUInt16LittleEndian(mds.AsSpan(sessionAt + 12), 1);
        BinaryPrimitives.WriteUInt16LittleEndian(mds.AsSpan(sessionAt + 14), 3);
        BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(sessionAt + 20), tracksAt);
        for (var leadIn = 0; leadIn < 3; leadIn++) mds[tracksAt + leadIn * 80 + 4] = (byte)(0xA0 + leadIn);
        for (var index = 0; index < tracks.Count; index++)
        {
            var (point, mode, size, start, offset, pregap, length) = tracks[index];
            var at = tracksAt + (3 + index) * 80;
            mds[at] = mode;
            mds[at + 1] = subchannel && size != CookedSector ? (byte)8 : (byte)0;
            mds[at + 4] = (byte)point;
            BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(at + 12), (uint)(extraAt + index * 8));
            BinaryPrimitives.WriteUInt16LittleEndian(mds.AsSpan(at + 16), (ushort)size);
            BinaryPrimitives.WriteInt32LittleEndian(mds.AsSpan(at + 36), start);
            BinaryPrimitives.WriteUInt64LittleEndian(mds.AsSpan(at + 40), (ulong)offset);
            BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(at + 48), 1);
            BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(at + 52), footerAt);
            BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(extraAt + index * 8), (uint)pregap);
            BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(extraAt + index * 8 + 4), (uint)length);
        }
        BinaryPrimitives.WriteUInt32LittleEndian(mds.AsSpan(footerAt), nameAt);
        "*.mdf"u8.CopyTo(mds.AsSpan(nameAt));
        edit?.Invoke(mds);
        var path = Path.Combine(root, "game.mds");
        File.WriteAllBytes(path, mds);
        return path;
    }

    // A Nero image. Version 2 stores raw sectors with track 1's two-second pregap ahead of it;
    // version 1 stores track 1 as 2048-byte sectors and the audio with 96 bytes of subchannel, and
    // gives its CUES times in BCD or, with bcdTimes false, in binary.
    public static string Nrg(string root, bool version2 = true, bool bcdTimes = true,
        Func<List<(string Id, byte[] Data)>, List<(string Id, byte[] Data)>>? edit = null)
    {
        byte Time(int value) => bcdTimes ? Bcd(value) : (byte)value;
        var body = new MemoryStream();
        var tracks = new List<(byte Mode, int Size, long Pregap, long Start, long End)>();
        if (version2)
        {
            body.Write(new byte[PregapSectors * RawSector]);
            tracks.Add((0x05, RawSector, 0, body.Length, body.Length + RawData.Length));
            body.Write(RawData);
            tracks.Add((0x07, RawSector, body.Length, body.Length + Silence.Length, body.Length + Silence.Length + Track2.Length));
            body.Write([.. Silence, .. Track2]);
            tracks.Add((0x07, RawSector, body.Length, body.Length, body.Length + Track3.Length));
            body.Write(Track3);
        }
        else
        {
            tracks.Add((0x00, CookedSector, 0, 0, Iso.Length));
            body.Write(Iso);
            var pregap = WithSubchannel(Silence);
            var two = WithSubchannel(Track2);
            tracks.Add((0x10, RawSector + 96, body.Length, body.Length + pregap.Length, body.Length + pregap.Length + two.Length));
            body.Write([.. pregap, .. two]);
            var three = WithSubchannel(Track3);
            tracks.Add((0x10, RawSector + 96, body.Length, body.Length, body.Length + three.Length));
            body.Write(three);
        }

        var cue = new MemoryStream();
        void CueEntry(byte control, byte track, byte index, int lba)
        {
            cue.WriteByte(control);
            cue.WriteByte(track);
            cue.WriteByte(index);
            cue.WriteByte(0);
            var value = new byte[4];
            if (version2) BinaryPrimitives.WriteInt32BigEndian(value, lba);
            else
            {
                var frames = lba + 150;
                value[1] = Time(frames / 4500);
                value[2] = Time(frames / 75 % 60);
                value[3] = Time(frames % 75);
            }
            cue.Write(value);
        }
        CueEntry(0x41, 0x00, 0x00, -150);
        CueEntry(0x41, 0x01, 0x00, -150);
        CueEntry(0x41, 0x01, 0x01, 0);
        CueEntry(0x01, 0x02, 0x00, DataSectors);
        CueEntry(0x01, 0x02, 0x01, Track2Start);
        CueEntry(0x01, 0x03, 0x00, Track3Start);
        CueEntry(0x01, 0x03, 0x01, Track3Start);
        CueEntry(0x01, 0xAA, 0x01, LeadOut);

        var blockSize = version2 ? 42 : 30;
        var dao = new byte[22 + 3 * blockSize];
        BinaryPrimitives.WriteUInt32BigEndian(dao, (uint)dao.Length);
        dao[20] = 1;
        dao[21] = 3;
        for (var index = 0; index < 3; index++)
        {
            var (mode, size, pregap, start, end) = tracks[index];
            var at = 22 + index * blockSize;
            BinaryPrimitives.WriteUInt16BigEndian(dao.AsSpan(at + 12), (ushort)size);
            dao[at + 14] = mode;
            long[] offsets = [pregap, start, end];
            for (var field = 0; field < 3; field++)
                if (version2) BinaryPrimitives.WriteUInt64BigEndian(dao.AsSpan(at + 18 + field * 8), (ulong)offsets[field]);
                else BinaryPrimitives.WriteUInt32BigEndian(dao.AsSpan(at + 18 + field * 4), (uint)offsets[field]);
        }
        var sinf = new byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(sinf, 3);
        var chunks = new List<(string Id, byte[] Data)>
        {
            (version2 ? "CUEX" : "CUES", cue.ToArray()), (version2 ? "DAOX" : "DAOI", dao), ("SINF", sinf), ("MTYP", [0, 0, 0, 1]),
        };
        if (edit is not null) chunks = edit(chunks);

        var listOffset = body.Length;
        foreach (var (id, data) in chunks.Append(("END!", Array.Empty<byte>())))
        {
            body.Write(Encoding.ASCII.GetBytes(id));
            var length = new byte[4];
            BinaryPrimitives.WriteUInt32BigEndian(length, (uint)data.Length);
            body.Write(length);
            body.Write(data);
        }
        if (version2)
        {
            body.Write("NER5"u8);
            var offset = new byte[8];
            BinaryPrimitives.WriteUInt64BigEndian(offset, (ulong)listOffset);
            body.Write(offset);
        }
        else
        {
            body.Write("NERO"u8);
            var offset = new byte[4];
            BinaryPrimitives.WriteUInt32BigEndian(offset, (uint)listOffset);
            body.Write(offset);
        }
        var path = Path.Combine(root, "game.nrg");
        File.WriteAllBytes(path, body.ToArray());
        return path;
    }

    // A RIFF WAVE file of 16-bit stereo 44.1 kHz PCM holding the samples.
    public static byte[] Wave(byte[] samples)
    {
        var header = new byte[WavePcm16Writer.HeaderSize];
        WavePcm16Writer.FormatHeader(header, (uint)samples.Length, 2, 44100);
        return [.. header, .. samples];
    }

    // Each 2352-byte sector followed by 96 bytes of subchannel, which a reader must leave out.
    private static byte[] WithSubchannel(byte[] raw)
    {
        var result = new byte[raw.Length / RawSector * (RawSector + 96)];
        for (var sector = 0; sector < raw.Length / RawSector; sector++)
        {
            raw.AsSpan(sector * RawSector, RawSector).CopyTo(result.AsSpan(sector * (RawSector + 96)));
            result.AsSpan(sector * (RawSector + 96) + RawSector, 96).Fill(0x5A);
        }
        return result;
    }

    public static string Msf(int sector) => $"{sector / 75 / 60:D2}:{sector / 75 % 60:D2}:{sector % 75:D2}";

    private static byte Bcd(int value) => (byte)((value / 10) << 4 | value % 10);

    private static string Text(string root, string name, params string[] lines)
    {
        var path = Path.Combine(root, name);
        File.WriteAllText(path, string.Join("\n", lines) + "\n");
        return path;
    }

    private static byte[] Noise(int length, int seed)
    {
        var bytes = new byte[length];
        new Random(seed).NextBytes(bytes);
        return bytes;
    }
}
