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
  /** Чем метить (владелец 20.11): нет поля — трубка, как до 20.11. Трубка и лента — печать на маркираторе, лазер — ОБ.019. */
  kind?: WireMarkKind;
  /** Ø трубки на маркираторе, мм (WIRE_MARK_TUBE_SIZES_MM); пусто — трубка по Ø провода, как до 20.11. */
  tubeMm?: string;
  /** Ширина ленты, мм (WIRE_MARK_TAPE_WIDTHS_MM); пусто — первая из списка. */
  tapeMm?: string;
  /** Лазер ОБ.019: режим, надпись по всей длине (пусто — надпись конца А), отступы от торцов и шаг, мм (пусто — по умолчанию). */
  laser?: { mode: LaserMode; middle?: string; frontMm?: string; backMm?: string; stepMm?: string };
};

/** Носитель метки на проводе (владелец 20.11): трубка и лента — печать на маркираторе, лазер — ОБ.019 при резке. */
export type WireMarkKind = "tube" | "tape" | "laser";
export const WIRE_MARK_KINDS: ReadonlyArray<readonly ["none" | WireMarkKind, string]> = [["none", "нет"], ["tube", "трубка"], ["tape", "лента"], ["laser", "лазер ОБ.019"]];
/** Трубки и ленты маркиратора (владелец 20.11): «лента 6, 9, 12; трубки 2,4 / 3,2 / 4,8 / 6 / 8». */
export const WIRE_MARK_TUBE_SIZES_MM: readonly number[] = [2.4, 3.2, 4.8, 6, 8];
export const WIRE_MARK_TAPE_WIDTHS_MM: readonly number[] = [6, 9, 12];
/** Лазер ОБ.019: «По краям» — надпись у каждого торца, «По всей длине» — одна надпись с шагом. */
export type LaserMode = "ends" | "full";
export const LASER_MODES: ReadonlyArray<readonly [LaserMode, string]> = [["ends", "По краям"], ["full", "По всей длине"]];
export const LASER_END_MM = 20;
export const LASER_STEP_MM = 100;
/** Станок печатает латиницей (транслитерирует карта станка) и не больше 30 знаков. */
export const LASER_TEXT_MAX = 30;

/** Поле `laserMark` операции резки (cutWire / cutWireStrip) — его читает карта станка ОБ.019. */
export type LaserMark = { mode: LaserMode; front: string; back: string; middle: string; frontMm: number; backMm: number; stepMm: number };
/** Поле `markMedia` операций печатной маркировки проводов: чем печатать. */
export type MarkMedia = { kind: "tube" | "tape"; sizeMm: number };

/** Что выбрано в переключателе «Метка на проводе». */
export const wireMarkKindOf = (cfg: WireMarkCfg | undefined): "none" | WireMarkKind => (!cfg?.enabled ? "none" : cfg.kind || "tube");
/** «3,2» из 3.2. */
export const mmRu = (mm: number) => String(mm).replace(".", ",");
const positiveMm = (value: string | undefined, fallback: number) => {
  const n = Number(String(value ?? "").trim().replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : fallback;
};
const clampText = (text: string) => text.trim().slice(0, LASER_TEXT_MAX);

/** Носитель печатной метки; трубка без выбранного Ø и лазер — null (операции как до 20.11 / печати нет). */
export function markMediaOf(cfg: WireMarkCfg | undefined): MarkMedia | null {
  const kind = wireMarkKindOf(cfg);
  if (kind === "tape") return { kind: "tape", sizeMm: positiveMm(cfg?.tapeMm, WIRE_MARK_TAPE_WIDTHS_MM[0]) };
  if (kind === "tube" && (cfg?.tubeMm || "").trim()) return { kind: "tube", sizeMm: positiveMm(cfg?.tubeMm, WIRE_MARK_TUBE_SIZES_MM[0]) };
  return null;
}

/** Лазерная метка провода с надписями концов А и В; по всей длине — своя надпись или надпись конца А. */
export function laserMarkOf(cfg: WireMarkCfg | undefined, front: string, back: string): LaserMark {
  const laser = cfg?.laser;
  const mode: LaserMode = laser?.mode === "full" ? "full" : "ends";
  return {
    mode, front: clampText(front), back: clampText(back), middle: mode === "full" ? clampText((laser?.middle || "").trim() || front) : "",
    frontMm: positiveMm(laser?.frontMm, LASER_END_MM), backMm: positiveMm(laser?.backMm, LASER_END_MM), stepMm: positiveMm(laser?.stepMm, LASER_STEP_MM),
  };
}

/** Что станок напечатает на проводе: по всей длине — одну надпись (своя или конца А), по краям — пару надписей концов. */
export function laserPrintedOf(cfg: WireMarkCfg | undefined, row: Pick<WireMarkRow, "textA" | "textB">): string[] {
  return cfg?.laser?.mode === "full" ? [clampText((cfg.laser.middle || "").trim() || row.textA)] : [clampText(row.textA), clampText(row.textB)];
}

/** Подпись партии резки: провода с одной подписью режутся вместе (по всей длине — по общей надписи, по краям — по паре). */
export const laserPrintedKey = (cfg: WireMarkCfg | undefined, row: Pick<WireMarkRow, "textA" | "textB"> | undefined): string => (row ? JSON.stringify(laserPrintedOf(cfg, row)) : "");

/** Предупреждения по проводам лазерной метки: надпись длиннее станка и разные надписи, ставшие одинаковыми после обрезки. */
export function laserTextWarnings(cfg: WireMarkCfg | undefined, rows: WireMarkRow[]): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const add = (key: string, text: string) => { (out[key] ||= []).includes(text) || out[key].push(text); };
  const full = cfg?.laser?.mode === "full";
  const own = full ? (cfg?.laser?.middle || "").trim() : "";
  // Надписи, которые уйдут на станок: (провод, исходный текст, конец).
  const sources = rows.flatMap((row) => (full ? (own ? [] : [{ row, raw: row.textA.trim() }]) : [{ row, raw: row.textA.trim() }, { row, raw: row.textB.trim() }]));
  sources.forEach(({ row, raw }) => {
    if (raw.length > LASER_TEXT_MAX) add(row.key, `надпись длиннее ${LASER_TEXT_MAX} знаков — на станке обрежется до «${clampText(raw)}»`);
  });
  const byCut = new Map<string, { rows: WireMarkRow[]; raws: Set<string> }>();
  sources.forEach(({ row, raw }) => {
    const hit = byCut.get(clampText(raw)) || { rows: [], raws: new Set<string>() };
    hit.raws.add(raw);
    if (!hit.rows.includes(row)) hit.rows.push(row);
    byCut.set(clampText(raw), hit);
  });
  byCut.forEach((hit, cut) => {
    if (hit.raws.size < 2) return;
    hit.rows.forEach((row) => add(row.key, `после обрезки до «${cut}» надпись совпадает с проводом ${hit.rows.filter((r) => r !== row).map((r) => `№ ${r.num}`).join(", ") || "этого же"} — напечатается одинаковая метка`));
  });
  return out;
}

/** Трубка маркиратора выбранного Ø из списка (до усадки, светлая первой); нет такой — "". */
export function wireMarkTubeBySize(names: string[], sizeMm: number): string {
  const fits = names.filter((name) => { const d = tubeBeforeShrink(name); return d !== null && Math.abs(d - sizeMm) < 0.05; });
  return (fits.find((name) => LIGHT_TUBE.test(name)) || fits[0] || "");
}

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

// «4/2мм», «ТУТ 2/1 белая», «ТТК 4,8/1,6»: в справочнике материалов «мм» после размера обычно нет (708К.5, 26.10).
const TUBE_SIZE = /(?:^|[^\d.,])(\d+(?:[.,]\d+)?)\s*\/\s*(\d+(?:[.,]\d+)?)(?![\d.,]*\d)/;
/** Светлая трубка — на ней видна печать маркиратора; тёмная — нет. */
const LIGHT_TUBE = /бел|прозрач|ж[её]лт/i;
/** Диаметр трубки до усадки, мм, из «4/2мм» / «2/1»; нет размера — null. */
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
  const fits = sized.filter((x) => x.d >= dia + 0.5);
  if (!fits.length) return sized[sized.length - 1].name;
  // Печать видна только на светлой трубке: из подходящих по размеру — меньшая светлая, иначе меньшая любая.
  return (fits.find((x) => LIGHT_TUBE.test(x.name)) || fits[0]).name;
}
