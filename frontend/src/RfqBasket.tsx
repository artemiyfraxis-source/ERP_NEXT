import { useState } from "react";
import { ApiError, failureText } from "./api";
import { quoteCell, rfqApi, type RfqMatrix } from "./purchasingRfqApi";
import { locked } from "./RfqMatrix";
import { GATE_LABELS, PRICIER_LABELS, type GateCode, type PricierCode } from "./rfqReasons";

/** «Подбор» (Roller_Buyer RfqBasket + shared/rfq-basket.ts): one offer per line from any supplier, clicked in the
 *  matrix, and what the whole set costs — the goods plus a delivery for every supplier used (the quote's own, else the
 *  «Доставка для подбора КП» setting). Two shortcuts fill the set: the cheapest per line, and the cheapest with deliveries
 *  counted over every subset of suppliers. Saved at once, then the invoice-request letters open. Money in kopecks here. */

export type BasketOffer = { quoteLineId: string; supplierId: string; coverGross: number | null; analog: boolean; declined: boolean };
export type BasketLine = { rfqLineId: string; offers: BasketOffer[] };
export type Delivery = { cost: number; known: boolean };
export type BasketSupplierTotal = { supplierId: string; lines: number; goods: number; delivery: Delivery };
export type BasketTotals = { suppliers: BasketSupplierTotal[]; goods: number; delivery: number; total: number; unpicked: number };
export type BasketPicks = ReadonlyMap<string, string>;

/** Rubles as the server sends them → kopecks. */
export const kop = (value: string | null | undefined) => (value === null || value === undefined || value === "" ? null : Math.round(Number(value) * 100));
const rub = (kopecks: number) => `${(kopecks / 100).toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;

const priced = (offer: BasketOffer) => !offer.declined && offer.coverGross !== null;

/** Offers a line may be filled from automatically: analogs only where no original was offered. */
export function eligibleOffers(line: BasketLine, preferOriginals: boolean): BasketOffer[] {
  const all = line.offers.filter(priced);
  if (!preferOriginals) return all;
  const originals = all.filter((offer) => !offer.analog);
  return originals.length ? originals : all;
}

export function basketTotals(lines: readonly BasketLine[], picks: BasketPicks, deliveryOf: (supplierId: string) => Delivery): BasketTotals {
  const bySupplier = new Map<string, BasketSupplierTotal>();
  let unpicked = 0;
  for (const line of lines) {
    const offer = line.offers.find((candidate) => candidate.quoteLineId === picks.get(line.rfqLineId) && priced(candidate));
    if (!offer) {
      if (line.offers.some(priced)) unpicked += 1;
      continue;
    }
    const total = bySupplier.get(offer.supplierId) ?? { supplierId: offer.supplierId, lines: 0, goods: 0, delivery: deliveryOf(offer.supplierId) };
    total.lines += 1;
    total.goods += offer.coverGross!;
    bySupplier.set(offer.supplierId, total);
  }
  const suppliers = [...bySupplier.values()].sort((a, b) => b.goods - a.goods);
  const goods = suppliers.reduce((sum, supplier) => sum + supplier.goods, 0);
  const delivery = suppliers.reduce((sum, supplier) => sum + supplier.delivery.cost, 0);
  return { suppliers, goods, delivery, total: goods + delivery, unpicked };
}

const cheapestOf = (offers: BasketOffer[]) =>
  offers.reduce<BasketOffer | null>((best, offer) => (!best || offer.coverGross! < best.coverGross! ? offer : best), null);

/** The cheapest offer of every line, deliveries not counted. */
export function cheapestPicks(lines: readonly BasketLine[], preferOriginals: boolean): Map<string, string> {
  const picks = new Map<string, string>();
  for (const line of lines) {
    const best = cheapestOf(eligibleOffers(line, preferOriginals));
    if (best) picks.set(line.rfqLineId, best.quoteLineId);
  }
  return picks;
}

/** The set of suppliers that covers every line for the least money, deliveries included: every subset is tried (a
 *  request has a handful of suppliers); beyond 14, the cheapest-per-line set is thinned while dropping one still saves. */
export function bestBasket(lines: readonly BasketLine[], deliveryOf: (supplierId: string) => Delivery, preferOriginals: boolean): Map<string, string> {
  const work = lines.map((line) => ({ rfqLineId: line.rfqLineId, offers: eligibleOffers(line, preferOriginals) })).filter((line) => line.offers.length > 0);
  const suppliers = [...new Set(work.flatMap((line) => line.offers.map((offer) => offer.supplierId)))];
  type Fill = { picks: Map<string, string>; total: number; used: number };
  const fill = (allowed: ReadonlySet<string>): Fill | null => {
    const picks = new Map<string, string>();
    const used = new Set<string>();
    let total = 0;
    for (const line of work) {
      const best = cheapestOf(line.offers.filter((offer) => allowed.has(offer.supplierId)));
      if (!best) return null;
      picks.set(line.rfqLineId, best.quoteLineId);
      used.add(best.supplierId);
      total += best.coverGross!;
    }
    for (const supplier of used) total += deliveryOf(supplier).cost;
    return { picks, total, used: used.size };
  };
  const better = (a: Fill, b: Fill | null) => !b || a.total < b.total || (a.total === b.total && a.used < b.used);
  let best: Fill | null = null;
  if (suppliers.length <= 14) {
    for (let mask = 1; mask < 1 << suppliers.length; mask++) {
      const found = fill(new Set(suppliers.filter((_, index) => mask & (1 << index))));
      if (found && better(found, best)) best = found;
    }
  } else {
    const allowed = new Set(suppliers);
    best = fill(allowed);
    for (let improved = true; improved && best;) {
      improved = false;
      for (const supplier of [...allowed]) {
        const found = fill(new Set([...allowed].filter((other) => other !== supplier)));
        if (found && better(found, best)) {
          best = found;
          allowed.delete(supplier);
          improved = true;
        }
      }
    }
  }
  return best?.picks ?? new Map();
}

/** Lines «Подбор» works on — not cancelled, not ordered yet — with their current offers. */
export function basketLines(m: RfqMatrix): BasketLine[] {
  return m.lines.filter((line) => !line.cancelled && !locked(line)).map((line) => ({
    rfqLineId: line.id,
    offers: m.suppliers.flatMap((supplier) => {
      const cell = quoteCell(line, supplier.supplier_id);
      if (!cell || cell.expired) return [];
      return [{ quoteLineId: cell.quote_line_id, supplierId: supplier.supplier_id, coverGross: cell.declined ? null : kop(cell.total), analog: cell.analog, declined: cell.declined }];
    }),
  }));
}

/** The buyer's current choices as a starting set. */
export function decidedPicks(m: RfqMatrix): Map<string, string> {
  return new Map(m.lines.filter((line) => !line.cancelled && line.decision && !locked(line)).map((line) => [line.id, line.decision!.quote_line_id]));
}

export function deliveryOfMatrix(m: RfqMatrix): (supplierId: string) => Delivery {
  const fallback = kop(m.settings.delivery_default ?? "1500") ?? 150000;
  return (supplierId) => {
    const known = kop(m.suppliers.find((supplier) => supplier.supplier_id === supplierId)?.delivery_cost);
    return known !== null ? { cost: known, known: true } : { cost: fallback, known: false };
  };
}

const supplierCount = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? "поставщик" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "поставщика" : "поставщиков"}`;

export function RfqBasket({ m, picks, setPicks, onClose, onSaved }: {
  m: RfqMatrix; picks: Map<string, string>; setPicks: (next: Map<string, string>) => void; onClose: () => void;
  /** After «Выбрать поставщиков по подбору»: the message, then the invoice requests come next. */
  onSaved: (message: string) => void;
}) {
  const [preferOriginals, setPreferOriginals] = useState(true);
  const [pricierCode, setPricierCode] = useState<PricierCode>("other");
  const [pricierNote, setPricierNote] = useState("Подбор: дешевле в сумме с доставкой — меньше поставщиков");
  const [gateCode, setGateCode] = useState<GateCode | "">("");
  const [gateNote, setGateNote] = useState("");
  const [analogBy, setAnalogBy] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const lines = basketLines(m);
  const supplier = new Map(m.suppliers.map((item) => [item.supplier_id, item]));
  const deliveryOf = deliveryOfMatrix(m);
  const deliveryDefault = deliveryOf("").cost;
  const totals = basketTotals(lines, picks, deliveryOf);
  const cheapest = cheapestPicks(lines, preferOriginals);
  const best = bestBasket(lines, deliveryOf, preferOriginals);
  const cheapestTotals = basketTotals(lines, cheapest, deliveryOf);
  const bestTotals = basketTotals(lines, best, deliveryOf);

  const picked = m.lines.filter((line) => picks.has(line.id) && !line.cancelled && !locked(line));
  const cellOf = (line: (typeof picked)[number]) => m.suppliers.map((item) => quoteCell(line, item.supplier_id)).find((cell) => cell?.quote_line_id === picks.get(line.id));
  const fresh = picked.filter((line) => line.decision?.quote_line_id !== picks.get(line.id));
  // The calculation's plan for the picked lines: the planned unit price × the need.
  const planOf = (line: (typeof picked)[number]) => (line.budget_unit_gross !== null ? Math.round(Number(line.budget_unit_gross) * Number(line.quantity) * 100) : null);
  const planBySupplier = new Map<string, number>();
  let plan = 0;
  let withoutPlan = 0;
  for (const line of picked) {
    const cell = cellOf(line);
    const value = planOf(line);
    if (!cell) continue;
    if (value === null) { withoutPlan += 1; continue; }
    plan += value;
    const owner = m.suppliers.find((item) => quoteCell(line, item.supplier_id)?.quote_line_id === cell.quote_line_id)?.supplier_id ?? "";
    planBySupplier.set(owner, (planBySupplier.get(owner) ?? 0) + value);
  }
  const goodsWithPlan = picked.reduce((sum, line) => (planOf(line) !== null ? sum + (kop(cellOf(line)?.total) ?? 0) : sum), 0);
  const notCheapest = fresh.filter((line) => line.cheapest_quote_line_id !== null && line.cheapest_quote_line_id !== picks.get(line.id)).length;
  const gate = fresh.filter((line) => !line.gate.passes).length;
  const analogs = fresh.filter((line) => cellOf(line)?.analog).length;
  const missing = picked.length === 0 ? "Выберите предложения в таблице"
    : notCheapest && pricierCode === "other" && !pricierNote.trim() ? "Опишите, почему не самое дешёвое"
      : gate && (!gateCode || (gateCode === "other" && !gateNote.trim())) ? "Укажите причину обхода правила КП"
        : analogs && !analogBy.trim() ? "Аналог: укажите, с кем согласована замена" : null;

  async function save() {
    setBusy(true);
    setError("");
    try {
      const result = await rfqApi.decideBatch(m.rfq.id, {
        picks: picked.map((line) => ({ rfq_line_id: line.id, quote_line_id: picks.get(line.id)!, expected_version: line.version })),
        ...(notCheapest ? { not_cheapest_code: pricierCode, not_cheapest_note: pricierCode === "other" ? pricierNote.trim() : null } : {}),
        ...(gate && gateCode ? { gate_override_code: gateCode, gate_override_note: gateCode === "other" ? gateNote.trim() : null } : {}),
        ...(analogs ? { analog_approved_by: analogBy.trim() } : {}),
      });
      onSaved(`Подбор сохранён: выбрано по ${result.decided} строк${result.kept ? `, без изменений — ${result.kept}` : ""}. Поставщик и количество записаны в строки закупки.`);
    } catch (reason) {
      const detail = reason instanceof ApiError ? (reason.detail as { code?: string; message?: string } | undefined) : undefined;
      if (detail?.code === "batch_partial") {
        onSaved(detail.message ?? failureText(reason, "Подбор записан не весь"));
        return;
      }
      setError(failureText(reason, "Подбор не сохранён"));
    } finally {
      setBusy(false);
    }
  }

  const summary = (value: BasketTotals) => `${rub(value.total)} · ${supplierCount(value.suppliers.length)}`;
  return (
    <section className="panel rfq-basket" aria-label="Подбор">
      <div className="rfq-letters-head">
        <h3>Подбор</h3>
        <span className="muted">Щёлкайте по ценам в таблице: по одной на строку. Доставка — из КП, а где не указана — {rub(deliveryDefault)} за поставщика.</span>
        <button type="button" className="secondary small rfq-letters-close" onClick={onClose}>Закрыть подбор</button>
      </div>
      <div className="rfq-basket-actions">
        <button type="button" className="secondary small" title="По каждой строке — самое дешёвое предложение, доставку не считая" onClick={() => setPicks(cheapest)}>
          Дешевле всего по строкам · {summary(cheapestTotals)}
        </button>
        <button type="button" className="primary small" title="Набор поставщиков с наименьшей суммой с учётом доставки каждого" onClick={() => setPicks(best)}>
          Дешевле всего с доставкой · {summary(bestTotals)}
        </button>
        <button type="button" className="secondary small" onClick={() => setPicks(new Map())}>Очистить</button>
        <label className="muted rfq-basket-check">
          <input type="checkbox" checked={preferOriginals} onChange={(event) => setPreferOriginals(event.target.checked)} /> аналоги — только где нет оригинала
        </label>
      </div>
      {totals.suppliers.length ? (
        <table className="grid rfq-basket-table">
          <thead>
            <tr><th className="grow">Поставщик</th><th className="num">Строк</th><th className="num">План</th><th className="num">Товары</th><th className="num">Доставка</th><th className="num">Итого</th></tr>
          </thead>
          <tbody>
            {totals.suppliers.map((total) => {
              const head = supplier.get(total.supplierId);
              const minimum = kop(head?.min_order_sum);
              const short = minimum && total.goods < minimum ? minimum - total.goods : 0;
              return (
                <tr key={total.supplierId}>
                  <td className="text">
                    {head?.name ?? total.supplierId}
                    {short ? <small className="rfq-buy" style={{ display: "block" }}>до мин. заказа не хватает {rub(short)}</small> : null}
                  </td>
                  <td className="num">{total.lines}</td>
                  <td className="num muted">{planBySupplier.has(total.supplierId) ? rub(planBySupplier.get(total.supplierId)!) : "—"}</td>
                  <td className="num">{rub(total.goods)}</td>
                  <td className="num" title={total.delivery.known ? "Из КП поставщика" : "Не указана в КП — принята по настройкам"}>{rub(total.delivery.cost)}{total.delivery.known ? "" : " *"}</td>
                  <td className="num">{rub(total.goods + total.delivery.cost)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th>Всего</th><th className="num">{picked.length}</th><th className="num">{plan ? rub(plan) : "—"}</th>
              <th className="num">{rub(totals.goods)}</th><th className="num">{rub(totals.delivery)}</th><th className="num">{rub(totals.total)}</th>
            </tr>
          </tfoot>
        </table>
      ) : <p className="muted">Ничего не выбрано.</p>}
      {plan ? (
        <p className={goodsWithPlan > plan ? "rfq-delta over" : "rfq-delta under"}>
          План по выбранным строкам {rub(plan)}, по подбору {rub(goodsWithPlan)} без доставки:{" "}
          {goodsWithPlan > plan ? `дороже на ${rub(goodsWithPlan - plan)}` : `дешевле на ${rub(plan - goodsWithPlan)}`} (
          {goodsWithPlan > plan ? "+" : "−"}{Math.abs(Math.round((goodsWithPlan / plan - 1) * 100))} %); с доставкой — {rub(totals.total)}
          {withoutPlan ? ` · без плана: ${withoutPlan} строк` : ""}
        </p>
      ) : picked.length ? <p className="muted">У выбранных строк нет плановых цен из расчёта.</p> : null}
      {totals.suppliers.some((total) => !total.delivery.known) && <p className="muted">* доставка не указана в КП — принята {rub(deliveryDefault)} (меняется в «Настройках»)</p>}
      {totals.unpicked ? <p className="rfq-buy">Без выбора: {totals.unpicked} строк с ценами</p> : null}
      {bestTotals.total < totals.total && picked.length ? (
        <p className="muted">«Дешевле всего с доставкой» выйдет на {rub(totals.total - bestTotals.total)} дешевле текущего набора.</p>
      ) : null}
      <div className="rfq-basket-reasons">
        {notCheapest > 0 && (
          <label>Не самое дешёвое по {notCheapest} строк — причина
            <select aria-label="Причина: не самое дешёвое" value={pricierCode} onChange={(event) => setPricierCode(event.target.value as PricierCode)}>
              {Object.entries(PRICIER_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
            {pricierCode === "other" && <input aria-label="Своими словами: не самое дешёвое" value={pricierNote} onChange={(event) => setPricierNote(event.target.value)} />}
          </label>
        )}
        {gate > 0 && (
          <label>Меньше КП, чем нужно, по {gate} строк — причина обхода
            <select aria-label="Причина обхода" value={gateCode} onChange={(event) => setGateCode(event.target.value as GateCode)}>
              <option value="" disabled>выберите…</option>
              {Object.entries(GATE_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
            {gateCode === "other" && <input aria-label="Своими словами: причина обхода" value={gateNote} onChange={(event) => setGateNote(event.target.value)} />}
          </label>
        )}
        {analogs > 0 && (
          <label>Аналоги ({analogs}) — с кем согласована замена
            <input aria-label="С кем согласован аналог" value={analogBy} maxLength={255} onChange={(event) => setAnalogBy(event.target.value)} placeholder="ФИО конструктора" />
          </label>
        )}
      </div>
      {error && <div className="error-banner" role="alert">{error}</div>}
      <div className="form-actions">
        {missing && <span className="muted" style={{ marginRight: "auto" }}>{missing}</span>}
        <button type="button" className="primary" disabled={missing !== null || busy} onClick={() => void save()}>
          {busy ? "Сохраняю…" : `Выбрать поставщиков по подбору (${picked.length} строк)`}
        </button>
      </div>
    </section>
  );
}
