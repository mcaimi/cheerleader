"""
Main-thread / worker-thread bridge for the Dear PyGui hex UI.

Dear PyGui widgets are manipulated from the main (render) thread. Long-running
hex operations (search, diff, save) therefore run on plain ``threading.Thread``
workers and report their results as events on a :class:`UiBridge` queue; the
frame loop drains the queue once per frame and dispatches each event to the
active controller (see :mod:`cheerleader.gui.app`).

Concurrency contracts (ported from the Textual hex tabs):

* **FIFO** — events are observed in post order.
* **Generations** — every event carries the controller-state generation it
belongs to; controllers drop events whose generation no longer matches
(:class:`Generation`, the port of the TUI ``_gen`` counter). A bumped
generation also means "the worker of the old generation is obsolete"; its
I/O failures (document closed underneath it) are treated as cancellation,
not errors — the worker decides via its own ``cancelled`` check.
* **Exclusive operations** — an operation that must not run concurrently
(saving) is tracked with a :class:`BusyTracker` (the port of the TUI
``_Busy`` pattern): completion events carry the :class:`WorkerHandle` that
produced them, and :meth:`BusyTracker.is_stale` drops completions from a
superseded or cancelled run.
* **Cancellation** — workers poll :attr:`WorkerHandle.cancel
<WorkerHandle>`; :class:`CancelFlag.cancelled` plugs directly into the
``cancelled`` callable of ``HexDocument.find`` / ``rfind`` / ``iter_diff``.
"""

import enum
import queue
import sys
import threading
import traceback
from collections.abc import Callable
from typing import Any, NamedTuple


class CancelFlag:
    """A thread-safe cancellation flag shared with worker threads."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation (safe from any thread)."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` has been called."""
        return self._event.is_set()

    def __bool__(self) -> bool:
        return self._event.is_set()


class WorkerHandle:
    """
    Identity + cancellation flag for one background operation run.

    Completion events carry the handle so the receiving controller can
    recognize stale completions of superseded runs (see :class:`BusyTracker`).
    ``thread`` is set by :func:`start_worker` and may be joined on shutdown.
    """

    def __init__(self) -> None:
        self.cancel = CancelFlag()
        self.thread: threading.Thread | None = None


def start_worker(
    target: Callable[[WorkerHandle], None],
    *,
    name: str = "gui-worker",
) -> WorkerHandle:
    """
    Run ``target(handle)`` on a daemon thread; return the handle immediately.

    The worker must poll ``handle.cancel`` between chunks. Errors are reported
    to the UI by the worker itself (post a FAILED event); an exception escaping
    *target* is printed to stderr instead of vanishing silently inside the
    thread. Daemon threads die with the process, so a shutdown may interrupt a
    run mid-chunk — safe by design: documents are read via ``os.pread`` and
    saves go through temp-file + atomic rename.
    """
    handle = WorkerHandle()

    def _runner() -> None:
        try:
            target(handle)
        except BaseException:  # noqa: BLE001 - daemon-thread crash guard
            print(f"gui worker {name!r} crashed:", file=sys.stderr)
            traceback.print_exc()

    thread = threading.Thread(target=_runner, name=name or "gui-worker", daemon=True)
    handle.thread = thread
    thread.start()
    return handle


class EventKind(enum.Enum):
    """
    Kinds of events a worker posts to the UI thread.

    Payloads are controller-specific; the kinds below cover the hex editor
    and hex-compare flows.
    """

    OPEN_FAILED = "open_failed"  # payload: str error message
    PROGRESS = "progress"  # payload: float in 0..1
    DIFF_BLOCKS = "diff_blocks"  # payload: list[int] of new differing block indexes
    DIFF_DONE = "diff_done"  # payload: (first_block | None, diff_byte_count)
    FOUND = "found"  # payload: (hit | None, pattern_len, wrapped)
    SAVE_DONE = "save_done"  # payload: str path written
    FAILED = "failed"  # payload: str error message
    BINARY_PARSED = "binary_parsed"  # payload: BinaryInfo (may carry .error)
    DISASM_DONE = "disasm_done"  # payload: (seg, sect, instrs, graph | None)
    STRINGS_DONE = "strings_done"  # payload: list[BinaryString]


class UiEvent(NamedTuple):
    """
    One worker→UI message.

    *generation* is the controller-state generation the event belongs to;
    *worker* identifies the producing run for stale-drop (may be ``None``
    for events posted synchronously by the controller itself).
    """

    generation: int
    kind: EventKind
    payload: Any = None
    worker: WorkerHandle | None = None


class UiBridge:
    """Thread-safe queue of :class:`UiEvent`, drained once per frame."""

    def __init__(self) -> None:
        self._queue: queue.Queue[UiEvent] = queue.Queue()

    def post(
        self,
        generation: int,
        kind: EventKind,
        payload: Any = None,
        worker: WorkerHandle | None = None,
    ) -> None:
        """Queue an event (safe from any thread)."""
        self._queue.put(UiEvent(generation, kind, payload, worker))

    def drain(self) -> list[UiEvent]:
        """Remove and return all pending events, oldest first."""
        events: list[UiEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events


class Generation:
    """
    Monotonic generation counter for stale-callback dropping.

    The port of the Textual ``_gen`` pattern: bump on every (re)load; events
    from an older generation are dropped by the controller.
    """

    def __init__(self) -> None:
        self._n = 0

    def next(self) -> int:
        """Start a new generation and return its id."""
        self._n += 1
        return self._n

    @property
    def current(self) -> int:
        return self._n

    def is_current(self, generation: int) -> bool:
        return generation == self._n


class BusyTracker:
    """
    Exclusive-operation marker (the port of the TUI ``_Busy`` pattern).

    A controller keeps one tracker. *op* names the in-flight operation
    (``"saving"``, ``"searching"``); completion events carry the
    :class:`WorkerHandle` that produced them, and :meth:`is_stale` drops
    completions from a superseded run — e.g. a search cancelled and
    restarted while an old completion message was still in flight.
    """

    def __init__(self) -> None:
        self._op: str | None = None
        self._worker: WorkerHandle | None = None

    @property
    def op(self) -> str | None:
        """Name of the in-flight operation, or ``None`` when idle."""
        return self._op

    def start(self, op: str, worker: WorkerHandle) -> None:
        """Mark *op* as in flight, owned by *worker*."""
        self._op = op
        self._worker = worker

    @property
    def worker(self) -> WorkerHandle | None:
        """The handle of the in-flight run, or ``None`` when idle."""
        return self._worker

    def clear(self) -> None:
        """Mark the tracker idle."""
        self._op = None
        self._worker = None

    def is_stale(self, worker: WorkerHandle | None) -> bool:
        """True when *worker* is not the currently tracked run."""
        return self._worker is None or worker is not self._worker
