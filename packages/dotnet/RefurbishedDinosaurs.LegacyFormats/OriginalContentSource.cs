using System.Buffers.Binary;
using System.Text;
using RefurbishedDinosaurs.Core.IO;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>A file in an <see cref="OriginalContentSource"/>.</summary>
/// <param name="Path">Path from the source's root with <c>/</c> separators.</param>
/// <param name="Size">The file's size in bytes.</param>
public sealed record ContentSourceEntry(string Path, long Size);

/// <summary>The kinds of <see cref="OriginalContentSource"/>, as <see cref="OriginalContentSource.Kind"/> gives them.</summary>
public static class ContentSourceKinds
{
    /// <summary>An installed directory.</summary>
    public const string Directory = "directory";
    /// <summary>An ISO 9660 image with 2048-byte sectors, such as a <c>.iso</c> file.</summary>
    public const string Iso9660 = "iso9660";
    /// <summary>A <c>.cue</c> sheet and the raw <c>.bin</c> image with 2352-byte sectors it describes.</summary>
    public const string CueBin = "cue-bin";
    /// <summary>
    /// An InstallShield cabinet set of major version 0, 5 or 6, opened from its <c>dataN.hdr</c> header (or a
    /// <c>dataN.cab</c> that holds the header) with its <c>dataN.cab</c> volumes beside it.
    /// </summary>
    public const string InstallShieldCabinet = "installshield-cabinet";
    /// <summary>
    /// An InstallShield 3 archive: one file, often named <c>_SETUP.1</c> or <c>*.Z</c>, holding the
    /// directory and file tables and the members' data. It is unsplit, or a split archive of one part.
    /// </summary>
    public const string InstallShieldArchive = "installshield3-archive";

    /// <summary>Whether <paramref name="kind"/> is one of the kinds above.</summary>
    public static bool IsSupported(string kind) => kind is Directory or Iso9660 or CueBin or InstallShieldCabinet or InstallShieldArchive;
}

/// <summary>
/// Read access to the user's original files, from an installed directory, an ISO 9660 image, a
/// cue/bin raw disc image, an InstallShield cabinet set or an InstallShield 3 archive, behind one interface. Paths are relative with <c>/</c> or <c>\</c>
/// separators and match ignoring case.
/// </summary>
public abstract class OriginalContentSource : IDisposable
{
    /// <summary>One of <see cref="ContentSourceKinds"/>.</summary>
    public abstract string Kind { get; }
    /// <summary>
    /// The ISO 9660 primary volume descriptor's volume identifier without its trailing spaces and NULs,
    /// or <see langword="null"/> for a source with no volume or an identifier that is all padding.
    /// Each of the 32 bytes reads as the Latin-1 (ISO-8859-1) character of the same value, so a byte
    /// above 0x7F gives the character U+0080 to U+00FF and two identifiers that differ in a byte
    /// before their trailing padding give different labels.
    /// </summary>
    public abstract string? Label { get; }
    /// <summary>The cue sheet of a <see cref="ContentSourceKinds.CueBin"/> source, otherwise <see langword="null"/>.</summary>
    public virtual CueBinSheet? Cue => null;
    /// <summary>
    /// The full path of the cue sheet file a <see cref="ContentSourceKinds.CueBin"/> source was
    /// opened from, as <see cref="OpenCueBin"/> chose it, otherwise <see langword="null"/>. The source
    /// reads the file once, when it opens. Reading the path again can see a file replaced since then,
    /// so hash <see cref="CueSheetBytes"/> to record the sheet <see cref="Cue"/> was parsed from.
    /// </summary>
    public virtual string? CuePath => null;
    /// <summary>
    /// The bytes of the cue sheet file that <see cref="OpenCueBin"/> read and parsed into
    /// <see cref="Cue"/>, for a <see cref="ContentSourceKinds.CueBin"/> source, otherwise
    /// <see langword="null"/>. Hash them with <c>FileFingerprint.Xxh3(source.CueSheetBytes.Value.Span)</c>
    /// to record the sheet as a role file.
    /// </summary>
    public virtual ReadOnlyMemory<byte>? CueSheetBytes => null;
    /// <summary>
    /// The full path of the <c>.bin</c> image a <see cref="ContentSourceKinds.CueBin"/> source reads,
    /// as <see cref="OpenCueBin"/> chose it, otherwise <see langword="null"/>. The source records the
    /// file's length and last-write time when it opens and checks them each time it opens the file
    /// again, so a rewrite that keeps both is not detected. A stream opened from this path is not checked, so read the image through
    /// <see cref="OpenBin"/>. To record an audio track's fingerprint from the image the source checked,
    /// pass <see cref="OpenBin"/> with the track's <see cref="CueBinSheet.TrackExtent"/> to
    /// <see cref="CddaTrackFingerprints.RecordAsync(Stream, CueBinTrackExtent, int, long, int, CancellationToken)"/>:
    /// the overload that takes a path resolves the files again, and given <see cref="CuePath"/> for a
    /// source opened from a <c>.bin</c> it can choose another BIN.
    /// </summary>
    public virtual string? BinPath => null;
    /// <summary>
    /// Opens the raw image of a <see cref="ContentSourceKinds.CueBin"/> source, the file at
    /// <see cref="BinPath"/>, as a read-only seekable stream of 2352-byte sectors. The stream reads
    /// the whole file.
    /// </summary>
    /// <exception cref="NotSupportedException">The source is not a cue/bin image.</exception>
    /// <exception cref="IOException">
    /// The image's length or last-write time differs from when the source was opened, or the file
    /// cannot be opened.
    /// </exception>
    public virtual Stream OpenBin() =>
        throw new NotSupportedException($"A {Kind} source has no BIN image.");
    /// <summary>
    /// Every file, sorted by path ignoring case. The sources this class opens list only paths
    /// <see cref="PortableAssetPath.Relative"/> accepts: a file whose path it rejects makes opening
    /// the source throw <see cref="InvalidDataException"/> naming the file and the rule its path breaks.
    /// </summary>
    public abstract IReadOnlyList<ContentSourceEntry> Files { get; }
    /// <summary>Looks up a file.</summary>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>.</exception>
    public abstract bool TryGetFile(string relativePath, out ContentSourceEntry? entry);
    /// <summary>Opens a file as a read-only seekable stream.</summary>
    /// <exception cref="FileNotFoundException">The source has no such file.</exception>
    /// <exception cref="InvalidDataException"><paramref name="relativePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>.</exception>
    public abstract Stream OpenRead(string relativePath);
    /// <summary>
    /// The volume space size the ISO 9660 primary volume descriptor declares, in 2048-byte logical
    /// blocks, or <see langword="null"/> for a source with no ISO 9660 volume, such as a directory.
    /// </summary>
    public virtual long? VolumeBlocks => null;
    /// <summary>
    /// Opens the ISO 9660 volume as a read-only seekable stream of <see cref="VolumeBlocks"/> times
    /// 2048 bytes from logical block 0: the image's bytes for <see cref="ContentSourceKinds.Iso9660"/>,
    /// and the user data of the data track's sectors for <see cref="ContentSourceKinds.CueBin"/>.
    /// Bytes past the declared volume, such as padding at the end of an image, are left out, so an
    /// <c>.iso</c> image and a cue/bin image of one disc read the same bytes. Hash it with
    /// <c>FileFingerprint.Xxh3Async</c> to record <c>AssetManifest.VolumeXxh3</c>.
    /// </summary>
    /// <exception cref="NotSupportedException">The source has no ISO 9660 volume.</exception>
    public virtual Stream OpenVolume() =>
        throw new NotSupportedException($"A {Kind} source has no ISO 9660 volume.");
    /// <summary>Releases the source. Streams already opened stay usable.</summary>
    public abstract void Dispose();

    /// <summary>
    /// Opens <paramref name="path"/> as a directory source when it is a directory, as a cue/bin image
    /// (see <see cref="OpenCueBin"/>) when it is a <c>.cue</c> file, as an InstallShield cabinet set
    /// (see <see cref="OpenInstallShieldCabinet(string, InstallShieldCabinetLimits?, InstallShieldCompressedFormat)"/>) with the
    /// default limits when it is a <c>.hdr</c> file, as an InstallShield 3 archive (see
    /// <see cref="OpenInstallShieldArchive(string, InstallShieldArchiveLimits?)"/>) with the default
    /// limits when any other file starts with that format's signature, and as an ISO 9660 image (see
    /// <see cref="OpenIso9660(string)"/>) otherwise. A cue sheet with another extension is not
    /// recognized here: open it with <see cref="OpenCueBin"/> or with the
    /// <see cref="ContentSourceKinds.CueBin"/> kind.
    /// </summary>
    /// <exception cref="NotSupportedException">
    /// The cabinet set's InstallShield major version is not 0, 5 or 6, or the InstallShield 3 archive
    /// is split into parts that are not all in this file or names another part number or count.
    /// </exception>
    /// <exception cref="FileNotFoundException">Nothing exists at <paramref name="path"/>.</exception>
    /// <exception cref="InvalidDataException">The image is not a valid volume of its kind.</exception>
    public static OriginalContentSource Open(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (Directory.Exists(path)) return new DirectoryContentSource(path);
        if (File.Exists(path))
        {
            var extension = Path.GetExtension(path);
            if (extension.Equals(".cue", StringComparison.OrdinalIgnoreCase)) return OpenCueBin(path);
            if (extension.Equals(".hdr", StringComparison.OrdinalIgnoreCase)) return OpenInstallShieldCabinet(path);
            if (InstallShieldArchiveOpener.HasSignature(path)) return OpenInstallShieldArchive(path);
            return OpenIso9660(path);
        }
        throw new FileNotFoundException("Original-content source does not exist.", path);
    }

    /// <summary>Opens <paramref name="path"/> as the kind of source a manifest declares.</summary>
    /// <param name="path">The directory, image or cue/bin path.</param>
    /// <param name="kind">One of <see cref="ContentSourceKinds"/>.</param>
    /// <exception cref="InvalidDataException"><paramref name="kind"/> is unsupported, or the image is not a valid volume of that kind.</exception>
    /// <exception cref="FileNotFoundException">Nothing exists at <paramref name="path"/>.</exception>
    /// <exception cref="NotSupportedException">
    /// The cabinet set's InstallShield major version is not 0, 5 or 6, or the InstallShield 3 archive
    /// is split into parts that are not all in this file or names another part number or count.
    /// </exception>
    public static OriginalContentSource Open(string path, string kind) => kind switch
    {
        ContentSourceKinds.Directory => OpenDirectory(path),
        ContentSourceKinds.Iso9660 => OpenIso9660(path),
        ContentSourceKinds.CueBin => OpenCueBin(path),
        ContentSourceKinds.InstallShieldCabinet => OpenInstallShieldCabinet(path),
        ContentSourceKinds.InstallShieldArchive => OpenInstallShieldArchive(path),
        _ => throw new InvalidDataException($"Unsupported original-content source kind '{kind}'.")
    };

    /// <summary>
    /// Opens an installed directory. Reparse points are skipped. Every file's path below the directory
    /// must pass <see cref="PortableAssetPath.Relative"/>, so the source lists only files it can open
    /// by the listed path and that copy out under the same name on every host. A file whose name
    /// holds a <c>\</c>, which Linux and macOS allow, is rejected too, since <c>\</c> reads as a
    /// separator in a source path.
    /// </summary>
    /// <exception cref="FileNotFoundException">The directory does not exist.</exception>
    /// <exception cref="InvalidDataException">
    /// A file's path is not portable. The message names the file and the rule its path breaks.
    /// </exception>
    public static OriginalContentSource OpenDirectory(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        if (!Directory.Exists(path)) throw new FileNotFoundException("Source directory does not exist.", path);
        return new DirectoryContentSource(path);
    }

    /// <summary>
    /// Opens an ISO 9660 image with 2048-byte sectors. It is checked when opened: block size, volume size
    /// against the file, both-endian fields agreeing, and every directory and file extent inside the
    /// volume. Each directory and file name reads byte for byte as Latin-1 (ISO-8859-1) and loses its
    /// <c>;</c> version suffix and trailing dots, unless that would give two entries of one directory the
    /// same name ignoring case, as <c>README.;1</c> and <c>README.;2</c> would: those keep their whole identifiers. The
    /// name must pass <see cref="PortableAssetPath.Relative"/> as one component, or opening throws
    /// <see cref="InvalidDataException"/> naming it. The source records the image's length and last-write time here, and every later read
    /// of the image through it (<see cref="OpenRead"/> and <see cref="OpenVolume"/>) compares them with
    /// the file when it opens it and fails with an <see cref="IOException"/> when either has changed.
    /// A rewrite that keeps both the length and the last-write time is not detected.
    /// </summary>
    /// <exception cref="FileNotFoundException">The file does not exist.</exception>
    /// <exception cref="IOException">The image changed while the source was being opened.</exception>
    /// <exception cref="InvalidDataException">The image is not a valid ISO 9660 volume.</exception>
    public static OriginalContentSource OpenIso9660(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath)) throw new FileNotFoundException("ISO image does not exist.", fullPath);
        // Recorded before the volume is checked, so the source describes only the image it checked.
        var image = ImageSnapshot.Take(fullPath);
        return new Iso9660ContentSource(image.OpenBuffered, ContentSourceKinds.Iso9660, null);
    }

    /// <summary>
    /// Opens an ISO 9660 image with 2048-byte sectors from a stream, such as an image built in memory,
    /// as a <see cref="ContentSourceKinds.Iso9660"/> source. Logical block 0 is at position 0 of the
    /// stream, and the image is checked as <see cref="OpenIso9660(string)"/> describes. The caller
    /// keeps ownership: the source never disposes <paramref name="image"/>, which must stay open and
    /// unchanged while the source or a stream opened from it is in use. Streams opened from the
    /// source each keep their own position and may be read at the same time; each read seeks
    /// <paramref name="image"/> under a lock that every source opened over <paramref name="image"/>
    /// shares, so read <paramref name="image"/> only through these sources.
    /// </summary>
    /// <param name="image">A readable, seekable stream holding the image.</param>
    /// <exception cref="ArgumentNullException"><paramref name="image"/> is null.</exception>
    /// <exception cref="ArgumentException"><paramref name="image"/> cannot be read or cannot seek.</exception>
    /// <exception cref="InvalidDataException">The image is not a valid ISO 9660 volume.</exception>
    public static OriginalContentSource OpenIso9660(Stream image)
    {
        ArgumentNullException.ThrowIfNull(image);
        if (!image.CanRead || !image.CanSeek)
            throw new ArgumentException("The ISO image stream must be readable and seekable.", nameof(image));
        var gate = SharedStreamView.GateFor(image);
        return new Iso9660ContentSource(() => new SharedStreamView(image, gate), ContentSourceKinds.Iso9660, null);
    }

    /// <summary>
    /// Opens the ISO 9660 volume on the data track of a cue/bin raw disc image. <paramref name="path"/>
    /// is the <c>.bin</c> file, the directory holding a <c>.cue</c> and the image, or any other file,
    /// which is read as the cue sheet whatever its extension. The image is the file the sheet's first
    /// <c>FILE</c> names, whatever its extension, else the <c>.bin</c> with the sheet's name, else the
    /// only <c>.bin</c> there; for a <c>.bin</c> input the sheet is the <c>.cue</c> with the same name,
    /// else the only one. A sheet found for a given <c>.bin</c> must not name a different BIN that is
    /// present. The sheet may name later <c>FILE</c> entries holding only audio tracks, such as one
    /// compressed audio file per track; they are not opened, need not exist, and the source reads only
    /// the image.
    /// The returned source gives the chosen files as <see cref="CuePath"/> and <see cref="BinPath"/>,
    /// and the bytes of the sheet it parsed as <see cref="CueSheetBytes"/>. It records the BIN's length
    /// and last-write time here. Every later read of the BIN through the source
    /// (<see cref="OpenRead"/>, <see cref="OpenVolume"/>, <see cref="OpenBin"/> and the audio checks
    /// of <c>AssetVerifier</c>) compares them with the file when it opens it, and fails with an
    /// <see cref="IOException"/> when either has changed. A rewrite that keeps both the length and the
    /// last-write time is not detected.
    /// The sheet is checked as <see cref="CueBinSheet.Parse"/> and <see cref="CueBinSheet.ValidateBin"/>
    /// describe, the data track ends where the image's second track's pregap or audio begins, or at
    /// the end of the image when the image holds no second track, and the volume is
    /// checked as <see cref="OpenIso9660(string)"/> describes. Every raw sector read is checked against
    /// the type the sheet declares for the data track: for <c>MODE1/2352</c> the sync pattern and mode
    /// byte 1, and for <c>MODE2/2352</c> the sync pattern, mode byte 2, and a CD-XA subheader whose two
    /// copies agree and whose submode marks Form 1. The user data is the 2048 bytes after the header
    /// for MODE1 and after the subheader for MODE2 Form 1, so one disc reads the same files and volume
    /// bytes in either layout. A sector that fails a check, such as a Form 2 sector of a file stored
    /// as interleaved audio or video, throws <see cref="InvalidDataException"/> naming the sector when
    /// it is read: while opening for the descriptors and directories, and from the stream
    /// <see cref="OpenRead"/> or <see cref="OpenVolume"/> returns for a file's or the volume's sectors.
    /// The one exception is a Form 2 sector whose 2324 data bytes are all zero, such as padding a
    /// CD-XA master leaves inside the volume space: <see cref="OpenVolume"/>, and the reads of the
    /// descriptors and directories while opening, read it as 2048 zero bytes, as a MODE1 image of the
    /// disc holds there, while <see cref="OpenRead"/> still throws when a file covers it. Such a sector
    /// inside a directory's extent holds no records, and one where a volume descriptor is expected
    /// fails as an invalid descriptor.
    /// The EDC, the ECC and the address in each sector's header are not checked.
    /// </summary>
    /// <exception cref="FileNotFoundException">Nothing exists at <paramref name="path"/>.</exception>
    /// <exception cref="IOException">The BIN changed while the source was being opened.</exception>
    /// <exception cref="InvalidDataException">
    /// The sheet, image or volume is not valid, a file given as the sheet is not a cue sheet, a sheet
    /// or BIN cannot be found next to the other, or the files are ambiguous.
    /// </exception>
    public static OriginalContentSource OpenCueBin(string path)
    {
        var (cuePath, binPath, sheet, cueBytes) = CueBinSheet.Resolve(path);
        // The sheet is checked against the recorded length, and every read below compares the file
        // with this record, so the source describes only the BIN it checked.
        var bin = ImageSnapshot.Take(binPath);
        sheet.ValidateBinLength(bin.Length);
        var sectors = bin.Length / CueBinSheet.RawSectorSize;
        long dataSectors = sheet.DataTrackSectors ?? sectors;
        if (dataSectors <= 16 || dataSectors > sectors)
            throw new InvalidDataException("Cue data track does not hold an ISO 9660 volume inside the BIN image.");
        return new Iso9660ContentSource(
            () => new RawDataTrackUserDataStream(bin.OpenBuffered(), dataSectors, sheet.DataTrackMode),
            ContentSourceKinds.CueBin, new CueBinFiles(sheet, cuePath, cueBytes, bin),
            openVolume: () => new RawDataTrackUserDataStream(
                bin.OpenBuffered(), dataSectors, sheet.DataTrackMode, emptyForm2AsZeros: true));
    }

    /// <summary>
    /// Opens an InstallShield cabinet set of major version 0, 5 or 6 from a file. <paramref name="path"/> is the
    /// <c>dataN.hdr</c> header, or a <c>dataN.cab</c> that holds the header. A <c>.cab</c> is read as
    /// the header only as far as the header's structures reach, within
    /// <see cref="InstallShieldCabinetLimits.MaximumHeaderBytes"/>. The volumes are the files
    /// in the same directory named like the header up to its first dot or digit, then the volume
    /// number and <c>.cab</c>, matched ignoring case: <c>data1.cab</c>, <c>data2.cab</c> and so on. A
    /// <c>data1.cab</c> that holds the header is therefore also read as volume 1.
    /// The set is checked when opened, as <see cref="InstallShieldCabinetSource"/> describes.
    /// </summary>
    /// <param name="path">The header file.</param>
    /// <param name="limits">The bounds to apply, or <see langword="null"/> for <see cref="InstallShieldCabinetLimits.Default"/>.</param>
    /// <param name="compressedFormat">
    /// How the set's compressed members store their deflate data. The header does not record it, so
    /// the caller chooses; the default is what Unshield reads without <c>-O</c>.
    /// </param>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="compressedFormat"/> is not a defined value.</exception>
    /// <exception cref="FileNotFoundException">The header or a volume a member needs does not exist.</exception>
    /// <exception cref="InvalidDataException">
    /// The header or a volume is truncated or malformed, a member's path is not accepted by <see cref="PortableAssetPath.Relative"/>, or the set
    /// exceeds <paramref name="limits"/>.
    /// </exception>
    /// <exception cref="NotSupportedException">The header's InstallShield major version is not 0, 5 or 6.</exception>
    public static InstallShieldCabinetSource OpenInstallShieldCabinet(
        string path, InstallShieldCabinetLimits? limits = null,
        InstallShieldCompressedFormat compressedFormat = InstallShieldCompressedFormat.LengthPrefixedChunks)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        limits ??= InstallShieldCabinetLimits.Default;
        limits.Validate();
        ValidateCompressedFormat(compressedFormat);
        return InstallShieldCabinetOpener.FromDirectory(path, limits, compressedFormat);
    }

    /// <summary>
    /// Opens an InstallShield cabinet set of major version 0, 5 or 6 held in another source, such as the ISO 9660 volume of
    /// a disc image. Volumes are looked up in <paramref name="container"/> as
    /// <see cref="OpenInstallShieldCabinet(string, InstallShieldCabinetLimits?, InstallShieldCompressedFormat)"/> describes, and are
    /// opened through it whenever a member is read, so keep <paramref name="container"/> usable while
    /// the cabinet set is in use.
    /// </summary>
    /// <param name="container">The source holding the header and its volumes.</param>
    /// <param name="headerPath">The header's path in <paramref name="container"/>.</param>
    /// <param name="limits">The bounds to apply, or <see langword="null"/> for <see cref="InstallShieldCabinetLimits.Default"/>.</param>
    /// <param name="compressedFormat">
    /// How the set's compressed members store their deflate data. The header does not record it, so
    /// the caller chooses; the default is what Unshield reads without <c>-O</c>.
    /// </param>
    /// <exception cref="ArgumentOutOfRangeException"><paramref name="compressedFormat"/> is not a defined value.</exception>
    /// <exception cref="FileNotFoundException">The header or a volume a member needs is not in <paramref name="container"/>.</exception>
    /// <exception cref="InvalidDataException">
    /// <paramref name="headerPath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>, the
    /// header or a volume is truncated or malformed, a member's path is not accepted by <see cref="PortableAssetPath.Relative"/>, or the set
    /// exceeds <paramref name="limits"/>.
    /// </exception>
    /// <exception cref="NotSupportedException">The header's InstallShield major version is not 0, 5 or 6.</exception>
    public static InstallShieldCabinetSource OpenInstallShieldCabinet(
        OriginalContentSource container, string headerPath, InstallShieldCabinetLimits? limits = null,
        InstallShieldCompressedFormat compressedFormat = InstallShieldCompressedFormat.LengthPrefixedChunks)
    {
        ArgumentNullException.ThrowIfNull(container);
        limits ??= InstallShieldCabinetLimits.Default;
        limits.Validate();
        ValidateCompressedFormat(compressedFormat);
        return InstallShieldCabinetOpener.FromSource(container, headerPath, limits, compressedFormat);
    }

    private static void ValidateCompressedFormat(InstallShieldCompressedFormat compressedFormat)
    {
        if (!Enum.IsDefined(compressedFormat))
            throw new ArgumentOutOfRangeException(nameof(compressedFormat), compressedFormat,
                "The compressed format is not a defined InstallShieldCompressedFormat value.");
    }

    /// <summary>
    /// Opens an InstallShield 3 archive from a file, such as a disc's <c>_SETUP.1</c>. The archive is
    /// checked when opened, as <see cref="InstallShieldArchiveSource"/> describes, and the file is
    /// opened again whenever a member is read.
    /// </summary>
    /// <param name="path">The archive file.</param>
    /// <param name="limits">The bounds to apply, or <see langword="null"/> for <see cref="InstallShieldArchiveLimits.Default"/>.</param>
    /// <exception cref="FileNotFoundException">The archive does not exist.</exception>
    /// <exception cref="InvalidDataException">
    /// The file is not an InstallShield 3 archive, its header or tables are truncated or malformed, a
    /// member's path is not accepted by <see cref="PortableAssetPath.Relative"/> or its data lies outside the archive, a member of a split archive
    /// of one part names another part, or the archive exceeds
    /// <paramref name="limits"/>.
    /// </exception>
    /// <exception cref="NotSupportedException">
    /// The archive is one part of a split archive of more than one part, its header sets a split flag
    /// and names a part number or part count other than 1, it holds an entry that spans parts, or it
    /// has a header of another size.
    /// </exception>
    public static InstallShieldArchiveSource OpenInstallShieldArchive(string path, InstallShieldArchiveLimits? limits = null)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        limits ??= InstallShieldArchiveLimits.Default;
        limits.Validate();
        return InstallShieldArchiveOpener.FromFile(path, limits);
    }

    /// <summary>
    /// Opens an InstallShield 3 archive held in another source, such as the ISO 9660 volume of a disc
    /// image. The archive is opened through <paramref name="container"/> whenever a member is read, so
    /// keep <paramref name="container"/> usable while the archive is in use.
    /// </summary>
    /// <param name="container">The source holding the archive.</param>
    /// <param name="archivePath">The archive's path in <paramref name="container"/>.</param>
    /// <param name="limits">The bounds to apply, or <see langword="null"/> for <see cref="InstallShieldArchiveLimits.Default"/>.</param>
    /// <exception cref="FileNotFoundException">The archive is not in <paramref name="container"/>.</exception>
    /// <exception cref="InvalidDataException">
    /// <paramref name="archivePath"/> is not accepted by <see cref="PortableAssetPath.Relative"/>, or
    /// the archive is not valid as <see cref="OpenInstallShieldArchive(string, InstallShieldArchiveLimits?)"/> describes.
    /// </exception>
    /// <exception cref="NotSupportedException">
    /// The archive is one part of a split archive of more than one part, its header sets a split flag
    /// and names a part number or part count other than 1, it holds an entry that spans parts, or it
    /// has a header of another size.
    /// </exception>
    public static InstallShieldArchiveSource OpenInstallShieldArchive(
        OriginalContentSource container, string archivePath, InstallShieldArchiveLimits? limits = null)
    {
        ArgumentNullException.ThrowIfNull(container);
        limits ??= InstallShieldArchiveLimits.Default;
        limits.Validate();
        return InstallShieldArchiveOpener.FromSource(container, archivePath, limits);
    }

    /// <summary>
    /// Opens an InstallShield 3 archive from a stream, such as an archive carved out of a
    /// self-extracting program. The archive's first byte is at position 0 of the stream, and its
    /// offsets count from there. The caller keeps ownership: the source never disposes
    /// <paramref name="archive"/>, which must stay open and unchanged while the source or a stream
    /// opened from it is in use. Member streams each keep their own position and may be read at the
    /// same time; each read seeks <paramref name="archive"/> under a lock that every source opened over
    /// it shares, so read <paramref name="archive"/> only through these sources.
    /// </summary>
    /// <param name="archive">A readable, seekable stream holding the archive.</param>
    /// <param name="limits">The bounds to apply, or <see langword="null"/> for <see cref="InstallShieldArchiveLimits.Default"/>.</param>
    /// <exception cref="ArgumentNullException"><paramref name="archive"/> is null.</exception>
    /// <exception cref="ArgumentException"><paramref name="archive"/> cannot be read or cannot seek.</exception>
    /// <exception cref="InvalidDataException">
    /// The archive is not valid as <see cref="OpenInstallShieldArchive(string, InstallShieldArchiveLimits?)"/> describes.
    /// </exception>
    /// <exception cref="NotSupportedException">
    /// The archive is one part of a split archive of more than one part, its header sets a split flag
    /// and names a part number or part count other than 1, it holds an entry that spans parts, or it
    /// has a header of another size.
    /// </exception>
    public static InstallShieldArchiveSource OpenInstallShieldArchive(Stream archive, InstallShieldArchiveLimits? limits = null)
    {
        ArgumentNullException.ThrowIfNull(archive);
        if (!archive.CanRead || !archive.CanSeek)
            throw new ArgumentException("The InstallShield 3 archive stream must be readable and seekable.", nameof(archive));
        limits ??= InstallShieldArchiveLimits.Default;
        limits.Validate();
        return InstallShieldArchiveOpener.FromStream(archive, limits);
    }
}

internal sealed class DirectoryContentSource : OriginalContentSource
{
    private readonly string root;
    private readonly Dictionary<string, (ContentSourceEntry Entry, string FullPath)> files;

    public DirectoryContentSource(string path)
    {
        root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(path));
        files = new Dictionary<string, (ContentSourceEntry, string)>(StringComparer.OrdinalIgnoreCase);
        var options = new EnumerationOptions
        {
            RecurseSubdirectories = true,
            IgnoreInaccessible = false,
            AttributesToSkip = FileAttributes.ReparsePoint
        };
        foreach (var fullPath in Directory.EnumerateFiles(root, "*", options))
        {
            var relative = Path.GetRelativePath(root, fullPath);
            // On Linux and macOS a '\' is part of a name, and would read as a separator below.
            if (Path.DirectorySeparatorChar == '/' && relative.Contains('\\'))
                throw new InvalidDataException(
                    $"Source file {AssetVerifier.JsonString(relative)} has a name that holds '\\', which Windows does not allow in a file name.");
            try
            {
                relative = PortableAssetPath.Relative(relative);
            }
            catch (InvalidDataException exception)
            {
                throw new InvalidDataException($"Source file has a path that is not portable. {exception.Message}", exception);
            }
            var entry = new ContentSourceEntry(relative, new FileInfo(fullPath).Length);
            if (!files.TryAdd(relative, (entry, fullPath)))
                throw new InvalidDataException($"Source contains duplicate path '{relative}'.");
        }
        Files = files.Values.Select(value => value.Entry)
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    public override string Kind => ContentSourceKinds.Directory;
    public override string? Label => null;
    public override IReadOnlyList<ContentSourceEntry> Files { get; }

    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
    {
        if (files.TryGetValue(PortableAssetPath.Relative(relativePath), out var value))
        {
            entry = value.Entry;
            return true;
        }
        entry = null;
        return false;
    }

    public override Stream OpenRead(string relativePath)
    {
        if (!files.TryGetValue(PortableAssetPath.Relative(relativePath), out var value))
            throw new FileNotFoundException("Source file was not found.", relativePath);
        return new FileStream(value.FullPath, FileMode.Open, FileAccess.Read, FileShare.Read);
    }

    public override void Dispose() { }
}

internal sealed class Iso9660ContentSource : OriginalContentSource
{
    private const int SectorSize = 2048;
    private const int MaximumDescriptors = 240;
    private const int MaximumDirectoryDepth = 32;
    private const int MaximumEntries = 100_000;
    private const uint MaximumDirectoryBytes = 64 * 1024 * 1024;

    private readonly Func<Stream> openImage;
    private readonly Func<Stream> openVolume;
    private readonly CueBinFiles? cueBin;
    private readonly long volumeLength;
    private readonly Dictionary<string, IsoEntry> files = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<IsoFile>? listing;

    // openImage returns a new seekable stream of 2048-byte sectors each time. A cue/bin source passes
    // the files it chose; its audio tracks are read from the BIN. When listing is given, every file
    // is added to it in directory order, depth first, as Iso9660 lists them. openVolume, when given,
    // opens the same sectors for OpenVolume and for the descriptors and directories read here, for a
    // cue/bin source whose volume reads accept sectors that file reads reject.
    public Iso9660ContentSource(
        Func<Stream> openImage, string kind, CueBinFiles? cueBin, List<IsoFile>? listing = null,
        Func<Stream>? openVolume = null)
    {
        this.openImage = openImage;
        this.openVolume = openVolume ?? openImage;
        Kind = kind;
        this.cueBin = cueBin;
        this.listing = listing;
        // The descriptors and directories are read as OpenVolume reads them, so a sector the volume
        // reads as zeros reads as zeros here too. A zero descriptor sector fails the descriptor check,
        // and a zero directory sector holds no records, so nothing is listed that the disc lacks.
        using var stream = this.openVolume();
        var imageLength = stream.Length;
        if (imageLength < 18L * SectorSize)
            throw new InvalidDataException("Source is too small to be an ISO9660 image.");

        var descriptor = FindPrimaryVolumeDescriptor(stream);
        var blockSize = ReadBothEndianUInt16(descriptor, 128, "logical block size");
        if (blockSize != SectorSize)
            throw new InvalidDataException("ISO9660 logical block size is unsupported.");
        var volumeSectors = ReadBothEndianUInt32(descriptor, 80, "volume space size");
        volumeLength = checked((long)volumeSectors * SectorSize);
        if (volumeSectors < 18 || volumeLength > imageLength)
            throw new InvalidDataException("ISO9660 declared volume exceeds the image.");
        Label = DecodeIdentifier(descriptor.AsSpan(40, 32));
        var root = ParseDirectoryRecord(descriptor, 156, descriptor[156]);
        if (!root.IsDirectory) throw new InvalidDataException("ISO9660 root record is not a directory.");
        ReadDirectory(stream, root, string.Empty, 0, new HashSet<(uint, uint)>());
        Files = files.Values.Select(value => value.Entry)
            .OrderBy(entry => entry.Path, StringComparer.OrdinalIgnoreCase).ToArray();
    }

    public override string Kind { get; }
    public override string? Label { get; }
    public override CueBinSheet? Cue => cueBin?.Sheet;
    public override string? CuePath => cueBin?.CuePath;
    public override ReadOnlyMemory<byte>? CueSheetBytes =>
        cueBin is null ? default(ReadOnlyMemory<byte>?) : cueBin.CueBytes;
    public override string? BinPath => cueBin?.Bin.Path;

    public override Stream OpenBin() => cueBin is null ? base.OpenBin() : cueBin.Bin.Open();
    public override IReadOnlyList<ContentSourceEntry> Files { get; }

    public override bool TryGetFile(string relativePath, out ContentSourceEntry? entry)
    {
        if (files.TryGetValue(PortableAssetPath.Relative(relativePath), out var value))
        {
            entry = value.Entry;
            return true;
        }
        entry = null;
        return false;
    }

    public override Stream OpenRead(string relativePath)
    {
        if (!files.TryGetValue(PortableAssetPath.Relative(relativePath), out var value))
            throw new FileNotFoundException("Source file was not found in the ISO image.", relativePath);
        return new ExtentReadStream(openImage(), checked((long)value.Extent * SectorSize), value.Entry.Size);
    }

    public override long? VolumeBlocks => volumeLength / SectorSize;

    public override Stream OpenVolume() => new ExtentReadStream(openVolume(), 0, volumeLength);

    public override void Dispose() { }

    private static byte[] FindPrimaryVolumeDescriptor(Stream stream)
    {
        var buffer = new byte[SectorSize];
        for (var index = 16; index < 16 + MaximumDescriptors; index++)
        {
            stream.Position = checked((long)index * SectorSize);
            ReadExactly(stream, buffer);
            if (!buffer.AsSpan(1, 5).SequenceEqual("CD001"u8) || buffer[6] != 1)
                throw new InvalidDataException($"Invalid ISO9660 volume descriptor at sector {index}.");
            if (buffer[0] == 1) return buffer.ToArray();
            if (buffer[0] == 255) break;
        }
        throw new InvalidDataException("ISO9660 primary volume descriptor was not found.");
    }

    private void ReadDirectory(
        Stream stream, DirectoryRecord directory, string parent, int depth,
        HashSet<(uint Extent, uint Length)> visited)
    {
        if (depth > MaximumDirectoryDepth)
            throw new InvalidDataException("ISO9660 directory depth exceeds the safety limit.");
        if (directory.DataLength > MaximumDirectoryBytes)
            throw new InvalidDataException("ISO9660 directory exceeds the safety limit.");
        ValidateExtent(directory.Extent, directory.DataLength);
        if (!visited.Add((directory.Extent, directory.DataLength))) return;

        var data = new byte[checked((int)directory.DataLength)];
        stream.Position = checked((long)directory.Extent * SectorSize);
        ReadExactly(stream, data);
        var records = new List<DirectoryRecord>();
        var offset = 0;
        while (offset < data.Length)
        {
            var recordLength = data[offset];
            if (recordLength == 0)
            {
                offset = Math.Min(data.Length, checked(((offset / SectorSize) + 1) * SectorSize));
                continue;
            }
            if (recordLength < 34 || offset + recordLength > data.Length)
                throw new InvalidDataException($"Invalid ISO9660 directory record at byte {offset}.");
            var record = ParseDirectoryRecord(data, offset, recordLength);
            offset += recordLength;
            if (record.Identifier is "\0" or "\u0001") continue;
            if (record.IsMultiExtent)
                throw new InvalidDataException($"Multi-extent ISO9660 entry is unsupported: '{record.Identifier}'.");
            records.Add(record);
        }
        // A name loses its version suffix and trailing dots unless that gives it the name of another
        // entry in the directory, as README.;1 and README.;2 would. Then every entry of that name keeps
        // its whole identifier, as the documentation standard writes disc paths (ENTRY-TYPES-11). Names
        // are compared ignoring case, as the source looks paths up.
        var shortened = records.Select(record => ShortIsoName(record.Identifier)).ToArray();
        var shared = shortened.GroupBy(name => name, StringComparer.OrdinalIgnoreCase)
            .Where(group => group.Count() > 1).Select(group => group.Key).ToHashSet(StringComparer.OrdinalIgnoreCase);
        for (var index = 0; index < records.Count; index++)
        {
            var record = records[index];
            var name = CheckedIsoName(record.Identifier,
                shared.Contains(shortened[index]) ? record.Identifier : shortened[index]);
            var relative = string.IsNullOrEmpty(parent) ? name : $"{parent}/{name}";
            ValidateExtent(record.Extent, record.DataLength);
            if (record.IsDirectory)
            {
                ReadDirectory(stream, record, relative, depth + 1, visited);
                continue;
            }
            var entry = new ContentSourceEntry(relative, record.DataLength);
            if (!files.TryAdd(relative, new IsoEntry(entry, record.Extent)))
                throw new InvalidDataException($"ISO9660 image contains duplicate path '{relative}'.");
            listing?.Add(new IsoFile(relative, record.Extent, record.DataLength));
            if (files.Count > MaximumEntries)
                throw new InvalidDataException("ISO9660 entry count exceeds the safety limit.");
        }
    }

    private void ValidateExtent(uint extent, uint length)
    {
        var start = checked((long)extent * SectorSize);
        var end = checked(start + length);
        if (start < 0 || end > volumeLength)
            throw new InvalidDataException("ISO9660 extent lies outside the declared volume.");
    }

    private static DirectoryRecord ParseDirectoryRecord(byte[] data, int offset, int recordLength)
    {
        if (recordLength < 34 || offset < 0 || offset + recordLength > data.Length)
            throw new InvalidDataException("ISO9660 directory record is truncated.");
        var identifierLength = data[offset + 32];
        if (33 + identifierLength > recordLength)
            throw new InvalidDataException("ISO9660 directory identifier is truncated.");
        var extent = ReadBothEndianUInt32(data, offset + 2, "extent");
        var length = ReadBothEndianUInt32(data, offset + 10, "data length");
        // Latin-1 keeps each byte as the character of the same value. ASCII would turn every byte
        // above 0x7F into '?', a name the disc does not hold.
        var identifier = Encoding.Latin1.GetString(data, offset + 33, identifierLength);
        var flags = data[offset + 25];
        return new(extent, length, identifier, (flags & 0x02) != 0, (flags & 0x80) != 0);
    }

    private static uint ReadBothEndianUInt32(byte[] data, int offset, string field)
    {
        var little = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(offset, 4));
        var big = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(offset + 4, 4));
        if (little != big) throw new InvalidDataException($"ISO9660 {field} byte orders disagree.");
        return little;
    }

    private static ushort ReadBothEndianUInt16(byte[] data, int offset, string field)
    {
        var little = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(offset, 2));
        var big = BinaryPrimitives.ReadUInt16BigEndian(data.AsSpan(offset + 2, 2));
        if (little != big) throw new InvalidDataException($"ISO9660 {field} byte orders disagree.");
        return little;
    }

    // The identifier without its version suffix and the dots that end it.
    private static string ShortIsoName(string identifier)
    {
        var separator = identifier.LastIndexOf(';');
        return (separator >= 0 ? identifier[..separator] : identifier).TrimEnd('.');
    }

    private static string CheckedIsoName(string identifier, string name)
    {
        // A separator inside one identifier would read as two components.
        if (name.Contains('/') || name.Contains('\\'))
            throw new InvalidDataException(
                $"ISO9660 identifier {AssetVerifier.JsonString(identifier)} holds a path separator.");
        try
        {
            return PortableAssetPath.Relative(name);
        }
        catch (InvalidDataException exception)
        {
            throw new InvalidDataException(
                $"ISO9660 identifier {AssetVerifier.JsonString(identifier)} is not a portable name. {exception.Message}",
                exception);
        }
    }

    // Latin-1 maps each byte to the character of the same value, so the label keeps every byte.
    private static string? DecodeIdentifier(ReadOnlySpan<byte> bytes)
    {
        var value = Encoding.Latin1.GetString(bytes).TrimEnd(' ', '\0');
        return value.Length == 0 ? null : value;
    }

    private static void ReadExactly(Stream stream, byte[] buffer)
    {
        var offset = 0;
        while (offset < buffer.Length)
        {
            var read = stream.Read(buffer, offset, buffer.Length - offset);
            if (read == 0) throw new EndOfStreamException("ISO9660 image ended unexpectedly.");
            offset += read;
        }
    }

    private sealed record IsoEntry(ContentSourceEntry Entry, uint Extent);
    private sealed record DirectoryRecord(
        uint Extent, uint DataLength, string Identifier, bool IsDirectory, bool IsMultiExtent);
}
