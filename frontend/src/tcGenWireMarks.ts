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
  /** Ø трубки на маркираторе, мм (WIRE_MARK_TUBE_SIZES_MM); пусто — «по Ø провода»: Ø, запомненный для этого Ø провода
   *  (`tubeByDia`), иначе ближайший Ø TP80E не меньше Ø провода. */
  tubeMm?: string;
  /** Ø трубки маркиратора, выбранный для Ø провода (владелец 20.11, «запомнить выбор»): «<Ø провода>» → Ø трубки. Уходит в
   *  сохранённый рецепт — новая КБ проекта берёт его оттуда (GET /api/techcards/generator-mark-defaults). */
  tubeByDia?: Record<string, string>;
  /** «Покрыть прозрачной трубкой» (владелец 20.11): нет поля — у ленты да (наклейка на проводе без покрытия отходит), у трубки нет. */
  cover?: boolean;
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

/** Ключ Ø провода в `tubeByDia`: «1.6» из 1.6 (как сервер, mark_defaults._dia_key); Ø неизвестен — "". */
export const wireDiaKey = (dia: number): string => (dia > 0 ? String(Math.round(dia * 1000) / 1000) : "");

/** Запас Ø трубки над Ø провода, мм: трубка должна налезть (тот же, что у pickWireMarkTube). */
export const WIRE_MARK_TUBE_MARGIN_MM = 0.5;

/** «По Ø провода» (владелец 20.11): меньший Ø трубки TP80E не меньше Ø провода + 0,5 мм (как pickWireMarkTube); толще всех —
 *  самый большой, Ø неизвестен — самый малый. */
export function markTubeForWire(dia: number): number {
  if (!(dia > 0)) return WIRE_MARK_TUBE_SIZES_MM[0];
  return WIRE_MARK_TUBE_SIZES_MM.find((mm) => mm >= dia + WIRE_MARK_TUBE_MARGIN_MM - 1e-9) ?? WIRE_MARK_TUBE_SIZES_MM[WIRE_MARK_TUBE_SIZES_MM.length - 1];
}

/** Ø трубки маркиратора: выбранный, иначе запомненный для этого Ø провода, иначе по Ø провода. */
export function markTubeMmOf(cfg: WireMarkCfg | undefined, wireDia: number): number {
  if ((cfg?.tubeMm || "").trim()) return positiveMm(cfg?.tubeMm, WIRE_MARK_TUBE_SIZES_MM[0]);
  const remembered = cfg?.tubeByDia?.[wireDiaKey(wireDia)];
  return remembered ? positiveMm(remembered, markTubeForWire(wireDia)) : markTubeForWire(wireDia);
}

/** Носитель печатной метки; `wireDia` — Ø изоляции самого толстого маркируемого провода (трубка «по Ø провода»). Трубка и
 *  лента — всегда с размером (владелец 20.11: печать на TP80E), лазер и «нет» — null. */
export function markMediaOf(cfg: WireMarkCfg | undefined, wireDia = 0): MarkMedia | null {
  const kind = wireMarkKindOf(cfg);
  if (kind === "tape") return { kind: "tape", sizeMm: positiveMm(cfg?.tapeMm, WIRE_MARK_TAPE_WIDTHS_MM[0]) };
  if (kind === "tube") return { kind: "tube", sizeMm: markTubeMmOf(cfg, wireDia) };
  return null;
}

/** Выбор Ø трубки в форме: `tubeMm` и запомненный Ø для Ø провода (`tubeByDia`); «по Ø провода» убирает запомненный. */
export function withTubeMm(cfg: WireMarkCfg, tubeMm: string, wireDia: number): WireMarkCfg {
  const key = wireDiaKey(wireDia);
  if (!key) return { ...cfg, tubeMm };
  const tubeByDia = { ...(cfg.tubeByDia || {}) };
  if (tubeMm.trim()) tubeByDia[key] = tubeMm; else delete tubeByDia[key];
  return { ...cfg, tubeMm, tubeByDia };
}

/** Прозрачная трубка поверх метки: выбрано в форме, иначе у ленты да, у трубки нет; лазер и «нет» — нет. */
export function wireMarkCoverOn(cfg: WireMarkCfg | undefined): boolean {
  const kind = wireMarkKindOf(cfg);
  if (kind !== "tube" && kind !== "tape") return false;
  return cfg?.cover ?? kind === "tape";
}
/** Прозрачная трубка длиннее метки на 10 мм. */
export const WIRE_MARK_COVER_EXTRA_MM = 10;
/** Длина отрезка прозрачной трубки, мм: длина метки (пусто — 15) + 10. */
export function wireMarkCoverLen(cfg: WireMarkCfg | undefined): string {
  const mark = positiveMm(cfg?.len, Number(WIRE_MARK_LEN_MM));
  return String(Math.round((mark + WIRE_MARK_COVER_EXTRA_MM) * 1000) / 1000);
}

/** Последний выбор маркировки проводов проекта (GET /api/techcards/generator-mark-defaults): `kind` — как метила самая новая КБ
 *  проекта («none» — без меток, null — КБ с маркировкой ещё не было), размеры — из самой новой КБ, где они были. */
export type WireMarkDefaults = { kind: "none" | WireMarkKind | null; tapeMm: number | null; tubeByDia: Record<string, number> };

/** Новая КБ без черновика и без маркировки (владелец 20.11, «последний выбор» буквально): чем метила последняя КБ проекта —
 *  «нет» (выключено), трубка, лента или лазер; ширина ленты и Ø трубки для каждого Ø провода — заранее, при любом выборе, чтобы
 *  переключение на ленту или трубку начиналось с них. Ни выбора, ни размеров — undefined (форма как была). */
export function wireMarksFromDefaults(defaults: WireMarkDefaults | null | undefined): WireMarkCfg | undefined {
  if (!defaults) return undefined;
  const kind = defaults.kind === "tube" || defaults.kind === "tape" || defaults.kind === "laser" ? defaults.kind : null;
  const tubeByDia = Object.fromEntries(Object.entries(defaults.tubeByDia || {})
    .filter(([dia, mm]) => Number(dia) > 0 && Number(mm) > 0).map(([dia, mm]) => [wireDiaKey(Number(dia)), String(mm)]));
  const sizes = {
    ...(defaults.tapeMm && defaults.tapeMm > 0 ? { tapeMm: String(defaults.tapeMm) } : {}),
    ...(Object.keys(tubeByDia).length ? { tubeByDia } : {}),
  };
  if (!kind && defaults.kind !== "none" && !Object.keys(sizes).length) return undefined;
  return { enabled: !!kind, mat: "", len: "", dist: "", ...(kind ? { kind } : {}), ...sizes };
}

/** Что уходит в сохранённый рецепт (`build_config.wireMarks`): чем метить («none» — выключено), размеры, запомненные Ø и
 *  покрытие — из него сервер отдаёт выбор проекта (mark_defaults.py). Ширина ленты — и у выключенной, если её выбирали. */
export function savedWireMarks(cfg: WireMarkCfg): Record<string, unknown> {
  const kind = wireMarkKindOf(cfg);
  const tubeByDia = Object.entries(cfg.tubeByDia || {}).filter(([dia, mm]) => dia && String(mm || "").trim());
  return {
    enabled: kind !== "none", kind,
    ...(kind === "tube" && (cfg.tubeMm || "").trim() ? { tubeMm: cfg.tubeMm!.trim() } : {}),
    ...(kind === "tape" || (cfg.tapeMm || "").trim() ? { tapeMm: String(positiveMm(cfg.tapeMm, WIRE_MARK_TAPE_WIDTHS_MM[0])) } : {}),
    ...(tubeByDia.length ? { tubeByDia: Object.fromEntries(tubeByDia) } : {}),
    ...(kind === "tube" || kind === "tape" ? { cover: wireMarkCoverOn(cfg) } : {}),
  };
}

/** Прозрачная ТУТ по умолчанию — КВТ ТУТ(HF) 2:1 (владелец 17.11: «прозрачная ТУТ без производителя — КВТ ТУТ(HF)»; 20.11 —
 *  арт. 83266, это 4/2). Артикулы — каталог КВТ (docs/data «Оболочки — термоусадка КВТ и REXANT»); размер без артикула в
 *  каталоге — наименованием без производителя: сервер подставит КВТ ТУТ(HF) по тому же правилу (etk_heat_shrink). */
const KVT_CLEAR_TUBES: ReadonlyArray<{ before: number; after: number; art?: string }> = [
  { before: 4, after: 2, art: "83266" }, { before: 6, after: 3 }, { before: 8, after: 4 }, { before: 10, after: 5, art: "83269" },
  { before: 12, after: 6, art: "83270" }, { before: 16, after: 8, art: "83271" }, { before: 20, after: 10, art: "83272" },
  { before: 25, after: 12.5, art: "85117" }, { before: 30, after: 15, art: "85118" },
];
const CLEAR_TUBE = /прозрач/i;
const COVER_TUBE = /тут|термоусад/i;
const GLUE_TUBE = /ттк|клеев/i;
/** Запас Ø трубки до усадки над тем, на что её надевают, мм (как у трубки маркировки, pickWireMarkTube). */
const COVER_MARGIN_MM = 0.5;

/** Прозрачная трубка поверх метки Ø `overMm` (лента — Ø провода, трубка — Ø трубки маркиратора): меньшая прозрачная ТУТ без
 *  клея из списка (состав, затем БД.МАТ), что налезает с запасом 0,5 мм; нет такой — КВТ ТУТ(HF) прозрачная по тому же правилу.
 *  `mm` — Ø трубки до усадки. */
export function pickCoverTube(items: ReadonlyArray<{ art?: string; name: string }>, overMm: number): { art: string; name: string; mm: number } {
  const need = Math.max(0, overMm) + COVER_MARGIN_MM;
  const text = (item: { art?: string; name: string }) => `${item.name} ${item.art || ""}`;
  const fit = items.map((item) => ({ item, d: tubeBeforeShrink(item.name) ?? tubeBeforeShrink(item.art || "") }))
    .filter((x): x is { item: { art?: string; name: string }; d: number } => x.d !== null && x.d >= need
      && CLEAR_TUBE.test(text(x.item)) && COVER_TUBE.test(text(x.item)) && !GLUE_TUBE.test(text(x.item)))
    .sort((a, b) => a.d - b.d)[0];
  if (fit) return { art: fit.item.art || fit.item.name, name: fit.item.name, mm: fit.d };
  const kvt = KVT_CLEAR_TUBES.find((t) => t.before >= need) ?? KVT_CLEAR_TUBES[KVT_CLEAR_TUBES.length - 1];
  const size = `${mmRu(kvt.before)}/${mmRu(kvt.after)}`;
  const name = kvt.art ? `ТУТ(HF) ${size} прозрачная` : `ТУТ ${size} прозрачная`;
  return { art: kvt.art || name, name, mm: kvt.before };
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
