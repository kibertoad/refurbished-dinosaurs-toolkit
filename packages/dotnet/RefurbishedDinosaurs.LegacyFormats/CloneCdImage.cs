using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace RefurbishedDinosaurs.LegacyFormats;

// Reads a CloneCD image: the .ccd, an INI file of [CloneCD], [Disc], [Session N], [Entry N] (the
// disc's table of contents) and [TRACK N] sections, and the .img with its name, which holds every
// raw sector from LBA 0 to the lead-out. The .sub beside them is not read. The table of contents
// and the [TRACK] sections must agree on every track's start and kind.
internal static partial class CloneCdImage
{
    [GeneratedRegex("^(?<kind>SESSION|ENTRY|TRACK) (?<number>\\d{1,3})$", RegexOptions.CultureInvariant)]
    private static partial Regex NumberedSection();

    [GeneratedRegex("^INDEX (?<number>\\d{1,2})$", RegexOptions.CultureInvariant)]
    private static partial Regex IndexKey();

    public static DiscImage Read(string ccdPath)
    {
        var name = Path.GetFileName(ccdPath);
        var sections = Sections(DiscImageOpener.ReadDescriptor(ccdPath, "CloneCD .ccd"), name);

        var version = Number(sections, "CLONECD", "Version", name);
        if (version is not (2 or 3))
            throw new NotSupportedException($"{name} is CloneCD version {version}; only versions 2 and 3 are read.");
        var sessions = Number(sections, "DISC", "Sessions", name);
        if (sessions != 1)
            throw new NotSupportedException(
                $"{name} holds {sessions} sessions; only single-session discs are read, so a CD-Extra or multisession copy is not.");
        if (Optional(sections, "DISC", "DataTracksScrambled", name) is { } scrambled && scrambled != 0)
            throw new NotSupportedException($"{name} stores its data tracks scrambled, which is not read.");
        var entryCount = Number(sections, "DISC", "TocEntries", name);
        foreach (var section in sections.Keys)
            if (NumberedSection().Match(section) is { Success: true } numbered &&
                numbered.Groups["kind"].Value == "SESSION" && numbered.Groups["number"].Value != "1")
                throw new NotSupportedException($"{name} has a [{section}] section; only single-session discs are read.");

        // The table of contents: the first and last track, the lead-out and each track's start.
        int? firstTrack = null, lastTrack = null;
        long? leadOut = null;
        var starts = new SortedDictionary<int, (long Lba, bool Data)>();
        for (var entry = 0; entry < entryCount; entry++)
        {
            var section = $"ENTRY {entry}";
            if (!sections.ContainsKey(section)) throw new InvalidDataException($"{name} has no [Entry {entry}] of its {entryCount} TocEntries.");
            if (Number(sections, section, "Session", name) != 1)
                throw new NotSupportedException($"{name} [Entry {entry}] is in another session; only single-session discs are read.");
            var point = Number(sections, section, "Point", name);
            var adr = Number(sections, section, "ADR", name);
            if (adr != 1)
                throw new NotSupportedException(
                    $"{name} [Entry {entry}] has ADR {adr} for point 0x{point:X2}; only ADR 1 entries of a single-session disc are read.");
            var control = Number(sections, section, "Control", name);
            var minute = Number(sections, section, "PMin", name);
            var second = Number(sections, section, "PSec", name);
            var frame = Number(sections, section, "PFrame", name);
            switch (point)
            {
                case 0xA0: firstTrack = Once(firstTrack, minute, name, point); break;
                case 0xA1: lastTrack = Once(lastTrack, minute, name, point); break;
                case 0xA2 or (>= 1 and <= 99):
                    var lba = Number(sections, section, "PLBA", name);
                    if (lba != (minute * 60L + second) * 75 + frame - 150)
                        throw new InvalidDataException(
                            $"{name} [Entry {entry}] gives PLBA {lba}, but PMin, PSec and PFrame give {(minute * 60L + second) * 75 + frame - 150}.");
                    if (point == 0xA2) leadOut = Once(leadOut, lba, name, point);
                    else if (!starts.TryAdd(point, (lba, (control & 0x04) != 0)))
                        throw new InvalidDataException($"{name} lists track {point} twice in its table of contents.");
                    break;
                default:
                    throw new NotSupportedException(
                        $"{name} [Entry {entry}] has point 0x{point:X2}, which is not read; only the tracks and the A0, A1 " +
                        "and A2 points of a single-session disc are.");
            }
        }
        if (sections.Keys.Count(key => key.StartsWith("ENTRY ", StringComparison.Ordinal)) != entryCount)
            throw new InvalidDataException($"{name} has [Entry] sections beyond its {entryCount} TocEntries.");
        if (firstTrack != 1 || lastTrack is null || leadOut is null || starts.Count == 0)
            throw new InvalidDataException($"{name}'s table of contents must give the first track as 1, the last track and the lead-out.");
        if (starts.Keys.First() != 1 || starts.Keys.Last() != lastTrack || starts.Count != lastTrack)
            throw new InvalidDataException($"{name}'s table of contents must list tracks 1 to {lastTrack}, each once.");
        var trackSections = sections.Keys.Count(key => key.StartsWith("TRACK ", StringComparison.Ordinal));
        if (trackSections != lastTrack)
            throw new InvalidDataException($"{name} has {trackSections} [TRACK] sections for {lastTrack} tracks.");

        var imagePath = Path.ChangeExtension(ccdPath, ".img");
        imagePath = CueBinSheet.MatchStem(ccdPath, CueBinSheet.Enumerate(Path.GetDirectoryName(ccdPath)!, ".img"))
            ?? throw new FileNotFoundException($"{name} reads its sectors from {Path.GetFileName(imagePath)}, which does not exist.", imagePath);
        var image = ImageSnapshot.Take(imagePath);
        if (image.Length == 0 || image.Length % CueBinSheet.RawSectorSize != 0)
            throw new InvalidDataException(
                $"{Path.GetFileName(imagePath)} must be a positive multiple of {CueBinSheet.RawSectorSize} bytes.");
        var sectors = image.Length / CueBinSheet.RawSectorSize;
        if (sectors != leadOut)
            throw new InvalidDataException(
                $"{Path.GetFileName(imagePath)} holds {sectors} sectors, but {name} puts the lead-out at sector {leadOut}.");

        var builder = new DiscLayoutBuilder();
        builder.Stored(image, 0, CueBinSheet.RawSectorSize, sectors);
        var mode = RawDataTrackMode.Mode1;
        long previous = 0;
        foreach (var (number, (start, data)) in starts)
        {
            var section = $"TRACK {number}";
            if (!sections.ContainsKey(section)) throw new InvalidDataException($"{name} has no [TRACK {number}] section.");
            var trackMode = Number(sections, section, "MODE", name);
            if (trackMode is not (0 or 1 or 2))
                throw new NotSupportedException($"{name} gives track {number} mode {trackMode}; only modes 0 (audio), 1 and 2 are read.");
            if ((trackMode != 0) != data)
                throw new InvalidDataException(
                    $"{name}'s table of contents marks track {number} as {(data ? "data" : "audio")}, but [TRACK {number}] gives mode {trackMode}.");
            if (number == 1 && trackMode == 0)
                throw new NotSupportedException($"{name} begins with an audio track; only discs whose first track holds the data are read.");
            if (number > 1 && trackMode != 0)
                throw new NotSupportedException($"{name} gives track {number} mode {trackMode}; only track 1 may hold data.");
            if (number == 1) mode = trackMode == 1 ? RawDataTrackMode.Mode1 : RawDataTrackMode.Mode2Form1;

            var indices = new SortedDictionary<int, long>();
            foreach (var (key, value) in sections[section])
                if (IndexKey().Match(key) is { Success: true } index)
                    indices[int.Parse(index.Groups["number"].Value, CultureInfo.InvariantCulture)] = Parse(value, name, section, key);
            if (!indices.TryGetValue(1, out var indexOne) || indexOne != start)
                throw new InvalidDataException(
                    $"{name} [TRACK {number}] must give INDEX 1 as {start}, the track's start in the table of contents.");
            if (number == 1 && (indexOne != 0 || indices.GetValueOrDefault(0, 0) != 0))
                throw new InvalidDataException($"{name} must start track 1 at sector 0 with no pregap stored ahead of it.");
            builder.Track(number, number == 1 ? DiscImageOpener.TypeOf(mode, cooked: false) : "AUDIO");
            foreach (var (index, sector) in indices)
            {
                if (sector < previous || sector >= sectors)
                    throw new InvalidDataException(
                        $"{name} [TRACK {number}] INDEX {index} is at sector {sector}, out of order or outside the image.");
                previous = sector;
                builder.Index(index, sector);
            }
        }

        var dataSectors = builder.StartOf(2, 0) ?? builder.StartOf(2, 1) ?? sectors;
        return new(DiscImageFormats.CloneCd, [ccdPath, imagePath], null, null,
            builder.Sheet(Path.GetFileName(imagePath), []), builder.Segments, builder.Position,
            new RawDiscDataTrack(mode, dataSectors, $"the CloneCD .ccd {name}"), new Dictionary<int, string>());
    }

    private static T Once<T>(T? existing, T value, string name, int point) where T : struct =>
        existing is null ? value : throw new InvalidDataException($"{name} gives point 0x{point:X2} twice.");

    // The sections by upper-case name, each with its keys by upper-case name.
    private static Dictionary<string, Dictionary<string, string>> Sections(byte[] bytes, string name)
    {
        var sections = new Dictionary<string, Dictionary<string, string>>(StringComparer.Ordinal);
        Dictionary<string, string>? current = null;
        var lines = Encoding.Latin1.GetString(bytes).Split('\n');
        for (var number = 1; number <= lines.Length; number++)
        {
            var line = lines[number - 1].Trim();
            if (line.Length == 0) continue;
            if (line[0] == '[' && line[^1] == ']')
            {
                var section = string.Join(' ', line[1..^1].Split(' ', StringSplitOptions.RemoveEmptyEntries)).ToUpperInvariant();
                if (NumberedSection().Match(section) is { Success: true } numbered)
                    section = $"{numbered.Groups["kind"].Value} {int.Parse(numbered.Groups["number"].Value, CultureInfo.InvariantCulture)}";
                else if (section is not ("CLONECD" or "DISC" or "CDTEXT"))
                    throw new InvalidDataException($"{name} line {number} opens section [{CueBinSheet.Truncate(line[1..^1])}], which is not read.");
                if (!sections.TryAdd(section, current = new(StringComparer.Ordinal)))
                    throw new InvalidDataException($"{name} has section [{section}] twice.");
                continue;
            }
            var equals = line.IndexOf('=');
            if (current is null || equals <= 0)
                throw new InvalidDataException($"{name} line {number} is neither a [section] nor a key=value line inside one.");
            var key = string.Join(' ', line[..equals].Split(' ', StringSplitOptions.RemoveEmptyEntries)).ToUpperInvariant();
            if (!current.TryAdd(key, line[(equals + 1)..].Trim()))
                throw new InvalidDataException($"{name} line {number} gives {key} a second time in its section.");
        }
        return sections;
    }

    private static int Number(Dictionary<string, Dictionary<string, string>> sections, string section, string key, string name) =>
        Optional(sections, section, key, name)
        ?? throw new InvalidDataException($"{name} gives no {key} in [{section}].");

    private static int? Optional(Dictionary<string, Dictionary<string, string>> sections, string section, string key, string name)
    {
        if (!sections.TryGetValue(section, out var keys)) return null;
        if (!keys.TryGetValue(key.ToUpperInvariant(), out var value)) return null;
        var parsed = Parse(value, name, section, key);
        return parsed is >= int.MinValue and <= int.MaxValue
            ? (int)parsed : throw new InvalidDataException($"{name} [{section}] {key} is out of range.");
    }

    // A decimal value, or a hexadecimal one written with 0x.
    private static long Parse(string value, string name, string section, string key)
    {
        var parsed = value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            ? long.TryParse(value[2..], NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture, out var hex) ? hex : (long?)null
            : long.TryParse(value, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var number) ? number : null;
        return parsed ?? throw new InvalidDataException(
            $"{name} [{section}] {key}={CueBinSheet.Truncate(value)} is not a number.");
    }
}
