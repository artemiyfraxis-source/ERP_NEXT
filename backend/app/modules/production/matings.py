"""Per-connector mating counters; the registry RF interface order defines the physical sides A and B."""

from .domain import ProductionValidationError

KINDS = ("measurement", "calibration", "manual", "initial", "correction")
MAX_COUNT = 2_147_483_647


def status_for(total: int, resource: int | None) -> str:
    if resource is None:
        return "unknown"
    if total >= resource:
        return "exhausted"
    return "warning" if total * 5 >= resource * 4 else "ok"


def end_names(rf) -> tuple[str, ...]:
    """A load or a one-port item has a single side; any other item with two RF interfaces has A and B."""
    return ("A", "B") if len(rf.interfaces) == 2 and rf.role != "load" else ("A",)


def validate_entry(*, end: str, delta: int, kind: str, note: str, total: int, first: bool) -> None:
    if end not in ("A", "B") or kind not in KINDS or type(delta) is not int or delta == 0:
        raise ProductionValidationError("Укажите сторону A/B, ненулевое целое число и вид сочленения")
    if delta < 0 and kind != "correction":
        raise ProductionValidationError("Уменьшить счётчик можно только исправлением")
    if kind in ("initial", "correction") and not note.strip():
        raise ProductionValidationError("Для начального значения или исправления укажите причину")
    if kind == "initial" and not first:
        raise ProductionValidationError("Начальное значение задаётся один раз, до первого сочленения стороны")
    if total + delta < 0:
        raise ProductionValidationError("Число сочленений не может быть отрицательным")
    if total + delta > MAX_COUNT:
        raise ProductionValidationError("Число сочленений слишком велико")
