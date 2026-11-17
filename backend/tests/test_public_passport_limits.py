from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from app.shared import public_passport_limits as module
from app.shared.public_passport_limits import PassportLimitsUnavailable, PublicPassportLimits


BACKEND = Path(__file__).resolve().parents[1]
# The Windows venv executable is a redirector with a different PID from its interpreter.
# This helper uses only the standard library; launch the actual interpreter so termination
# targets the process holding the OS lock, rather than racing redirector/child teardown.
CHILD_PYTHON = getattr(sys, "_base_executable", sys.executable)


def _stop(process):
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)


def test_failure_budget_is_shared_across_processes(tmp_path):
    script = '''
import sys
from app.shared.public_passport_limits import PublicPassportLimits
limits = PublicPassportLimits(sys.argv[1], failures=7)
print(sum(limits.allow_failure(now=10) for _ in range(20)))
'''
    processes = [subprocess.Popen([CHILD_PYTHON, "-c", script, str(tmp_path)], cwd=BACKEND,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        counts = []
        for process in processes:
            output, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
            counts.append(int(output))
        assert sum(counts) == 7
    finally:
        for process in processes:
            _stop(process)


def test_pdf_slots_are_shared_and_release_when_a_process_dies(tmp_path):
    script = '''
import json, os, sys, time
from pathlib import Path
from app.shared.public_passport_limits import PublicPassportLimits
directory = Path(sys.argv[1])
with PublicPassportLimits(directory).pdf_slot() as acquired:
    marker = directory / sys.argv[2]
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps({"acquired": acquired, "pid": os.getpid()}))
    temporary.replace(marker)
    deadline = time.monotonic() + 20
    while not (directory / "release").exists() and time.monotonic() < deadline:
        time.sleep(.02)
'''
    processes = [subprocess.Popen([CHILD_PYTHON, "-c", script, str(tmp_path), f"ready-{index}"], cwd=BACKEND)
                 for index in range(2)]
    try:
        deadline = time.monotonic() + 15
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert all(process.poll() is None for process in processes), "Slot holder exited before readiness"
            assert time.monotonic() < deadline, "PDF slot holders did not become ready"
            time.sleep(.02)
        for index, process in enumerate(processes):
            assert json.loads((tmp_path / f"ready-{index}").read_text()) == {"acquired": True, "pid": process.pid}
        limits = PublicPassportLimits(tmp_path)
        with limits.pdf_slot() as acquired:
            assert not acquired
        processes[0].terminate()
        processes[0].wait(timeout=5)
        with limits.pdf_slot() as acquired:
            assert acquired
            with limits.pdf_slot() as third:
                assert not third
    finally:
        (tmp_path / "release").touch()
        for process in processes:
            _stop(process)


def test_success_budget_is_thread_safe_and_separate_per_instrument_and_token(tmp_path):
    limits = PublicPassportLimits(tmp_path, views=7)
    def view(_):
        with limits.view("instrument-a", "private-token-a", now=10) as allowed:
            return allowed
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(view, range(40))) == 7
    with limits.view("instrument-b", "private-token-a", now=10) as allowed:
        assert allowed
    with limits.view("instrument-a", "private-token-b", now=10) as allowed:
        assert allowed
    assert "private-token" not in (tmp_path / "counters.json").read_text()


def test_windows_expire_and_prune_inactive_links(tmp_path):
    limits = PublicPassportLimits(tmp_path, failures=1, views=1)
    assert limits.allow_failure(now=0)
    assert not limits.allow_failure(now=59.99)
    with limits.view("a", "a", now=0) as allowed:
        assert allowed
    assert limits.allow_failure(now=60)
    assert list(json.loads((tmp_path / "counters.json").read_text())) == ["failures"]
    with limits.view("a", "a", now=60) as allowed:
        assert allowed


def test_waiter_samples_time_only_after_acquiring_counter_lock(tmp_path, monkeypatch):
    limits = PublicPassportLimits(tmp_path, failures=1)
    assert limits.allow_failure(now=20)
    acquired = False
    @contextmanager
    def delayed_lock(*args, **kwargs):
        nonlocal acquired
        acquired = True
        yield True
    monkeypatch.setattr(limits, "_lock", delayed_lock)
    monkeypatch.setattr(module.time, "monotonic", lambda: 21 if acquired else 19)
    assert not limits.allow_failure()


def test_failed_render_refunds_reservation_but_never_refunds_a_new_window(tmp_path):
    limits = PublicPassportLimits(tmp_path, views=1)
    with pytest.raises(RuntimeError):
        with limits.view("a", "a", now=10) as allowed:
            assert allowed
            raise RuntimeError("render")
    with limits.view("a", "a", now=10) as allowed:
        assert allowed
    with pytest.raises(RuntimeError):
        with limits.view("b", "b", now=10) as allowed:
            assert allowed
            with limits.view("b", "b", now=70) as new_allowed:
                assert new_allowed
            raise RuntimeError("old render")
    with limits.view("b", "b", now=70) as allowed:
        assert not allowed


def test_state_failure_does_not_fall_back_to_process_local_admission(tmp_path):
    limits = PublicPassportLimits(tmp_path)
    (tmp_path / "counters.json").write_text("invalid state")
    with pytest.raises(PassportLimitsUnavailable):
        limits.allow_failure()


def test_failed_atomic_write_is_reported_as_unavailable(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(PassportLimitsUnavailable):
        PublicPassportLimits(tmp_path).allow_failure()
    assert not list(tmp_path.glob(".counters-*"))
