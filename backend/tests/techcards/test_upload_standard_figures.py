"""scripts/figures/upload_standard_figures.py: manifest.json → один PUT на строку СТО. Без сети: только план, сводка
и тело запроса; файлы — синтетические, в tmp."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "figures" / "upload_standard_figures.py"
_spec = importlib.util.spec_from_file_location("upload_standard_figures", SCRIPT)
upload = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = upload  # dataclasses look the module up while the class is built
_spec.loader.exec_module(upload)


def _entry(code: str, index: int, file: str, role: str = "norm", kind: str = "gost", caption: str = "ГОСТ Р 72182.2-2025, табл. А.1") -> dict:
    return {"code": code, "index": index, "role": role, "kind": kind, "caption": caption, "file": file, "dpi": 300}


def _folder(tmp_path: Path, entries: list[dict], files: dict[str, bytes]) -> Path:
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / "manifest.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_figures_group_by_code_in_index_order_and_type_comes_from_the_extension(tmp_path):
    folder = _folder(tmp_path, [
        _entry("4.3.14", 2, "b.jpg", "defect", "ipc", "IPC/WHMA-A-620E, рис. 3-12"),
        _entry("4.3.14", 1, "a.png"),
        _entry("4.1.2", 1, "c.JPEG", "ref", "ipc", "IPC/WHMA-A-620E,\n рис. 1"),
    ], {"a.png": b"\x89PNG-a", "b.jpg": b"\xff\xd8\xff-b", "c.JPEG": b"\xff\xd8\xff-c"})
    ready, problems = upload.plan(folder, upload.read_manifest(folder))
    assert problems == []
    assert list(ready) == ["4.1.2", "4.3.14"]
    assert [(f.index, f.content_type) for f in ready["4.3.14"]] == [(1, "image/png"), (2, "image/jpeg")]
    assert ready["4.1.2"][0].content_type == "image/jpeg" and ready["4.1.2"][0].caption == "IPC/WHMA-A-620E, рис. 1"
    body = upload.payload(ready["4.3.14"])
    assert [(f["role"], f["source_kind"], f["content_type"]) for f in body["figures"]] == [
        ("norm", "gost", "image/png"), ("defect", "ipc", "image/jpeg"),
    ]
    assert base64.b64decode(body["figures"][1]["content_base64"]) == b"\xff\xd8\xff-b"
    assert "2 рис." in upload.summary_line("4.3.14", ready["4.3.14"])


def test_a_code_with_any_bad_entry_stays_out_and_is_reported(tmp_path):
    entries = [
        _entry("4.3.14", 1, "a.png"), _entry("4.3.14", 2, "missing.png"),
        _entry("4.1.2", 1, "x.gif"), _entry("4.2.1", 1, "a.png", role="good"),
        *[_entry("4.4.1.1", index, "a.png") for index in range(1, 8)],
        _entry("4.5.1", 1, "a.png", caption=" "),
    ]
    folder = _folder(tmp_path, entries, {"a.png": b"\x89PNG", "x.gif": b"GIF89a"})
    ready, problems = upload.plan(folder, upload.read_manifest(folder))
    assert ready == {}
    text = "\n".join(problems)
    for expected in ("нет файла «missing.png»", "не .png / .jpg", "роль «good»", "больше 6", "нет подписи"):
        assert expected in text


def test_dry_run_sends_nothing_and_prints_totals(tmp_path, capsys, monkeypatch):
    folder = _folder(tmp_path, [_entry("4.3.14", 1, "a.png")], {"a.png": b"\x89PNG"})
    monkeypatch.setattr(upload, "put_code", lambda *args: (_ for _ in ()).throw(AssertionError("no network in dry-run")))
    assert upload.main([str(folder), "--base-url", "http://127.0.0.1:1"]) == 0
    out = capsys.readouterr().out
    assert "Итого: строк СТО 1, рисунков 1 (ГОСТ 1, IPC 0)" in out and "dry-run" in out


def test_apply_needs_the_token_from_the_environment(tmp_path, capsys, monkeypatch):
    folder = _folder(tmp_path, [_entry("4.3.14", 1, "a.png")], {"a.png": b"\x89PNG"})
    monkeypatch.delenv("ERP_API_TOKEN", raising=False)
    assert upload.main([str(folder), "--base-url", "http://127.0.0.1:1", "--apply"]) == 2
    assert "Нет ERP_API_TOKEN" in capsys.readouterr().out
    sent = []
    monkeypatch.setenv("ERP_API_TOKEN", "erp_test")
    monkeypatch.setattr(upload, "put_code", lambda base, token, code, figures: sent.append((base, token, code)) or len(figures))
    assert upload.main([str(folder), "--base-url", "http://127.0.0.1:1", "--apply"]) == 0
    assert sent == [("http://127.0.0.1:1", "erp_test", "4.3.14")]
