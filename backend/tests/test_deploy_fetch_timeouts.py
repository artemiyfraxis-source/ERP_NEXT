"""Every GitHub network step in the server deploy scripts is time-bounded (19.11.2026: an unbounded
`git fetch` in update-request.sh hung 33 minutes and blocked the release)."""
from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SSH_OPTS = ("ConnectTimeout=20", "ServerAliveInterval=15", "ServerAliveCountMax=4")
NETWORK = re.compile(r"\bgit\b[^\n]*\b(fetch|clone|ls-remote)\b")


def _lines(name: str) -> list[str]:
    text = (DEPLOY / name).read_bytes().decode("utf-8")
    assert "\r" not in text, f"{name}: нужны LF-окончания"
    return text.splitlines()


def test_ssh_command_has_timeouts() -> None:
    for name in ("update-request.sh", "auto-update.sh", "install-auto-update.sh"):
        cmds = [ln for ln in _lines(name) if ln.startswith("export GIT_SSH_COMMAND=")]
        assert cmds, name
        for cmd in cmds:
            for opt in SSH_OPTS:
                assert opt in cmd, f"{name}: нет {opt}"


def test_every_github_git_call_is_wrapped_in_timeout() -> None:
    for path in sorted(DEPLOY.glob("*.sh")):
        for ln in _lines(path.name):
            code = ln.split("#", 1)[0]
            if not NETWORK.search(code) or code.lstrip().startswith(("echo", "[")):
                continue
            assert re.search(r"timeout.*git", code), f"{path.name}: git без timeout: {ln.strip()}"


def test_update_request_remembers_request_only_after_fetch() -> None:
    lines = _lines("update-request.sh")
    fetch = next(i for i, ln in enumerate(lines) if "fetch -q --depth=1" in ln)
    stamps = [i for i, ln in enumerate(lines) if 'echo "$req" > "$seen"' in ln]
    assert stamps[-1] > fetch, "запрос нельзя помечать увиденным до успешной загрузки"
