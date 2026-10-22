/* ТК / способ отгрузки (владелец 22.10.2026): выбор из фиксированного списка, условия доставки выводятся из него.
   В базе `carrier` остаётся свободной строкой: старое значение вне списка показывается и сохраняется как есть. */
export const PICKUP_CARRIER = "Самовывоз";
export const CARRIERS = [PICKUP_CARRIER, "СДЭК", "Деловые линии", "Почта России", "Курьер (наш)", "Яндекс Доставка"] as const;

export type DeliveryTerms = "САМОВЫВОЗ" | "ДОСТАВКА ТК";
const TERMS_TEXT: Record<DeliveryTerms, string> = { "САМОВЫВОЗ": "Самовывоз", "ДОСТАВКА ТК": "Доставка ТК" };
export const termsText = (terms: string | null | undefined) => (terms ? TERMS_TEXT[terms as DeliveryTerms] ?? terms : "");

/** Условия по способу: самовывоз — САМОВЫВОЗ, любой другой из списка — ДОСТАВКА ТК; пусто и старый текст — не выводятся. */
export function termsFor(carrier: string): DeliveryTerms | null {
  const value = carrier.trim();
  if (value === PICKUP_CARRIER) return "САМОВЫВОЗ";
  return (CARRIERS as readonly string[]).includes(value) ? "ДОСТАВКА ТК" : null;
}
/** Самовывоз — только когда способ выбран явно: старые карточки с одними условиями САМОВЫВОЗ адрес не теряют из вида. */
export const isPickup = (carrier: string) => carrier.trim() === PICKUP_CARRIER;

/** Выпадашка ТК. Значение вне списка (старый свободный текст) — отдельная строка «… (прежнее)», выбранная как есть. */
export function CarrierSelect({ value, onChange, disabled, label, empty = "не выбрано" }: {
  value: string; onChange: (value: string) => void; disabled?: boolean; label: string; empty?: string;
}) {
  const current = value.trim();
  const legacy = current && !(CARRIERS as readonly string[]).includes(current) ? current : "";
  return <select aria-label={label} value={current} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
    <option value="">{empty}</option>
    {CARRIERS.map((name) => <option key={name} value={name}>{name}</option>)}
    {legacy && <option value={legacy}>{legacy} (прежнее)</option>}
  </select>;
}
