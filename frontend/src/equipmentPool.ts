// «Любой свободный» станок из группы одинаковых: строка оснастки ЭТК с инв. № ОБ.NNN.X и/или артикулом «…(R)»
// называет не станок, а пул модели. Единственное место с этим правилом (экран ЭТК, печать ЭТК, печать ТД).

const POOL_INVENTORY = /^(ОБ|OB)\.(\d+)\.X$/i;
const POOL_ARTICLE = /\(R\)\s*$/i;

export type EquipmentPool = { model: string; tail: string; pool: string };

function splitName(name: string): { head: string; tail: string } {
  const [head, ...rest] = name.split("|");
  return { head: head.trim(), tail: rest.join("|").trim() };
}

/** Пул ОБ.027.X → «ОБ.027.*»; обычный инв. № и пустое значение — null. */
export function poolInventory(inventory: string | null | undefined): string | null {
  const match = POOL_INVENTORY.exec(String(inventory ?? "").trim());
  return match ? `ОБ.${match[2]}.*` : null;
}

/** Разбор строки оснастки: null — обычный станок (показывать как есть). */
export function equipmentPool(name: string | null | undefined, inventory: string | null | undefined): EquipmentPool | null {
  const { head, tail } = splitName(String(name ?? ""));
  const pool = poolInventory(inventory);
  const byArticle = POOL_ARTICLE.test(head);
  if (!pool && !byArticle) return null;
  const model = head.replace(POOL_ARTICLE, "").trim();
  if (!model && !pool) return null;
  return { model, tail, pool: pool ?? "" };
}

/** «EW-50A — любой свободный ОБ.027.*» (хвост артикула после «|» — через « · »); null — строка обычная. */
export function poolToolLabel(name: string | null | undefined, inventory: string | null | undefined): string | null {
  const parsed = equipmentPool(name, inventory);
  if (!parsed) return null;
  const main = [parsed.model, `любой свободный${parsed.pool ? ` ${parsed.pool}` : ""}`].filter(Boolean).join(" — ");
  return parsed.tail ? `${main} · ${parsed.tail}` : main;
}
