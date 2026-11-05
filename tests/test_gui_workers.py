"""Unit tests for the GUI worker/UI bridge (pure logic, no display)."""

from cheerleader.gui.workers import (
    BusyTracker,
    CancelFlag,
    EventKind,
    Generation,
    UiBridge,
    WorkerHandle,
    start_worker,
)


def test_cancel_flag() -> None:
    flag = CancelFlag()
    assert not flag.cancelled
    assert not flag
    flag.cancel()
    assert flag.cancelled
    assert flag


def test_bridge_empty_drain() -> None:
    bridge = UiBridge()
    assert bridge.drain() == []


def test_bridge_fifo_order() -> None:
    bridge = UiBridge()
    bridge.post(1, EventKind.PROGRESS, 0.5)
    bridge.post(1, EventKind.FOUND, (None, 3, False))
    bridge.post(2, EventKind.FAILED, "io error")
    events = bridge.drain()
    assert [(e.generation, e.kind, e.payload) for e in events] == [
        (1, EventKind.PROGRESS, 0.5),
        (1, EventKind.FOUND, (None, 3, False)),
        (2, EventKind.FAILED, "io error"),
    ]
    assert all(e.worker is None for e in events)
    assert bridge.drain() == []  # fully drained


def test_bridge_carries_worker_handle() -> None:
    bridge = UiBridge()
    handle = WorkerHandle()
    bridge.post(3, EventKind.SAVE_DONE, "/tmp/out", worker=handle)
    (event,) = bridge.drain()
    assert event.worker is handle
    assert event.kind is EventKind.SAVE_DONE


def test_bridge_cross_thread_post() -> None:
    bridge = UiBridge()

    def worker(_handle: WorkerHandle) -> None:
        bridge.post(7, EventKind.DIFF_BLOCKS, [1, 2, 3])

    handle = start_worker(worker, name="test-poster")
    assert handle.thread is not None
    handle.thread.join(timeout=5)
    events = bridge.drain()
    assert len(events) == 1
    assert events[0].generation == 7
    assert events[0].payload == [1, 2, 3]


def test_worker_receives_its_handle() -> None:
    seen: dict[str, WorkerHandle] = {}

    def worker(handle: WorkerHandle) -> None:
        seen["handle"] = handle

    handle = start_worker(worker, name="test-identity")
    assert handle.thread is not None
    handle.thread.join(timeout=5)
    assert seen["handle"] is handle


def test_start_worker_swallows_exceptions(capsys) -> None:
    def worker(_handle: WorkerHandle) -> None:
        raise RuntimeError("boom")

    handle = start_worker(worker, name="crasher")
    assert handle.thread is not None
    handle.thread.join(timeout=5)
    assert not handle.thread.is_alive()
    assert "boom" in capsys.readouterr().err


def test_generation_stale_drop() -> None:
    gen = Generation()
    assert gen.current == 0
    first = gen.next()
    assert first == 1
    assert gen.is_current(first)
    second = gen.next()
    assert second == 2
    assert not gen.is_current(first)
    assert gen.is_current(second)


def test_busy_tracker_lifecycle() -> None:
    tracker = BusyTracker()
    assert tracker.op is None
    w1, w2 = WorkerHandle(), WorkerHandle()
    tracker.start("searching", w1)
    assert tracker.op == "searching"
    assert not tracker.is_stale(w1)
    assert tracker.is_stale(w2)
    assert tracker.is_stale(None)
    # A restart supersedes the old run.
    tracker.start("searching", w2)
    assert tracker.is_stale(w1)
    assert not tracker.is_stale(w2)
    tracker.clear()
    assert tracker.op is None
    # A completion landing after clear is stale.
    assert tracker.is_stale(w2)
