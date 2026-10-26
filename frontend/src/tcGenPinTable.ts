/*
 * Таблица «Распиновка и сростки» «Сложной сборки» (владелец 25.10: «настройка распиновки сростков — не всё удобно настраивается»):
 * строка — одна жила (провод кабельной строки), в ней оба конца: куда приходит конец А и конец В. Конец — контакт разъёма
 * своей стороны, сросток (первый класс: выбрать тот же «С1» — значит соединить концы), хвост (свободный конец) или «не задан».
 * Состояние формы то же (контакты разъёмов `pins`, `splices`, `tails`) — таблица лишь второй способ править его,
 * черновики и собранные карты открываются как раньше. Логика правок — pinoutCheck.ts и tcGenSplices.ts.
 */
import { unassignPin, type PinoutEnd, type PinUnit } from "./pinoutCheck";
import { assignPinJoined, joinSplice, leaveJoin, setTail, spliceLabel } from "./tcGenSplices";
import { endJoined, type GenData, type GenState, type Side, type WireEnd } from "./tcGenLogic";

/** Значение выбора конца: "" — не задан, "tail", "new" (новый сросток), "sp:<id>", "pin:<разъём>:<контакт>", "pin:<разъём>:auto". */
export type EndValue = string;

export type PinCell = {
  side: Side;
  /** Выбранное значение для поля выбора. */
  value: EndValue;
  /** Куда ещё идёт этот конец (ответвление на второй разъём той же стороны): «XS2·3». Только показ. */
  extra: string[];
  /** Короткий текст итога: «XS1·1», «С1», «хвост», «—». */
  text: string;
};
export type PinRow = {
  key: string; idx: number; unit: number; num: string; name: string; colorName: string; color: string | null; stripe: string | null;
  /** Длина, мм (0 — не задана); правится в строке первой жилы кабеля. */
  length: number; first: boolean;
  cells: Record<Side, PinCell>;
  /** Что не так со строкой — по-русски, рядом со строкой. */
  problems: string[];
};

export type PinConn = { id: string; key: string; side: Side; designation: string; conn: string; count: number };

const sameEnd = (a: WireEnd, b: WireEnd) => a.idx === b.idx && a.unit === b.unit && a.side === b.side;

/** Разъёмы таблицы по сторонам: позиционное обозначение («XS1») или ключ («А1»). `counts` — контактов в корпусе (0/нет — не известно). */
export function pinConns(ends: PinoutEnd[], S: Pick<GenState, "endpoints">, counts: Record<string, number | null | undefined>): PinConn[] {
  return ends.map((end) => {
    const ep = S.endpoints.find((e) => e.id === end.id);
    const used = Math.max(0, ...end.wires.flatMap((w) => w.cores.filter((p): p is number => p != null)));
    return { id: end.id, key: end.key, side: end.side, designation: (ep?.label || "").trim(), conn: end.conn, count: counts[end.id] || used };
  });
}

export const connName = (c: Pick<PinConn, "key" | "designation">) => c.designation || c.key;

/** Строки таблицы — все жилы (экраны не берём: их контакт по желанию). */
export function pinRows(conns: PinConn[], units: PinUnit[], S: Pick<GenState, "splices" | "tails">, pinoutEnds: PinoutEnd[]): PinRow[] {
  const seen = new Set<number>();
  const dupBy = new Map<string, number[]>(pinoutEnds.map((e) => [e.id, e.dups]));
  return units.filter((u) => !u.shield).map((u): PinRow => {
    const first = !seen.has(u.idx);
    seen.add(u.idx);
    const cells = {} as Record<Side, PinCell>;
    const problems: string[] = [];
    (["A", "B"] as const).forEach((side) => {
      const sideConns = conns.filter((c) => c.side === side);
      const pins = sideConns.filter((c) => u.at[c.id] != null);
      const end: WireEnd = { idx: u.idx, unit: u.unit, side };
      const spliceId = (S.splices || []).find((sp) => sp.ends.some((e) => sameEnd(e, end)))?.id;
      const joined = endJoined(S, u.idx, u.unit, side);
      let cell: PinCell;
      if (pins.length) {
        const [head, ...rest] = pins;
        cell = { side, value: `pin:${head.id}:${u.at[head.id]}`, extra: rest.map((c) => `${connName(c)}·${u.at[c.id]}`), text: `${connName(head)}·${u.at[head.id]}` };
        pins.forEach((c) => { if ((dupBy.get(c.id) || []).includes(u.at[c.id]!)) problems.push(`Контакт ${u.at[c.id]} разъёма ${connName(c)} занят и другим проводом`); });
      } else if (spliceId) cell = { side, value: `sp:${spliceId}`, extra: [], text: spliceLabel(S, spliceId) };
      else if (joined === "tail") cell = { side, value: "tail", extra: [], text: "хвост" };
      else {
        cell = { side, value: "", extra: [], text: "—" };
        // Сторона с разъёмами ждёт контакта; сторона без разъёмов — свободный конец, это не ошибка.
        if (sideConns.length) problems.push(`Конец ${side === "A" ? "А" : "В"} не задан: выберите контакт, сросток или свободный конец`);
      }
      cells[side] = cell;
    });
    if (cells.A.value.startsWith("sp:") && cells.A.value === cells.B.value) problems.push("Оба конца провода в одном сростке");
    if (!cells.A.value && !cells.B.value && !problems.length) problems.push("Провод никуда не идёт");
    return { key: u.key, idx: u.idx, unit: u.unit, num: u.num, name: u.name, colorName: u.colorName, color: u.color, stripe: u.stripe, length: u.length, first, cells, problems };
  });
}

/** Занятые контакты разъёма: контакт → номера жил (для подсказки «занят: № 3» в списке). */
export function occupiedPins(conn: PinConn, units: PinUnit[]): Map<number, string[]> {
  const map = new Map<number, string[]>();
  units.filter((u) => !u.shield && u.at[conn.id] != null).forEach((u) => map.set(u.at[conn.id]!, [...(map.get(u.at[conn.id]!) || []), u.num]));
  return map;
}

/** Первый свободный контакт разъёма; корпус без известного числа контактов — следующий за занятыми. null — все заняты. */
export function firstFreePin(conn: PinConn, units: PinUnit[], doubled = false): number | null {
  const busy = occupiedPins(conn, units);
  const limit = conn.count > 0 ? conn.count : Math.max(0, ...busy.keys()) + 1;
  for (let pin = 1; pin <= limit; pin += 1) if (!busy.has(pin) || (doubled && (busy.get(pin) || []).length < 2)) return pin;
  return null;
}

/** Поставить конец жилы туда, что выбрано в таблице; всё прежнее у этого конца (контакт, сросток, хвост) снимается. */
export function applyEnd(D: GenData, S: GenState, conns: PinConn[], units: PinUnit[], row: Pick<PinRow, "idx" | "unit">, side: Side, value: EndValue): GenState {
  const end: WireEnd = { idx: row.idx, unit: row.unit, side };
  const unit = units.find((u) => u.idx === row.idx && u.unit === row.unit);
  const sideConns = conns.filter((c) => c.side === side);
  // Контакты этой жилы на разъёмах стороны, кроме выбранного, снимаются: конец стоит в одном месте.
  const clearPins = (state: GenState, keep: string | null) => sideConns.reduce((acc, c) => (c.id !== keep && unit?.at[c.id] != null ? unassignPin(D, acc, c.id, row.idx, row.unit) : acc), state);
  if (value === "") return clearPins(leaveJoin(D, S, end), null);
  if (value === "tail") return clearPins(setTail(D, S, end), null);
  if (value === "new") return clearPins(joinSplice(D, S, end, "new"), null);
  if (value.startsWith("sp:")) return clearPins(joinSplice(D, S, end, value.slice(3)), null);
  const [, epId, pinText] = value.split(":");
  const conn = sideConns.find((c) => c.id === epId);
  if (!conn) return S;
  const pin = pinText === "auto" ? firstFreePin(conn, units) : Number(pinText);
  if (!pin) return S;
  return clearPins(assignPinJoined(D, S, epId, row.idx, row.unit, pin), epId);
}

/** Сростки таблицы: «С1», его концы (подписи «№ 3 · конец В»), число концов, замечание. */
export type SpliceLine = { id: string; label: string; /** Своё название (пусто — «С1» по порядку). */ name: string; ends: Array<{ end: WireEnd; num: string; text: string; where: string }>; problem: string };
export function spliceLines(S: Pick<GenState, "splices">, units: PinUnit[], conns: PinConn[]): SpliceLine[] {
  return (S.splices || []).map((sp) => {
    const ends = sp.ends.map((end) => {
      const u = units.find((x) => x.idx === end.idx && x.unit === end.unit);
      const other = (["A", "B"] as const).filter((s) => s !== end.side)[0];
      const to = u ? conns.filter((c) => c.side === other && u.at[c.id] != null).map((c) => `${connName(c)}·${u.at[c.id]}`) : [];
      return { end, num: u?.num ?? "?", text: `${u?.name || "провод"}${u?.colorName ? ` · ${u.colorName}` : ""}`, where: to.length ? `→ ${to.join(", ")}` : "" };
    });
    return { id: sp.id, label: spliceLabel(S, sp.id), name: sp.name ?? "", ends, problem: sp.ends.length < 2 ? "В сростке меньше двух концов" : "" };
  });
}

/** Сводка над таблицей: «9 жил · сростков 2 · хвостов 2 · замечаний 1». */
export function pinSummary(rows: PinRow[], S: Pick<GenState, "splices" | "tails">): string {
  const bad = rows.filter((r) => r.problems.length).length;
  const tails = (S.tails || []).length;
  return `${rows.length} жил · сростков ${(S.splices || []).length} · свободных концов ${tails}${bad ? ` · замечаний ${bad}` : ""}`;
}
