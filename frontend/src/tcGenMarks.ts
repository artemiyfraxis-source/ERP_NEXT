/*
 * «Маркировка жгута» генератора: какие метки требует КД — одна строка на метку (что маркируем, надпись, материал,
 * где, сколько на изделие). Строки уходят в операцию insMark (`op.marks`), etk_builder кладёт их в ЭТК списком меток,
 * материалы — в комплектующие операции, норма времени — по числу меток (БД.ОП, на штуку). Пустой список — прежнее
 * поведение: бирки из состава.
 */

/** Где метка: у разъёма, на обоих концах провода, на расстоянии от конца, по КД, своё место. */
export type MarkPlace = "conn" | "ends" | "dist" | "kd" | "free";
export type MarkRow = { target: string; text: string; material: string; place: MarkPlace; mm: string; where: string; qty: string };
/** Материал маркировки из БД.МАТ: наименование (оно же ключ строки), группа, единица. */
export type MarkMat = { name: string; group: string; unit: string };
/** Метка в конфиге операции insMark: art/name/qty — как у бирок из состава (комплектующие), остальное — для ЭТК. */
export type MarkSpec = {
  art: string; name: string; qty: number; target: string; text: string; place: string; placeKind: MarkPlace; placeMm: string;
};

export const MARK_PLACES: ReadonlyArray<readonly [MarkPlace, string]> = [
  ["conn", "У разъёма"], ["ends", "На концах"], ["dist", "От конца, мм"], ["kd", "По КД"], ["free", "Своё место"],
];
export const WHOLE_HARNESS = "Жгут целиком";

const MARK_GROUP_RE = /бирк|маркир|кембрик|этикет|маркер/i;
const TUBE_GROUP_RE = /^(тут|ттк)$/i;

export function newMarkRow(patch: Partial<MarkRow> = {}): MarkRow {
  return { target: "", text: "", material: "", place: "conn", mm: "", where: "", qty: "1", ...patch };
}

/** Место метки словами — как его прочтёт монтажник в ЭТК. */
export function markPlaceText(row: Pick<MarkRow, "place" | "mm" | "where">): string {
  if (row.place === "conn") return "у разъёма";
  if (row.place === "ends") return "на обоих концах";
  if (row.place === "dist") return row.mm.trim() ? `${row.mm.trim()} мм от конца` : "от конца, мм по КД";
  if (row.place === "kd") return "по КД";
  return row.where.trim() || "по КД";
}

function qtyOf(value: string): number {
  const number = Number(String(value || "").replace(",", "."));
  return Number.isFinite(number) && number > 0 ? number : 1;
}

/** Строка в работе: есть что или какая надпись. */
export function markRowActive(row: MarkRow): boolean {
  return Boolean(row.text.trim() || row.target.trim());
}

/** Материалы маркировки из записей БД.МАТ: бирки, кембрики, этикетки — сначала, затем термоусадочные трубки. */
export function markMatsFromRecords(records: Array<Record<string, string>>): MarkMat[] {
  const all = records.map((fields) => ({
    name: String(fields["Наименование"] || "").trim(), group: String(fields["Группа"] || "").trim(), unit: String(fields["Ед. изм."] || "").trim(),
  })).filter((item) => item.name);
  const marks = all.filter((item) => MARK_GROUP_RE.test(item.group) || MARK_GROUP_RE.test(item.name));
  const tubes = all.filter((item) => !marks.includes(item) && TUBE_GROUP_RE.test(item.group));
  return [...marks, ...tubes];
}

type CompLike = { art?: string; name?: string };
const norm = (value: string) => value.trim().toLowerCase().replace(/\s+/g, " ");

/** Материал строки в справочнике или в составе; null — «нет в справочнике». Пустой материал — не проверяется. */
export function markMaterialSource(material: string, mats: MarkMat[], comps: CompLike[]): "ref" | "kit" | null {
  const key = norm(material);
  if (!key) return "ref";
  if (comps.some((comp) => norm(comp.art || "") === key || norm(comp.name || "") === key)) return "kit";
  return mats.some((mat) => norm(mat.name) === key) ? "ref" : null;
}

/** Строки → метки операции insMark. Материал из состава отдаёт свой артикул и наименование. */
export function markSpecs(rows: MarkRow[], comps: CompLike[]): MarkSpec[] {
  return rows.filter(markRowActive).map((row) => {
    const material = row.material.trim();
    const comp = material ? comps.find((item) => norm(item.art || "") === norm(material) || norm(item.name || "") === norm(material)) : undefined;
    return {
      art: comp ? (comp.art || comp.name || material) : material, name: comp ? (comp.name || comp.art || material) : material,
      qty: qtyOf(row.qty), target: row.target.trim(), text: row.text.trim(), place: markPlaceText(row), placeKind: row.place, placeMm: row.mm.trim(),
    };
  });
}

/** Подпись операции: «Маркировка — X1, X2, W1 и ещё 3». */
export function markLabel(specs: MarkSpec[]): string {
  const names = specs.map((spec) => spec.text || spec.target);
  const head = names.slice(0, 4).join(", ");
  return `Маркировка — ${head}${names.length > 4 ? ` и ещё ${names.length - 4}` : ""}`;
}

/** Сохранённая сборка → строки меток (форма генератора открывается с ними). Старая конфигурация — пусто. */
export function markRowsFromConfig(config: unknown): MarkRow[] {
  const ops = config && typeof config === "object" ? (config as { ops?: unknown }).ops : null;
  if (!Array.isArray(ops)) return [];
  const op = ops.find((item) => item && typeof item === "object" && (item as { type?: unknown }).type === "insMark") as { marks?: unknown } | undefined;
  const marks = Array.isArray(op?.marks) ? op!.marks as Array<Record<string, unknown>> : [];
  if (!marks.some((mark) => mark && (mark.text || mark.target))) return [];
  const str = (value: unknown) => (value === undefined || value === null ? "" : String(value));
  return marks.map((mark) => {
    const kind = MARK_PLACES.some(([key]) => key === mark.placeKind) ? mark.placeKind as MarkPlace : "free";
    return newMarkRow({
      target: str(mark.target), text: str(mark.text), material: str(mark.art || mark.name), place: kind,
      mm: str(mark.placeMm), where: kind === "free" ? str(mark.place) : "", qty: str(mark.qty) || "1",
    });
  });
}

/** Варианты «Что маркируем»: жгут, разъёмы сторон, провода из формы. */
export function markTargets(connectors: Array<{ name: string; art: string }>, wires: string[]): string[] {
  return [
    WHOLE_HARNESS,
    ...connectors.map((item) => `Разъём ${item.name}${item.art ? ` (${item.art})` : ""}`),
    ...wires.map((art) => `Провод ${art}`),
  ];
}

/** «По разъёмам»: метка у каждого разъёма, надпись — его обозначение. */
export function markRowsByConnectors(connectors: Array<{ name: string; art: string }>, material: string): MarkRow[] {
  return connectors.map((item) => newMarkRow({
    target: `Разъём ${item.name}${item.art ? ` (${item.art})` : ""}`, text: item.name, material, place: "conn", qty: "1",
  }));
}

/** «Из чертежа жгута»: разъёмы с обозначением (X1…) и провода с маркировкой (колонка «Маркировка» таблицы
 *  соединений = `circuit`). Провод метят на обоих концах — 2 шт. на изделие. */
export function markRowsFromHarness(design: unknown, material: string): MarkRow[] {
  const doc = design && typeof design === "object" ? design as Record<string, unknown> : {};
  const content = doc.content && typeof doc.content === "object" ? doc.content as Record<string, unknown> : doc;
  const list = (value: unknown) => (Array.isArray(value) ? value.filter((item) => item && typeof item === "object") as Array<Record<string, unknown>> : []);
  const text = (value: unknown) => (typeof value === "string" ? value.trim() : "");
  const connectors = list(content.connectors).filter((item) => text(item.designation)).map((item) => newMarkRow({
    target: `Разъём ${text(item.designation)}${text(item.partNumber) ? ` (${text(item.partNumber)})` : ""}`,
    text: text(item.designation), material, place: "conn", qty: "1",
  }));
  const wires = list(content.wires).map((item, index) => ({ item, index })).filter(({ item }) => text(item.circuit)).map(({ item, index }) => newMarkRow({
    target: `Провод W${index + 1}`, text: text(item.circuit), material, place: "ends", qty: "2",
  }));
  return [...connectors, ...wires];
}

/** Быстрое заполнение дописывает строки, которых ещё нет (та же цель и надпись), пустые строки заменяет. */
export function mergeMarkRows(current: MarkRow[], extra: MarkRow[]): MarkRow[] {
  const kept = current.filter(markRowActive);
  const key = (row: MarkRow) => `${norm(row.target)}|${norm(row.text)}`;
  const seen = new Set(kept.map(key));
  return [...kept, ...extra.filter((row) => !seen.has(key(row)))];
}
