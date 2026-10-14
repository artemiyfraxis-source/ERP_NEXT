"""Builds ter-lminus.json — the first approximate L− of БД.ТЕР terminals as a body for
`POST /api/references/import/ter` (owner 13.10.2026: «попробуй на первое время заполнить сам L-»).

    cd backend && python data/terminals/build_ter_lminus.py

The generator cuts a wire «между разъёмами» as length + L+ and «от краёв разъёмов» as length − L−; the second length is
the first plus one housing (mating face to rear face) per end, so both give one cut when L− = housing length − L+.
The housing lengths are read from the makers' drawings (ter-lminus-sources.json names the document per series); L+ is
the terminal's as the mirror holds it. Every row writes only an empty L− and its note («Источник L-»), and carries
`old` — the article, L+ and the empty L− as read here — so on the base a row whose terminal is missing, whose L+ was
changed or whose L− was filled meanwhile is refused («значение изменилось с момента выгрузки»), never created or
overwritten. Send it with `dry_run=1` first.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.references.importers import TER_CARD_COLUMNS  # noqa: E402
from app.modules.references.load_mirror import default_source, read_mirror  # noqa: E402

SOURCES = HERE / "ter-lminus-sources.json"
BODY = HERE / "ter-lminus.json"
ESTIMATES_BODY = HERE / "ter-lengths-estimates.json"
DF1B_BODY = HERE / "hirose-df1b.json"  # contacts loaded from the Hirose catalogue: in the base once that body is applied
SHARE = Decimal("0.83")  # contact length / housing length: JST PH 5.7/6.85, ZH 4.0/4.8, KLS 6.45/7.95, Hirose DF1B 9.5/11.5
NOTE_FIELD = TER_CARD_COLUMNS[0]  # «Источник L-»: the БД.ТЕР column the note goes to
NOTE_PREFIX = "оценка по чертежу корпуса"


def _mm(text: object) -> Decimal:
    return Decimal(str(text).strip().replace(",", "."))


def shown(value: Decimal) -> str:
    """Millimetres as the base writes them: 2.750 → «2,75», 6.0 → «6»."""
    text = format(value.normalize(), "f")
    return text.replace(".", ",")


def mirror_terminals(source: Path | None = None) -> dict[str, dict[str, str]]:
    """БД.ТЕР records of the mirror, as `load_mirror` stores them, by REEL article."""
    records = read_mirror(source or default_source()).tables["ter"]
    return {record.fields["terArticle"]: dict(record.fields) for record in records if record.fields.get("terArticle")}


def build(sources: dict[str, Any], terminals: dict[str, dict[str, str]]) -> list[dict[str, dict[str, str]]]:
    """Rows of the body; a terminal the sources name that the mirror lacks, that has no L+ or already has L−, or whose
    L− would not be positive is an error in the sources, not a silent skip."""
    rows: list[dict[str, dict[str, str]]] = []
    seen: set[str] = set()
    for housing in sources["housings"]:
        length = _mm(housing["length_mm"])
        for article in housing["terminals"]:
            if article in seen:
                raise ValueError(f"{article}: назван дважды")
            seen.add(article)
            fields = terminals.get(article)
            if fields is None:
                raise ValueError(f"{article}: нет в БД.ТЕР зеркала")
            l_plus = str(fields.get("terLPlus") or "").strip()
            if not l_plus:
                raise ValueError(f"{article}: нет L+ — L− не выводится")
            if str(fields.get("terLMinus") or "").strip():
                raise ValueError(f"{article}: L− уже заполнен")
            l_minus = length - _mm(l_plus)
            if l_minus <= 0:
                raise ValueError(f"{article}: корпус {shown(length)} мм не длиннее L+ {l_plus} мм")
            note = (f"{NOTE_PREFIX}: L− = {housing['housing']} {shown(length)} мм − L+ {l_plus} мм; "
                    f"{housing['doc']}, {housing['url']}")
            if housing.get("remark"):
                note += f"; {housing['remark']}"
            # Only keys every БД.ТЕР record answers to: a base loaded before the sheet columns were kept (preview) has
            # no «L-» column, and the importer keeps terLMinus and «L-» equal where it has one.
            old = {"terArticle": article, "terLPlus": fields["terLPlus"], "terLMinus": "", NOTE_FIELD: ""}
            rows.append({"fields": {"terArticle": article, "terLMinus": shown(l_minus), NOTE_FIELD: note}, "old": old})
    return rows


def catalogue_terminals() -> dict[str, dict[str, str]]:
    """Mirror terminals plus the Hirose DF1B contacts (hirose-df1b.json: no L+ / L−, they are created by that body)."""
    terminals = mirror_terminals()
    for row in json.loads(DF1B_BODY.read_text(encoding="utf-8"))["rows"]:
        terminals.setdefault(row["fields"]["terArticle"], dict(row["fields"]))
    return terminals


def build_estimates(sources: dict[str, Any], terminals: dict[str, dict[str, str]]) -> list[dict[str, dict[str, str]]]:
    """Rows of the estimates body: L+ and L− of terminals whose L+ is missing or longer than the housing, or whose L− is
    wrong. L− = housing length − L+; L+ is the contact length of the maker's drawing (`basis` «contact»), 0,83 of the
    housing where no contact drawing was found («proportion», an estimate), or the base's own («keep»). Every row says
    so in «Источник L-» and carries `old` — what the base holds now — so a changed cell is refused, never overwritten."""
    rows: list[dict[str, dict[str, str]]] = []
    seen: set[str] = set()
    for group in sources["estimates"]:
        length, l_plus = _mm(group["length_mm"]), _mm(group["l_plus"])
        basis = group["basis"]
        if basis == "contact" and _mm(group["contact_mm"]) != l_plus:
            raise ValueError(f"{group['housing']}: L+ не равен длине контакта по чертежу")
        if basis == "proportion" and abs(l_plus - length * SHARE) > Decimal("0.06"):
            raise ValueError(f"{group['housing']}: L+ {l_plus} не 0,83 от корпуса {length}")
        l_minus = length - l_plus
        if not 0 < l_plus < length or l_minus <= 0:
            raise ValueError(f"{group['housing']}: L+ {l_plus} вне корпуса {length}")
        if basis == "contact":
            how = f"L+ = длина контакта {shown(l_plus)} мм по чертежу"
        elif basis == "proportion":
            how = "L+ = 0,83 от корпуса (чертёж контакта не найден; доля по JST PH, ZH, KLS, Hirose DF1B)"
        else:
            how = f"L+ {shown(l_plus)} мм оставлен как в базе"
        note = (f"примерно: {how}; L− = {group['housing']} {shown(length)} мм − L+ {shown(l_plus)} мм; "
                f"{group['doc']}, {group['url']}")
        if group.get("remark"):
            note += f"; {group['remark']}"
        for article in group["terminals"]:
            if article in seen:
                raise ValueError(f"{article}: назван дважды")
            seen.add(article)
            fields = terminals.get(article)
            if fields is None:
                raise ValueError(f"{article}: нет в БД.ТЕР зеркала и в hirose-df1b.json")
            old_plus = str(fields.get("terLPlus") or "").strip()
            old_minus = str(fields.get("terLMinus") or "").strip()
            if basis == "keep" and _mm(old_plus) != l_plus:
                raise ValueError(f"{article}: L+ в базе {old_plus}, источник считает {l_plus}")
            new = {"terArticle": article, "terLMinus": shown(l_minus), NOTE_FIELD: note}
            if basis != "keep":
                new["terLPlus"] = shown(l_plus)
            rows.append({"fields": new,
                         "old": {"terArticle": article, "terLPlus": old_plus, "terLMinus": old_minus, NOTE_FIELD: ""}})
    return rows


def write_body(path: Path, rows: list[dict[str, dict[str, str]]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump({"rows": rows}, stream, ensure_ascii=False, indent=1)
        stream.write("\n")


def main() -> int:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    estimates = build_estimates(sources, catalogue_terminals())
    write_body(ESTIMATES_BODY, estimates)
    print(f"{ESTIMATES_BODY.name}: {len(estimates)} строк (L+ и L- примерно, см. «Источник L-»)")
    rows = build(sources, mirror_terminals())
    with BODY.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump({"rows": rows}, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    skipped = sum(len(item["terminals"]) for item in sources["not_filled"])
    print(f"{BODY.name}: {len(rows)} строк, серий {len(sources['housings'])}; не заполнено (см. not_filled): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
