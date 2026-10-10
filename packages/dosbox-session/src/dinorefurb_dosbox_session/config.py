"""The ``dosbox.conf`` and Agent config a session generates for its emulator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import ConfigurationRefused

#: The file the guest writes on C: once its drives are set up.
READINESS_MARKER = "DRREADY.TXT"


@dataclass(frozen=True)
class Media:
    """A host path mounted read-only on a guest drive letter other than C:.

    ``kind`` is ``iso`` for a CD image (mounted with ``imgmount -t iso``) or ``directory`` for a
    folder (mounted with ``mount -ro``).
    """

    drive: str
    path: Path
    kind: Literal["iso", "directory"]


@dataclass(frozen=True)
class AgentLimits:
    """The limits the generated Agent config gives the debugger server."""

    request_timeout_ms: int = 15000
    max_message_bytes: int = 1048576
    max_memory_read_bytes: int = 65536
    max_trace_events: int = 100000


@dataclass(frozen=True)
class EmulatorConfig:
    """What the caller chooses about the emulator's configuration.

    ``sections`` holds further ``dosbox.conf`` settings by section, such as
    ``{"cpu": {"cycles": "fixed 10000"}}``. The session owns ``[autoexec]`` and, while host sound
    is muted, ``[midi] mididevice``. ``nosound`` is refused, because it broke structured readiness
    at the pinned revision; host audio is muted in the mixer and the emulated sound devices stay
    configured. ``keep_host_sound`` leaves host audio and MIDI output on.
    """

    media: tuple[Media, ...] = ()
    sections: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    keep_host_sound: bool = False
    limits: AgentLimits = field(default_factory=AgentLimits)


def _check(config: EmulatorConfig) -> None:
    for section, values in config.sections.items():
        name = section.lower()
        if name == "autoexec":
            raise ConfigurationRefused("The session writes [autoexec] itself; it cannot be set.")
        for key, value in values.items():
            if key.lower() == "nosound" and value.strip().lower() in ("true", "1", "yes"):
                raise ConfigurationRefused(
                    "nosound=true failed structured readiness at the pinned revision. Host audio is muted "
                    "in the mixer by default instead."
                )
            if name == "midi" and key.lower() == "mididevice" and not config.keep_host_sound:
                raise ConfigurationRefused("[midi] mididevice is set to none while host sound is muted.")
    for media in config.media:
        letter = media.drive.upper()
        if len(letter) != 1 or not "D" <= letter <= "Z":
            raise ConfigurationRefused(f"Media drive {media.drive!r} must be a letter from D to Z.")
    letters = [m.drive.upper() for m in config.media]
    if len(set(letters)) != len(letters):
        raise ConfigurationRefused("Two media share a drive letter.")


def dosbox_conf(config: EmulatorConfig, drive_c: Path, marker_token: str) -> str:
    """The ``dosbox.conf`` text: the caller's sections, the audio settings and ``[autoexec]``.

    ``[autoexec]`` mutes host audio unless ``keep_host_sound`` is set, mounts ``drive_c`` as C:
    and each medium read-only, and last writes ``marker_token`` to :data:`READINESS_MARKER` on C:.
    """
    _check(config)
    sections: dict[str, dict[str, str]] = {name.lower(): dict(values) for name, values in config.sections.items()}
    if not config.keep_host_sound:
        sections.setdefault("midi", {})["mididevice"] = "none"
    lines: list[str] = []
    for name, values in sections.items():
        lines.append(f"[{name}]")
        lines.extend(f"{key}={value}" for key, value in values.items())
        lines.append("")
    lines.append("[autoexec]")
    if not config.keep_host_sound:
        lines.append("mixer master 0:0 /noshow")
    lines.append(f'mount c "{drive_c}"')
    for media in config.media:
        letter = media.drive.lower()
        if media.kind == "iso":
            lines.append(f'imgmount {letter} "{media.path}" -t iso')
        else:
            lines.append(f'mount {letter} "{media.path}" -ro')
    lines.append(f"echo {marker_token}> C:\\{READINESS_MARKER}")
    return "\n".join(lines) + "\n"


def agent_env(endpoint: str, emulator: Path, drive_c: Path, limits: AgentLimits) -> str:
    """The Agent config text for the debugger server and the upstream client."""
    return (
        "transport=named_pipe\n"
        f"endpoint={endpoint}\n"
        f"dosbox_executable={emulator}\n"
        f"dosbox_workdir={drive_c}\n"
        "profile=production\n"
        f"request_timeout_ms={limits.request_timeout_ms}\n"
        f"max_message_bytes={limits.max_message_bytes}\n"
        f"max_memory_read_bytes={limits.max_memory_read_bytes}\n"
        f"max_trace_events={limits.max_trace_events}\n"
    )
