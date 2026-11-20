// «Любой свободный» станок из группы одинаковых: строка оснастки ЭТК с инв. № ОБ.NNN.X и/или артикулом «…(R)»
// называет не станок, а пул модели. Единственное место с этим правилом (экран ЭТК, печать ЭТК, печать ТД).
// Номер конкретного станка (ОБ.027.1) всегда главнее «(R)».

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
