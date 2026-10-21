/** Roller_Buyer's fixed reason lists of a decision (shared/decisions.ts); backend purchasing/decision_reasons.py. */

export type GateCode = "urgent" | "single_source" | "repeat_fresh_quote" | "other";
export type PricierCode = "lead_time" | "availability" | "moq" | "quality" | "other";

export const GATE_LABELS: Record<GateCode, string> = {
  urgent: "Срочно",
  single_source: "Единственный производитель или поставщик",
  repeat_fresh_quote: "Повторная закупка по свежему КП",
  other: "Другое",
};

export const PRICIER_LABELS: Record<PricierCode, string> = {
  lead_time: "Срок",
  availability: "Наличие",
  moq: "Минимальная партия",
  quality: "Качество или происхождение",
  other: "Другое",
};

/** The reasons part of a decision request: codes, the words for «Другое», the analog sign-off. */
export type ReasonFields = {
  gate_override_code?: GateCode; gate_override_note?: string | null;
  not_cheapest_code?: PricierCode; not_cheapest_note?: string | null;
  analog_approved_by?: string;
};
