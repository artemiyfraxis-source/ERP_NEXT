import { useEffect, useMemo, useState } from "react";
import { failureText, procurementApi, type KpRowInput } from "./api";
import { projectLabel } from "./internalProjects";
import { formatAmount } from "./ProcurementSheetColumns";
import { ordersApi, type OpenDecisionLine, type OpenDecisionSupplier } from "./purchasingOrdersApi";

/* «Новый счёт» (Roller_Buyer NewOrderForm) из решений по запросам КП: строки «Готов к запросу», чьё решение уже записало
   поставщика и количество, но заказ ещё не оформлен, — по поставщику. «Оформить заказ поставщику» — обычный запрос
   поставщику ERP (№ заказа, документ запроса, строки «Жду счет»); цены решения по желанию сразу ложатся «Ценой факт»
   (как «Внести КП»), доставка из КП — фактом заказа. Номер и дату счёта вносит потом «Вставить счёт». */

type Draft = { checked: boolean; quantity: string; price: string };

const decimal = (value: string) => value.trim().replace(/\s/g, "").replace(",", ".");
const isNumber = (value: string) => decimal(value) !== "" && Number.isFinite(Number(decimal(value))) && Number(decimal(value)) > 0;

/** Почему количество решения больше потребности: мин. партия и/или кратность (излишек уйдёт в свободный остаток). */
export function surplusHint(line: Pick<OpenDecisionLine, "need" | "decided_quantity" | "moq" | "pack">): string {
  const need = Number(line.need);
  if (!(Number(line.decided_quantity) > need)) return "";
  const moq = line.moq !== null && Number(line.moq) > need;
  const reason = moq ? `мин. партия${line.pack ? " и кратность" : ""}` : "кратность";
  return `нужно ${need.toLocaleString("ru-RU")} — ${reason}`;
}

export function NewOrderForm({ onDone, onCancel }: {
  onDone: (orderNumbers: string[], notes: string[]) => void;
  onCancel: () => void;
}) {
  const [suppliers, setSuppliers] = useState<OpenDecisionSupplier[] | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState("");
  const [rows, setRows] = useState<Record<string, Draft>>({});
  const [delivery, setDelivery] = useState("");
  const [withPrices, setWithPrices] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refusals, setRefusals] = useState<string[]>([]);

  useEffect(() => {
    let live = true;
    ordersApi.decisions()
      .then((result) => { if (live) setSuppliers(result.suppliers); })
      .catch((reason) => { if (live) setError(failureText(reason, "Решения не загрузились")); });
    return () => { live = false; };
  }, []);

  const group = suppliers?.find((item) => item.supplier === picked);
  function pick(name: string) {
    setPicked(name);
    setRefusals([]);
    const found = suppliers?.find((item) => item.supplier === name);
    const next: Record<string, Draft> = {};
    for (const line of found?.lines ?? []) {
      next[line.line_id] = { checked: line.blockers.length === 0, quantity: line.quantity ?? line.decided_quantity, price: line.unit_gross };
    }
    setRows(next);
    // Доставка из КП — обычно счёт выставляет ровно её.
    setDelivery(found?.delivery_cost ?? "");
  }

  const chosen = useMemo(() => (group?.lines ?? []).filter((line) => rows[line.line_id]?.checked), [group, rows]);
  const sum = chosen.reduce((total, line) => {
    const row = rows[line.line_id];
    return isNumber(row.price) && isNumber(row.quantity) ? total + Number(decimal(row.price)) * Number(decimal(row.quantity)) : total;
  }, 0);
  const rub = group?.currency === "RUB";
  const minimum = group?.min_order_sum ? Number(group.min_order_sum) : 0;
  const bad = chosen.filter((line) => !isNumber(rows[line.line_id].quantity) || (withPrices && rub && !isNumber(rows[line.line_id].price)));
  const deliveryBad = delivery.trim() !== "" && !(Number(decimal(delivery)) >= 0);

  async function submit() {
    if (!group || !chosen.length || bad.length || deliveryBad) return;
    setBusy(true);
    setError("");
    setRefusals([]);
    try {
      // 1. Количество, изменённое здесь, — в строку до запроса (документ запроса печатает его).
      const changed = chosen.filter((line) => decimal(rows[line.line_id].quantity) !== decimal(line.quantity ?? ""));
      const versions = new Map(chosen.map((line) => [line.line_id, line.version]));
      if (changed.length) {
        const saved = await procurementApi.saveFactsBatch(changed.map((line) => ({
          line_id: line.line_id, version: line.version, actual_quantity: decimal(rows[line.line_id].quantity),
        })));
        for (const line of saved.lines) versions.set(line.id, line.version);
        const failed = [...saved.failed.map((item) => `${item.legacy_id}: ${item.error}`), ...saved.skipped.map((id) => `${id}: строка изменилась — обновите`)];
        if (failed.length) {
          setRefusals(failed);
          return;
        }
      }
      // 2. Запрос поставщику ERP — № заказа, документ, строки «Жду счет»; чего нельзя — причины по строкам.
      const result = await procurementApi.buildSupplierRequests(chosen.map((line) => line.line_id));
      const byId = new Map(chosen.map((line) => [line.line_id, line]));
      const notes = result.skipped.map((item) => `${byId.get(item.line_id)?.legacy_id ?? item.line_id}: ${item.reason}`);
      const fresh = new Map(result.lines.map((line) => [line.id, line]));
      for (const order of result.orders) {
        // 3. Цены решения — «Ценой факт» всего заказа одним «Внести КП» (строки остаются «Жду счет»).
        if (withPrices && rub) {
          const kpRows: KpRowInput[] = order.line_ids.flatMap((id) => {
            const line = fresh.get(id);
            const decision = byId.get(id);
            if (!line || !decision) return [];
            return [{
              line_id: id, version: line.version, actual_quantity: line.actual_quantity, actual_unit: line.actual_unit,
              currency: "RUB", driver: "gross", price: null, vat_rate: decision.vat_rate, price_with_vat: decimal(rows[id].price),
              sum: null, invoice_item_name: null, invoice_number: null, invoice_date: null, expected_arrival: null,
            }];
          });
          try {
            if (kpRows.length) await procurementApi.saveKp(order.order_number, kpRows, "");
          } catch (reason) {
            notes.push(`${order.order_number}: цены решения не внесены — ${failureText(reason, "ошибка")}. Внесите их «Вставить КП» или счётом`);
          }
        }
        // 4. Доставка из КП — фактом заказа.
        if (delivery.trim() && Number(decimal(delivery)) > 0) {
          try {
            await ordersApi.saveFacts(order.order_number, 0, { delivery_cost: decimal(delivery) });
          } catch (reason) {
            notes.push(`${order.order_number}: доставка не сохранена — ${failureText(reason, "ошибка")}`);
          }
        }
      }
      if (!result.orders.length) {
        setRefusals(notes.length ? notes : ["Заказ не оформлен: строки изменились — обновите"]);
        return;
      }
      onDone(result.orders.map((order) => order.order_number), notes);
    } catch (reason) {
      setError(failureText(reason, "Заказ не оформлен"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel new-order" aria-label="Новый счёт">
      <div className="panel-heading">
        <h2>Новый счёт</h2>
        <span>из решений по запросам КП, ещё не заказанных</span>
      </div>
      <div className="new-order-body">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {!suppliers && !error && <div className="empty">Загружаю…</div>}
        {suppliers && suppliers.length === 0 && (
          <div className="empty">Нет выбранных, но не заказанных решений. Сначала выберите поставщика в «Запросах КП».</div>
        )}
        {suppliers && suppliers.length > 0 && (
          <label className="doc-inline-label">Поставщик
            <select aria-label="Поставщик" value={picked} onChange={(event) => pick(event.target.value)}>
              <option value="" disabled>Выберите поставщика</option>
              {suppliers.map((item) => <option key={item.supplier} value={item.supplier}>{item.supplier} · {item.lines.length} поз.</option>)}
            </select>
          </label>
        )}
        {group && (
          <>
            <div className="table-wrap">
              <table className="grid new-order-lines" aria-label="Строки нового счёта">
                <thead>
                  <tr>
                    <th aria-label="В заказ" />
                    <th className="grow">Позиция</th><th>Проект</th><th>Запрос КП</th>
                    <th className="num">Кол-во</th><th className="num">Цена с НДС, {rub ? "₽" : group.currency}/ед</th><th>НДС</th><th className="num">Сумма</th>
                  </tr>
                </thead>
                <tbody>
                  {group.lines.map((line) => {
                    const row = rows[line.line_id];
                    if (!row) return null;
                    const hint = surplusHint(line);
                    const lineSum = isNumber(row.price) && isNumber(row.quantity) ? Number(decimal(row.price)) * Number(decimal(row.quantity)) : null;
                    const blocked = line.blockers.length > 0;
                    return (
                      <tr key={line.line_id} className={blocked ? "muted-row" : undefined}>
                        <td>
                          <input type="checkbox" aria-label={`В заказ ${line.legacy_id}`} disabled={blocked || busy} checked={row.checked}
                            onChange={(event) => setRows((current) => ({ ...current, [line.line_id]: { ...current[line.line_id], checked: event.target.checked } }))} />
                        </td>
                        <td className="text"><strong>{line.legacy_id}</strong> {line.name}
                          {line.article && <small className="muted">{line.article}</small>}
                          {blocked && <small className="tone-warn">не оформится: {line.blockers.join("; ")}</small>}
                        </td>
                        <td>{projectLabel(line.project) || "—"}</td>
                        <td>{line.rfq_number}{line.lead_days !== null && <small className="muted">срок {line.lead_days} дн.</small>}</td>
                        <td className="num">
                          <input className="new-order-input" aria-label={`Количество ${line.legacy_id}`} inputMode="decimal" disabled={busy} value={row.quantity}
                            onChange={(event) => setRows((current) => ({ ...current, [line.line_id]: { ...current[line.line_id], quantity: event.target.value } }))} />
                          <small className="muted">{line.unit}</small>
                          {hint && <small className="tone-warn" title="Излишек при приёмке уйдёт в свободный остаток">{hint}</small>}
                        </td>
                        <td className="num">
                          <input className="new-order-input" aria-label={`Цена ${line.legacy_id}`} inputMode="decimal" disabled={busy} value={row.price}
                            onChange={(event) => setRows((current) => ({ ...current, [line.line_id]: { ...current[line.line_id], price: event.target.value } }))} />
                        </td>
                        <td>{Number(line.vat_rate)}%{line.vat_included ? "" : <small className="muted">в КП без НДС</small>}</td>
                        <td className="num">{lineSum !== null ? formatAmount(lineSum) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="new-order-form">
              <label>Доставка, ₽ с НДС
                <input inputMode="decimal" aria-label="Доставка" placeholder="0" disabled={busy} value={delivery} onChange={(event) => setDelivery(event.target.value)} />
              </label>
              <label className="inline-check">
                <input type="checkbox" checked={withPrices && rub} disabled={!rub || busy} onChange={(event) => setWithPrices(event.target.checked)} />
                Внести цены решения «Ценой факт»{!rub && <small className="muted"> — КП не в рублях: цену внесите «Внести КП в валюте»</small>}
              </label>
              <span className="new-order-total">Сумма с НДС: <b>{formatAmount(sum)} {rub ? "₽" : group.currency}</b>
                {Number(decimal(delivery)) > 0 && <> + доставка {formatAmount(decimal(delivery))} = <b>{formatAmount(sum + Number(decimal(delivery)))}</b></>}
              </span>
            </div>
            {minimum > 0 && sum < minimum && (
              <div className="warning-inline" role="status">
                Минимальная сумма заказа у поставщика по КП — {formatAmount(minimum)} ₽, в счёте {formatAmount(sum)} ₽: не хватает {formatAmount(minimum - sum)} ₽.
                Добавьте позиции или уточните у поставщика.
              </div>
            )}
            {bad.length > 0 && <div className="warning-inline">Количество или цена не числом: {bad.map((line) => line.legacy_id).join(", ")}</div>}
            {refusals.length > 0 && (
              <div className="warning-inline" role="alert">
                <strong>Не оформлено:</strong>
                <ul>{refusals.map((text) => <li key={text}>{text}</li>)}</ul>
              </div>
            )}
            <p className="muted">Отмеченные строки уйдут поставщику обычным запросом ERP: номер заказа, документ запроса, строки «Жду счёт». Номер и дату счёта внесёт «Вставить счёт».</p>
          </>
        )}
        <div className="move-actions">
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>Отмена</button>
          <button type="button" className="primary" disabled={busy || !group || !chosen.length || bad.length > 0 || deliveryBad} onClick={() => void submit()}>
            {busy ? "Оформляем…" : `Оформить заказ поставщику${chosen.length ? ` (${chosen.length})` : ""}`}
          </button>
        </div>
      </div>
    </section>
  );
}
