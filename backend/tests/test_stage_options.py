"""Опции стенда: без ERP_STAGE_OPTIONS (прод, CI) всё выключено и не включается; на стенде — файл JSON."""

import json

import pytest

from app.shared import stage_options


def test_without_the_variable_every_option_is_off_and_cannot_be_switched(monkeypatch):
    monkeypatch.delenv(stage_options.ENV, raising=False)
    assert not stage_options.available()
    assert not any(stage_options.states().values())
    with pytest.raises(stage_options.StageOptionsUnavailable):
        stage_options.set_enabled("procurement.today", True)


def test_on_the_stage_an_option_is_switched_and_read_back(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    monkeypatch.setenv(stage_options.ENV, str(path))
    assert stage_options.enabled("procurement.today") is False
    stage_options.set_enabled("procurement.today", True)
    assert stage_options.enabled("procurement.today") is True
    stage_options.set_enabled("procurement.today", False)
    assert stage_options.enabled("procurement.today") is False


def test_unknown_keys_in_the_file_are_ignored_and_kept(monkeypatch, tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"other.branch": True, "warehouse.word_search": True}), encoding="utf-8")
    monkeypatch.setenv(stage_options.ENV, str(path))
    assert stage_options.states()["warehouse.word_search"] is True
    assert "other.branch" not in stage_options.states()
    stage_options.set_enabled("procurement.kpi", True)
    assert json.loads(path.read_text(encoding="utf-8"))["other.branch"] is True


def test_unknown_key_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(stage_options.ENV, str(tmp_path / "o.json"))
    with pytest.raises(KeyError):
        stage_options.set_enabled("nope", True)
