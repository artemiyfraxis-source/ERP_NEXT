/*
 * «Маркировка проводов: откуда → куда» «Сложной сборки» (владелец 26.10, 708К.5 п.8: «схема печати маркировок на каждом
 * проводе — 2 шт, в начале и в конце, пример X1.1 to X2.2 и X2.2 to X1.1»). Тексты берутся из распиновки: конец А
 * «<разъём А>.<контакт А> to <разъём В>.<контакт В>», конец В — наоборот. Здесь — чистая часть (тексты, подбор трубки по Ø
 * провода, подпись операции); конец провода по распиновке находит makeGen (wireMarkRows_), операции строит он же.
 */

/** Настройка формы; нет поля (черновики и карты до 26.10) — маркировки проводов нет. */
export type WireMarkCfg = {
  enabled: boolean;
  /** Трубка: позиция состава или БД.МАТ (по наименованию); пусто — по Ø изоляции проводов. */
  mat: string;
  /** Длина метки, мм. */
  len: string;
  /** Расстояние метки от конца провода, мм. */
  dist: string;
  /** Правки надписей: «<провод>.<жила>:A» / «:B» → текст; пусто — надпись по распиновке. */
  texts?: Record<string, string>;
};

export const WIRE_MARK_LEN_MM = "15";
export const WIRE_MARK_DIST_MM = "20";

/** Провод, чьи концы известны: у каждого конца — разъём и контакт либо сросток; свободный конец и «не задан» — без маркировки. */
export type WireMarkRow = {
  key: string; idx: number; unit: number; num: string;
  /** Конец А и конец В как читает монтажник: «X1.1», «С1». */
  from: string; to: string;
  /** Надписи: по распиновке или правленые. */
  textA: string; textB: string;
  autoA: string; autoB: string;
};
/** Провод, который в маркировку не попал, и почему («№ 3: свободный конец В»). */
export type WireMarkSkip = { key: string; num: string; reason: string };

export const markTextOf = (from: string, to: string) => `${from} to ${to}`;
export const wireMarkKey = (idx: number, unit: number) => `${idx}.${unit}`;

export function wireMarkRow(key: string, idx: number, unit: number, num: string, from: string, to: string, texts: Record<string, string> | undefined): WireMarkRow {
  const autoA = markTextOf(from, to), autoB = markTextOf(to, from);
  return { key, idx, unit, num, from, to, autoA, autoB, textA: (texts?.[`${key}:A`] ?? "").trim() || autoA, textB: (texts?.[`${key}:B`] ?? "").trim() || autoB };
}

/** Все надписи на печать: по два на провод — конец А, затем конец В. */
export const wireMarkTexts = (rows: WireMarkRow[]): string[] => rows.flatMap((row) => [row.textA, row.textB]);

const TUBE_SIZE = /(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)\s*мм/i;
/** Диаметр трубки до усадки, мм, из «4/2мм»; нет размера — null. */
export function tubeBeforeShrink(name: string): number | null {
  const m = TUBE_SIZE.exec(name);
  return m ? Number(m[1].replace(",", ".")) : null;
}

/** Трубка под Ø изоляции: меньшая из тех, чей диаметр до усадки не меньше Ø провода с запасом 0,5 мм (нет такой — самая
 *  большая); Ø неизвестен или размера в наименованиях нет — первая из списка. Пустой список — "". */
export function pickWireMarkTube(names: string[], dia: number): string {
  if (!names.length) return "";
  const sized = names.map((name) => ({ name, d: tubeBeforeShrink(name) })).filter((x): x is { name: string; d: number } => x.d !== null).sort((a, b) => a.d - b.d);
  if (!sized.length || !(dia > 0)) return names[0];
  return (sized.find((x) => x.d >= dia + 0.5) || sized[sized.length - 1]).name;
}
