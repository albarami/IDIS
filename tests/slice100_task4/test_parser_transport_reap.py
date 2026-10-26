"""Post-kill reap contracts for OCR and media subprocess cleanup.

Process doubles never sleep and never start a real ``multiprocessing.Process``.
``join`` advances a patched clock only when the test asks it to.
"""

from __future__ import annotations

import logging
import sys
import types
from queue import Empty

import pytest

from idis.parsers import media, ocr
from idis.parsers.media import MediaTimeoutError, _MediaDeadline
from idis.parsers.ocr import OcrTimeoutError

_ABSENT_PID = 2_147_483_647
_REAP_FLOOR_SECONDS = 0.20
_POSITIVE_JOIN_SECONDS = 0.01
_LARGE_REMAINING_SECONDS = 10.0
_EXPIRED_CLOCK_SECONDS = 50.0
_HUNG_TIMEOUT_SECONDS = 2.0
_MAX_QUEUE_GETS = 1_000
_OCR_LOGGER = "idis.parsers.ocr"
_MEDIA_LOGGER = "idis.parsers.media"
_OCR_LEAK_CODE = "OCR_WORKER_HANDLE_UNREAPED"
_MEDIA_LEAK_CODE = "MEDIA_WORKER_HANDLE_UNREAPED"
_FAKE_ROOT_PID = 424_242
_TREE_TIMEOUT_SECONDS = 0.0
_GRANDCHILD_COUNT = 2
_PARSER_NAMES = ("ocr", "media")
_NO_GRANDCHILD_TRACES = (
    ("children:True", "process-terminate"),
    ("children:True", "wait:0.0", "process-terminate"),
)


class _Clock:
    """Monotonic stand-in. It moves only when a double advances it."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _CloseDouble:
    """Parent-side process double for the close and reap path."""

    def __init__(
        self,
        *,
        clock: _Clock,
        pid: int,
        reap_on_join_at_least: float | None,
        advance_clock: bool,
        reap_only_after_kill: bool = False,
    ) -> None:
        self.clock = clock
        self.pid = pid
        self.reap_on_join_at_least = reap_on_join_at_least
        self.advance_clock = advance_clock
        self.reap_only_after_kill = reap_only_after_kill
        self.alive = True
        self.events: list[str] = []
        self.join_timeouts: list[float | None] = []
        self.post_kill_joins: list[float | None] = []
        self.close_calls = 0
        self.kill_calls = 0
        self.alive_at_close: list[bool] = []
        self._killed = False

    def is_alive(self) -> bool:
        self.events.append(f"is_alive:{self.alive}")
        return self.alive

    def start(self) -> None:
        self.events.append("start")

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self._killed = True
        self.events.append("kill")

    def join(self, timeout: float | None = None) -> None:
        if self._killed:
            self.post_kill_joins.append(timeout)
        self.join_timeouts.append(timeout)
        kind = "join-after-kill" if self._killed else "join"
        self.events.append(f"{kind}:{timeout!r}")
        if self.advance_clock and timeout is not None:
            self.clock.advance(timeout)
        if self._join_reaps(timeout):
            self.alive = False
            self.events.append("reaped")

    def close(self) -> None:
        self.close_calls += 1
        self.alive_at_close.append(self.alive)
        self.events.append("close")
        if self.alive:
            self.events.append("close-while-alive")

    def _join_reaps(self, timeout: float | None) -> bool:
        if self.reap_on_join_at_least is None or timeout is None:
            return False
        if timeout < self.reap_on_join_at_least:
            return False
        return self._killed if self.reap_only_after_kill else True


class _EmptyQueue:
    """Queue double. ``get`` advances the clock and never blocks."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.gets = 0
        self.closed = False
        self.cancelled = False

    def get(self, *_args: object, **kwargs: object) -> object:
        self.gets += 1
        if self.gets > _MAX_QUEUE_GETS:
            raise AssertionError("deadline was not reached")
        timeout = kwargs.get("timeout", 0.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            timeout = 0.0
        self._clock.advance(float(timeout))
        raise Empty

    def get_nowait(self) -> object:
        raise Empty

    def close(self) -> None:
        self.closed = True

    def cancel_join_thread(self) -> None:
        self.cancelled = True

    def join_thread(self) -> None:
        return None


@pytest.fixture
def absent_pid() -> int:
    """Return a pid ``psutil`` will not attach to a live process."""
    import psutil

    if psutil.pid_exists(_ABSENT_PID):
        raise RuntimeError(f"fixture pid {_ABSENT_PID} is live")
    return _ABSENT_PID


def _positive_join_double(clock: _Clock, pid: int) -> _CloseDouble:
    """Die only once ``join`` is called with a timeout of at least 0.01 s."""
    return _CloseDouble(
        clock=clock,
        pid=pid,
        reap_on_join_at_least=_POSITIVE_JOIN_SECONDS,
        advance_clock=True,
    )


def _unreaped_double(clock: _Clock, pid: int) -> _CloseDouble:
    """Stay alive. ``join`` spends its timeout on the patched clock."""
    return _CloseDouble(
        clock=clock,
        pid=pid,
        reap_on_join_at_least=None,
        advance_clock=True,
    )


def _budget_pin_double(clock: _Clock, pid: int, *, advance_clock: bool) -> _CloseDouble:
    """Stay alive through the budget join, then reap on the post-kill join."""
    return _CloseDouble(
        clock=clock,
        pid=pid,
        reap_on_join_at_least=_POSITIVE_JOIN_SECONDS,
        advance_clock=advance_clock,
        reap_only_after_kill=True,
    )


def _patch_ocr_clock(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    monkeypatch.setattr(ocr.time, "monotonic", clock)


def _expired_media_deadline(clock: _Clock) -> _MediaDeadline:
    return _MediaDeadline(expires_at=clock.now - 1.0, clock=clock)


def _large_media_deadline(clock: _Clock) -> _MediaDeadline:
    return _MediaDeadline(expires_at=clock.now + _LARGE_REMAINING_SECONDS, clock=clock)


def _assert_single_leak(
    caplog: pytest.LogCaptureFixture,
    *,
    logger_name: str,
    token: str,
    pid: int,
) -> None:
    records = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and record.name == logger_name
    ]
    heads = [record.getMessage().partition(" ")[0] for record in records]
    assert heads == [token]
    message = records[0].getMessage()
    assert message == token or message.startswith(f"{token} ")
    recorded_pid = getattr(records[0], "pid", None)
    if recorded_pid is None:
        assert str(pid) in message
    else:
        assert recorded_pid == pid


def _assert_closed_only_after_reap(process: _CloseDouble) -> None:
    assert "close" in process.events, process.events
    assert "close-while-alive" not in process.events, process.events
    close_at = process.events.index("close")
    prior = process.events[:close_at]
    reap_joins = [
        index for index, event in enumerate(prior) if event.startswith("join-after-kill:")
    ]
    assert reap_joins, process.events
    assert "reaped" in prior, process.events
    assert reap_joins[0] < prior.index("reaped")
    assert prior[-1] == "is_alive:False", process.events
    assert process.alive_at_close == [False]


def _install_doubles(
    monkeypatch: pytest.MonkeyPatch,
    mp_module: types.ModuleType,
    process: _CloseDouble,
    queue: _EmptyQueue,
) -> None:
    def process_factory(*_args: object, **_kwargs: object) -> _CloseDouble:
        return process

    def queue_factory(*_args: object, **_kwargs: object) -> _EmptyQueue:
        return queue

    monkeypatch.setattr(mp_module, "Process", process_factory)
    monkeypatch.setattr(mp_module, "Queue", queue_factory)


def _unused_target(*_args: object) -> None:
    return None


class _PsutilGrandchild:
    """Grandchild double. It records signals and is never a real process."""

    def __init__(self, trace: list[str], label: str) -> None:
        self._trace = trace
        self.label = label
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._trace.append("grandchild-terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self._trace.append("grandchild-kill")

    def wait(self, _timeout: float | None = None) -> None:
        self.wait_calls += 1
        self._trace.append("grandchild-wait")

    def __repr__(self) -> str:
        return self.label


class _PsutilRoot:
    """psutil.Process double for the multiprocessing root."""

    def __init__(
        self,
        trace: list[str],
        children: list[_PsutilGrandchild],
        *,
        raise_on_signal: bool,
    ) -> None:
        self._trace = trace
        self._children = children
        self._raise_on_signal = raise_on_signal
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0

    def children(self, recursive: bool = False) -> list[_PsutilGrandchild]:
        self._trace.append(f"children:{recursive}")
        return list(self._children)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._trace.append("psutil-root-terminate")
        if self._raise_on_signal:
            raise RuntimeError("psutil root terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self._trace.append("psutil-root-kill")
        if self._raise_on_signal:
            raise RuntimeError("psutil root kill")

    def wait(self, _timeout: float | None = None) -> None:
        self.wait_calls += 1
        self._trace.append("psutil-root-wait")
        if self._raise_on_signal:
            raise RuntimeError("psutil root wait")

    def __repr__(self) -> str:
        return "psutil-root"


class _WaitProcsRecord:
    """Arguments received by the fake ``psutil.wait_procs``."""

    def __init__(self) -> None:
        self.procs: list[list[object]] = []
        self.timeouts: list[float | None] = []
        self.pids: list[int] = []


class _TerminateDouble:
    """multiprocessing.Process stand-in for ``_terminate_process_tree``."""

    def __init__(self, trace: list[str], pid: int | None) -> None:
        self._trace = trace
        self.pid = pid
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._trace.append("process-terminate")

    def kill(self) -> None:
        self.kill_calls += 1
        self._trace.append("process-kill")


def _parser(parser_name: str) -> types.ModuleType:
    if parser_name == "ocr":
        return ocr
    return media


def _call_terminate(parser_name: str, process: _TerminateDouble) -> None:
    parser = _parser(parser_name)
    parser._terminate_process_tree(  # noqa: SLF001
        process,  # type: ignore[arg-type]
        timeout_seconds=_TREE_TIMEOUT_SECONDS,
    )


def _install_psutil(
    monkeypatch: pytest.MonkeyPatch,
    trace: list[str],
    *,
    child_count: int,
    raise_on_root_signal: bool,
    leave_last_alive: bool,
) -> tuple[_PsutilRoot, list[_PsutilGrandchild], _WaitProcsRecord]:
    """Install a fake ``psutil`` whose root signals raise when asked."""
    children = [_PsutilGrandchild(trace, f"grandchild-{index}") for index in range(child_count)]
    root = _PsutilRoot(trace, children, raise_on_signal=raise_on_root_signal)
    alive = children[-1] if leave_last_alive and children else None
    record = _WaitProcsRecord()

    def process_factory(pid: int) -> _PsutilRoot:
        record.pids.append(pid)
        return root

    def wait_procs(
        procs: list[object],
        timeout: float | None = None,
        _callback: object = None,
    ) -> tuple[list[object], list[object]]:
        copied = list(procs)
        record.procs.append(copied)
        record.timeouts.append(timeout)
        trace.append(f"wait:{timeout}")
        if alive is None or all(item is not alive for item in copied):
            return (copied, [])
        gone = [item for item in copied if item is not alive]
        return (gone, [alive])

    fake = types.ModuleType("psutil")
    fake.Process = process_factory
    fake.wait_procs = wait_procs
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return root, children, record


def _install_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
    trace: list[str],
) -> _WaitProcsRecord:
    """Install a fake ``psutil`` whose ``Process`` raises ``NoSuchProcess``."""
    record = _WaitProcsRecord()

    class NoSuchProcess(Exception):
        """Stand-in for ``psutil.NoSuchProcess``."""

    def process_factory(pid: int) -> None:
        record.pids.append(pid)
        raise NoSuchProcess(pid)

    def wait_procs(
        procs: list[object],
        timeout: float | None = None,
        _callback: object = None,
    ) -> tuple[list[object], list[object]]:
        record.procs.append(list(procs))
        record.timeouts.append(timeout)
        trace.append(f"wait:{timeout}")
        return ([], [])

    fake = types.ModuleType("psutil")
    fake.NoSuchProcess = NoSuchProcess
    fake.Process = process_factory
    fake.wait_procs = wait_procs
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return record


def _close_started_expired(
    parser_name: str,
    process: _CloseDouble,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if parser_name == "ocr":
        _patch_ocr_clock(monkeypatch, clock)
        ocr._close_ocr_process(  # noqa: SLF001
            process=process,  # type: ignore[arg-type]
            started=True,
            deadline=clock.now - 1.0,
        )
        return
    media._close_media_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_expired_media_deadline(clock),
    )


def test_ocr_zero_budget_close_waits_past_a_single_nonblocking_join(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    _patch_ocr_clock(monkeypatch, clock)
    process = _positive_join_double(clock, absent_pid)

    ocr._close_ocr_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=clock.now - 1.0,
    )

    assert process.close_calls == 1, process.events
    assert process.alive_at_close == [False]


def test_media_zero_budget_close_waits_past_a_single_nonblocking_join(
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    process = _positive_join_double(clock, absent_pid)

    media._close_media_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_expired_media_deadline(clock),
    )

    assert process.close_calls == 1, process.events
    assert process.alive_at_close == [False]


def test_ocr_unreaped_process_is_logged_when_the_reap_bound_is_spent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    _patch_ocr_clock(monkeypatch, clock)
    process = _unreaped_double(clock, absent_pid)

    with caplog.at_level(logging.ERROR, logger=_OCR_LOGGER):
        ocr._close_ocr_process(  # noqa: SLF001
            process=process,  # type: ignore[arg-type]
            started=True,
            deadline=clock.now - 1.0,
        )

    _assert_single_leak(
        caplog,
        logger_name=_OCR_LOGGER,
        token=_OCR_LEAK_CODE,
        pid=absent_pid,
    )
    assert process.close_calls == 0
    assert clock.now == _EXPIRED_CLOCK_SECONDS + _REAP_FLOOR_SECONDS


def test_media_unreaped_process_is_logged_when_the_reap_bound_is_spent(
    caplog: pytest.LogCaptureFixture,
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    process = _unreaped_double(clock, absent_pid)

    with caplog.at_level(logging.ERROR, logger=_MEDIA_LOGGER):
        media._close_media_process(  # noqa: SLF001
            process=process,  # type: ignore[arg-type]
            started=True,
            deadline=_expired_media_deadline(clock),
        )

    _assert_single_leak(
        caplog,
        logger_name=_MEDIA_LOGGER,
        token=_MEDIA_LEAK_CODE,
        pid=absent_pid,
    )
    assert process.close_calls == 0
    assert clock.now == _EXPIRED_CLOCK_SECONDS + _REAP_FLOOR_SECONDS


def test_ocr_expired_budget_final_join_is_the_reap_floor(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    _patch_ocr_clock(monkeypatch, clock)
    process = _budget_pin_double(clock, absent_pid, advance_clock=False)

    ocr._close_ocr_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=clock.now - 1.0,
    )

    assert process.post_kill_joins == [_REAP_FLOOR_SECONDS], process.join_timeouts
    assert process.join_timeouts[0] == 0.0
    assert ocr._OCR_PROCESS_REAP_SECONDS == _REAP_FLOOR_SECONDS  # noqa: SLF001


def test_media_expired_budget_final_join_is_the_reap_floor(absent_pid: int) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    process = _budget_pin_double(clock, absent_pid, advance_clock=False)

    media._close_media_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_expired_media_deadline(clock),
    )

    assert process.post_kill_joins == [_REAP_FLOOR_SECONDS], process.join_timeouts
    assert process.join_timeouts[0] == 0.0
    assert media._MEDIA_PROCESS_REAP_SECONDS == _REAP_FLOOR_SECONDS  # noqa: SLF001


def test_ocr_large_remaining_budget_final_join_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(0.0)
    _patch_ocr_clock(monkeypatch, clock)
    process = _budget_pin_double(clock, absent_pid, advance_clock=False)

    ocr._close_ocr_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_LARGE_REMAINING_SECONDS,
    )

    assert process.post_kill_joins == [_LARGE_REMAINING_SECONDS], process.join_timeouts
    assert process.join_timeouts[0] == _LARGE_REMAINING_SECONDS / 2.0


def test_media_large_remaining_budget_final_join_is_unchanged(absent_pid: int) -> None:
    clock = _Clock(0.0)
    process = _budget_pin_double(clock, absent_pid, advance_clock=False)

    media._close_media_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_large_media_deadline(clock),
    )

    assert process.post_kill_joins == [_LARGE_REMAINING_SECONDS], process.join_timeouts
    assert process.join_timeouts[0] == _LARGE_REMAINING_SECONDS / 2.0


def test_ocr_hung_worker_times_out_kills_and_does_not_close(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(0.0)
    _patch_ocr_clock(monkeypatch, clock)
    process = _unreaped_double(clock, absent_pid)
    _install_doubles(monkeypatch, ocr.mp, process, _EmptyQueue(clock))

    with pytest.raises(OcrTimeoutError):
        ocr._run_ocr_worker(  # noqa: SLF001
            target=_unused_target,
            args=(),
            timeout_seconds=_HUNG_TIMEOUT_SECONDS,
        )

    assert process.kill_calls == 1
    assert process.close_calls == 0
    assert process.alive is True


def test_media_hung_worker_times_out_kills_and_does_not_close(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(0.0)
    process = _unreaped_double(clock, absent_pid)
    _install_doubles(monkeypatch, media.mp, process, _EmptyQueue(clock))
    deadline = _MediaDeadline(expires_at=_HUNG_TIMEOUT_SECONDS, clock=clock)

    with pytest.raises(MediaTimeoutError):
        media._run_media_worker(  # noqa: SLF001
            target=_unused_target,
            args=(),
            deadline=deadline,
        )

    assert process.kill_calls == 1
    assert process.close_calls == 0
    assert process.alive is True


def test_ocr_reapable_process_is_closed_only_after_the_reap_join(
    monkeypatch: pytest.MonkeyPatch,
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    _patch_ocr_clock(monkeypatch, clock)
    process = _positive_join_double(clock, absent_pid)

    ocr._close_ocr_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=clock.now - 1.0,
    )

    _assert_closed_only_after_reap(process)


def test_media_reapable_process_is_closed_only_after_the_reap_join(
    absent_pid: int,
) -> None:
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    process = _positive_join_double(clock, absent_pid)

    media._close_media_process(  # noqa: SLF001
        process=process,  # type: ignore[arg-type]
        started=True,
        deadline=_expired_media_deadline(clock),
    )

    _assert_closed_only_after_reap(process)


def _root_was_waited(record: _WaitProcsRecord, root: _PsutilRoot) -> bool:
    return any(item is root for procs in record.procs for item in procs)


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_psutil_never_waits_on_or_signals_the_root(
    monkeypatch: pytest.MonkeyPatch,
    parser_name: str,
) -> None:
    trace: list[str] = []
    root, children, record = _install_psutil(
        monkeypatch,
        trace,
        child_count=_GRANDCHILD_COUNT,
        raise_on_root_signal=True,
        leave_last_alive=True,
    )
    process = _TerminateDouble(trace, _FAKE_ROOT_PID)

    _call_terminate(parser_name, process)

    first, second = children
    assert record.procs == [[first, second]], (record.procs, trace)
    assert not _root_was_waited(record, root)
    assert record.timeouts == [_TREE_TIMEOUT_SECONDS]
    assert record.pids == [_FAKE_ROOT_PID]
    assert first.terminate_calls == 1
    assert second.terminate_calls == 1
    assert second.kill_calls == 1
    assert first.kill_calls == 0
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert root.terminate_calls == 0
    assert root.kill_calls == 0
    assert root.wait_calls == 0
    assert trace == [
        "children:True",
        "grandchild-terminate",
        "grandchild-terminate",
        "wait:0.0",
        "grandchild-kill",
        "process-terminate",
    ]


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_terminate_with_no_grandchildren_does_not_wait_on_the_root(
    monkeypatch: pytest.MonkeyPatch,
    parser_name: str,
) -> None:
    trace: list[str] = []
    root, children, record = _install_psutil(
        monkeypatch,
        trace,
        child_count=0,
        raise_on_root_signal=False,
        leave_last_alive=False,
    )
    process = _TerminateDouble(trace, _FAKE_ROOT_PID)

    _call_terminate(parser_name, process)

    assert children == []
    assert tuple(trace) in _NO_GRANDCHILD_TRACES, (trace, record.procs)
    assert record.procs in ([], [[]])
    assert not _root_was_waited(record, root)
    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert root.terminate_calls == 0
    assert root.kill_calls == 0
    assert root.wait_calls == 0


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_terminate_falls_back_when_psutil_import_fails(
    monkeypatch: pytest.MonkeyPatch,
    parser_name: str,
) -> None:
    trace: list[str] = []
    monkeypatch.setitem(sys.modules, "psutil", None)
    process = _TerminateDouble(trace, _FAKE_ROOT_PID)

    _call_terminate(parser_name, process)

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert trace == ["process-terminate"]


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_terminate_falls_back_when_process_lookup_raises(
    monkeypatch: pytest.MonkeyPatch,
    parser_name: str,
) -> None:
    trace: list[str] = []
    record = _install_lookup_failure(monkeypatch, trace)
    process = _TerminateDouble(trace, _FAKE_ROOT_PID)

    _call_terminate(parser_name, process)

    assert process.terminate_calls == 1
    assert process.kill_calls == 0
    assert record.procs == []
    assert record.pids == [_FAKE_ROOT_PID]
    assert trace == ["process-terminate"]


@pytest.mark.parametrize("parser_name", _PARSER_NAMES)
def test_close_never_calls_psutil_on_the_root(
    monkeypatch: pytest.MonkeyPatch,
    parser_name: str,
) -> None:
    trace: list[str] = []
    root, children, record = _install_psutil(
        monkeypatch,
        trace,
        child_count=_GRANDCHILD_COUNT,
        raise_on_root_signal=True,
        leave_last_alive=True,
    )
    clock = _Clock(_EXPIRED_CLOCK_SECONDS)
    process = _budget_pin_double(clock, _FAKE_ROOT_PID, advance_clock=False)

    _close_started_expired(parser_name, process, clock, monkeypatch)

    assert root.terminate_calls == 0, (trace, record.procs, process.close_calls, process.events)
    assert root.kill_calls == 0
    assert root.wait_calls == 0
    assert process.close_calls == 1, process.events
    assert not _root_was_waited(record, root)
    first, second = children
    assert record.procs == [[first, second]]
    assert second.kill_calls == 1
    assert first.kill_calls == 0
