"""The ``disc-archiver-gui`` window: the same archiving as the command, for people who prefer a window.

It opens on the personal-use notice and does nothing until the person confirms it. The formats
the disc profile recommends are ticked from the start, so the default choice is the one the
restoration's importer reads.
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import backends, drives, tools
from .disc import DiscError
from .formats import FORMATS
from .notice import NOTICE
from .pipeline import MANIFEST_NAME, archive, manifest_ok
from .profile import BUILTIN_PROFILES, Profile, load_profile, recommended_formats

ACCEPT_TEXT = "I own this disc, and I will keep the copy to myself and never share it or anything taken from it."


@dataclass(frozen=True)
class FormatChoice:
    """How one format is offered: whether it can be chosen, why not, and whether it starts ticked."""

    id: str
    label: str
    enabled: bool
    selected: bool
    reason: str = ""


def format_choices(profile: Profile, found: dict[str, Path | None] | None = None, raw: bool = True) -> list[FormatChoice]:
    """The format list for a profile, with the recommended formats ticked and what blocks the rest.

    ``raw`` is false when the copy will hold no raw sectors (a plain data track copy), which rules
    out the formats that store every sector.
    """
    found = tools.available_tools() if found is None else found
    recommended = set(recommended_formats(profile, raw=raw))
    choices = []
    for fmt in FORMATS:
        missing = [name for name in fmt.needs if found.get(name) is None]
        reason = f"needs {', '.join(missing)}" if missing else ""
        if not raw and (fmt.complete or fmt.id.startswith("iso-")):
            # A data track copy holds neither raw sectors nor audio.
            reason = "needs redumper or cdrdao"
        label = fmt.title + (" (recommended)" if fmt.id in recommended else "")
        choices.append(FormatChoice(fmt.id, label, not reason, fmt.id in recommended and not reason, reason))
    return choices


def backend_label(backend: backends.Backend) -> str:
    """A backend's name with what it is missing."""
    missing = backend.missing()
    return backend.title + (f" - needs {', '.join(missing)}" if missing else "")


class ArchiverWindow:
    """The main window."""

    def __init__(self, root: tk.Tk, profile: Profile | None = None) -> None:
        self.root = root
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.result: dict[str, object] | None = None
        self.profiles: dict[str, Profile] = {p.title: p for p in BUILTIN_PROFILES.values()}
        if profile is not None:
            self.profiles = {profile.title: profile, **self.profiles}
        root.title("Disc Archiver - personal copies of discs you own")
        root.minsize(720, 640)
        frame = ttk.Frame(root, padding=12)
        frame.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self._notice(frame)
        self._source(frame)
        self._profile(frame)
        self._formats(frame)
        self._output(frame)
        self._run(frame)
        self.profile_name.set(next(iter(self.profiles)))
        self._profile_changed()
        self.source_kind.trace_add("write", lambda *_: self._profile_changed())
        self._update_state()
        root.after(100, self._poll)

    # Layout

    def _notice(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Read this first", padding=8)
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)
        tk.Label(
            box,
            text="For your own archive only. Never share the copy, or anything taken from it, with anyone.",
            font=("TkDefaultFont", 12, "bold"),
            foreground="#a40000",
            wraplength=680,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        text = scrolledtext.ScrolledText(box, height=12, wrap="word", relief="flat", background="#fff4e5", foreground="#5c2b00")
        text.insert("1.0", NOTICE)
        text.configure(state="disabled")
        text.grid(row=1, column=0, sticky="ew")
        self.accepted = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text=ACCEPT_TEXT, variable=self.accepted, command=self._update_state).grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )

    def _source(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Copy from", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=4)
        box.columnconfigure(1, weight=1)
        self.source_kind = tk.StringVar(value="drive")
        ttk.Radiobutton(box, text="Disc in drive", value="drive", variable=self.source_kind).grid(row=0, column=0, sticky="w")
        self.drive = tk.StringVar()
        self.drive_box = ttk.Combobox(box, textvariable=self.drive)
        self.drive_box.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(box, text="Refresh", command=self._refresh_drives).grid(row=0, column=2)
        ttk.Label(box, text="Copied with").grid(row=1, column=0, sticky="w")
        self.backend_labels = {backend_label(b): b for b in backends.BACKENDS}
        auto = backends.backend_by_id("auto")
        self.backend = tk.StringVar(value=backend_label(auto))
        backend_box = ttk.Combobox(box, textvariable=self.backend, values=list(self.backend_labels), state="readonly")
        backend_box.grid(row=1, column=1, sticky="ew", padx=4, pady=2)
        backend_box.bind("<<ComboboxSelected>>", lambda _: self._profile_changed())
        self.backend_notes = ttk.Label(box, wraplength=640, justify="left")
        self.backend_notes.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Radiobutton(box, text="Image I already made", value="image", variable=self.source_kind).grid(row=2, column=0, sticky="w")
        self.image = tk.StringVar()
        ttk.Entry(box, textvariable=self.image).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Button(box, text="Browse...", command=self._browse_image).grid(row=2, column=2)
        self._refresh_drives()

    def _profile(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Game", padding=8)
        box.grid(row=2, column=0, sticky="ew", pady=4)
        box.columnconfigure(0, weight=1)
        self.profile_name = tk.StringVar()
        self.profile_box = ttk.Combobox(box, textvariable=self.profile_name, values=list(self.profiles), state="readonly")
        self.profile_box.grid(row=0, column=0, sticky="ew")
        self.profile_box.bind("<<ComboboxSelected>>", lambda _: self._profile_changed())
        ttk.Button(box, text="Load the restoration's disc profile...", command=self._browse_profile).grid(row=0, column=1, padx=4)
        self.profile_notes = ttk.Label(box, wraplength=640, justify="left")
        self.profile_notes.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _formats(self, parent: ttk.Frame) -> None:
        self.format_box = ttk.LabelFrame(parent, text="Formats to write", padding=8)
        self.format_box.grid(row=3, column=0, sticky="ew", pady=4)
        self.format_vars: dict[str, tk.BooleanVar] = {}
        self.format_buttons: dict[str, ttk.Checkbutton] = {}
        for row, fmt in enumerate(FORMATS):
            var = tk.BooleanVar(value=False)
            button = ttk.Checkbutton(self.format_box, text=fmt.title, variable=var)
            button.grid(row=row // 2, column=row % 2, sticky="w", padx=(0, 16))
            self.format_vars[fmt.id] = var
            self.format_buttons[fmt.id] = button
        ttk.Button(self.format_box, text="Use the recommended formats", command=self._profile_changed).grid(
            row=len(FORMATS) // 2 + 1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Label(
            self.format_box,
            text="A disc copied from a drive also keeps the dump itself in the 'archival' folder.",
        ).grid(row=len(FORMATS) // 2 + 2, column=0, columnspan=2, sticky="w")

    def _output(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Save to", padding=8)
        box.grid(row=4, column=0, sticky="ew", pady=4)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Folder").grid(row=0, column=0, sticky="w")
        self.output = tk.StringVar(value=str(Path.home() / "Disc archive"))
        ttk.Entry(box, textvariable=self.output).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(box, text="Browse...", command=self._browse_output).grid(row=0, column=2)
        ttk.Label(box, text="Name").grid(row=1, column=0, sticky="w")
        self.name = tk.StringVar(value="disc")
        ttk.Entry(box, textvariable=self.name).grid(row=1, column=1, sticky="ew", padx=4, pady=2)

    def _run(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=5, column=0, sticky="ew", pady=4)
        bar.columnconfigure(1, weight=1)
        self.start_button = ttk.Button(bar, text="Make the copy", command=self.start)
        self.start_button.grid(row=0, column=0)
        self.progress = ttk.Progressbar(bar, mode="indeterminate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=8)
        self.log = scrolledtext.ScrolledText(parent, height=12, state="disabled")
        self.log.grid(row=6, column=0, sticky="nsew")
        parent.rowconfigure(6, weight=1)

    # Behaviour

    def _refresh_drives(self) -> None:
        found = drives.list_drives()
        self.drive_box.configure(values=[d.name for d in found])
        if found and not self.drive.get():
            self.drive.set(found[0].name)

    def _browse_image(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Disc images", "*.cue *.iso *.ccd *.chd"), ("All files", "*")])
        if path:
            self.image.set(path)
            self.source_kind.set("image")
            self.name.set(Path(path).stem)
            self._profile_changed()

    def _browse_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output.set(path)

    def _browse_profile(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Disc profiles", "*.json"), ("All files", "*")])
        if path:
            self.add_profile(Path(path))

    def add_profile(self, path: Path) -> None:
        """Load a profile file and select it."""
        try:
            profile = load_profile(path)
        except DiscError as error:
            messagebox.showerror("Disc profile", str(error))
            return
        self.profiles = {profile.title: profile, **self.profiles}
        self.profile_box.configure(values=list(self.profiles))
        self.profile_name.set(profile.title)
        self._profile_changed()

    def raw_copy(self) -> bool:
        """Whether the copy will hold raw sectors: an existing image is assumed to, a data track copy does not."""
        return self.source_kind.get() == "image" or self.backend_labels[self.backend.get()].id != "data-copy"

    def _profile_changed(self) -> None:
        profile = self.profiles[self.profile_name.get()]
        backend = self.backend_labels[self.backend.get()]
        self.backend_notes.configure(text=backend.description if self.source_kind.get() == "drive" else "")
        for choice in format_choices(profile, raw=self.raw_copy()):
            button = self.format_buttons[choice.id]
            button.configure(text=choice.label + (f" - {choice.reason}" if choice.reason else ""))
            button.state(["!disabled"] if choice.enabled else ["disabled"])
            self.format_vars[choice.id].set(choice.selected)
        parts = [f"Recommended: {', '.join(recommended_formats(profile, raw=self.raw_copy()))}."]
        if profile.notes:
            parts.append(profile.notes)
        self.profile_notes.configure(text=" ".join(parts))

    def _update_state(self) -> None:
        busy = self.worker is not None and self.worker.is_alive()
        self.start_button.state(["!disabled"] if self.accepted.get() and not busy else ["disabled"])

    def selected_formats(self) -> list[str]:
        """The ticked formats."""
        return [fmt.id for fmt in FORMATS if self.format_vars[fmt.id].get()]

    def start(self) -> None:
        """Validate the choices and start the copy on a worker thread."""
        if not self.accepted.get():
            messagebox.showwarning("Disc Archiver", "Confirm the notice at the top first.")
            return
        formats = self.selected_formats()
        if not formats:
            messagebox.showwarning("Disc Archiver", "Tick at least one format.")
            return
        output = Path(self.output.get()).expanduser()
        if (output / MANIFEST_NAME).exists() and not messagebox.askyesno(
            "Disc Archiver", f"{output} already holds a copy. Write over it?"
        ):
            return
        job: dict[str, object] = {
            "output": output,
            "profile": self.profiles[self.profile_name.get()],
            "formats": formats,
            "name": self.name.get(),
        }
        if self.source_kind.get() == "drive":
            job["drive"] = self.drive.get().strip()
            job["backend"] = self.backend_labels[self.backend.get()].id
        else:
            job["image"] = Path(self.image.get())
        self._clear_log()
        self.worker = threading.Thread(target=self._work, args=(job,), daemon=True)
        self.worker.start()
        self.progress.start(12)
        self._update_state()

    def _work(self, job: dict) -> None:  # type: ignore[type-arg]
        try:
            manifest = archive(log=lambda line: self.messages.put(("log", line)), **job)
            self.messages.put(("done", manifest))
        except DiscError as error:
            self.messages.put(("error", str(error)))
        except Exception as error:  # noqa: BLE001 - shown to the person instead of lost on a thread
            self.messages.put(("error", f"{type(error).__name__}: {error}"))

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self._append(str(value))
                else:
                    self._finish(kind, value)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finish(self, kind: str, value: object) -> None:
        self.progress.stop()
        self.worker = None
        self._update_state()
        if kind == "error":
            self._append(f"Failed: {value}")
            messagebox.showerror("Disc Archiver", str(value))
            return
        manifest: dict = value  # type: ignore[assignment]
        self.result = manifest
        lines = [f"{o['format']}: {o['verification']['status']}" for o in manifest["outputs"]]
        lines += [f"{u['format']}: not written ({u['reason']})" for u in manifest["unavailable"]]
        lines += [
            f"Profile check failed: {c['check']} expected {c['expected']!r}, found {c['found']!r}"
            for c in manifest["profile"]["checks"]
            if not c["matched"]
        ]
        for line in lines:
            self._append(line)
        title = "Copy made" if manifest_ok(manifest) else "Copy made, with problems"
        messagebox.showinfo("Disc Archiver", f"{title}.\n\n" + "\n".join(lines) + "\n\nKeep it to yourself.")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def run(argv: list[str] | None = None) -> None:
    """GUI entry point. ``--profile FILE`` preselects a restoration's disc profile."""
    parser = argparse.ArgumentParser(prog="disc-archiver-gui")
    parser.add_argument("--profile", help="a restoration's disc profile file to select")
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    profile = None
    if arguments.profile:
        try:
            profile = load_profile(arguments.profile)
        except DiscError as error:
            print(f"error: {error}", file=sys.stderr)
            sys.exit(2)
    root = tk.Tk()
    ArchiverWindow(root, profile)
    root.mainloop()
