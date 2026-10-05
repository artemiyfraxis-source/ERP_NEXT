"""scripts/assembly-corpus/export.py: the corpus from GET /api/techcards/assembly/corpus becomes the folders of
docs/assembly-corpus (README «Именование»). Pure functions only — no network; files go to a temporary corpus root."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "assembly-corpus" / "export.py"
_spec = importlib.util.spec_from_file_location("assembly_corpus_export", SCRIPT)
export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export)

DRAFT = {"mode": "manual", "assemblyName": "Жгут питания", "partQty": 10, "ops": [
    {"type": "customOp", "label": "Пайка экрана", "tPrep": "300", "tOp": "45"},
]}
FINAL = {**DRAFT, "ops": [{**DRAFT["ops"][0], "tOp": "60"}]}


def _item(draft_id: str, created_at: str, **changes) -> dict:
    item = {
        "id": draft_id, "project": "708К.4", "item_index": "КБ1", "drawing_document_id": "d0c",
        "drawing_url": "/api/techcards/kitsets/708%D0%9A.4/documents/d0c", "source": "claude-code",
        "author": "API: Claude Code", "status": "final", "final_revision": 2,
        "lesson": "Что поправили: 1. оп. 1 tOp 45 → 60.\nПравило: если экран плотный, то пайка 60 с.",
        "note": "Провод 2 шт., экран паять", "created_at": created_at, "updated_at": created_at,
        "draft_config": DRAFT, "final_config": FINAL,
    }
    item.update(changes)
    return item


# Newest first, as the API returns it.
PAYLOAD = {"items": [
    _item("bbbbbbbb-0000-0000-0000-000000000002", "2026-10-05T14:00:00+03:00", lesson=""),
    _item("cccccccc-0000-0000-0000-000000000003", "2026-10-05T13:00:00+03:00", project="P-300", item_index="КБ12",
          draft_config={**DRAFT, "assemblyName": ""}, final_config={**FINAL, "assemblyName": "Кабель коаксиальный"}),
    _item("aaaaaaaa-0000-0000-0000-000000000001", "2026-10-05T12:00:00+03:00"),
]}


@pytest.mark.parametrize(("text", "expected"), [
    ("708К.4-КБ1", "708k-4-kb1"),
    ("P-300 КБ12", "p-300-kb12"),
    ("  Жгут №5 / щит  ", "zhgut-5-schit"),
    ("ёлка-Ъ", "elka"),
    ("", ""),
])
def test_slug_is_lowercase_latin_digits_and_hyphens(text, expected):
    assert export.slug(text) == expected


def test_drawing_ids_name_by_position_and_number_later_examples_r2_by_creation():
    names = export.drawing_ids(PAYLOAD["items"])
    assert names == {
        "aaaaaaaa-0000-0000-0000-000000000001": "708k-4-kb1",
        "bbbbbbbb-0000-0000-0000-000000000002": "708k-4-kb1-r2",
        "cccccccc-0000-0000-0000-000000000003": "p-300-kb12",
    }
    # No project and КБ at all: the draft id keeps the folder unique.
    assert export.drawing_ids([{"id": "Ff00ee11-2", "project": "", "item_index": ""}]) == {"Ff00ee11-2": "draft-ff00ee11"}


def test_plan_writes_the_four_files_of_each_example():
    files, skipped = export.plan_export(PAYLOAD["items"])
    assert skipped == []
    assert sorted(files) == sorted(f"{folder}/{name}" for folder in ("708k-4-kb1", "708k-4-kb1-r2", "p-300-kb12")
                                   for name in export.FILES)
    assert json.loads(files["708k-4-kb1/draft.json"]) == DRAFT
    assert json.loads(files["708k-4-kb1/final.json"]) == FINAL
    assert files["708k-4-kb1/draft.json"].endswith("}\n") and "Жгут питания" in files["708k-4-kb1/draft.json"]
    spec = files["708k-4-kb1/spec.md"]
    assert spec.startswith("# 708К.4 КБ1 — Жгут питания\n")
    assert "aaaaaaaa-0000-0000-0000-000000000001" in spec and "Провод 2 шт., экран паять" in spec
    assert files["708k-4-kb1/lesson.md"].startswith("# Урок: 708К.4 КБ1 — Жгут питания\n\nЧто поправили")
    assert "Урок ещё не записан." in files["708k-4-kb1-r2/lesson.md"]
    # The assembly name of the final wins when the draft had none.
    assert files["p-300-kb12/spec.md"].startswith("# P-300 КБ12 — Кабель коаксиальный\n")
    assert all("\r" not in text for text in files.values())


def test_an_example_without_the_final_config_is_skipped_not_exported():
    files, skipped = export.plan_export([_item("dddddddd-1", "2026-10-05T12:00:00", final_config=None)])
    assert files == {} and skipped == ["708k-4-kb1: нет пары черновик/итог — пропущен"]


def test_dry_run_writes_nothing_and_a_write_is_lf_only_and_idempotent(tmp_path):
    root = tmp_path / "docs" / "assembly-corpus"
    root.mkdir(parents=True)
    files, _ = export.plan_export(PAYLOAD["items"][2:])

    report = export.write_files(root, files, dry_run=True)
    assert report == [f"создать   708k-4-kb1/{name}" for name in sorted(export.FILES)]
    assert list(root.iterdir()) == []

    export.write_files(root, files, dry_run=False)
    written = (root / "708k-4-kb1" / "spec.md").read_bytes()
    assert b"\r\n" not in written and written.decode("utf-8") == files["708k-4-kb1/spec.md"]
    assert export.write_files(root, files, dry_run=False) == [
        f"без изменений  708k-4-kb1/{name}" for name in sorted(export.FILES)
    ]
    changed = {**files, "708k-4-kb1/lesson.md": "# Урок\n\nновый\n"}
    assert "обновить  708k-4-kb1/lesson.md" in export.write_files(root, changed, dry_run=True)


@pytest.mark.parametrize("relative", [
    "../outside/spec.md", "708k-4-kb1/../../spec.md", "708k-4-kb1/notes.txt", "spec.md/..", "/etc/spec.md",
])
def test_never_writes_outside_the_corpus_or_other_file_names(tmp_path, relative):
    root = tmp_path / "corpus"
    root.mkdir()
    with pytest.raises(export.ExportError):
        export.write_files(root, {relative: "x\n"}, dry_run=False)
    assert list(tmp_path.iterdir()) == [root] and list(root.iterdir()) == []


def test_the_real_corpus_root_is_docs_assembly_corpus():
    assert export.CORPUS_ROOT == SCRIPT.parents[2] / "docs" / "assembly-corpus"
    assert (export.CORPUS_ROOT / "README.md").is_file()


def test_without_a_token_it_stops_before_any_request(monkeypatch, capsys):
    monkeypatch.delenv("ERP_API_TOKEN", raising=False)
    monkeypatch.setattr(export, "fetch_corpus", lambda *args: pytest.fail("no request without a token"))
    assert export.main(["--dry-run"]) == 2
    assert "ERP_API_TOKEN" in capsys.readouterr().out
