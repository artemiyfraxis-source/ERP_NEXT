"""Per-connector mating counters; registry RF order defines the physical ends."""

from .domain import ProductionValidationError

KINDS = ("measurement", "calibration", "manual", "initial", "correction")
STATUS_LABELS = {"warning": "Ресурс почти исчерпан", "exhausted": "Ресурс исчерпан — заменить"}


def status_for(total: int, resource: int | None) -> str:
    if resource is None:
        return "unknown"
    if total >= resource:
        return "exhausted"
    return "warning" if total * 5 >= resource * 4 else "ok"


def end_names(rf) -> tuple[str, ...]:
    return ("A", "B") if len(rf.interfaces) == 2 and rf.role != "load" else ("A",)


def validate_entry(*, end: str, delta: int, kind: str, note: str, total: int, initial_recorded: bool):
    if end not in ("A", "B") or kind not in KINDS or type(delta) is not int or delta == 0:
        raise ProductionValidationError("Укажите сторону A/B, ненулевое целое число и вид сочленения")
    if delta < 0 and kind != "correction":
        raise ProductionValidationError("Уменьшить счётчик можно только исправлением")
    if kind in ("initial", "correction") and not note.strip():
        raise ProductionValidationError("Для начального значения или исправления укажите причину")
    if kind == "initial" and (initial_recorded or total != 0):
        raise ProductionValidationError("Начальное значение задаётся один раз до начала учёта")
    if total + delta < 0:
        raise ProductionValidationError("Число сочленений не может быть отрицательным")
