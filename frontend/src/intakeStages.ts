import { DISPATCH_STAGES, projectDispatchStage, shipmentDispatchStage, type DispatchStage } from "./dispatchStage";
import type { DispatchShipment, IntakeRow } from "./productionIntakeApi";
import { isoDay } from "./Gantt";

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
export const permittedPack = (row: IntakeRow) => Math.min(packable(row), Math.max(0, num(row.pack_permission_remaining)));
export const canPack = (row: IntakeRow) => permittedPack(row) > 0;
/** Решение «упаковывать» по КБ: новое имя поля `pack_allowed`, старое — `pack_decided`. */
export const packAllowed = (row: IntakeRow) => Boolean(row.pack_allowed ?? row.pack_decided);

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

export type Tab = DispatchStage | "all";

/** Подпись счётчиков — осталась только у «Данных отправок» (на «Приёмке с производства» убрана 06.11). */
export const COUNTER_SCOPE = "Счётчики: КБ · Отгружено за 30 дней · Все — включая загруженную историю";

export function recentShipment(shipment: DispatchShipment, now = new Date()): boolean {
  const since = new Date(now); since.setDate(since.getDate() - SHIPPED_DAYS);
  const day = String(shipment.shipped_at ?? "").slice(0, 10);
  // Local calendar days (Moscow in the shop): toISOString() is UTC and dropped today's shipments 00:00–03:00.
  return SHIPPED.includes(shipment.status) && day >= isoDay(since) && day <= isoDay(now);
}

/** Общая единица счёта — КБ. Отправки дополняют отсутствующие КБ, но не заменяют серверные остатки приёмки. */
export function dispatchBoard(intake: IntakeRow[] = [], shipments: DispatchShipment[] = [], now = new Date()) {
  const rowsByKey = new Map(intake.map((row) => [kbKey(row), row]));
  const known = new Set(rowsByKey.keys());
  for (const shipment of shipments) for (const line of shipment.lines) {
    const key = kbKey(line);
    if (known.has(key)) continue;
    const row = rowsByKey.get(key) ?? {
      project: line.project, item_index: line.item_index, name: line.name, unit: line.unit, customer: shipment.customer,
      deadline: null, stage: "Принято складом", project_stage: "Принято складом", pack_allowed: false,
      needed: "0", launched: "0", delivered: "0", accepted: "0", packed: "0", sent: "0", remaining: "0",
      good_id: null, good_version: null, grn: line.grn ?? null, location: "", tasks: [], acts: [],
    };
    // Только уже набранное. Это строка отправок, не новый акт приёмки и не разрешение упаковки.
    if (shipment.status !== "Отменена") {
      const picked = Math.max(0, num(line.picked));
      row.accepted = row.packed = String(round2(num(row.packed) + picked));
      if (SHIPPED.includes(shipment.status)) row.sent = String(round2(num(row.sent) + picked));
    }
    rowsByKey.set(key, row);
  }
  const rows = [...rowsByKey.values()];
  const recent = shipments.filter((shipment) => recentShipment(shipment, now));
  const pieces = new Map(rows.map((row) => {
    const quantities = stagePieces(row, shipments);
    // Накопленное sent не содержит даты и не определяет вкладку последних 30 дней.
    quantities.shipped = round2(recent.flatMap((shipment) => shipment.lines).filter((line) => sameKb(line, row)).reduce((sum, line) => sum + Math.max(0, num(line.picked)), 0));
    return [kbKey(row), quantities];
  }));
  const inTab = (row: Kb, tab: Tab) => tab === "all" || (pieces.get(kbKey(row))?.[tab] ?? 0) > 0;
  const counts = Object.fromEntries([...DISPATCH_STAGES.map(([stage]) => [stage, rows.filter((row) => inTab(row, stage)).length]), ["all", rows.length]]) as Record<Tab, number>;
  return { rows, pieces, counts, inTab };
}

/** Есть ли у кладовщика работа в шаге: принять готовое, упаковать (с разрешением), вписать трек, отгрузить.
 *  Принять досрочно из «В производстве» можно (владелец 29.10: предупреждать, не запрещать), но это не работа по умолчанию. */
export function hasWork(tab: DispatchStage, row: IntakeRow, pieces: Pieces): boolean {
  if (tab === "ready") return pieces.ready > 0;
  if (tab === "production") return pieces.production > 0 && num(row.remaining) > pieces.ready;
  if (tab === "accepted") return canPack(row);
  if (tab === "packed" || tab === "to_ship") return pieces[tab] > 0;
  return false;
}

/** Вкладка по умолчанию: первая, где у кладовщика есть настоящее действие (Готово → Принято → Упаковано → На отгрузку);
 *  без него — первая непустая, «В ожидании» — последней; пусто везде — «Все». */
export const WORK_ORDER: DispatchStage[] = ["ready", "accepted", "packed", "to_ship"];
const SHOW_ORDER: DispatchStage[] = [...WORK_ORDER, "production", "shipped", "waiting"];
export function defaultTab(rows: IntakeRow[], pieces: (row: IntakeRow) => Pieces, warehouse: boolean): Tab {
  if (warehouse) for (const stage of WORK_ORDER) if (rows.some((row) => hasWork(stage, row, pieces(row)))) return stage;
  for (const stage of SHOW_ORDER) if (rows.some((row) => pieces(row)[stage] > 0)) return stage;
  return "all";
}
