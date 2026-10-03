# ADR 0005: Refurbished Dinosaurs runtime media libraries

Status: accepted

Restorations have independent Smacker readers and presenters, an AVI/Cinepak/RLE8/ADPCM
implementation, and an unmet AF11 FLI playback need. Their reusable parts are format decoding,
bounded stream access and sequential playback cadence. Game triggers and presentation policies
have different requirements and remain downstream.

Runtime package IDs and namespaces use RefurbishedDinosaurs. ScientificMethod continues to name
the analysis and research tools. Rename Core and LegacyFormats cleanly, with a major release and
an explicit migration guide; provide no aliases or forwarding assemblies.

Keep Core and non-video LegacyFormats. Move Smacker into Media.Smacker, and add Media.Avi,
Media.Fli and Media.Playback. The four media packages have no dependency on each other, Core,
MonoGame, host codecs or FFmpeg. A package follows a coherent format family or presentation
responsibility; do not create a package for each Huffman tree, surface or PCM helper.

Managed decoders are the runtime default because their supported profiles and bounds are
explicit and do not require native-binary distribution. FFmpeg remains useful for local
inspection, synthetic oracle checks and downstream fallback until required profiles pass.
This decision does not promise complete codec coverage or original playback fidelity.

FLI has one demonstrated consumer in this inventory. Its published AF11 format, small bounded
implementation and isolated optional dependency justify sharing it. FLC/AF12 is excluded until
there is a consumer and synthetic coverage. Historical malformed frames are rejected rather
than clipped; any edition-specific repair belongs in the restoration.

Smacker gains an additive interleaved PCM16 audio API for packed mono/stereo and 8/16-bit input.
The unsigned mono8 API remains. AVI exposes sequential payloads without claiming index-derived
keyframes. The shared clock accepts cadence and an initial delay, calls every dependent frame
in order, holds the final interval, and leaves audio buffering/synchronization to the host.

All six runtime packages share the existing .NET version/tag series and release workflow. New
NuGet IDs require trusted-publishing coverage before release. Tests use only synthetic inputs;
owned media validation and original-derived reports stay in the restoration repositories.
