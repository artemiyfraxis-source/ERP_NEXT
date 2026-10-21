import type { CSSProperties } from "react";
import { formatDay, unitLabel } from "./format";
import { useProjectLabel } from "./internalProjects";
import { quoteCell, unasked, type RfqCell, type RfqLine, type RfqMatrix, type RfqSupplier } from "./purchasingRfqApi";
import { money, number, quantity } from "./rfqUi";

/** The comparison matrix of a request (Roller_Buyer RfqPage + renderOfferCell, in ERP's dense sticky layout): rows are
 *  the request lines, two columns per supplier — the price with VAT and what covering the need costs. A cell shows how
 *  the seller sells it (pack, minimum, price breaks, the break the bought quantity falls into), «берём N = k × упаковка»,
 *  the gap to the plan, «база» and «мин». The checkbox column picks lines for the selection bar; in «Подбор» a click
 *  takes the offer into the set instead of opening the decision. */

export const REASONS: Record<string, string> = {
  key: "ключевой", category: "по категории", rotation: "давно не спрашивали", plan: "указан в строке", manual: "вручную",
};

export const chip = (tone: "ok" | "warn" | "neutral"): CSSProperties => ({
  display: "inline-block", borderRadius: 999, padding: "1px 7px", fontSize: 11, fontWeight: 600,
  color: tone === "neutral" ? "var(--muted-foreground)" : `var(--tone-${tone})`,
  background: tone === "warn" ? "var(--warn-soft)" : tone === "neutral" ? "var(--panel-soft)" : "color-mix(in srgb, var(--tone-ok) 12%, transparent)",
});
const groupEdge: CSSProperties = { borderLeft: "1px solid var(--border)" };

export function gateText(line: RfqLine) {
  return `КП ${line.gate.priced}/${line.gate.required}`;
}

export function gateHint(line: RfqLine) {
  const parts = [`КП с ценой: ${line.gate.priced} из ${line.gate.required} нужных`];
  if (line.gate.require_non_key && !line.gate.has_non_key) parts.push("нет КП от неключевого поставщика");
  return parts.join("; ");
}

/** A line whose supplier is chosen and which already left «В работе»/«Готов к запросу»: ordered, fixed. */
export const locked = (line: RfqLine) => Boolean(line.decision && !line.editable);

/** A line can be taken out, cancelled or split until a supplier is chosen for it. */
export const pickable = (line: RfqLine) => !line.cancelled && !line.decision;

/** «ни у кого нет — уберите в «Перезапрос»» / «пока нет цен»: no priced offer while someone asked has answered. */
export function nobodyHint(matrix: RfqMatrix, line: RfqLine): string | null {
  if (line.cancelled || line.decision) return null;
  const askedHere = matrix.suppliers.filter((supplier) => !unasked(line, supplier.supplier_id));
  const offers = askedHere.map((supplier) => quoteCell(line, supplier.supplier_id)).filter((cell): cell is RfqCell => Boolean(cell));
  if (offers.some((cell) => !cell.declined && !cell.expired)) return null;
  const answered = askedHere.filter((supplier) => supplier.declined_at || quoteCell(line, supplier.supplier_id));
  if (answered.length === 0) return null;
  return answered.length === askedHere.length ? "ни у кого нет — уберите в «Перезапрос»" : "пока нет цен";
}

/** The offer can go into «Подбор»: a current price on a line still open. */
export const basketable = (line: RfqLine, cell: RfqCell | undefined) =>
  Boolean(cell && !cell.declined && !cell.expired && cell.gross_unit !== null && !line.cancelled && !locked(line));

function PlanDelta({ pct }: { pct: string }) {
  const value = Math.round(Number(pct));
  if (value === 0) return <small className="muted rfq-delta">как в плане</small>;
  return <small className={`rfq-delta ${value > 0 ? "over" : "under"}`}>{value > 0 ? "+" : "−"}{Math.abs(value)} % к плану</small>;
}

/** How the seller sells it: the pack and every price break, so the price can be read (Roller renderOfferCell). */
function OfferTerms({ cell, unit }: { cell: RfqCell; unit: string }) {
  const tiers = cell.tiers_gross ?? [];
  const packWord = unitLabel(unit) === "м" ? "бухта" : "упаковка";
  const moq = cell.moq && Number(cell.moq) > 1 && cell.moq !== cell.pack ? cell.moq : null;
  if (!cell.pack && !moq && tiers.length === 0) return null;
  return (
    <div className="rfq-offer-terms">
      {cell.pack && <div>{packWord} {quantity(cell.pack)} {unitLabel(unit)}</div>}
      {moq && <div>мин. партия {quantity(moq)} {unitLabel(unit)}</div>}
      {tiers.length > 0 && (
        <div>
          до {quantity(tiers[0].from)} {unitLabel(unit)} — {money(cell.base_gross ?? null)}
          {tiers.map((tier) => <span key={tier.from}>; от {quantity(tier.from)} — {money(tier.gross)}</span>)}
        </div>
      )}
    </div>
  );
}

export function MatrixCell({ line, supplier, basket, onOpen, onBasket }: {
  line: RfqLine; supplier: RfqSupplier; basket: Map<string, string> | null; onOpen: () => void; onBasket: (quoteLineId: string) => void;
}) {
  if (unasked(line, supplier.supplier_id)) {
    return (
      <td colSpan={2} className="rfq-cell-unasked" style={groupEdge} title="Эту позицию у поставщика не запрашивали">
        не запрашивали
      </td>
    );
  }
  const cell = quoteCell(line, supplier.supplier_id);
  if (!cell) {
    return <>
      <td style={{ ...groupEdge, color: "var(--muted-foreground)" }}>—</td>
      <td style={{ color: "var(--muted-foreground)" }}>—</td>
    </>;
  }
  const inBasket = basket !== null && basket.get(line.id) === cell.quote_line_id;
  const background = inBasket ? "color-mix(in srgb, var(--primary) 14%, transparent)"
    : cell.decided ? "var(--sheet-selected)"
      : cell.cheapest ? "color-mix(in srgb, var(--tone-ok) 10%, transparent)" : undefined;
  const dim = cell.expired || cell.declined;
  const clickable = basket ? basketable(line, cell) : true;
  const style: CSSProperties = {
    background, cursor: clickable ? "pointer" : undefined, color: dim ? "var(--muted-foreground)" : undefined, verticalAlign: "top",
    outline: inBasket ? "2px solid var(--primary)" : undefined, outlineOffset: -2,
  };
  const label = `${line.name} — ${supplier.name}`;
  const open = () => {
    if (!clickable) return;
    if (basket) onBasket(cell.quote_line_id);
    else onOpen();
  };
  const unit = unitLabel(line.unit);
  const raised = cell.cover_qty !== null && Number(cell.cover_qty) > Number(line.quantity) + 1e-9;
  const terms = Boolean(cell.pack || (cell.tiers_gross ?? []).length || (cell.moq && Number(cell.moq) > 1));
  const isLocked = cell.decided && locked(line);
  return <>
    <td
      className="rfq-cell" style={{ ...style, ...groupEdge }} role="button" tabIndex={0} aria-label={label} aria-pressed={basket ? inBasket : undefined}
      onClick={open} onKeyDown={(event) => { if (event.key === "Enter") open(); }}
    >
      {cell.declined ? <span className="tone-danger">отказ</span> : <strong style={{ display: "inline" }}>{number(cell.gross_unit)}</strong>}
      {!cell.declined && <span className="muted"> /{unit}</span>}
      {cell.cheapest && <span style={{ ...chip("ok"), marginLeft: 4 }}>мин</span>}
      {cell.baseline && (
        <span
          style={{ ...chip("neutral"), marginLeft: 4 }}
          title="«Удобная» цена — с ней сравнивается выбор, разница идёт в экономию. Это самое дешёвое КП ключевых поставщиков, а если ключевые не ответили — первое полученное КП."
        >база</span>
      )}
      {cell.applied_from && <small className="muted" style={{ display: "block" }}>ступень от {quantity(cell.applied_from)} {unit}</small>}
      {!cell.declined && <OfferTerms cell={cell} unit={line.unit} />}
      {!cell.declined && cell.plan_pct !== null && cell.plan_pct !== undefined && <PlanDelta pct={cell.plan_pct} />}
      <small className="muted" style={{ display: "block" }}>
        {[cell.in_stock ? "в наличии" : cell.lead_days !== null ? `${cell.lead_days} дн` : "",
          cell.analog ? `аналог${cell.analog_note ? `: ${cell.analog_note}` : ""}` : "", cell.expired ? "срок КП истёк" : ""]
          .filter(Boolean).join(" · ")}
      </small>
    </td>
    <td className="rfq-cell" style={style} onClick={open}>
      {cell.decided && (
        <span className={isLocked ? "muted" : "tone-ok"} title={isLocked ? `Выбрано и заказано — строка уже «${line.status ?? ""}», поменять нельзя` : "Выбрано — можно поменять, пока строка в работе"}>
          {isLocked ? "🔒 заказано " : "✓ выбрано "}
        </span>
      )}
      {cell.declined ? "" : number(cell.total)}
      {!cell.declined && cell.cover_qty !== null && (terms || raised) && (
        <small className={raised ? "rfq-buy" : "rfq-buy exact"} style={{ display: "block" }}>
          берём {quantity(cell.cover_qty)} {unit}{cell.packs && cell.pack ? ` = ${quantity(cell.packs)} × ${quantity(cell.pack)} ${unit}` : ""}
        </small>
      )}
    </td>
  </>;
}

export function SupplierHead({ supplier, editable, total, deliveryDefault, onLines, onSent, onQuote, onDeclined, onRemove }: {
  supplier: RfqSupplier; editable: boolean; total: number; deliveryDefault?: string; onLines: () => void; onSent: () => void;
  onQuote: () => void; onDeclined: () => void; onRemove: () => void;
}) {
  const state = supplier.declined_at
    ? `отказался${supplier.decline_note ? `: ${supplier.decline_note}` : ""}`
    : supplier.answered ? "КП получено" : supplier.sent_at ? `отправлено ${formatDay(supplier.sent_at)}` : "не отправлено";
  return (
    <div style={{ display: "grid", gap: 2, textTransform: "none", letterSpacing: 0, fontSize: 12 }}>
      <strong style={{ fontSize: 13, color: "var(--foreground)" }} title={supplier.is_key ? "Ключевой поставщик" : undefined}>
        {supplier.name}{supplier.is_key ? " ★" : ""}
      </strong>
      <span className={supplier.declined_at ? "tone-danger" : supplier.answered ? "tone-ok" : "muted"}>{state} · {REASONS[supplier.reason] ?? ""}</span>
      <span className="muted">
        позиций: {supplier.line_ids?.length ?? total} из {total}
        {editable && <>
          {" · "}
          <button type="button" className="link-button" style={{ padding: 0, fontSize: 12 }} aria-label={`Изменить позиции ${supplier.name}`} onClick={onLines}>изменить</button>
        </>}
      </span>
      {(supplier.delivery_cost !== null || supplier.min_order_sum) && (
        <span className="muted">
          {supplier.delivery_cost !== null ? (Number(supplier.delivery_cost) === 0 ? "доставка бесплатно" : `доставка ${money(supplier.delivery_cost)}`) : ""}
          {supplier.delivery_cost !== null && supplier.min_order_sum ? " · " : ""}
          {supplier.min_order_sum ? `мин. заказ ${money(supplier.min_order_sum)}` : ""}
        </span>
      )}
      {supplier.basket_total && (
        <span title={supplier.delivery_known === false
          ? `Все позиции с ценой у этого поставщика; доставка в КП не указана — принята ${money(deliveryDefault ?? null)} (меняется в «Настройках»)`
          : "Все позиции с ценой у этого поставщика, с доставкой"}
        >
          всё у него: <strong style={{ display: "inline" }}>{money(supplier.basket_total)}</strong>{supplier.delivery_known === false ? "*" : ""} ({supplier.basket_lines} поз.)
        </span>
      )}
      {supplier.min_order_warning && <span className="tone-warn">{supplier.min_order_warning}</span>}
      {editable && (
        <span style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 2 }}>
          {!supplier.sent_at && <button type="button" className="secondary small" style={{ padding: "2px 8px" }} onClick={onSent}>Отправлено</button>}
          <button type="button" className="secondary small" style={{ padding: "2px 8px" }} onClick={onQuote} aria-label={`+ КП ${supplier.name}`}>+ КП</button>
          {!supplier.answered && !supplier.declined_at && <button type="button" className="link-button" style={{ padding: 0 }} onClick={onDeclined}>отказ</button>}
          {!supplier.answered && <button type="button" className="link-button" style={{ padding: 0 }} onClick={onRemove}>убрать</button>}
        </span>
      )}
    </div>
  );
}

export type MatrixActions = {
  onOpen: (lineId: string, supplierId: string) => void;
  onBasket: (lineId: string, quoteLineId: string) => void;
  onLines: (supplier: RfqSupplier) => void;
  onSent: (supplier: RfqSupplier) => void;
  onQuote: (supplier: RfqSupplier) => void;
  onDeclined: (supplier: RfqSupplier) => void;
  onRemove: (supplier: RfqSupplier) => void;
  /** «разделить» under a line; absent — no split here */
  onSplit?: (line: RfqLine) => void;
};

/** Sticky first columns: [the checkbox], Позиция (article, line id, project under it), Кол-во, План, КП. */
function stickyLayout(withCheck: boolean) {
  const widths = withCheck ? [30, 250, 76, 118, 64] : [250, 76, 118, 64];
  const left = widths.map((_, index) => widths.slice(0, index).reduce((sum, width) => sum + width, 0));
  return (index: number, head = false): CSSProperties => ({
    position: "sticky", left: left[index], zIndex: head ? 3 : 1, minWidth: widths[index], maxWidth: widths[index],
    background: head ? "var(--accent)" : "var(--card)", whiteSpace: "normal",
    boxShadow: index === widths.length - 1 ? "1px 0 0 var(--border)" : undefined,
  });
}

export function MatrixTable({ matrix, lines, editable, selectable = editable, picked, onPick, basket, actions }: {
  matrix: RfqMatrix; lines: RfqLine[]; editable: boolean; selectable?: boolean; picked: Set<string>;
  onPick: (ids: string[], on: boolean) => void; basket: Map<string, string> | null; actions: MatrixActions;
}) {
  const project = useProjectLabel();
  const { rfq, suppliers } = matrix;
  const withCheck = editable && selectable;
  const sticky = stickyLayout(withCheck);
  const at = (index: number) => (withCheck ? index + 1 : index);
  const live = matrix.lines.filter((line) => !line.cancelled);
  const allPickable = lines.filter(pickable).map((line) => line.id);
  return (
    <div className="inwork-scroll" style={{ maxHeight: "68vh" }}>
      <table className="inwork-table rfq-matrix" aria-label={`Матрица ${rfq.number}`} style={{ borderCollapse: "separate", borderSpacing: 0 }}>
        <thead>
          <tr>
            {withCheck && (
              <th rowSpan={2} style={sticky(0, true)}>
                {allPickable.length > 0 && (
                  <input
                    type="checkbox" aria-label="Все строки" checked={allPickable.every((lineId) => picked.has(lineId))}
                    onChange={(event) => onPick(allPickable, event.target.checked)}
                  />
                )}
              </th>
            )}
            <th rowSpan={2} style={sticky(at(0), true)}>Позиция</th>
            <th rowSpan={2} style={sticky(at(1), true)}>Кол-во</th>
            <th rowSpan={2} style={sticky(at(2), true)} title="Цена с НДС за единицу из расчёта проекта и где её нашли. В ячейках предложений — насколько КП дороже или дешевле плана.">План с НДС</th>
            <th rowSpan={2} style={sticky(at(3), true)} title="Правило конкуренции: поставщика можно выбрать без вопросов, когда по строке есть не меньше N КП с ценой (N — в «Настройках») и, если так настроено, хотя бы одно не от ключевого. Иначе выбор сохраняется только с причиной обхода.">КП</th>
            {suppliers.map((supplier) => (
              <th key={supplier.supplier_id} colSpan={2} style={{ ...groupEdge, verticalAlign: "top", minWidth: 200, whiteSpace: "normal", fontWeight: 400 }}>
                <SupplierHead
                  supplier={supplier} editable={editable && !basket} total={live.length} deliveryDefault={matrix.settings.delivery_default}
                  onLines={() => actions.onLines(supplier)} onSent={() => actions.onSent(supplier)} onQuote={() => actions.onQuote(supplier)}
                  onDeclined={() => actions.onDeclined(supplier)} onRemove={() => actions.onRemove(supplier)}
                />
              </th>
            ))}
          </tr>
          <tr>
            {suppliers.map((supplier) => [
              <th key={`${supplier.supplier_id}-p`} style={groupEdge}>Цена с НДС</th>,
              <th key={`${supplier.supplier_id}-t`}>Сумма</th>,
            ])}
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => {
            const hint = nobodyHint(matrix, line);
            // The article repeats the name on most calc lines — shown only when it adds something (as in the pool).
            const article = line.article && line.article.trim().toLowerCase() !== line.name.trim().toLowerCase() ? line.article : "";
            const meta = [article, line.legacy_id, line.status, line.need_by ? `к ${formatDay(line.need_by)}` : ""].filter(Boolean).join(" · ");
            const where = [project(line.project ?? null) || line.project, line.designation].filter(Boolean).join(" · ");
            return (
              <tr key={line.id} className={line.cancelled ? "rfq-row-cancelled" : picked.has(line.id) ? "rfq-row-picked" : undefined}>
                {withCheck && (
                  <td style={sticky(0)}>
                    {pickable(line) && !basket && (
                      <input type="checkbox" aria-label={`Выбрать ${line.legacy_id ?? line.name}`} checked={picked.has(line.id)} onChange={(event) => onPick([line.id], event.target.checked)} />
                    )}
                  </td>
                )}
                <td style={sticky(at(0))}>
                  <strong className={line.cancelled ? "rfq-struck" : undefined}>{line.name}</strong>
                  {line.cancelled && <span className="muted"> · отменена</span>}
                  <small className="muted" style={{ display: "block" }}>{meta}</small>
                  {where && <small className="muted" style={{ display: "block" }} title="Проект · назначение заявки">{where}</small>}
                  {hint && <small className={hint.startsWith("ни у кого") ? "rfq-nobody" : "muted"} style={{ display: "block" }}>{hint}</small>}
                  {editable && actions.onSplit && !basket && !line.cancelled && !locked(line) && (
                    <button
                      type="button" className="link-button rfq-split" aria-label={`Разделить ${line.legacy_id ?? line.name}`}
                      title="Разделить количество, чтобы части взять у разных поставщиков — КП останутся у обеих частей"
                      onClick={() => actions.onSplit?.(line)}
                    >разделить</button>
                  )}
                </td>
                <td style={sticky(at(1))}>{quantity(line.quantity)} {unitLabel(line.unit)}</td>
                <td style={sticky(at(2))}>
                  {money(line.budget_unit_gross)}
                  {line.delta_pct !== null && (
                    <small className={Number(line.delta_pct) > 0 ? "tone-danger" : "tone-ok"} style={{ display: "block" }}>
                      Δ {Number(line.delta_pct) > 0 ? "+" : ""}{number(line.delta_pct, 1)}%
                    </small>
                  )}
                  {line.plan_supplier && (line.plan_link && /^https?:\/\//i.test(line.plan_link)
                    ? <a className="muted rfq-plan-link" href={line.plan_link} target="_blank" rel="noreferrer" title={line.plan_link}>{line.plan_supplier} ↗</a>
                    : <small className="muted" style={{ display: "block" }}>{line.plan_supplier}</small>)}
                </td>
                <td style={sticky(at(3))}>
                  <span style={chip(line.gate.passes ? "ok" : "warn")} title={gateHint(line)}>{gateText(line)}</span>
                  {line.decision && (
                    <small className={locked(line) ? "muted" : "tone-ok"} style={{ display: "block" }} title="Цена выбранного КП с НДС; в строку закупки она не пишется — деньги вносятся по счёту">
                      {locked(line) ? "🔒 заказано" : "✓ решено"} · {money(line.decision.unit_gross)}
                    </small>
                  )}
                </td>
                {suppliers.map((supplier) => (
                  <MatrixCell
                    key={supplier.supplier_id} line={line} supplier={supplier} basket={basket}
                    onOpen={() => actions.onOpen(line.id, supplier.supplier_id)}
                    onBasket={(quoteLineId) => actions.onBasket(line.id, quoteLineId)}
                  />
                ))}
              </tr>
            );
          })}
        </tbody>
        {suppliers.length > 0 && (
          <tfoot>
            <tr>
              <td colSpan={withCheck ? 5 : 4} style={{ ...sticky(0), maxWidth: undefined }}><strong>Выбрано у поставщика</strong></td>
              {suppliers.map((supplier) => (
                <td key={supplier.supplier_id} colSpan={2} style={groupEdge}>{supplier.chosen_total ? money(supplier.chosen_total) : ""}</td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
