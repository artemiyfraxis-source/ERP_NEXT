"""The Windows agent's files as the server hands them out (owner 28.10.2026, «Сканер с любого ПК»).

`tools/print-agent/agent.ps1` and `install.ps1` ship inside the image (Dockerfile copies them to /app/tools/print-agent);
the agent's version is the one written in agent.ps1 itself (`$script:AgentVersion = 'x.y.z'`), so a release is one
commit: change the script, bump the number. An agent that reports an older version in its heartbeat is told where to
download the new file and its SHA-256 (`update_for`); the install script and a fresh PC read the same files."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

FILES = frozenset({"agent.ps1", "install.ps1"})
_VERSION = re.compile(r"\$script:AgentVersion\s*=\s*'(\d+(?:\.\d+){0,3})'")


def agent_dir() -> Path:
    override = os.environ.get("ERP_AGENT_DIR", "").strip()
    return Path(override) if override else Path(__file__).resolve().parents[4] / "tools" / "print-agent"


@dataclass(frozen=True)
class Release:
    version: str
    sha256: str
    size: int


def read_file(name: str) -> bytes | None:
    if name not in FILES:
        return None
    path = agent_dir() / name
    try:
        return path.read_bytes()
    except OSError:
        return None


def version_tuple(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text or "")
    return tuple(int(part) for part in parts[:4])


def release() -> Release | None:
    """The current agent.ps1: its version, hash and size; None — the files are not on this server."""
    content = read_file("agent.ps1")
    if content is None:
        return None
    found = _VERSION.search(content.decode("utf-8-sig", errors="replace"))
    if found is None:
        return None
    return Release(version=found.group(1), sha256=hashlib.sha256(content).hexdigest(), size=len(content))


def update_for(agent_version: str) -> dict | None:
    """What an agent of `agent_version` must download, or None — it is current (or newer: never a downgrade)."""
    current = release()
    if current is None or version_tuple(agent_version) >= version_tuple(current.version):
        return None
    return {"version": current.version, "sha256": current.sha256, "size": current.size,
            "path": "/api/stock/scan/agent/files/agent.ps1"}
