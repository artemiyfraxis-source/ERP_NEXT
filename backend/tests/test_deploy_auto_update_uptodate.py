"""auto-update.sh answers an explicit update request even when main is already installed (20.11.2026: runs
44665483896 and 44723206031 waited 45 minutes for an update-result.json nobody wrote)."""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "auto-update.sh"


def _bash() -> str | None:
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        return str(git_bash) if git_bash.exists() else None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("git") is None, reason="нужны bash и git")


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _setup(tmp_path: Path, *, request: bool, last_result: dict | None = None) -> tuple[Path, subprocess.CompletedProcess]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    (origin / "f.txt").write_text("x", encoding="utf-8")
    _git(origin, "add", "f.txt")
    _git(origin, "commit", "-q", "-m", "c1")
    short = _git(origin, "rev-parse", "--short=7", "HEAD")

    home = tmp_path / "erp"
    (home / "config").mkdir(parents=True)
    (home / "config" / "github-deploy-key").write_text("k", encoding="utf-8")
    (home / "app").mkdir()
    (home / "app" / "REVISION").write_text(short + "\n", encoding="utf-8")
    (home / "status").mkdir()
    if last_result is not None:
        (home / "status" / "update-result.json").write_text(json.dumps(last_result), encoding="utf-8")
    if request:
        (home / "update-now").write_text("", encoding="utf-8")

    shims = tmp_path / "shims"  # flock (absent on Windows) and python3 (a Store stub on Windows)
    shims.mkdir()
    (shims / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    (shims / "python3").write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8",
                                   newline="\n")
    env = {**os.environ, "ERP_HOME": home.as_posix(), "ERP_GIT_REPO": origin.as_posix(),
           "PATH": shims.as_posix() + os.pathsep + os.environ["PATH"]}
    proc = subprocess.run([BASH, str(SCRIPT)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    return home, proc


def test_request_with_current_version_writes_fresh_ok_result(tmp_path: Path) -> None:
    before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    home, proc = _setup(tmp_path, request=True, last_result={"migration": "0208", "db_revision": "0208_x", "ok": False})
    assert proc.returncode == 0, proc.stderr
    short = (home / "app" / "REVISION").read_text(encoding="utf-8").strip()
    result = json.loads((home / "status" / "update-result.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["step"] == "finished"
    assert result["revision"] == short
    assert result["message"] == f"уже установлена {short}; новых миграций нет"
    assert result["db_revision"] == "0208_x" and result["migration"] == "0208"
    finished = datetime.datetime.strptime(result["finished_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)
    assert finished >= before  # the workflow requires finished_at later than its dispatch time
    assert not (home / "update-now").exists()


def test_request_without_previous_result_still_answers(tmp_path: Path) -> None:
    home, proc = _setup(tmp_path, request=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads((home / "status" / "update-result.json").read_text(encoding="utf-8"))["ok"] is True


def test_scheduled_run_with_current_version_stays_silent(tmp_path: Path) -> None:
    home, proc = _setup(tmp_path, request=False)
    assert proc.returncode == 0, proc.stderr
    assert not (home / "status" / "update-result.json").exists()
