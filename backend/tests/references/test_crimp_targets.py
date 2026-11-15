"""Batch equipment capabilities derive from terminal codes, including uncertain links."""

from unittest.mock import MagicMock

import pytest

from app.modules.references.crimps import crimp_targets
from app.modules.references.domain import TERMINAL_TABS, TechOperationRecord, TechOperationTab
from app.shared.http_auth import Principal, authenticated_principal
from tests.factories import Actor
from tests.references.test_api import references_api  # noqa: F401

BASE = "/api/references/tech-operations"


def terminal(code, article, series="XHP", maker="JST", *, tab=TechOperationTab.TERMINAL, aliases=False):
    fields = ({"Аппликатор": code, "Артикул\nконтакта (REEL)": article, "Series": series, "Производитель": maker}
              if aliases else {"terApplicator": code, "terArticle": article, "terSeries": series, "terManufacturer": maker})
    return TechOperationRecord.create(tab=tab, fields=fields, display_text=article,
                                      search_text="", source_sheet="БД.ТЕР", sort_key=article)


def test_batch_groups_contacts_and_keeps_uncertainty_per_article():
    uow = MagicMock()
    uow.tech_operations.list.return_value = [
        terminal("S5/M21/A14", "SXH-002T-P0.6"),
        terminal("M21/A14", "SXH-001T-P0.6", aliases=True),
        terminal("возможно M21/A14", "SXH-001T-P0.6"),  # confirmed duplicate wins
        terminal("возможно M21/A14", "SXH-001T-P0.6N"),
        terminal("A14/возможно A14", "SXH-002T-P0.6"),
        terminal("M210/A140", "OTHER"),  # whole codes only
        terminal("возможно A3", "T-3", "ZHR"),
        terminal("A14", "OTHER-MAKER", maker="Другой"),
        terminal("M21", "НШВ-1", "НШВ", "КВТ", tab=TechOperationTab.LUG),
        terminal("", "UNLINKED"),
    ]
    result = crimp_targets(uow)["by_code"]
    jst = [group for group in result["A14"] if group["maker"] == "JST"]
    assert jst == [
        {"series": "XHP", "maker": "JST", "articles": ["SXH-001T-P0.6", "SXH-002T-P0.6"], "doubtful": False},
        {"series": "XHP", "maker": "JST", "articles": ["SXH-001T-P0.6N"], "doubtful": True},
    ]
    assert {group["maker"] for group in result["A14"]} == {"JST", "Другой"}
    assert result["A3"] == [{"series": "ZHR", "maker": "JST", "articles": ["T-3"], "doubtful": True}]
    assert jst == [group for group in result["M21"] if group["maker"] == "JST"]
    assert any(group["articles"] == ["НШВ-1"] for group in result["M21"])
    assert result.get("A1", []) == result.get("", []) == []
    uow.tech_operations.list.assert_called_once_with(tab=TERMINAL_TABS, query="", limit=40_000)


def test_batch_endpoint_and_uncoded_tool(references_api):
    factory = references_api.app.state.references_uow_factory
    with factory() as uow:
        uow.tech_operations.save(terminal("M21/A14", "SSH-003GA-P0.2", "SHR, SHD"))
        uow.commit()
    tool = references_api.post(BASE, json={"tab": "ob", "fields": {"Тип": "Матрицы", "Наменование": "Губки"}}).json()
    response = references_api.get(f"{BASE}/crimp-targets")
    assert response.status_code == 200, response.text
    index = response.json()["by_code"]
    assert index["A14"] == index["M21"] == [
        {"series": "SHR, SHD", "maker": "JST", "articles": ["SSH-003GA-P0.2"], "doubtful": False},
    ]
    assert index.get(tool["fields"].get("Код в базе терминалов", ""), []) == []
    assert references_api.get(f"{BASE}/{tool['id']}/crimps").json()["terminals"] == []


@pytest.mark.parametrize("permission, status", [("view", 200), ("none", 403)])
def test_batch_has_the_same_view_permission_as_codes(references_api, permission, status):
    references_api.app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(name="Читатель", department="ОГТ"), email="viewer@example.test", roles=frozenset(),
        permissions={"erpref": permission},
    )
    assert references_api.get(f"{BASE}/crimp-codes").status_code == status
    assert references_api.get(f"{BASE}/crimp-targets").status_code == status
