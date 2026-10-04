using System.Buffers;
using System.Globalization;
using System.IO.Hashing;
using RefurbishedDinosaurs.Core.Assets;

namespace RefurbishedDinosaurs.LegacyFormats;

/// <summary>The outcome of <see cref="CddaTrackFingerprints.VerifyAsync"/>.</summary>
/// <param name="Problem">
/// <see langword="null"/> when the track matched. Otherwise <see cref="AssetProblem.WrongSize"/>,
/// <see cref="AssetProblem.AudioOffsetOutOfRange"/>, <see cref="AssetProblem.AudioAlignmentAmbiguous"/>,
/// <see cref="AssetProblem.AudioHashMismatch"/>, or <see cref="AssetProblem.Unreadable"/> when the
/// samples a check needs lie past the end of the image.
/// </param>
/// <param name="AnchorShifts">
/// Every shift, in samples and ascending, at which the anchor matched. A positive shift means the
/// track's audio sits later in this image than in the recorded one. Empty when the anchor was not
/// searched.
/// </param>
/// <param name="Detail">A sentence giving the expected and found values.</param>
public sealed record CddaTrackVerification(AssetProblem? Problem, IReadOnlyList<int> AnchorShifts, string Detail)
{
    /// <summary>Whether the track matched its fingerprint.</summary>
    public bool IsMatch => Problem is null;

    /// <summary>The shift the anchor matched at, when it matched at exactly one; otherwise <see langword="null"/>.</summary>
    public int? Shift => AnchorShifts.Count == 1 ? AnchorShifts[0] : null;
}

/// <summary>
/// Records and checks <see cref="CddaTrackFingerprint"/> values against the audio tracks of a raw
/// cue/bin image, whose sectors hold 16-bit stereo samples as the image stores them.
/// </summary>
/// <remarks>
/// A rip shifted by a drive read offset moves the end of a track into the sectors that follow it, so
/// a check may read past the track's extent into the rest of the image. A check that needs samples
/// past the end of the image is reported as <see cref="AssetProblem.Unreadable"/>, never as a
/// mismatch.
/// </remarks>
public static class CddaTrackFingerprints
{
    private const int BufferSize = 1024 * 1024;
    private const int Bytes = CddaTrackFingerprint.BytesPerSample;
    private const int ShiftsInDetail = 10;

    /// <summary>
    /// Records the fingerprint of audio track <paramref name="track"/> of the cue/bin image at
    /// <paramref name="cueBinPath"/>, found and checked as <see cref="OriginalContentSource.OpenCueBin"/>
    /// finds and checks the sheet and image.
    /// </summary>
    /// <param name="cueBinPath">The <c>.cue</c> file, the <c>.bin</c> file, or the directory holding them.</param>
    /// <param name="track">The audio track's number.</param>
    /// <param name="toleranceSamples">See <see cref="CddaTrackFingerprint.ToleranceSamples"/>.</param>
    /// <param name="anchorOffset">See <see cref="CddaTrackFingerprint.AnchorOffset"/>.</param>
    /// <param name="anchorSamples">See <see cref="CddaTrackFingerprint.AnchorSamples"/>.</param>
    /// <param name="cancellationToken">Cancels the reads.</param>
    /// <exception cref="ArgumentException">
    /// The track is not an audio track of the sheet, the values would make an invalid fingerprint, or
    /// the anchor's samples also match at another shift within the tolerance.
    /// </exception>
    /// <exception cref="FileNotFoundException">Nothing exists at <paramref name="cueBinPath"/>.</exception>
    /// <exception cref="InvalidDataException">The sheet or image is not valid, or the files are ambiguous.</exception>
    public static async Task<CddaTrackFingerprint> RecordAsync(
        string cueBinPath,
        int track,
        int toleranceSamples,
        long anchorOffset,
        int anchorSamples,
        CancellationToken cancellationToken = default)
    {
        var (_, binPath, sheet) = CueBinSheet.Resolve(cueBinPath);
        sheet.ValidateBin(binPath);
        if (track < 1 || track > sheet.Tracks.Count || sheet.Tracks[track - 1].Type != "AUDIO")
            throw new ArgumentException($"Track {track:D2} is not an audio track of the cue sheet.", nameof(track));
        await using var image = OpenImage(binPath);
        var extent = sheet.TrackExtent(track, image.Length / CueBinSheet.RawSectorSize);
        return await RecordAsync(image, extent, toleranceSamples, anchorOffset, anchorSamples, cancellationToken)
            .ConfigureAwait(false);
    }

    /// <summary>
    /// Records the fingerprint of the audio track at <paramref name="extent"/> of a raw image. The
    /// anchor must match at no shift within the tolerance other than its own, or the fingerprint
    /// could never verify unambiguously.
    /// </summary>
    /// <param name="image">The raw image, readable and seekable, positioned anywhere. It is not disposed.</param>
    /// <param name="extent">The track's sectors, from <see cref="CueBinSheet.TrackExtent"/>.</param>
    /// <param name="toleranceSamples">See <see cref="CddaTrackFingerprint.ToleranceSamples"/>.</param>
    /// <param name="anchorOffset">See <see cref="CddaTrackFingerprint.AnchorOffset"/>.</param>
    /// <param name="anchorSamples">See <see cref="CddaTrackFingerprint.AnchorSamples"/>.</param>
    /// <param name="cancellationToken">Cancels the reads.</param>
    /// <exception cref="ArgumentException">
    /// The stream cannot read or seek, the values would make an invalid fingerprint, or the anchor's
    /// samples also match at another shift within the tolerance.
    /// </exception>
    /// <exception cref="InvalidDataException">The extent ends past the image.</exception>
    public static async Task<CddaTrackFingerprint> RecordAsync(
        Stream image,
        CueBinTrackExtent extent,
        int toleranceSamples,
        long anchorOffset,
        int anchorSamples,
        CancellationToken cancellationToken = default)
    {
        CheckImage(image);
        ArgumentNullException.ThrowIfNull(extent);
        var samples = checked(extent.Sectors * CddaTrackFingerprint.SamplesPerSector);
        var zero = new string('0', FileFingerprint.Xxh3Length);
        var fingerprint = new CddaTrackFingerprint(
            extent.Track, samples, toleranceSamples, anchorOffset, anchorSamples, zero, zero);
        try { fingerprint.Validate(); }
        catch (InvalidDataException exception) { throw new ArgumentException(exception.Message, exception); }

        var trackStart = checked(extent.StartSector * CddaTrackFingerprint.SamplesPerSector);
        if (trackStart + samples > image.Length / Bytes)
            throw new InvalidDataException($"Track {extent.Track:D2} ends past the image.");
        var window = await ReadAsync(image, trackStart + anchorOffset - toleranceSamples,
            anchorSamples + 2 * toleranceSamples, cancellationToken).ConfigureAwait(false);
        var anchor = XxHash128.HashToUInt128(window.AsSpan(toleranceSamples * Bytes, anchorSamples * Bytes));
        var shifts = FindAnchor(window, fingerprint, anchor);
        if (shifts.Count != 1)
            throw new ArgumentException(
                $"Track {extent.Track:D2} anchor also matches at shifts {Describe(shifts)}; choose an anchor " +
                "whose samples do not repeat within the tolerance.", nameof(anchorOffset));
        var central = await HashAsync(image, trackStart + toleranceSamples, samples - 2L * toleranceSamples,
            cancellationToken).ConfigureAwait(false);
        return fingerprint with { AnchorXxh3 = Format(anchor), CentralXxh3 = Format(central) };
    }

    /// <summary>
    /// Checks the audio track at <paramref name="extent"/> of a raw image against its fingerprint: its
    /// length within the tolerance, then the anchor at every shift within the tolerance, then, when the
    /// anchor matched at exactly one shift, the central samples at that shift. Each step runs only
    /// when the one before it passed.
    /// </summary>
    /// <param name="image">The raw image, readable and seekable, positioned anywhere. It is not disposed.</param>
    /// <param name="extent">The track's sectors in this image, from <see cref="CueBinSheet.TrackExtent"/>.</param>
    /// <param name="fingerprint">The recorded fingerprint, validated before anything is read.</param>
    /// <param name="cancellationToken">Cancels the reads.</param>
    /// <exception cref="ArgumentException">The stream cannot read or seek, or the extent is of another track.</exception>
    /// <exception cref="InvalidDataException">The fingerprint is invalid.</exception>
    /// <exception cref="IOException">The image could not be read.</exception>
    public static async Task<CddaTrackVerification> VerifyAsync(
        Stream image,
        CueBinTrackExtent extent,
        CddaTrackFingerprint fingerprint,
        CancellationToken cancellationToken = default)
    {
        CheckImage(image);
        ArgumentNullException.ThrowIfNull(extent);
        ArgumentNullException.ThrowIfNull(fingerprint);
        fingerprint.Validate();
        if (extent.Track != fingerprint.Track)
            throw new ArgumentException(
                $"The extent is of track {extent.Track:D2}, the fingerprint of track {fingerprint.Track:D2}.", nameof(extent));

        var tolerance = fingerprint.ToleranceSamples;
        var samples = checked(extent.Sectors * CddaTrackFingerprint.SamplesPerSector);
        if (Math.Abs(samples - fingerprint.Samples) > tolerance)
            return new(AssetProblem.WrongSize, [],
                $"Expected {fingerprint.Samples} samples, within {tolerance}; found {samples}.");

        var trackStart = checked(extent.StartSector * CddaTrackFingerprint.SamplesPerSector);
        var imageSamples = image.Length / Bytes;
        var windowStart = trackStart + fingerprint.AnchorOffset - tolerance;
        var windowLength = fingerprint.AnchorSamples + 2 * tolerance;
        if (windowStart + windowLength > imageSamples)
            return new(AssetProblem.Unreadable, [],
                $"The anchor's search window ends {windowStart + windowLength - imageSamples} samples past the image; no shift was checked.");
        var window = await ReadAsync(image, windowStart, windowLength, cancellationToken).ConfigureAwait(false);
        var shifts = FindAnchor(window, fingerprint, Parse(fingerprint.AnchorXxh3));
        if (shifts.Count == 0)
            return new(AssetProblem.AudioOffsetOutOfRange, shifts,
                $"The anchor matched at no shift within {tolerance} samples.");
        if (shifts.Count > 1)
            return new(AssetProblem.AudioAlignmentAmbiguous, shifts,
                $"The anchor matched at shifts {Describe(shifts)}; the central samples were not checked.");

        var shift = shifts[0];
        var centralStart = trackStart + tolerance + shift;
        var centralLength = fingerprint.Samples - 2L * tolerance;
        if (centralStart + centralLength > imageSamples)
            return new(AssetProblem.Unreadable, shifts,
                $"At shift {shift} the central samples end {centralStart + centralLength - imageSamples} samples past the image; they were not checked.");
        var central = Format(await HashAsync(image, centralStart, centralLength, cancellationToken).ConfigureAwait(false));
        return central.Equals(fingerprint.CentralXxh3, StringComparison.Ordinal)
            ? new(null, shifts, $"Matched at shift {shift}.")
            : new(AssetProblem.AudioHashMismatch, shifts,
                $"At shift {shift} expected central xxh3 {fingerprint.CentralXxh3}; found {central}.");
    }

    internal static FileStream OpenImage(string path) => new(path, FileMode.Open, FileAccess.Read, FileShare.Read,
        bufferSize: 0, FileOptions.Asynchronous);

    private static void CheckImage(Stream image)
    {
        ArgumentNullException.ThrowIfNull(image);
        if (!image.CanRead || !image.CanSeek)
            throw new ArgumentException("The image stream must be readable and seekable.", nameof(image));
    }

    // window holds the samples from AnchorOffset - ToleranceSamples, so the anchor at shift s starts
    // at sample ToleranceSamples + s of it.
    private static List<int> FindAnchor(byte[] window, CddaTrackFingerprint fingerprint, UInt128 anchor)
    {
        var shifts = new List<int>();
        var length = fingerprint.AnchorSamples * Bytes;
        for (var shift = -fingerprint.ToleranceSamples; shift <= fingerprint.ToleranceSamples; shift++)
            if (XxHash128.HashToUInt128(window.AsSpan((fingerprint.ToleranceSamples + shift) * Bytes, length)) == anchor)
                shifts.Add(shift);
        return shifts;
    }

    private static async Task<byte[]> ReadAsync(Stream image, long firstSample, int samples, CancellationToken cancellationToken)
    {
        var buffer = new byte[samples * Bytes];
        image.Position = checked(firstSample * Bytes);
        await image.ReadExactlyAsync(buffer, cancellationToken).ConfigureAwait(false);
        return buffer;
    }

    private static async Task<UInt128> HashAsync(Stream image, long firstSample, long samples, CancellationToken cancellationToken)
    {
        var hash = new XxHash128();
        var buffer = ArrayPool<byte>.Shared.Rent(BufferSize);
        try
        {
            image.Position = checked(firstSample * Bytes);
            for (var remaining = checked(samples * Bytes); remaining > 0;)
            {
                var chunk = buffer.AsMemory(0, (int)Math.Min(BufferSize, remaining));
                await image.ReadExactlyAsync(chunk, cancellationToken).ConfigureAwait(false);
                hash.Append(chunk.Span);
                remaining -= chunk.Length;
            }
        }
        finally { ArrayPool<byte>.Shared.Return(buffer); }
        return hash.GetCurrentHashAsUInt128();
    }

    // FileFingerprint's form: the 128-bit value written big-endian as 32 lower-case hex digits.
    private static string Format(UInt128 hash) => hash.ToString("x32", CultureInfo.InvariantCulture);

    private static UInt128 Parse(string hash) => UInt128.Parse(hash, NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture);

    private static string Describe(IReadOnlyList<int> shifts) =>
        string.Join(", ", shifts.Take(ShiftsInDetail)) + (shifts.Count > ShiftsInDetail ? $" and {shifts.Count - ShiftsInDetail} more" : "");
}
