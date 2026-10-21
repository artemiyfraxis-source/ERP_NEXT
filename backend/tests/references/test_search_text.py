"""Поиск по базам (владелец 16.10.2026): сервер ищет по тем же правилам, что таблицы на экране (frontend searchText.ts)."""

import pytest

from app.modules.references.search_text import matches_search, search_key, search_rows, search_words
from tests.references.test_api import references_api  # noqa: F401 — фикстура API справочников


@pytest.mark.parametrize(("value", "query"), [
    ("Кабель RG-58 C/U", "RG58"), ("Кабель RG-58 C/U", "rg 58"), ("RG58", "rg-58"),
    ("0511910600", "051191-0600"), ("051191-0600", "0511910600"),
    ("SHR-02V-S-B", "SHR02VSB"), ("SHR02VSB", "shr-02v-s-b"),
    ("МГТФ 0,35", "MГТФ"), ("МГТФ 0,35", "мгтф 0.35"), ("МГТФ 0.35", "0,35"),
    ("Наконечник НШВИ", "HШBИ"), ("PK 50-3-11", "РК 50"), ("СЦНК", "CЦHK"),
    ("Molex Micro-Fit 3.0", "  fit   molex "),
])
def test_finds_what_the_shop_types(value, query):
    assert matches_search([value], search_words(query))


def test_every_word_must_match_and_words_do_not_span_values():
    assert not matches_search(["Molex", "Micro-Fit 3.0"], search_words("micro jst"))
    assert not matches_search(["Molex", "5557"], search_words("molex5557"))
    assert not matches_search(["МГТФ 0,35"], search_words("0,5"))
    assert not matches_search(["3,00 - 3,50"], search_words("0,35"))  # соседние числа не склеиваются
    assert matches_search(["что угодно"], search_words("  -  "))


def test_several_words_first_as_one_mark_then_in_any_order():
    rows = [("RG58", ""), ("RG-58 C/U", ""), ("RG59 B/U", "Ø 3,58"), ("МГТФ 0,35", ""), ("Molex", "5557")]

    def marks(query: str, limit: int = 10) -> list[str]:
        return [mark for mark, _ in search_rows(rows, lambda row: row, search_words(query), limit)]

    assert marks("rg 58") == ["RG58", "RG-58 C/U"]
    assert marks("rg 58", limit=1) == ["RG58"]
    assert marks("0,35 мгтф") == ["МГТФ 0,35"]
    assert marks("5557 molex") == ["Molex"]
    assert marks("rg 60") == []


def test_key_matches_the_screen():
    assert search_key("RG-58 C/U") == "rg58cu"
    assert search_key("МГТФ 0,35") == search_key("MГTФ 0.35")
    assert search_words("  SHR-02V  0,35 ") == ["shr02v", "0.35"]


def test_tech_operations_query_uses_the_same_rules(references_api):  # noqa: F811
    references_api.put("/api/references/tech-operations/snapshot", json={
        "schema_version": 14, "source_updated_at": "2026-09-20T12:00:00+03:00",
        "records": [
            {"tab": "ter", "display_text": "Molex 5557 39-00-0038", "source_sheet": "БД.ТЕР", "sort_key": "001",
             "fields": {"terManufacturer": "Molex", "terSeries": "5557", "terArticle": "39-00-0038"}},
            {"tab": "ter", "display_text": "КВТ НВИ 1.5-3", "source_sheet": "БД.ТЕР", "sort_key": "002",
             "search_text": "вилочный наконечник", "fields": {"terManufacturer": "КВТ", "terSeries": "НВИ", "terArticle": "НВИ 1.5-3"}},
        ],
    })

    def found(q: str) -> list[str]:
        result = references_api.get("/api/references/tech-operations", params={"tab": "ter", "q": q})
        assert result.status_code == 200
        return [item["display_text"] for item in result.json()["records"]]

    assert found("39000038") == ["Molex 5557 39-00-0038"]
    assert found("hви 1,5 kbt") == ["КВТ НВИ 1.5-3"]  # латинские H, K, B и запятая
    assert found("наконечник вилочный") == ["КВТ НВИ 1.5-3"]  # поисковый текст импорта, слова в любом порядке
    assert found("molex квт") == []
    assert len(found(" ")) == 2
