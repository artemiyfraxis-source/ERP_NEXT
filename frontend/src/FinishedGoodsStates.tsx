import type { FinishedGood, FinishedGoodsMove, GoodsStateKey } from "./api";
import { formatMoment } from "./format";

/* Сквозной статус готовой продукции (владелец 12.10.2026): штуки КБ идут В производстве → Принято складом →
   Упаковано → Отгружено. Один вид на «Отгрузке», «Складе» и в карточке проекта: полоска долей и подписи с точкой
   того же цвета — подпись и есть легенда. */

export const GOODS_STATES: ReadonlyArray<{ key: GoodsStateKey; label: string; short: string }> = [
  { key: "production", label: "В производстве", short: "в пр-ве" },
  { key: "accepted", label: "Принято складом", short: "принято" },
  { key: "packed", label: "Упаковано", short: "упак." },
  { key: "shipped", label: "Отгружено", short: "отгр." },
];

export type GoodsPieces = Record<GoodsStateKey, number>;

const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
const round = (value: number) => Math.round(value * 1000) / 1000;

/** Штуки строки ГП по состояниям — те же формулы, что у регистра на сервере (GoodsRegister): принято всего, упаковано
 *  всего (набрано в отправки), отгружено всего. */
export function goodsPieces(good: Pick<FinishedGood, "ordered" | "accepted" | "shipped" | "sent">): GoodsPieces {
  const ordered = num(good.ordered); const accepted = num(good.accepted); const packed = num(good.shipped); const sent = num(good.sent);
  return {
    production: round(Math.max(ordered - accepted, 0)), accepted: round(Math.max(accepted - packed, 0)),
    packed: round(Math.max(packed - sent, 0)), shipped: round(sent),
  };
}

/** Штуки из ответа сервера («pieces» сквозного статуса проекта). */
export function piecesOf(pieces: Partial<Record<GoodsStateKey, string>>): GoodsPieces {
  return { production: num(pieces.production), accepted: num(pieces.accepted), packed: num(pieces.packed), shipped: num(pieces.shipped) };
}

export function sumPieces(list: GoodsPieces[]): GoodsPieces {
  const total: GoodsPieces = { production: 0, accepted: 0, packed: 0, shipped: 0 };
  for (const item of list) for (const state of GOODS_STATES) total[state.key] = round(total[state.key] + item[state.key]);
  return total;
}

/** «В производстве 20 шт, принято складом 30 шт, …» — для чтения с экрана и выгрузки. */
export function piecesText(pieces: GoodsPieces, unit = "шт") {
  return GOODS_STATES.map((state) => `${state.label.toLowerCase()} ${pieces[state.key]} ${unit}`).join(", ");
}

/** Последние ходы журнала КБ одной строкой на ход — подсказка к полоске. */
export function movesHint(moves: FinishedGoodsMove[], limit = 5) {
  if (!moves.length) return "Ходов по КБ ещё не было";
  const lines = moves.slice(0, limit).map((move) => {
    const where = move.shipment_number ? ` · ${move.shipment_number}` : "";
    const why = move.reason ? ` · ${move.reason}` : "";
    return `${formatMoment(move.occurred_at)} ${move.kind_label}: ${move.quantity} шт${where} — ${move.actor || "—"}${why}`;
  });
  return ["Последние ходы:", ...lines, ...(moves.length > limit ? [`…ещё ${moves.length - limit}`] : [])].join("\n");
}

/** Полоска долей и подписи «в пр-ве 20 · принято 30 · упак. 0 · отгр. 0». */
export function GoodsStateBar({ pieces, unit = "шт", title, bar = true }: { pieces: GoodsPieces; unit?: string; title?: string; bar?: boolean }) {
  const total = GOODS_STATES.reduce((sum, state) => sum + pieces[state.key], 0);
  return <span className="goods-states" title={title} aria-label={piecesText(pieces, unit)} role="img">
    {bar && <span className="goods-bar" aria-hidden="true">
      {total > 0 ? GOODS_STATES.filter((state) => pieces[state.key] > 0).map((state) =>
        <span key={state.key} className={`goods-seg is-${state.key}`} style={{ flexGrow: pieces[state.key] }} />)
        : <span className="goods-seg is-empty" />}
    </span>}
    <span className="goods-counts" aria-hidden="true">{GOODS_STATES.map((state) =>
      <span key={state.key} className={`goods-count is-${state.key}${pieces[state.key] > 0 ? "" : " is-zero"}`}>
        <i className="goods-dot" />{state.short} <b>{pieces[state.key]}</b>
      </span>)}</span>
  </span>;
}
