"""Assigned norm per СИ (owner 18.11.2026): catalog ids, the deliberate proposal, the write check and the CSV script."""

import csv
import json
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.domain import ProductionValidationError
from app.modules.production.instrument_norm_assignment import kept_norm, norm_problem, propose
from app.modules.production.instrument_norms import catalog, item_spec, norm_by_id, norm_for, norms_data
from app.modules.production.rf_infrastructure import RfRepository
from app.modules.production.rf_interfaces import RfDetails, RfInterface
from app.shared.persistence import AuditEventRow
from app.workflows import si_norm_assignment
from tests.production.test_api import production_api  # noqa: F401
from tests.production.test_instrument_serials import create, sessions


def end(series, body="розетка", ohm=50):
    return RfInterface(series, body, "штырь" if body == "вилка" else "гнездо", ohm)


def adapter(a, b, *, role="adapter", norm=""):
    return RfDetails((a, b), role, norm_id=norm)


def test_every_catalog_entry_has_a_unique_id_and_a_source():
    data = norms_data()
    ids = [g["id"] for m in data["makers"] for g in m["groups"]] + [t["id"] for t in data["type_norms"]] + \
          [s["id"] for s in data["mil_adapters"]]
    assert len(ids) == len(set(ids)) == len(catalog())
    assert all(t["source"] and t["url"] and t["level"] in ("tu", "iec", "escc") for t in data["type_norms"])
    assert data["assignment"]["precedence"][0].startswith("изготовитель")


@pytest.mark.parametrize("norm_id,ghz,vswr,loss", [
    ("escc-3402-003-sma", 1, 1.108, 0.02 + 0.06), ("escc-3402-003-sma", 18, 1.244, 0.02 + 0.06 * 18 ** 0.5),
    ("escc-3402-003-sma-angle", 12, 1.17, 0.02 + 0.06 * 12 ** 0.5),
    ("escc-3402-010-tnc-plug-jack", 18, 1.19, 0.07 * 18 ** 0.5), ("escc-3402-010-tnc-jack-jack", 12, 1.186, 0.07 * 12 ** 0.5),
    ("escc-3402-010-tnc-plug-plug", 12.4, 1.162, 0.07 * 12.4 ** 0.5),
    ("mikran-pk2-18", 12, 1.10, 0.15), ("mikran-pk2-18", 18, 1.15, 0.15),
    ("planar-c50smnm-2", 2.5, 1.152, 0.60), ("planar-c50smnm-2", 18, 1.329, 1.75), ("planar-c50nmnm-2", 6, 1.222, 1.00),
    ("mil-55339-51a-58", 9, 1.35, None), ("mil-55339-51a-58", 11, 1.5, None), ("mil-55339-7a", 11, 1.30, None),
    ("iec-61169-8-bnc", 4, 1.30, None), ("iec-61169-8-bnc-angle", 4, 1.35, None),
])
def test_type_norm_limits_follow_the_research(norm_id, ghz, vswr, loss):
    norm = norm_by_id(norm_id)
    assert norm.limit(ghz) == pytest.approx(vswr)
    if loss is not None:
        assert norm.loss.limit(ghz) == pytest.approx(loss)
    elif norm_id.startswith("iec"):
        assert norm.loss is None


def test_tnc_plug_plug_stops_at_the_verified_segment_and_bnc_at_4_ghz():
    assert norm_by_id("escc-3402-010-tnc-plug-plug").limit(13) is None
    assert norm_by_id("iec-61169-8-bnc").limit(4.5) is None


@pytest.mark.parametrize("name,details,expected", [
    ("Переходник SMA female–SMA female S-322", adapter(end("SMA"), end("SMA")), "escc-3402-003-sma"),  # not MIL /55
    ("Переходник SMA угловой", adapter(end("SMA", "вилка"), end("SMA")), "escc-3402-003-sma-angle"),
    ("Переходник TNC", adapter(end("TNC", "вилка"), end("TNC")), "escc-3402-010-tnc-plug-jack"),
    ("Переходник TNC", adapter(end("TNC"), end("TNC")), "escc-3402-010-tnc-jack-jack"),
    ("Переходник TNC–TNC T-311", adapter(end("TNC", "вилка", None), end("TNC", "вилка", None)), "escc-3402-010-tnc-plug-plug"),
    ("Переходник N-322", adapter(end("N"), end("N")), "mil-55339-7a"),  # another maker: not the Микран ТУ
    ("Переходник N-311", adapter(end("N", "вилка"), end("N", "вилка")), "mil-55339-5a"),
    ("Переходник N-312", adapter(end("N", "вилка"), end("N")), "mil-55339-3a"),
    ("Переход Микран PK2-18", adapter(end("N"), end("N")), "mikran-pk2-18"),
    ("Переходник SMA–TNC ST-311", adapter(end("SMA", "вилка"), end("TNC", "вилка")), "mil-55339-40b-43b"),  # /43B
    ("Переходник TNC–N TN-322", adapter(end("TNC"), end("N")), "mil-55339-51a-58"),  # /58
    ("Переходник TNC–BNC TB-321", adapter(end("TNC"), end("BNC", "вилка")), "mil-55339-38a"),
    ("Переходник SMA–BNC SB-311", adapter(end("SMA", "вилка"), end("BNC", "вилка")), "mil-55339-45b"),
    ("Кабель измерительный C50SMNM.2", adapter(end("SMA", "вилка"), end("N", "вилка"), role="test_cable"), "planar-c50smnm-2"),
    ("Переходник BNC female–BNC female B-322", adapter(end("BNC"), end("BNC")), "iec-61169-8-bnc"),
    ("Переход ADP1A-35F-35F", adapter(end("3.5mm"), end("3.5mm")), "adp1a-35"),
    ("Переход ADP1A-NM-NF", adapter(end("N", "вилка"), end("N")), "adp1a-n"),  # maker before ТУ
    ("Переходник SMA–N SN-311", adapter(end("SMA", "вилка"), end("N", "вилка")), "mil-55339-54b"),
    ("Переходник SMA–BNC", adapter(end("SMA"), end("BNC", "вилка")), "mil-55339-44c"),
    ("Переходник BNC–N BN-312", adapter(end("BNC", "вилка"), end("N")), "mil-55339-24b"),
    ("Переходник BNC–N BN-321", adapter(end("BNC"), end("N", "вилка")), "mil-55339-20a"),
])
def test_proposal_follows_maker_tu_iec_escc_mil(name, details, expected):
    assert propose(name, "", "", details).norm_id == expected


@pytest.mark.parametrize("details,reason", [
    (adapter(end("BNC", ohm=75), end("BNC", ohm=75)), "75 Ом"),
    (adapter(end("BNC", ohm=None), end("BNC", "вилка", None)), "BNC бывает 50 и 75 Ом"),
    (adapter(end("RP-SMA"), end("SMA")), "нет нормы на RP-SMA"),
    (adapter(end("TNC", "вилка"), end("BNC")), "нет листа MIL-PRF-55339 на сочетание родов TNC вилка – BNC розетка"),
    (adapter(end("SMA", "вилка"), end("BNC")), "сочетание родов SMA вилка – BNC розетка"),  # /47B ambiguous
    (adapter(end("SMA"), end("3.5mm")), "нет нормы на пару"),
    (adapter(end("N", "вилка"), end("тип III", "вилка"), role="test_cable"), "кабельная сборка"),
    (adapter(end("N", "вилка"), end("тип III", "вилка"), role="test_cable"), "C50NMIIIM.2"),
    (RfDetails((), "adapter"), "нет двух ВЧ-интерфейсов"),
])
def test_unassigned_items_carry_the_reason(details, reason):
    proposal = propose("Кабель измерительный C50NMIIIM.2", "", "Планар", details)
    assert proposal.norm_id == "" and reason in proposal.reason


def test_items_without_a_norm_role_are_not_proposed():
    assert propose("ВАЦ", "", "", RfDetails((end("N"), end("N")), "vna")) is None


def test_write_check_needs_a_catalog_id_that_fits_the_item():
    sma = adapter(end("SMA"), end("SMA"), norm="escc-3402-003-sma")
    assert norm_problem(sma) == "" and norm_problem(replace(sma, norm_id="")) == ""
    assert "отсутствует в каталоге" in norm_problem(replace(sma, norm_id="no-such"))
    assert "не подходит к паре" in norm_problem(replace(sma, norm_id="mil-55339-54b"))
    assert "не подходит к паре" in norm_problem(adapter(end("TNC"), end("TNC"), norm="escc-3402-010-tnc-plug-jack"))
    assert "не подходит к паре" in norm_problem(adapter(end("SMA", "вилка"), end("BNC"), norm="mil-55339-44c"))
    assert norm_problem(adapter(end("SMA"), end("BNC", "вилка"), norm="mil-55339-44c")) == ""
    assert "50 Ом" in norm_problem(adapter(end("BNC", ohm=75), end("BNC", ohm=75), norm="iec-61169-8-bnc"))
    assert "оба ВЧ-интерфейса" in norm_problem(RfDetails((), "adapter", norm_id="escc-3402-003-sma"))
    with pytest.raises(ProductionValidationError):
        RfDetails((end("SMA"), end("SMA")), "vna", norm_id="escc-3402-003-sma")
    with pytest.raises(ProductionValidationError):
        RfDetails(norm_id="Bad Id")


def test_card_edit_keeps_a_fitting_norm_and_clears_a_stale_one():
    before = adapter(end("SMA"), end("SMA"), norm="escc-3402-003-sma")
    assert kept_norm(before, replace(before, norm_id="", source="Паспорт")) == "escc-3402-003-sma"
    assert kept_norm(before, adapter(end("SMA"), end("N"))) == ""
    assert kept_norm(before, replace(before, role="unknown", norm_id="")) == ""


def test_passport_reads_only_the_assignment():
    details = adapter(end("SMA"), end("SMA"), norm="escc-3402-003-sma")
    assert norm_for("Переходник", "", details).reference == "ESCC 3402/003"
    spec = item_spec("Переходник", "", details)
    assert spec.title == "ESCC 3402/003" and spec.temp_min_c is None and spec.temp_source == ""


ROWS = [
    {"inventory_number": "СИ.034", "name": "Переходник SMA female–SMA female S-322", "doc_designation": "", "manufacturer": "",
     "role": "adapter", "interfaces": [{"series": "SMA", "body": "розетка", "center": "гнездо", "impedance_ohm": 50}] * 2},
    {"inventory_number": "СИ.016", "name": "Переходник BNC male–BNC male B-311", "doc_designation": "", "manufacturer": "",
     "role": "adapter", "interfaces": [{"series": "BNC", "body": "вилка", "center": "штырь", "impedance_ohm": 75}] * 2},
    {"inventory_number": "СИ.002", "name": "Planar S50180", "doc_designation": "", "manufacturer": "Планар",
     "role": "vna", "interfaces": []},
]


def test_script_proposes_from_a_read_only_export(tmp_path):
    source, out = tmp_path / "rows.json", tmp_path / "out.csv"
    source.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
    assert si_norm_assignment.main(["--rows", str(source), "--csv", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert [(r["inventory_no"], r["norm_id"]) for r in rows] == [("СИ.034", "escc-3402-003-sma"), ("СИ.016", "")]
    assert rows[0]["limits"].startswith("КСВН ≤ 1,1 + 0,008·F в полосе 0–18 ГГц") and "0,06·√F" in rows[0]["limits"]
    assert rows[0]["norm_reference"] == "ESCC 3402/003" and "75 Ом" in rows[1]["reason"]


def _card(client, number):
    item = create(client, number)
    response = client.put(f"/api/production/instruments/{item['id']}/rf-interfaces", json={
        "version": 0, "role": "adapter", "interfaces": [{"series": "SMA", "body": "розетка", "center": "гнездо", "impedance_ohm": 50}] * 2})
    assert response.status_code == 200, response.text
    return item


def test_apply_writes_reviewed_rows_with_guard_and_audit_and_the_card_keeps_it(production_api):
    client, _ = production_api
    first, second = _card(client, "СИ.034"), _card(client, "СИ.035")
    rows = [{"inventory_no": "СИ.034", "norm_id": "escc-3402-003-sma", "current_norm_id": "", "reason": "ESCC"},
            {"inventory_no": "СИ.035", "norm_id": "escc-3402-003-sma", "current_norm_id": "mil-55339-55", "reason": "ESCC"},
            {"inventory_no": "СИ.999", "norm_id": "escc-3402-003-sma", "current_norm_id": "", "reason": ""}]
    with app.state.production_uow_factory() as uow:
        outcome = si_norm_assignment.apply(uow, rows, datetime(2026, 11, 18))
        uow.commit()
    assert outcome["written"] == ["СИ.034: — → escc-3402-003-sma"] and len(outcome["refused"]) == 2
    with sessions()() as session:
        assert RfRepository(session).get(UUID(first["id"])).norm_id == "escc-3402-003-sma"
        events = list(session.scalars(select(AuditEventRow).where(AuditEventRow.event_type == "production.rf_norm_assigned")))
    assert [(e.details["before"], e.details["after"]) for e in events] == [("", "escc-3402-003-sma")]

    url = f"/api/production/instruments/{first['id']}/rf-interfaces"
    card = client.get(url).json()["rf"]
    assert card["norm_id"] == "escc-3402-003-sma"
    # The card echoes the id back on save and keeps it; it can't change it.
    assert client.put(url, json={**card, "source": "Паспорт"}).json()["rf"]["norm_id"] == "escc-3402-003-sma"
    card = client.get(url).json()["rf"]
    refused = client.put(url, json={**card, "norm_id": "mil-55339-55"})
    assert refused.status_code == 422 and "служба качества" in refused.json()["detail"]
    # A changed pair clears the stale norm; the passport then says «норма не назначена».
    changed = client.put(url, json={**card, "interfaces": [card["interfaces"][0], {**card["interfaces"][1], "series": "N"}]})
    assert changed.status_code == 200 and changed.json()["rf"]["norm_id"] == ""
    assert client.get(f"/api/production/instruments/{second['id']}/rf-interfaces").json()["rf"]["norm_id"] == ""


def test_repository_refuses_an_id_outside_the_catalog(production_api):
    client, _ = production_api
    item = _card(client, "СИ.034")
    with sessions()() as session:
        repository = RfRepository(session)
        current = repository.get(UUID(item["id"]))
        with pytest.raises(ProductionValidationError, match="отсутствует в каталоге"):
            repository.save(UUID(item["id"]), replace(current, norm_id="no-such"))
