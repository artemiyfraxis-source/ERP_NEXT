"""OP-1: every notification kind a producer sends has a Russian label in the inbox (frontend/src/notificationLabels.ts).

A kind without a label shows as «Событие», so a new producer would silently lose its name on the screen."""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
LABELS = BACKEND.parent / "frontend" / "src" / "notificationLabels.ts"


def _module_constants(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _sent_kinds() -> dict[str, str]:
    """kind → the file that sends it, from every `notification_requested(kind=…)` call in app/."""
    kinds: dict[str, str] = {}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "notification_requested"):
                continue
            for keyword in node.keywords:
                if keyword.arg != "kind":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant):
                    kinds[value.value] = path.relative_to(BACKEND).as_posix()
                elif isinstance(value, ast.Name) and value.id in constants:
                    kinds[constants[value.id]] = path.relative_to(BACKEND).as_posix()
                else:
                    raise AssertionError(f"{path}: kind уведомления не литерал и не константа модуля")
    return kinds


def test_every_sent_kind_has_a_russian_label():
    labelled = set(re.findall(r"^\s*(\w+):\s*\{\s*label:", LABELS.read_text(encoding="utf-8"), re.MULTILINE))
    sent = _sent_kinds()

    assert {"nonconformity", "quarantine", "status", "ready", "ship"} <= set(sent)  # the OP-1 rules are found
    assert {kind: where for kind, where in sent.items() if kind not in labelled} == {}
