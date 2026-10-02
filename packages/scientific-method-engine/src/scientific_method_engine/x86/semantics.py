"""The seam between instruction semantics and the evidence layer (ADR 0003, phase 2).

A semantics backend computes what an instruction does to values. The evidence layer keeps segment
attribution, the memory model, producers, guards, assumptions, frames, events and every control
transfer. A backend changes a ``State`` only through its public methods: ``get``, ``put``,
``reg``, ``setreg``, ``push``, ``pop``, ``access``, ``address``, ``event`` and the flag records
(``set_flags``, ``forget_flags``, ``carry``, ``carry_value``, ``save_flags``, ``restore_flags``,
``direction_flag`` and ``interrupt_flag``).
"""
from contextlib import contextmanager
from typing import Protocol


class Backend(Protocol):
    """The operations a semantics backend supplies."""

    name: str

    def ordinary(self, state, ins, image):
        """Apply one instruction that is neither a control transfer nor a string operation.

        Raise ``StopPath`` naming the reason when the instruction's semantics are not supported.
        """

    def condition(self, state, mnemonic):
        """Evaluate a conditional branch's flag predicate on the current flags.

        Return ``(answer, info)``: ``answer`` is True, False or None when unresolved, and ``info``
        holds the ``branch`` event fields that describe the flag producer.
        """

    def string_iteration(self, state, ins, operation, width, source_segment, delta):
        """Apply one iteration of an accepted ``movs``, ``stos`` or ``lods`` string form.

        ``source_segment`` names the source operand's segment register (``movs`` and ``lods``
        only), and ``delta`` is the signed step the direction flag selects for SI and DI.
        """


_backends = {}
_default = None
_selected = None


def register(backend, default=False):
    """Make ``backend`` selectable by its name. The default backend serves every report."""
    global _default
    _backends[backend.name] = backend
    if default or _default is None:
        _default = backend.name


def current():
    """The backend a new ``State`` uses: the default unless a test selected another."""
    return _backends[_selected or _default]


def names():
    """The registered backends' names, the default first."""
    return [_default] + sorted(name for name in _backends if name != _default)


@contextmanager
def selected(name):
    """Make states created inside the block use the backend ``name``.

    Differential tests use this to run one case on each backend. Reports and the CLI never select
    a backend; they use the default.
    """
    global _selected
    previous, _selected = _selected, _backends[name].name
    try:
        yield _backends[name]
    finally:
        _selected = previous
