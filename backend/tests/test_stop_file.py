"""Файл-стоп локальной ERP: обновление останавливает им API и фоновые процессы, кто бы их ни запустил."""

import os
import threading
import time

from app.shared.stop_file import watch_stop_file


def _watch(path):
    stopped = threading.Event()
    thread = watch_stop_file(str(path), interval=0.02, stop=lambda code: stopped.set())
    return thread, stopped


def test_a_stop_file_written_after_the_start_stops_the_process(tmp_path):
    stop_file = tmp_path / "erp.stop"
    _, stopped = _watch(stop_file)
    time.sleep(0.05)
    assert not stopped.is_set()
    stop_file.write_text("стоп", encoding="utf-8")
    assert stopped.wait(2)


def test_a_stop_file_older_than_the_process_does_not_stop_it(tmp_path):
    stop_file = tmp_path / "erp.stop"
    stop_file.write_text("прошлое обновление", encoding="utf-8")
    old = time.time() - 60
    os.utime(stop_file, (old, old))
    _, stopped = _watch(stop_file)
    assert not stopped.wait(0.2)
    stop_file.write_text("это обновление", encoding="utf-8")  # и поток наблюдения заканчивается
    assert stopped.wait(2)


def test_without_the_setting_nothing_is_watched(monkeypatch):
    monkeypatch.delenv("ERP_STOP_FILE", raising=False)
    assert watch_stop_file() is None
