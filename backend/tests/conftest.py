"""Session-wide pytest setup shared by every backend suite."""

from __future__ import annotations

import gc
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

_PREFIX = "erp-pytest-"
_STALE_AFTER_SECONDS = 24 * 60 * 60
_OWN_BASETEMP = "_erp_own_basetemp"


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    # Without --basetemp pytest keeps numbered run dirs plus a `pytest-current` link. On Windows that
    # link is a file symlink to a directory, stat() on it fails with WinError 5 in the session-finish
    # cleanup, and a fully green run exits with code 1. A per-process basetemp has no numbered dirs and
    # no link; the pid keeps two concurrent runs from wiping each other's directory.
    if config.option.basetemp is not None:
        return
    root = Path(tempfile.gettempdir())
    _remove_stale_runs(root)
    basetemp = root / f"{_PREFIX}{os.getpid()}"
    config.option.basetemp = str(basetemp)
    setattr(config, _OWN_BASETEMP, basetemp)


def pytest_unconfigure(config: pytest.Config) -> None:
    basetemp = getattr(config, _OWN_BASETEMP, None)
    if basetemp is None:
        return
    gc.collect()  # drop unreferenced SQLite connections so Windows lets the .db files go
    shutil.rmtree(basetemp, ignore_errors=True)


def _remove_stale_runs(root: Path) -> None:
    # A run that still held a file open at exit leaves its directory behind; sweep it once it is old
    # enough that no live run can own it.
    cutoff = time.time() - _STALE_AFTER_SECONDS
    for path in root.glob(f"{_PREFIX}*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
