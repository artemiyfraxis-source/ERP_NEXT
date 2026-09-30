"""Локальная ERP на ПК: процесс сам завершается, когда появляется файл-стоп из ERP_STOP_FILE.

Обновление (scripts/update-local.ps1) останавливает API и фоновые процессы этим файлом, а уже потом taskkill:
ERP, запущенную из окна администратора, процесс планировщика убить не может («порт 8022 занят и не освободился»,
30.09), а она сама завершиться может всегда. Срабатывает только файл новее старта процесса, поэтому процессы,
запущенные после записи файла, не гаснут. Без ERP_STOP_FILE (сервер, тесты) ничего не делает."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path


def watch_stop_file(
    path: str | None = None, *, interval: float = 1.0, stop: Callable[[int], object] = os._exit,
) -> threading.Thread | None:
    target = path if path is not None else os.getenv("ERP_STOP_FILE", "").strip()
    if not target:
        return None
    stop_file = Path(target)
    started = time.time()

    def loop() -> None:
        while True:
            try:
                if stop_file.stat().st_mtime >= started:
                    stop(0)
                    return
            except OSError:
                pass  # файла нет — работаем дальше
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="erp-stop-file", daemon=True)
    thread.start()
    return thread
