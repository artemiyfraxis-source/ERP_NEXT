// «Любой свободный» станок из группы одинаковых: строка оснастки ЭТК с инв. № ОБ.NNN.X и/или артикулом «…(R)»
// называет не станок, а пул модели. Единственное место с этим правилом (экран ЭТК, печать ЭТК, печать ТД).
// Номер конкретного станка (ОБ.027.1) всегда главнее «(R)».

import type { EtkSetupUnit } from "./api";

const POOL_NUMBER = /^\s*[ОO][БB]\s*\.\s*(\d+)\s*(?:\.\s*([XХ]|\d+)\s*)?$/i;
const RETIRED_MARK = /\(R\)/gi;

export type EquipmentPool = { model: string; tail: string; pool: string };

/** Разбор инв. №: пустой / «ОБ.027.X» / голый «ОБ.100» / номер станка / прочее. */
function inventoryKind(inventory: string): { kind: "empty" | "pool" | "bare" | "unit"; pool: string } {
  const value = inventory.trim();
  if (!value) return { kind: "empty", pool: "" };
  const match = POOL_NUMBER.exec(value);
  if (!match) return { kind: "unit", pool: "" };
  const pool = `ОБ.${match[1]}.*`;
  if (!match[2]) return { kind: "bare", pool };
  return /^\d+$/.test(match[2]) ? { kind: "unit", pool: "" } : { kind: "pool", pool };
}

/** Разбор строки оснастки: null — обычный станок (показывать как есть). */
export function equipmentPool(name: string | null | undefined, inventory: string | null | undefined): EquipmentPool | null {
  const raw = String(name ?? "");
  const retired = /\(R\)/i.test(raw);
  const clean = raw.replace(RETIRED_MARK, "").replace(/[ \t]{2,}/g, " ");
  const [head, ...noteParts] = clean.split(" — ");
  const [article, ...descParts] = head.split("|");
  const model = article.trim();
  const tail = [descParts.join("|").trim(), noteParts.join(" — ").trim()].filter(Boolean).join(" · ");
  const inv = inventoryKind(String(inventory ?? ""));
  const isPool = inv.kind === "pool" || ((inv.kind === "empty" || inv.kind === "bare") && retired);
  if (!isPool || !model) return null;
  return { model, tail, pool: inv.pool };
}

/** «EW-50A — любой свободный ОБ.027.*» (описание и примечание — через « · »); null — строка обычная. */
export function poolToolLabel(name: string | null | undefined, inventory: string | null | undefined): string | null {
  const parsed = equipmentPool(name, inventory);
  if (!parsed) return null;
  const main = `${parsed.model} — любой свободный${parsed.pool ? ` ${parsed.pool}` : ""}`;
  return parsed.tail ? `${main} · ${parsed.tail}` : main;
}

/** dd.MM HH:mm in local time; "" for an unparsable value. */
function readyStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(date.getDate())}.${two(date.getMonth() + 1)} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

/** «EW-50A — ОБ.027.2 · налажен 20.11 14:30»: the pool row of a task ETK with a ready setup of a concrete unit of that
 *  pool. A unit of another pool (or none) leaves the pool text of `poolToolLabel`; non-pool rows stay null. */
export function setupUnitLabel(
  name: string | null | undefined, inventory: string | null | undefined, unit?: Pick<EtkSetupUnit, "inventory_number" | "ready_at"> | null,
): string | null {
  const parsed = equipmentPool(name, inventory);
  if (!parsed) return null;
  const match = unit ? POOL_NUMBER.exec(unit.inventory_number) : null;
  const poolNumber = /^ОБ\.(\d+)\.\*$/.exec(parsed.pool)?.[1];
  if (!unit || !match || !match[2] || !/^\d+$/.test(match[2]) || !poolNumber || Number(match[1]) !== Number(poolNumber)) return poolToolLabel(name, inventory);
  const stamp = readyStamp(unit.ready_at);
  const main = `${parsed.model} — ОБ.${match[1]}.${match[2]}${stamp ? ` · налажен ${stamp}` : ""}`;
  return parsed.tail ? `${main} · ${parsed.tail}` : main;
}

/** «EW-50A — любой свободный ОБ.027.* · налажены: ОБ.027.2, ОБ.027.4»: the pool row of a card-only ETK with the units of
 *  that pool set up on open tasks (no dates). Units of another pool are ignored; none left → the pool text of
 *  `poolToolLabel`; non-pool rows stay null. */
/** Parts of a card-ETK pool row with units set up on open tasks: the pool text, the canonical unit numbers (same pool
 *  only, deduped, sorted) and the description tail. null — not a pool row or no matching unit. */
export function poolSetupParts(
  name: string | null | undefined, inventory: string | null | undefined, units: Array<Pick<EtkSetupUnit, "inventory_number">>,
): { main: string; units: string[]; tail: string } | null {
  const parsed = equipmentPool(name, inventory);
  const poolNumber = parsed ? /^ОБ\.(\d+)\.\*$/.exec(parsed.pool)?.[1] : undefined;
  if (!parsed || !poolNumber) return null;
  const found = new Map<string, number>();
  for (const unit of units) {
    const match = POOL_NUMBER.exec(unit.inventory_number);
    if (!match || !match[2] || !/^\d+$/.test(match[2]) || Number(match[1]) !== Number(poolNumber)) continue;
    // Canonical label from the pool itself: «ОБ.27.2», «OB.027.02» and «ОБ.027.2» are one unit.
    found.set(parsed.pool.replace("*", String(Number(match[2]))), Number(match[2]));
  }
  if (!found.size) return null;
  const list = [...found.entries()].sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0])).map(([label]) => label);
  return { main: `${parsed.model} — любой свободный ${parsed.pool}`, units: list, tail: parsed.tail };
}

/** «EW-50A — любой свободный ОБ.027.* · налажены: ОБ.027.2, ОБ.027.4 · <tail>»; no matching unit → `poolToolLabel`. */
export function poolSetupLabel(
  name: string | null | undefined, inventory: string | null | undefined, units: Array<Pick<EtkSetupUnit, "inventory_number">>,
): string | null {
  const parts = poolSetupParts(name, inventory, units);
  if (!parts) return poolToolLabel(name, inventory);
  const main = `${parts.main} · налажены: ${parts.units.join(", ")}`;
  return parts.tail ? `${main} · ${parts.tail}` : main;
}
