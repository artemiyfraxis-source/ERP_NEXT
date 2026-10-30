import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, type DispatchStage } from "./dispatchStage";
import type { DispatchShipment, IntakeRow } from "./productionIntakeApi";

/* Шаги КБ на «Приёмке с производства» (владелец 29.10.2026): штуки одной КБ могут быть сразу в нескольких шагах —
   часть в производстве, часть принята, часть упакована, — и строка КБ видна в каждой вкладке, где у неё есть штуки. */

export const num = (value: string | number | null | undefined) => {
  const parsed = Number(String(value ?? "").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : 0;
};
export const round2 = (value: number) => Math.round(value * 100) / 100;
const norm = (text: string) => text.trim().toUpperCase();
const RANK: Record<DispatchStage, number> = Object.fromEntries(DISPATCH_STAGES.map(([stage], index) => [stage, index])) as Record<DispatchStage, number>;
const SHIPPED = ["Отгружена", "Закрыта"];
/** Сколько дней уехавшие КБ (которых в приёмке уже нет) ещё видны во вкладке «Отгружено»; раньше — в «Журнале». */
export const SHIPPED_DAYS = 30;

type Kb = Pick<IntakeRow, "project" | "item_index">;
export const kbKey = (row: Kb) => `${norm(row.project)}\u0000${norm(row.item_index)}`;
export const sameKb = (a: Kb, b: Kb) => kbKey(a) === kbKey(b);
export const ofProject = (shipment: DispatchShipment, project: string) => shipment.lines.some((line) => norm(line.project) === norm(project));
export const ofKb = (shipment: DispatchShipment, row: Kb) => shipment.lines.some((line) => sameKb(line, row));

/** Принято и ещё не набрано в отправки — столько можно упаковать. */
export const packable = (row: IntakeRow) => round2(Math.max(num(row.accepted) - num(row.packed), 0));
/** Упаковывать можно после решения менеджера «упаковывать» (владелец 29.10). */
export const canPack = (row: IntakeRow) => row.pack_decided === true && packable(row) > 0;

export type Pieces = Record<DispatchStage, number>;
const zero = (): Pieces => ({ waiting: 0, production: 0, ready: 0, accepted: 0, packed: 0, to_ship: 0, shipped: 0 });

/** Штуки КБ по шагам. Непринятое: сданное цехом (или всё, что можно принять, когда КБ уже «Готово» / ОТК и дальше) — «Готово»,
 *  остальное — «В производстве» или «В ожидании» по этапу КБ. Принятое — по отправкам: набранное в заявку ещё на складе
 *  («Принято»), упакованное — «Упаковано» или «На отгрузку», уехавшее — «Отгружено». */
export function stagePieces(row: IntakeRow, shipments: DispatchShipment[]): Pieces {
  const pieces = zero();
  const own = projectDispatchStage(row.stage || row.project_stage);
  const remaining = num(row.remaining);
  const unaccepted = num(row.needed) > 0 ? Math.max(num(row.needed) - num(row.accepted), 0) : remaining;
  pieces.ready = RANK[own] >= RANK.ready ? remaining : Math.min(remaining, Math.max(num(row.delivered) - num(row.accepted), 0));
  const rest = Math.max(unaccepted - pieces.ready, 0);
  if (own === "waiting") pieces.waiting = rest; else pieces.production = rest;
  pieces.accepted = packable(row);
  let gone = 0;
  for (const shipment of shipments) {
    const stage = shipmentDispatchStage(shipment, "Принято складом");
    if (!stage) continue;
    for (const line of shipment.lines) {
      if (!sameKb(line, row) || !(num(line.picked) > 0)) continue;
      if (stage === "shipped") gone += num(line.picked);
      else pieces[RANK[stage] < RANK.packed ? "accepted" : stage] += num(line.picked);
    }
  }
  pieces.shipped = Math.max(num(row.sent), gone);
  for (const [stage] of DISPATCH_STAGES) pieces[stage] = round2(pieces[stage]);
  return pieces;
}

/** КБ, которые уехали целиком (сервер их из приёмки убрал), — строкой из отправок за последние `SHIPPED_DAYS` дней. */
export function shippedOnlyRows(rows: IntakeRow[], shipments: DispatchShipment[], now = new Date()): IntakeRow[] {
  const since = new Date(now); since.setDate(since.getDate() - SHIPPED_DAYS);
  const from = since.toISOString().slice(0, 10);
  const known = new Set(rows.map(kbKey));
  const extra = new Map<string, IntakeRow>();
  for (const shipment of shipments) {
    if (!SHIPPED.includes(shipment.status) || String(shipment.shipped_at ?? "") < from) continue;
    for (const line of shipment.lines) {
      const key = kbKey(line);
      if (known.has(key) || !(num(line.picked) > 0)) continue;
      const row = extra.get(key) ?? {
        project: line.project, customer: shipment.customer, deadline: null, item_index: line.item_index, name: line.name, unit: line.unit,
        stage: "Отгружено", project_stage: "Отгружено", pack_decided: false, needed: "0", launched: "0", delivered: "0", accepted: "0",
        packed: "0", sent: "0", remaining: "0", good_id: null, good_version: null, grn: line.grn ?? null, location: "", tasks: [], acts: [],
      };
      const add = (field: "needed" | "accepted" | "packed" | "sent", value: string) => String(round2(num(row[field]) + num(value)));
      extra.set(key, { ...row, needed: add("needed", line.requested), accepted: add("accepted", line.picked), packed: add("packed", line.picked), sent: add("sent", line.picked) });
    }
  }
  return [...extra.values()];
}

export type Tab = DispatchStage | "all";

/** Есть ли у кладовщика работа в шаге: принять, упаковать (после решения), вписать трек, отгрузить. */
export function hasWork(tab: DispatchStage, row: IntakeRow, pieces: Pieces): boolean {
  if (tab === "ready") return pieces.ready > 0;
  // В ожидании и в производстве принимать есть что, когда можно принять больше, чем сдал цех («всё, что в работе», 28.10).
  if (tab === "waiting" || tab === "production") return pieces[tab] > 0 && num(row.remaining) > pieces.ready;
  if (tab === "accepted") return canPack(row);
  if (tab === "packed" || tab === "to_ship") return pieces[tab] > 0;
  return false;
}
