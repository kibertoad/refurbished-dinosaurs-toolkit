"""A client for the rust-dos debug server, with this session's request IDs.

rust-dos serves its debugger as HTTP and JSON on a local port. The client speaks that protocol with
the standard library alone; it imports nothing of rust-dos. Every request carries an
``X-Request-Id`` from the client's own namespace, which the server copies into the journal entries
the request causes. The client offers reads, breakpoints and the journal. Requests that resume or
change the guest go through :class:`~dinorefurb_dosbox_session.rustdos.RustDosSession`, which
tracks them.
"""

from __future__ import annotations

import base64
import http.client
import json
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import RequestIds
from .errors import ServerRefused


@dataclass(frozen=True)
class Reply:
    """One answer from the debug server.

    ``body`` is the parsed JSON, or the raw bytes of a reply that is not JSON. ``req`` is the
    server's number for the request (``X-Debug-Req``) and ``request_id`` the ID the client sent.
    ``state_seq`` is the journal entry that last changed the machine when the server answered, for
    an answer from the machine (``X-Debug-State-Seq``), else ``None``.
    """

    status: int
    body: Any
    request_id: str
    req: int | None
    state_seq: int | None

    def field(self, name: str) -> Any:
        """A field of a JSON object body, or ``None`` when the body has no such field."""
        return self.body.get(name) if isinstance(self.body, dict) else None


@dataclass(frozen=True)
class MemoryRead:
    """Bytes read from guest memory, and the machine state they were read in.

    ``address`` is the physical address the server resolved, as hex. ``state_seq`` is the journal
    entry that last changed the machine, and ``stop_seq`` the stop the machine was in (``None``
    while it ran). A write that carries ``If-State-Seq: state_seq`` is made only while the machine
    is still in that state.
    """

    address: str
    data: bytes
    state_seq: int
    stop_seq: int | None


def _int_header(headers: http.client.HTTPMessage, name: str) -> int | None:
    value = headers.get(name)
    try:
        return None if value is None else int(value)
    except ValueError:
        return None


class RustDosClient:
    """Requests to one rust-dos debug server at ``address`` (``host:port``).

    Each request opens its own connection, as the server closes it after every answer. A reply
    with a status of 400 or above raises :class:`~dinorefurb_dosbox_session.errors.ServerRefused`
    with the status and body. Transport errors (``OSError``) propagate.
    """

    def __init__(self, address: str, ids: RequestIds, timeout: float = 30.0) -> None:
        host, _, port = address.rpartition(":")
        if not host or not port.isdigit():
            raise ValueError(f"a debug server address is host:port, not {address!r}")
        self.address = address
        self.ids = ids
        self.timeout = timeout
        #: The ID of the last request sent, so a caller can find it in the journal after a
        #: transport error lost its reply.
        self.last_request_id: str | None = None
        self._host = host
        self._port = int(port)

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        if_state_seq: int | None = None,
        timeout: float | None = None,
    ) -> Reply:
        """Sends one request and returns the reply.

        ``query`` values of ``None`` are left out and booleans are sent as ``true`` or ``false``.
        ``body`` is sent as JSON. ``if_state_seq`` adds the ``If-State-Seq`` header, so the
        server carries the request out only while the machine is still paused in that state.

        :raises ServerRefused: the server answered with a status of 400 or above.
        :raises OSError: the request or its reply did not get through. A reply that arrives
            incomplete or unreadable raises ``ConnectionError``.
        """
        if query:
            pairs = {
                key: ("true" if value is True else "false" if value is False else value)
                for key, value in query.items()
                if value is not None
            }
            path = f"{path}?{urllib.parse.urlencode(pairs)}"
        request_id = self.ids.next()
        self.last_request_id = request_id
        headers = {"X-Request-Id": request_id}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if if_state_seq is not None:
            headers["If-State-Seq"] = str(if_state_seq)
        connection = http.client.HTTPConnection(self._host, self._port, timeout=timeout or self.timeout)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            parsed: Any = raw
            if content_type.startswith("application/json"):
                parsed = json.loads(raw.decode("utf-8"))
            elif content_type.startswith("text/"):
                parsed = raw.decode("utf-8", errors="replace")
            reply = Reply(
                response.status,
                parsed,
                request_id,
                _int_header(response.headers, "X-Debug-Req"),
                _int_header(response.headers, "X-Debug-State-Seq"),
            )
        except (http.client.HTTPException, ValueError) as error:
            # A reply cut short (a bad status line, a short body, JSON that ends early) leaves the
            # request's fate as unknown as a dropped connection does, so it raises the same way.
            raise ConnectionError(f"{method} {path} got no complete reply: {error!r}") from error
        finally:
            connection.close()
        if reply.status >= 400:
            raise ServerRefused(method, path, reply)
        return reply

    def get(self, path: str, **query: Any) -> Any:
        """The body of a ``GET`` of ``path`` with ``query``."""
        return self.request("GET", path, query=query).body

    # The server

    def version(self) -> dict[str, Any]:
        """``/api/version``: the server's name, version, ``api_version``, ``features`` and ``instance``."""
        return self.get("/api/version")

    def status(self) -> dict[str, Any]:
        """``/api/status``: whether the machine is paused, its stop, program, CPU mode and more."""
        return self.get("/api/status")

    def drives(self) -> dict[str, Any]:
        """``/api/drive``: the mounted drives."""
        return self.get("/api/drive")

    def log(self, grep: str | None = None, limit: int | None = None) -> str:
        """The emulator's log as text, optionally only lines that contain ``grep``."""
        return self.get("/api/log", grep=grep, limit=limit, format="text")

    # The machine

    def registers(self) -> dict[str, Any]:
        """``/api/registers``: the registers as hex strings, with a ``system`` object."""
        return self.get("/api/registers")

    def read_memory(self, address: str, length: int) -> MemoryRead:
        """Reads ``length`` bytes at ``address`` (``SEG:OFF``, ``lin:ADDR`` or ``phys:ADDR``, in hex).

        :raises ServerRefused: the server refused the address or the length.
        """
        if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
            raise ValueError(f"a memory read needs a positive length, not {length!r}")
        reply = self.request("GET", "/api/memory", query={"addr": address, "len": length, "format": "base64"})
        data = base64.b64decode(reply.field("base64") or "")
        state_seq = reply.field("state_seq")
        if not isinstance(state_seq, int) or len(data) != length:
            raise ServerRefused("GET", "/api/memory", reply)
        return MemoryRead(str(reply.field("addr")), data, state_seq, reply.field("stop_seq"))

    def disassemble(self, address: str | None = None, count: int = 10) -> dict[str, Any]:
        """``/api/disasm`` as JSON, from ``address`` or ``CS:IP``."""
        return self.get("/api/disasm", addr=address, count=count, format="json")

    def screen_text(self) -> str:
        """The text screen. The server refuses it (409) in a graphics mode."""
        return self.get("/api/screen/text", format="text")

    def screen(self) -> dict[str, Any]:
        """``/api/screen/text`` as JSON: the rows in ``lines``, and their colours in ``attrs``.

        ``attrs`` has, for each row, the runs of cells that share an attribute, as
        ``{"col", "len", "attr"}`` with the attribute in hex; ``col`` and ``len`` count characters
        of the row's line. A highlighted menu item is usually a run of its own. ``blink`` says
        whether attribute bit 7 blinks the character (else it selects a bright background) and
        ``mono`` whether the adapter is an MDA or Hercules. The server refuses it (409) in a
        graphics mode.
        """
        return self.get("/api/screen/text")

    def stats(self) -> dict[str, Any]:
        """``/api/stats``: the emulator's execution speed over about the last second.

        Its ``frame`` object covers the last quarter second: ``emulated_speed`` is emulated
        seconds per wall-clock second, near 1 while the guest runs at full speed, and ``fps`` the
        frames the program draws a second.
        """
        return self.get("/api/stats")

    def screenshot(self) -> bytes:
        """The screen as a PNG."""
        return self.get("/api/screenshot")

    # Breakpoints

    def breakpoints(self) -> dict[str, Any]:
        """``/api/breakpoints``: the breakpoints, by physical address, and the stops that are on."""
        return self.get("/api/breakpoints")

    def set_breakpoint(self, address: str, once: bool = False) -> dict[str, Any]:
        """Sets an execution breakpoint at ``address``; with ``once`` it goes once it stops the machine."""
        return self.request("POST", "/api/breakpoints", body={"addr": address, "once": once}).body

    def break_on(self, **stops: bool) -> dict[str, Any]:
        """Turns stops on or off: ``program_start``, ``program_exit`` or ``mode_switch``."""
        return self.request("POST", "/api/breakpoints", body=stops).body

    def remove_breakpoints(self, address: str | None = None) -> dict[str, Any]:
        """Removes the breakpoint at ``address``, or every breakpoint without one."""
        return self.request("DELETE", "/api/breakpoints", query={"addr": address}).body

    def watchpoints(self) -> dict[str, Any]:
        """``/api/watchpoints``: the watchpoints, with their values."""
        return self.get("/api/watchpoints")

    def set_watchpoint(self, address: str, length: int) -> dict[str, Any]:
        """Stops the machine right after an instruction changes 1, 2 or 4 bytes at ``address``."""
        return self.request("POST", "/api/watchpoints", body={"addr": address, "len": length}).body

    def remove_watchpoints(self, address: str | None = None) -> dict[str, Any]:
        """Removes the watchpoint at ``address``, or every watchpoint without one."""
        return self.request("DELETE", "/api/watchpoints", query={"addr": address}).body

    # The journal

    def journal(
        self,
        after: int,
        types: tuple[str, ...] = (),
        limit: int | None = None,
        wait_ms: int | None = None,
    ) -> dict[str, Any]:
        """The journal entries after entry ``after``, oldest first, optionally of some ``types``.

        With ``wait_ms`` the server waits up to that long for a matching entry. The reply has
        ``entries``, ``next`` (the ``after`` of the next page) and ``instance``.
        """
        return self.get(
            "/api/journal", after=after, type=",".join(types) or None, limit=limit, wait=wait_ms
        )

    def operation(self, op: int, wait_ms: int = 0) -> dict[str, Any]:
        """``/api/ops/N``: how operation ``op`` ended, waiting up to ``wait_ms`` for it to end.

        ``state`` is ``pending``, ``stopped``, ``done``, ``failed``, ``superseded`` or ``cancelled``.
        """
        timeout = max(self.timeout, wait_ms / 1000 + 10)
        return self.request("GET", f"/api/ops/{op}", query={"wait": wait_ms}, timeout=timeout).body
