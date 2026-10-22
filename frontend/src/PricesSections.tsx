import { useEffect, useState } from "react";
import { failureText } from "./api";
import { NBSP, formatDay, formatNumber } from "./format";
import {
  CURRENCY_SIGNS, purchasingPricesApi, type ItemPrices, type SupplierPrices, type UpdPurchase,
} from "./purchasingPricesApi";

/** Sections of the item and supplier cards of the price book. A UPD number is a button when the host passes `onOpenUpd` (the UPD card lives in another screen). */
export const kopecks = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : `${formatNumber(value / 100, { digits: 2, minDigits: 2 })}${NBSP}₽`;
const price4 = (value: number) => formatNumber(value / 10_000, { digits: 4, minDigits: 2 });
const day = (value: string | null | undefined) => (value ? formatDay(value) : "—");
const INVOICE_STATUS: Record<string, string> = { to_pay: "ждёт оплаты", paid: "оплачен", return: "возврат", other: "прочее" };

export function UpdNumber({ id, number, onOpenUpd }: { id: string; number: string; onOpenUpd?: (id: string) => void }) {
  const text = number || "без номера";
  return onOpenUpd
    ? <button type="button" className="link-button table-link" aria-label={`Открыть УПД ${text}`} onClick={() => onOpenUpd(id)}>{text}</button>
    : <>{text}</>;
}

function usePrices<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    setData(null);
    load().then((next) => { if (live) { setData(next); setError(""); } })
      .catch((reason) => { if (live) setError(failureText(reason, "Цены не загрузились")); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

export function ItemPricesSections({ itemId, onOpenUpd }: { itemId: string; onOpenUpd?: (id: string) => void }) {
  const { data, error } = usePrices<ItemPrices>(() => purchasingPricesApi.item(itemId), [itemId]);
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загружаю цены…</div>;
  const { summary } = data;
  const upd: UpdPurchase[] = data.upd;
  return (
    <div className="prices-sections">
      <h3>Справочные цены</h3>
      {data.refs.length === 0 ? <p className="muted">В прайсах и старых КП этой позиции нет.</p> : (
        <div className="table-wrap history-table">
          <table className="grid" aria-label="Справочные цены">
            <thead><tr><th>Поставщик</th><th>Его обозначение</th><th className="num">Цена за единицу</th><th>НДС</th><th className="num">От, шт.</th><th>Срок</th><th>Источник</th></tr></thead>
            <tbody>
              {data.refs.map((ref) => (
                <tr key={ref.id}>
                  <td className="text">{ref.supplier_name || "—"}</td>
                  <td>{ref.offered_part || "—"}{ref.offered_manufacturer ? <small> {ref.offered_manufacturer}</small> : null}</td>
                  <td className="num">{price4(ref.unit_price4)}{NBSP}{CURRENCY_SIGNS[ref.currency]}</td>
                  <td>{ref.vat_included ? "с НДС" : "без НДС"}</td>
                  <td className="num">{ref.quantity === null ? "—" : formatNumber(ref.quantity)}</td>
                  <td>{ref.lead_time || "—"}</td><td className="text">{ref.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3>Покупали по УПД</h3>
      {upd.length === 0 ? <p className="muted">В УПД этой позиции не нашли.</p> : (
        <>
          <p>
            За единицу ({data.unit}) с НДС: мин. <strong>{kopecks(summary.min)}</strong>, последняя <strong>{kopecks(summary.last)}</strong>
            {summary.last_date ? <> ({day(summary.last_date)}{summary.last_supplier ? `, ${summary.last_supplier}` : ""})</> : null}.
            {" "}Считаются только разобранные и проверенные УПД.
          </p>
          <div className="table-wrap history-table">
            <table className="grid" aria-label="Покупки по УПД">
              <thead><tr><th>Дата</th><th>Поставщик</th><th>УПД</th><th className="num">Кол-во</th><th className="num">Цена с НДС</th></tr></thead>
              <tbody>
                {upd.map((p, index) => (
                  <tr key={`${p.upd_id}:${index}`}>
                    <td>{day(p.date)}</td><td className="text">{p.supplier_name || "—"}</td>
                    <td><UpdNumber id={p.upd_id} number={p.number} onOpenUpd={onOpenUpd} />{p.needs_review ? <small> на проверке</small> : null}</td>
                    <td className="num">{p.quantity === null ? "—" : formatNumber(p.quantity)} {p.unit}</td>
                    <td className="num" title={p.item_unit_price_with_vat === null ? `Единицу «${p.unit}» не пересчитать в «${data.unit}»` : undefined}>
                      {kopecks(p.unit_price_with_vat)}{p.unit ? `/${p.unit}` : ""}
                      {p.item_unit_price_with_vat !== null && p.item_unit_price_with_vat !== p.unit_price_with_vat ? <small> = {kopecks(p.item_unit_price_with_vat)}/{data.unit}</small> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {data.invoices.length > 0 && (
        <>
          <h3>В счетах Kaiten</h3>
          <div className="table-wrap history-table">
            <table className="grid" aria-label="Позиции счетов">
              <thead><tr><th>Дата</th><th>Поставщик</th><th>Счёт</th><th>Статус</th><th className="num">Кол-во</th><th className="num">Цена</th></tr></thead>
              <tbody>
                {data.invoices.map((p, index) => (
                  <tr key={`${p.invoice_id}:${index}`}>
                    <td>{day(p.date)}</td><td className="text">{p.supplier_name || "—"}</td><td>{p.number ?? "—"}</td>
                    <td>{INVOICE_STATUS[p.status] ?? p.status}</td>
                    <td className="num">{p.quantity === null ? "—" : formatNumber(p.quantity)} {p.unit}</td>
                    <td className="num">{kopecks(p.price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

export function SupplierPricesSections({ supplierId, onOpenUpd }: { supplierId: string; onOpenUpd?: (id: string) => void }) {
  const { data, error } = usePrices<SupplierPrices>(() => purchasingPricesApi.supplier(supplierId), [supplierId]);
  if (error) return <div className="error-banner" role="alert">{error}</div>;
  if (!data) return <div className="empty">Загружаю документы…</div>;
  const { upd, invoices } = data;
  return (
    <div className="prices-sections">
      <h3>УПД и счета</h3>
      <ul className="supplier-stats">
        <li>УПД: <strong>{upd.count}</strong> на <strong>{kopecks(upd.total_gross)}</strong>{upd.last_date ? `, последний ${day(upd.last_date)}` : ""}</li>
        <li>Доля в закупках по УПД: <strong>{upd.share_pct === null ? "—" : `${formatNumber(upd.share_pct, { digits: 1 })}%`}</strong></li>
        <li>Счета Kaiten: <strong>{invoices.count}</strong> на <strong>{kopecks(invoices.total)}</strong>; не оплачено: <strong>{invoices.unpaid_count}</strong> на <strong>{kopecks(invoices.unpaid_total)}</strong></li>
      </ul>
      {upd.recent.length > 0 && (
        <div className="table-wrap history-table">
          <table className="grid" aria-label="Последние УПД">
            <thead><tr><th>Дата</th><th>УПД</th><th>Вид</th><th className="num">Сумма</th></tr></thead>
            <tbody>
              {upd.recent.map((d) => (
                <tr key={d.id}><td>{day(d.date)}</td><td><UpdNumber id={d.id} number={d.number} onOpenUpd={onOpenUpd} /></td><td>{d.kind || "—"}</td><td className="num">{kopecks(d.total_gross)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3>Чаще всего покупали</h3>
      {data.top_positions.length === 0 ? <p className="muted">Покупок по УПД нет.</p> : (
        <div className="table-wrap history-table">
          <table className="grid" aria-label="Что покупали у поставщика">
            <thead><tr><th>Позиция</th><th className="num">Закупок</th><th className="num">На сумму</th><th className="num">Последняя цена</th><th>Когда</th></tr></thead>
            <tbody>
              {data.top_positions.map((p) => (
                <tr key={p.key}><td className="text">{p.name}{p.article ? <small> {p.article}</small> : null}</td><td className="num">{p.purchases}</td><td className="num">{kopecks(p.sum)}</td><td className="num">{kopecks(p.last_price)}{p.unit ? `/${p.unit}` : ""}</td><td>{day(p.last_date)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.offers.length > 0 && (
        <>
          <h3>Что предлагает <span className="muted">{data.offers_total > data.offers.length ? `— показаны ${data.offers.length} из ${data.offers_total}` : `— ${data.offers_total}`}</span></h3>
          <div className="table-wrap history-table">
            <table className="grid" aria-label="Предложения поставщика">
              <thead><tr><th>Позиция</th><th>Его обозначение</th><th className="num">Цена</th><th>НДС</th><th>Срок</th></tr></thead>
              <tbody>
                {data.offers.map((o) => (
                  <tr key={o.item_id}><td className="text">{o.item_name}</td><td>{o.offered_part || "—"}</td><td className="num">{price4(o.unit_price4)}{NBSP}{CURRENCY_SIGNS[o.currency]}</td><td>{o.vat_included ? "с НДС" : "без НДС"}</td><td>{o.lead_time || "—"}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
