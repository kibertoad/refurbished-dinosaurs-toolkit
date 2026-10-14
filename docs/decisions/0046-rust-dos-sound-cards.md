# ADR 0046: the sound cards of a rust-dos session

Status: accepted. Amends the launch of [ADR 0040](0040-rust-dos-session-backend.md), which ran
rust-dos with `--no-config`.

## Context

`RustDosSession` launched rust-dos with `--no-config` and refused `--config` in
`emulator_arguments`, so every run got rust-dos's default sound cards: an SB16 at 220h, IRQ 7,
DMA 1 and HDMA 5, and a Gravis Ultrasound at 240h, IRQ 5 and DMA 3. Many DOS programs take their
card's resources from a file their own setup program wrote, or have them built in, and fail to
start when the emulated card is elsewhere. A restoration can patch the program's file in
`prepare_drive`, but that differs from the configuration its reference captures were made with,
and a program with built-in resources has no such workaround (issue 501).

rust-dos v1.7.2, the pinned release, sets the cards from `[sound]` in its configuration file
(`sbtype`, `sbbase`, `irq`, `dma`, `hdma`, `gus`, `gusbase`, `gusirq`, `gusdma`) and has no
command-line option for them. Read in its source and run against the pinned executable:

- It reads only the file named by `--config`, never merges files, and with a file that sets nothing
  else starts as with `--no-config`: the same startup commands, then `C:\AUTOEXEC.BAT`.
- A value it does not take, or an Ultrasound that shares the Sound Blaster's base port, IRQ or a
  DMA channel, is a warning on standard error that names the file. The emulator still starts, with
  its default for the value, without the Ultrasound, or with the two cards sharing the line.
- `BLASTER` and `ULTRASND` in the guest's environment follow the cards.
- A strict-match state load compares the hardware settings, the sound cards among them, so a run
  started from a checkpoint needs the cards the saving run had.

## Decision

1. `RustDosConfig` takes `sound_blaster` (`SoundBlaster`: model, base, IRQ, DMA, HDMA, or `None`
   for no card) and `ultrasound` (`Ultrasound`: base, IRQ, DMA, or `None`). The defaults are
   rust-dos's, so a run that sets neither gets the cards it always had.
2. The session writes `rust-dos.conf` in the run directory with those `[sound]` settings and
   nothing else, and passes it with `--config` in place of `--no-config`. Both options stay owned:
   `emulator_arguments` cannot carry them. The session takes no other configuration-file settings
   from the caller; a further setting gets its own typed field the same way, when a request shows
   a need for it.
3. The session refuses, before anything starts, a value rust-dos v1.7.2 would not take, an HDMA for
   a model without a 16-bit channel, and an Ultrasound that shares the Sound Blaster's base port,
   IRQ or a DMA channel. A run that wants a Sound Blaster on IRQ 5 moves the Ultrasound or sets
   `ultrasound=None`.
4. After launch, a warning from rust-dos that names `rust-dos.conf` fails the start with
   `EmulatorRefused`, so a later rust-dos with other rules cannot run on settings it replaced.
5. `session.json` records `sound`: the file and the values passed for each card, or `null` for a
   card left out.
6. An `awe32` takes its sample ROM as `rom`, passed as `awe32rom` and recorded with its SHA-256,
   and is refused without one. Without `awe32rom`, rust-dos loads the first `awe32.raw` it finds in
   its own directory or the working directory, or plays without one, so the same configuration
   would differ between hosts. rust-dos's warning for a ROM it could not load names the setting,
   not the file, and fails the start as well.
7. A run that starts from a checkpoint is refused before launch when its cards, the ROM included,
   differ from the `sound` record of the run that saved it, in place of failing on rust-dos's
   strict-match comparison after the boot.
8. The native check sets both cards away from their defaults and requires `BLASTER` and `ULTRASND`
   in the target's environment to name them.

## Consequences

A run can match the sound configuration of the original release it is compared with. The session
still takes no free-form configuration: anything a caller sets is validated against the pinned
release and recorded. Every run now gets a configuration file, so the run directory holds
`rust-dos.conf` and rust-dos treats the run directory as its configuration directory, which only
`MOUNT -pr` and game profiles read, and the session uses neither.
