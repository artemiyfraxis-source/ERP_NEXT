"""Offers comparison (port of Roller_Buyer offers.test.ts / decision.test.ts) and the paste parser."""

from datetime import date
from decimal import Decimal as D
from itertools import count
from uuid import UUID, uuid4

from app.modules.purchasing.offers import (
    Offer,
    Reasons,
    Tier,
    baseline,
    buy_quantity,
    check_decision,
    cheapest,
    cover,
    current_offers,
    delta_pct,
    gate_status,
    gross_unit,
    normalize_decision,
    read_tiers,
    tier_price,
)
from app.modules.purchasing.rfq import (
    MatchTarget,
    guess_category,
    guess_columns,
    match_rows,
    parse_lead,
    parse_pasted_table,
    parse_ru_number,
    paste_rows,
)

TODAY = date(2026, 9, 10)
_ids = count(1)


def offer(**over) -> Offer:
    n = next(_ids)
    values = dict(
        quote_line_id=UUID(int=n), supplier_id=UUID(int=10_000 + n), is_key=False, received_on=date(2026, 9, 3),
        gross_unit=D("10"), total=None, declined=False, analog=False, valid_until=None,
    )
    values.update(over)
    return Offer(**values)


def rules(min_quotes=3, require_non_key=True):
    return {"today": TODAY, "min_quotes": min_quotes, "require_non_key": require_non_key}


def test_gross_unit_adds_vat_only_when_the_price_is_net():
    assert gross_unit(D("50"), True, D("22")) == D("50")
    assert gross_unit(D("48"), False, D("22")) == D("58.56")
    assert gross_unit(D("48"), False, D("0")) == D("48")


def test_buy_quantity_moq_first_then_whole_packs():
    assert buy_quantity(D("87"), None, None) == D("87")
    assert buy_quantity(D("87"), D("100"), None) == D("100")
    assert buy_quantity(D("87"), None, D("50")) == D("100")  # two reels of 50
    assert buy_quantity(D("120"), D("100"), D("50")) == D("150")
    assert buy_quantity(D("100"), None, D("50")) == D("100")  # exactly two reels, not three
    assert buy_quantity(D("0.3"), None, D("0.1")) == D("0.3")  # no drift on decimal packs


def test_price_breaks_take_the_highest_step_not_above_the_quantity():
    tiers = read_tiers([{"from": "1000", "price": "80"}, {"from": "500", "price": "90"}, {"from": "x", "price": "1"}])
    assert tiers == (Tier(D("500"), D("90")), Tier(D("1000"), D("80")))
    assert tier_price(D("100"), tiers, D("87")) == D("100")
    assert tier_price(D("100"), tiers, D("500")) == D("90")
    assert tier_price(D("100"), tiers, D("999")) == D("90")
    assert tier_price(D("100"), tiers, D("1000")) == D("80")


def test_cover_compares_by_what_covering_the_need_costs():
    small = cover(D("87"), price=D("16"), vat_included=True, vat_rate=D("22"))
    bulk = cover(D("87"), price=D("10"), vat_included=True, vat_rate=D("22"), moq=D("1000"))
    assert (small.quantity, small.total_gross) == (D("87"), D("1392.00"))
    assert (bulk.quantity, bulk.total_gross) == (D("1000"), D("10000.00"))
    a = offer(gross_unit=small.unit_gross, total=small.total_gross)
    b = offer(gross_unit=bulk.unit_gross, total=bulk.total_gross)
    assert cheapest([a, b]) == a  # 16 ₽ × 87 m beats 10 ₽ × 1000 m
    tiered = cover(D("900"), price=D("10"), vat_included=True, vat_rate=D("22"), tiers=(Tier(D("1000"), D("8")),))
    assert tiered.total_gross == D("9000.00")  # at 900 m the base price applies
    net = cover(D("3"), price=D("10"), vat_included=False, vat_rate=D("22"), pack=D("2"))
    assert (net.quantity, net.unit_gross, net.total_gross) == (D("4"), D("12.2"), D("48.80"))
    assert cover(D("3"), price=None, vat_included=True, vat_rate=D("22")) is None


def test_latest_offer_of_a_supplier_supersedes_and_expired_ones_drop():
    supplier = uuid4()
    early = offer(supplier_id=supplier, received_on=date(2026, 9, 3), gross_unit=D("12"))
    late = offer(supplier_id=supplier, received_on=date(2026, 9, 5), gross_unit=D("11"))
    other = offer()
    current = current_offers([late, early, other], TODAY)
    assert late in current and early not in current and len(current) == 2
    stale = offer(valid_until=date(2026, 9, 9))
    assert stale not in current_offers([stale, other], TODAY)
    valid_today = offer(valid_until=TODAY)
    assert current_offers([valid_today], TODAY) == [valid_today]


def test_cheapest_ignores_declines_and_ties_go_to_the_earlier_offer():
    declined = offer(declined=True, gross_unit=None)
    a = offer(gross_unit=D("9"), received_on=date(2026, 9, 4))
    b = offer(gross_unit=D("9"), received_on=date(2026, 9, 3))
    assert cheapest([declined, a, b]) == b
    assert cheapest([declined]) is None


def test_baseline_follows_the_quote_snapshot_not_the_current_flag():
    key1 = offer(is_key=True, gross_unit=D("13"))
    key2 = offer(is_key=True, gross_unit=D("12.5"))
    cheap = offer(gross_unit=D("9"))
    assert baseline([key1, key2, cheap]) == key2
    # The supplier is key now, but its quote was entered while it was not — the snapshot rules.
    second = offer(received_on=date(2026, 9, 5), gross_unit=D("8"))
    first = offer(received_on=date(2026, 9, 2), gross_unit=D("15"), is_key=False)
    assert baseline([second, first]) == first


def test_gate_needs_n_priced_offers_and_a_non_key_one_when_required():
    k = lambda: offer(is_key=True)  # noqa: E731
    gate = gate_status([k(), k(), k()], 3, True)
    assert (gate.priced, gate.has_non_key, gate.passes) == (3, False, False)
    assert gate_status([k(), k(), k()], 3, False).passes is True
    assert gate_status([k(), k(), offer()], 3, True).passes is True
    assert gate_status([k(), offer()], 3, True).passes is False
    assert gate_status([k(), offer(declined=True, gross_unit=None), offer()], 3, True).priced == 2


def fields(violations):
    return [violation.field for violation in violations]


def test_cheapest_with_a_passing_gate_needs_no_reasons():
    cheap = offer(gross_unit=D("9"))
    offers = [offer(is_key=True), offer(), cheap]
    assert check_decision(offers, cheap.quote_line_id, reasons=Reasons(), **rules()) == []


def test_too_few_or_all_key_offers_require_a_gate_reason():
    only = offer()
    violations = check_decision([only, offer(is_key=True)], only.quote_line_id, reasons=Reasons(), **rules())
    assert fields(violations) == ["gate_override_reason"]
    assert "Нужно КП: 3, есть 2" in violations[0].message
    keys = [offer(is_key=True, gross_unit=D("9")), offer(is_key=True), offer(is_key=True)]
    violations = check_decision(keys, keys[0].quote_line_id, reasons=Reasons(), **rules())
    assert fields(violations) == ["gate_override_reason"] and "неключевого" in violations[0].message
    assert check_decision(keys, keys[0].quote_line_id, reasons=Reasons(), **rules(require_non_key=False)) == []
    assert check_decision([only], only.quote_line_id, reasons=Reasons(gate_override_reason="  "), **rules()) != []
    assert check_decision([only], only.quote_line_id, reasons=Reasons(gate_override_reason="Срочно"), **rules()) == []


def test_pricier_by_total_needs_a_reason_and_an_analog_its_approver():
    pricey = offer(gross_unit=D("15"))
    offers = [offer(is_key=True), offer(gross_unit=D("9")), pricey]
    assert fields(check_decision(offers, pricey.quote_line_id, reasons=Reasons(), **rules())) == ["not_cheapest_reason"]
    assert check_decision(offers, pricey.quote_line_id, reasons=Reasons(not_cheapest_reason="Срок"), **rules()) == []
    # Lower unit price behind a big MOQ is not «cheaper»: totals decide.
    unit_cheap = offer(gross_unit=D("5"), total=D("5000"))
    unit_dear = offer(gross_unit=D("8"), total=D("16"))
    assert check_decision([offer(is_key=True, total=D("20")), unit_cheap, unit_dear], unit_dear.quote_line_id,
                          reasons=Reasons(), **rules()) == []
    analog = offer(gross_unit=D("5"), analog=True)
    offers = [offer(is_key=True), offer(), analog]
    assert fields(check_decision(offers, analog.quote_line_id, reasons=Reasons(), **rules())) == ["analog_approved_by"]


def test_a_superseded_declined_or_expired_offer_cannot_be_chosen():
    supplier = uuid4()
    old = offer(supplier_id=supplier, received_on=date(2026, 9, 1))
    fresh = offer(supplier_id=supplier, received_on=date(2026, 9, 4))
    assert fields(check_decision([old, fresh], old.quote_line_id, reasons=Reasons(), **rules(1))) == ["quote_line_id"]
    no = offer(declined=True, gross_unit=None)
    assert fields(check_decision([no, offer()], no.quote_line_id, reasons=Reasons(), **rules(1))) == ["quote_line_id"]
    stale = offer(valid_until=date(2026, 9, 1))
    violations = check_decision([stale], stale.quote_line_id, reasons=Reasons(), **rules(1))
    assert "истёк" in violations[0].message


def test_normalize_drops_reasons_that_were_not_needed():
    cheap = offer(gross_unit=D("9"))
    offers = [offer(is_key=True), offer(), cheap]
    result = normalize_decision(offers, cheap.quote_line_id, reasons=Reasons("Срочно", "Срок", "Иванов"), **rules())
    assert result == Reasons()
    only = offer(analog=True)
    kept = normalize_decision([only], only.quote_line_id, reasons=Reasons(" Срочно ", "Срок", "Иванов"), **rules())
    assert kept == Reasons("Срочно", None, "Иванов")


def test_delta_pct_against_the_budget():
    assert delta_pct(D("108"), D("100")) == D("8.0")
    assert delta_pct(D("90"), D("120")) == D("-25.0")
    assert delta_pct(None, D("1")) is None and delta_pct(D("1"), None) is None


def test_paste_parses_header_columns_numbers_and_lead():
    text = (
        "№\tНаименование\tАртикул\tКол-во\tЦена с НДС\tСтавка НДС\tСрок поставки\r\n"
        "1\tРазъём SHR-02V-S-B\tSHR-02V-S-B\t100\t1 234,50\t22%\t2-3 недели\n"
        "2\tКабель МГТФ 0,12\t\t50\t12.5\t20\tв наличии\n"
        "3\tПредохранитель\t\t1\tнет\t\tснят с производства\n"
    )
    header, rows = parse_pasted_table(text)
    assert header is not None and len(rows) == 3
    columns = guess_columns(header, rows)
    assert columns == {"name": 1, "article": 2, "qty": 3, "unit": -1, "price": 4, "vat": 5, "lead": 6}
    parsed = paste_rows(rows, columns)
    assert parsed[0].price == D("1234.50") and parsed[0].vat_rate == D("22") and parsed[0].lead_days == 21
    assert parsed[1].in_stock and parsed[1].lead_days == 0 and parsed[1].vat_rate == D("20")
    assert parsed[2].declined and parsed[2].price is None
    assert parse_ru_number("1 234,56 руб.") == D("1234.56") and parse_ru_number("abc") is None
    assert parse_lead("под заказ 30 раб. дней") == (42, False, False)


def test_paste_without_header_guesses_name_then_qty_and_price():
    header, rows = parse_pasted_table("Разъём XH-2A длинный\t10\t5,5\nКлемма\t20\t3\n")
    assert header is None
    columns = guess_columns(header, rows)
    assert (columns["name"], columns["qty"], columns["price"]) == (0, 1, 2)


def test_paste_rows_match_by_article_code_key_then_by_name():
    a, b, c = uuid4(), uuid4(), uuid4()
    targets = [
        MatchTarget(a, "Разъём", "SHR-02V-S-B"),
        MatchTarget(b, "Кабель МГТФ 0,12 белый", None),
        MatchTarget(c, "Разъём", "SHR-02V-S"),
    ]
    _, rows = parse_pasted_table("shr 02v-s-b разъём\t\t1\nКабель МГТФ белый 0,12\t\t2\nЧто-то другое\t\t3\n")
    matched = match_rows(paste_rows(rows, {"name": 0, "article": 1, "price": 2}), targets)
    assert [(row.rfq_line_id, row.matched_by) for row in matched] == [(a, "article"), (b, "name"), (None, None)]


def test_category_guess():
    assert guess_category("Кабель МГТФ") == "cable"
    assert guess_category("Разъём SHR") == "connector"
    assert guess_category("Резистор 10к") == "ekb"
    assert guess_category("Болт М3") == "other"
