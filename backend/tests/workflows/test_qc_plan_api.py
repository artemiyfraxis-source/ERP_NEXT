"""Окно ОТК (владелец 19.11): что проверять — из шагов «Контроль» ЭТК и СТО ФРАК-001, СИ из реестра, ход замеров КСВН,
причины брака — типовые дефекты СТО разделов операций ЭТК."""

from app.main import app
from app.modules.techcards.project_cards import ProjectTechcard
from app.workflows.qc_plan_api import check_kind
from tests.production.test_api import production_api  # noqa: F401
from tests.workflows.test_coax_vswr_api import BAD, COAX_CONFIG, NORM, NOW, _as, _body, _instrument, _launch

OPERATIONS = [
    {"position": 1, "code": "CUT_COAX+2strip", "type": "coaxCut", "title": "Резка кабеля", "card": {
        "name": "Резка кабеля", "tooling": [],
        "steps": [{"kind": "Операция", "text": "Отрезать кабель", "tolerance": ""},
                  {"kind": "Контроль", "text": "Длина отрезка по ЭТК", "tolerance": "500 ± 5 мм"}]}},
    {"position": 2, "code": "SDR_COAX_pin", "type": "coaxPin", "title": "", "card": {
        "name": "", "tooling": [],
        "steps": [{"kind": "Контроль", "text": "Галтель ровная, без наплывов и непропая", "tolerance": ""}]}},
    {"position": 3, "code": "CTRL_VSWR_coax", "type": "coaxVswr", "title": "Контроль КСВН", "card": {
        "name": "Контроль КСВН", "tooling": [],
        "steps": [{"kind": "Контроль", "text": "Измерить КСВН с двух концов (S11 и S22)", "tolerance": "≤ 1,35"}]}},
]


def _coax_card(operations=OPERATIONS, config=COAX_CONFIG, norm=NORM, index="КБ7"):
    card = ProjectTechcard.create(
        project="591K.3", item_index=index, product_name="Кабель ВЧ", source_calculation="", deadline=None, now=NOW,
    ).rebuild({
        "operations": operations,
        "build_config": {"ops": [{"type": op["type"], "stage": op["title"] or "Пайка", "opNumber": op["code"], "tOp": 60, "tPrep": 0}
                                 for op in operations], **(config or {})},
        "kit": {"part_quantity": "10"}, **({"coaxVswrNorm": norm} if norm else {}),
    }, NOW).issue(NOW)
    with app.state.techcards_uow_factory() as uow:
        uow.techcards.save(card)
        uow.commit()
    return card


def test_check_kind_reads_the_control_step():
    assert check_kind("coaxVswr", "Измерить", "") == "vswr"
    assert check_kind("", "Сравнить наибольший КСВН с нормой", "") == "vswr"
    assert check_kind("", "Прозвонить цепи тестером", "") == "continuity"
    assert check_kind("", "Длина отрезка", "500 ± 5 мм") == "dimensions"
    assert check_kind("", "Трубка плотно облегает кабель, складок нет", "") == "visual"


def test_plan_lists_etk_checks_with_the_sto_method_instruments_progress_and_defects(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    task = _launch(client, _coax_card(), key="qc-plan-1", quantity=4)
    analyser = _instrument(client)
    assert client.post("/api/production/vswr", json=_body(task, analyser, BAD)).status_code == 200

    response = client.get(f"/api/production/qc/plan/{task['id']}")

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["task"]["project"] == "591K.3" and plan["etk"]["revision"] is not None
    checks = {check["kind"]: check for check in plan["checks"]}
    # Прозвонка — из СТО 4.4.5 (экран коаксиала проверяют тестером цепей), шага «Контроль» о ней в ЭТК нет.
    assert list(checks) == ["visual", "dimensions", "vswr", "continuity"] and checks["continuity"]["items"] == []
    assert checks["visual"]["tools"] == ["Лупа 4×", "Визуально", "Лупа 10×"]
    assert "Штангенциркуль" in checks["dimensions"]["tools"] and len(checks["dimensions"]["tools"]) == len(
        {tool.casefold() for tool in checks["dimensions"]["tools"]})
    assert checks["dimensions"]["items"] == [{"operation": "1. Резка кабеля", "text": "Длина отрезка по ЭТК", "tolerance": "500 ± 5 мм"}]
    assert checks["visual"]["items"][0]["operation"] == "2. Пайка центрального контакта"
    assert checks["visual"]["method"].startswith("СТО ФРАК-001-2026, разд. ")
    assert checks["vswr"]["method"].startswith("СТО ФРАК-002-2026") and checks["vswr"]["required"] is True
    vswr = plan["vswr"]
    assert (vswr["total"], vswr["measured"], vswr["failed"], vswr["max_vswr"], vswr["norm_vswr_max"]) == (4, 1, 1, 1.42, 1.35)
    assert vswr["units"][0]["position"] == 1 and vswr["units"][0]["verdict"] == "fail"
    codes = [row["code"] for row in plan["defects"]]
    assert "4.8.16" in codes and any(code.startswith("4.1.") for code in codes) and len(codes) == len(set(codes))
    # ВАЦ — тот, которым мерили; штангенциркуля в реестре нет — так и сказано, а не подставлено чужое СИ.
    assert [(item["inventory_number"], item["purpose"]) for item in plan["instruments"]] == [("ВАЦ-01", "КСВН")]
    assert plan["missing_instruments"] == [{"tool": "Штангенциркуль", "purpose": "Размеры"},
                                           {"tool": "Тестер цепей", "purpose": "Прозвонка"}]


def test_plan_picks_a_registered_caliper_and_skips_vswr_when_the_etk_turns_it_off(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    config = {"coax": {**COAX_CONFIG["coax"], "vswrControl": {"mode": "none"}}}
    task = _launch(client, _coax_card(config=config, norm={**NORM, "control": {"mode": "none"}}), key="qc-plan-2", quantity=2)
    client.post("/api/production/instruments", json={
        "inventory_number": "СИ-020", "name": "Штангенциркуль ШЦ-I-150", "instrument_type": "Штангенциркуль",
        "verification_number": "ПВ-1", "verified_at": "2026-09-01T00:00:00", "verification_due": "2027-09-01T00:00:00"})

    plan = client.get(f"/api/production/qc/plan/{task['id']}").json()

    vswr_check = next(check for check in plan["checks"] if check["kind"] == "vswr")
    assert vswr_check["required"] is False and plan["vswr"]["total"] == 0
    assert [(item["inventory_number"], item["purpose"]) for item in plan["instruments"]] == [("СИ-020", "Размеры")]
    assert plan["missing_instruments"] == [{"tool": "Тестер цепей", "purpose": "Прозвонка"}]


def test_plan_of_a_task_without_etk_operations_has_no_checks_to_invent(production_api):  # noqa: F811
    client, _ = production_api
    _as({"quality": "edit", "production": "edit"})
    packing = [{"position": 1, "code": "BOX", "type": "box", "title": "Упаковка", "card": {
        "name": "Упаковка", "tooling": [], "steps": [{"kind": "Операция", "text": "Уложить в пакет", "tolerance": ""}]}}]
    task = _launch(client, _coax_card(operations=packing, config=None, norm=None, index="КБ8"), key="qc-plan-3", quantity=1)

    plan = client.get(f"/api/production/qc/plan/{task['id']}").json()

    assert plan["checks"] == [] and plan["vswr"] is None and plan["defects"] == []
    _as({"myops": "edit"}, name="Монтажник")
    assert client.get(f"/api/production/qc/plan/{task['id']}").status_code == 403
