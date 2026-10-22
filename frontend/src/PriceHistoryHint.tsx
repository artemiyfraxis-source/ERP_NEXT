import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { formatDate } from "./format";
import { formatMoney } from "./ProcurementSheetColumns";

/** «Покупали ранее» : прошлые оплаченные закупки той же позиции. */
export type PastPurchase = {
  line_id: string; date: string; supplier: string | null; price_with_vat: string; unit: string | null;
  quantity: string | null; order_number: string | null;
};
export type PriceHistory = { purchases: PastPurchase[]; delta_pct: string | null };

const BATCH = 200;

/** One batch request per rendered list. */
export function usePriceHistory(ids: string[]): Record<string, PriceHistory> {
  const [history, setHistory] = useState<Record<string, PriceHistory>>({});
  const key = useMemo(() => [...ids].sort().join(","), [ids]);
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const wanted = key.split(",");
    void (async () => {
      const merged: Record<string, PriceHistory> = {};
      try {
        for (let from = 0; from < wanted.length; from += BATCH) {
          Object.assign(merged, await api<Record<string, PriceHistory>>("/api/procurement/price-history", {
            method: "POST", body: JSON.stringify({ line_ids: wanted.slice(from, from + BATCH) }),
          }));
        }
      } catch { /* the hint is optional: no history, no hint */ }
      if (alive) setHistory(merged);
    })();
    return () => { alive = false; };
  }, [key]);
  return history;
}

function purchaseText(purchase: PastPurchase): string {
  const unit = purchase.unit ? ` / ${purchase.unit}` : "";
  return [`${formatMoney(purchase.price_with_vat)}${unit}`, purchase.supplier, formatDate(purchase.date)].filter(Boolean).join(" · ");
}

/** «Покупали: 12,30 ₽ · ООО Х · 03.09.2026» + дельта (дороже — красная, дешевле — зелёная); title — до 5 закупок. */
export function PriceHint({ info }: { info: PriceHistory | undefined }) {
  if (!info || !info.purchases.length) return null;
  const last = info.purchases[0];
  const delta = info.delta_pct === null ? null : Number(info.delta_pct);
  const title = info.purchases.map((purchase) => {
    const extra = [purchase.quantity ? `${purchase.quantity} ${purchase.unit ?? ""}`.trim() : "", purchase.order_number].filter(Boolean).join(", ");
    return purchaseText(purchase) + (extra ? ` (${extra})` : "");
  }).join("\n");
  return (
    <small className="price-hint" title={title}>
      Покупали: {purchaseText(last)}
      {delta !== null && Number.isFinite(delta) && delta !== 0 && (
        <strong className={delta > 0 ? "price-hint-up" : "price-hint-down"}>
          {` ${delta > 0 ? "+" : "−"}${String(Math.abs(delta)).replace(".", ",")}%`}
        </strong>
      )}
    </small>
  );
}
