namespace RefurbishedDinosaurs.Core.Assets;

/// <summary>
/// Identifies one CD audio track of a cue/bin image when the rip may be shifted by a drive read
/// offset, which moves every sample of the track by the same amount. A sample here is one 16-bit
/// stereo sample pair, 4 bytes, as drive offsets are counted; a raw sector holds 588 of them.
/// </summary>
/// <remarks>
/// Verification slides the anchor across every shift up to <see cref="ToleranceSamples"/> in either
/// direction. It accepts the track only when the anchor matches at exactly one shift and the central
/// samples at that shift hash to <see cref="CentralXxh3"/>. The central samples leave out
/// <see cref="ToleranceSamples"/> at each end of the recorded track, which a shifted rip may have
/// filled with the neighbouring audio or with silence.
/// </remarks>
/// <param name="Track">
/// The track number in the cue sheet, from 2 to 99. Track 1 of a cue/bin source is its data track.
/// </param>
/// <param name="Samples">
/// Samples in the recorded track: from its <c>INDEX 01</c> to the next track's <c>INDEX 00</c>, that
/// track's <c>INDEX 01</c> when it has no <c>INDEX 00</c>, or the end of the image for the last track.
/// </param>
/// <param name="ToleranceSamples">
/// The largest shift, in either direction, that still identifies the track, and the largest
/// difference in length accepted. At most <see cref="MaximumToleranceSamples"/>.
/// </param>
/// <param name="AnchorOffset">Where the anchor starts, in samples from the start of the recorded track.</param>
/// <param name="AnchorSamples">The anchor's length in samples, from 1 to <see cref="MaximumAnchorSamples"/>.</param>
/// <param name="AnchorXxh3">XXH3-128 of the anchor's bytes, as <see cref="FileFingerprint"/> formats it.</param>
/// <param name="CentralXxh3">
/// XXH3-128 of the bytes of the recorded track's samples from <see cref="ToleranceSamples"/> up to
/// <see cref="Samples"/> minus <see cref="ToleranceSamples"/>.
/// </param>
public sealed record CddaTrackFingerprint(
    int Track,
    long Samples,
    int ToleranceSamples,
    long AnchorOffset,
    int AnchorSamples,
    string AnchorXxh3,
    string CentralXxh3)
{
    /// <summary>Bytes in one sample: two channels of 16 bits.</summary>
    public const int BytesPerSample = 4;

    /// <summary>Samples in one raw 2352-byte sector.</summary>
    public const int SamplesPerSector = 588;

    /// <summary>
    /// The largest <see cref="ToleranceSamples"/>: ten sectors. Verification hashes the anchor once per
    /// shift, so the tolerance bounds its work.
    /// </summary>
    public const int MaximumToleranceSamples = 10 * SamplesPerSector;

    /// <summary>The largest <see cref="AnchorSamples"/>: one second of audio.</summary>
    public const int MaximumAnchorSamples = 75 * SamplesPerSector;

    /// <summary>
    /// Throws unless the track is 2 to 99, the tolerance and anchor lengths are in range, the anchor
    /// lies at least <see cref="ToleranceSamples"/> inside both ends of the track, so every shift
    /// tried reads samples of the track, and both hashes are XXH3-128 fingerprints.
    /// </summary>
    /// <exception cref="InvalidDataException">A value breaks one of these rules.</exception>
    public void Validate()
    {
        if (Track is < 2 or > 99)
            throw new InvalidDataException($"Audio track number {Track} is outside 2 to 99.");
        if (ToleranceSamples is < 0 or > MaximumToleranceSamples)
            throw new InvalidDataException(
                $"Audio track {Track:D2} tolerance must be 0 to {MaximumToleranceSamples} samples.");
        if (AnchorSamples is < 1 or > MaximumAnchorSamples)
            throw new InvalidDataException(
                $"Audio track {Track:D2} anchor must be 1 to {MaximumAnchorSamples} samples long.");
        if (AnchorOffset < ToleranceSamples || AnchorOffset > Samples - AnchorSamples - ToleranceSamples)
            throw new InvalidDataException(
                $"Audio track {Track:D2} anchor must lie at least {ToleranceSamples} samples inside the track.");
        if (!FileFingerprint.IsXxh3(AnchorXxh3) || !FileFingerprint.IsXxh3(CentralXxh3))
            throw new InvalidDataException($"Audio track {Track:D2} has an invalid xxh3 value.");
    }
}
