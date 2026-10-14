/*
 * Свивка группами: пара — частный случай группы, в группе два провода и больше (три провода питания, четыре сигнальных
 * и т. п.). Строка «Пары и группы для свивки» хранит провода по позиции в списке кабелей формы: `a`, `b` — первые два
 * (как в старых черновиках и сборках), `more` — третий и дальше. В сборку (`build_config.twist.pairs`) группа уходит
 * списком позиций — сервер читает его как есть («1) красный + чёрный + белый»).
 *
 * Модуль без зависимостей: его читают и форма (tcGenLogic), и построение чертежа (generatorToHarness, configToHarness).
 */

/** Строка свивки: провода по позиции в списке кабелей формы ("" — не выбран). */
export type TwistRow = { a: string; b: string; more?: string[] };

/** Провода строки по порядку, с невыбранными. */
export const rowMembers = (row: TwistRow): string[] => [row.a, row.b, ...(row.more ?? [])];

const chosen = (row: TwistRow, count: number) =>
  rowMembers(row).filter((value) => value !== "").map(Number).filter((pos) => Number.isInteger(pos) && pos >= 0 && pos < count);

/** Группы свивки по строкам: выбранные провода без повторов (в пределах `count` кабелей); меньше двух — не группа. */
export function twistGroupsOf(rows: readonly TwistRow[], count = Number.MAX_SAFE_INTEGER): number[][] {
  return rows.map((row) => [...new Set(chosen(row, count))]).filter((group) => group.length >= 2);
}

// ── Правка строк ─────────────────────────────────────────────────────────────────────────────────────────────────

/** k-й провод строки: 0 и 1 — `a`, `b`; дальше — `more`. Пустое значение у третьего и дальше убирает его из строки. */
export function setTwistMember(rows: readonly TwistRow[], index: number, k: number, value: string): TwistRow[] {
  return rows.map((row, i) => {
    if (i !== index) return row;
    if (k === 0) return { ...row, a: value };
    if (k === 1) return { ...row, b: value };
    const more = [...(row.more ?? [])];
    if (value === "") more.splice(k - 2, 1); else more[k - 2] = value;
    return more.length ? { ...row, more } : { a: row.a, b: row.b };
  });
}

/** «+ провод»: ещё одно место в строке — пара становится тройкой. */
export function addTwistMember(rows: readonly TwistRow[], index: number): TwistRow[] {
  return rows.map((row, i) => (i === index ? { ...row, more: [...(row.more ?? []), ""] } : row));
}

// ── Проверки ─────────────────────────────────────────────────────────────────────────────────────────────────────

export type TwistProblems = { error: string; warnings: string[] };

/**
 * Проверка строк свивки перед сборкой. Стоп — провод в двух группах (свить один провод в две группы нельзя).
 * Спросить — строка с одним проводом (в свивку не войдёт), провод дважды в строке, разное число проводов у кабелей
 * группы (свиваются по наименьшему).
 * `names[pos]` — наименование кабеля, `units[pos]` — проводов (жил) в нём.
 */
export function twistGroupProblems(rows: readonly TwistRow[], names: readonly string[], units: readonly number[]): TwistProblems {
  const warnings: string[] = [];
  const name = (pos: number) => `«${names[pos] || `кабель ${pos + 1}`}»`;
  const where = new Map<number, number[]>();
  rows.forEach((row, i) => {
    const picked = chosen(row, names.length);
    const unique = [...new Set(picked)];
    const label = `Свивка ${i + 1}`;
    if (picked.length !== unique.length) {
      const twice = unique.filter((pos) => picked.indexOf(pos) !== picked.lastIndexOf(pos));
      warnings.push(`${label}: ${twice.map(name).join(", ")} выбран дважды.`);
    }
    if (unique.length === 1) warnings.push(`${label}: выбран один провод — в свивку не войдёт.`);
    if (unique.length < 2) return;
    unique.forEach((pos) => where.set(pos, [...(where.get(pos) ?? []), i + 1]));
    const counts = unique.map((pos) => Math.max(1, units[pos] || 1));
    if (new Set(counts).size > 1) {
      warnings.push(`${label}: у кабелей разное число проводов (${counts.join(", ")}) — свиваются ${Math.min(...counts)}.`);
    }
  });
  const twice = [...where].filter(([, list]) => list.length > 1);
  const error = twice.length
    ? `Свивка: ${twice.map(([pos, list]) => `${name(pos)} — в свивках ${list.join(" и ")}`).join("; ")}. Провод свивается только в одной группе.`
    : "";
  return { error, warnings };
}

// ── Чертёж ───────────────────────────────────────────────────────────────────────────────────────────────────────

/** Свивка на чертеже: группы — ключи проводов чертежа; две — свитая пара редактора, три и больше — группа. */
export type TwistPlan = { pairs: Array<[string, string]>; groups: string[][] };

/**
 * Провода чертежа по группам свивки. `unitsOf(pos)` — ключи проводов кабеля на позиции `pos` (у кабеля из N проводов —
 * N ключей). «Парами и группами»: i-й провод каждого кабеля группы — в i-ю группу (по наименьшему числу проводов);
 * «Все вместе» — одна группа из всех проводов выбранных кабелей.
 */
export function planTwist(mode: "pairs" | "all", groups: readonly (readonly number[])[], unitsOf: (pos: number) => string[]): TwistPlan {
  const keyed: string[][] = mode === "all"
    ? [groups.flat().flatMap(unitsOf)]
    : groups.flatMap((group) => {
      const lists = group.map(unitsOf);
      const n = Math.min(...lists.map((list) => list.length));
      return Array.from({ length: Number.isFinite(n) ? n : 0 }, (_, i) => lists.map((list) => list[i]));
    });
  const pairs: Array<[string, string]> = [];
  const big: string[][] = [];
  keyed.forEach((keys) => {
    if (keys.length === 2) pairs.push([keys[0], keys[1]]);
    else if (keys.length > 2) big.push(keys);
  });
  return { pairs, groups: big };
}
