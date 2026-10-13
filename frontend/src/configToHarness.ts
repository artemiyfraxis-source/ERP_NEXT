/*
 * «Построить чертёж» на вкладках «Коакс» и «Ручная сборка»: план чертежа по конфигурации сборки, дальше — та же сборка
 * документа, что у типовых вкладок (generatorToHarness: разъёмы по библиотеке или свободным корпусом, провода с контакта
 * на контакт, свивка, ТУТ). Модуль грузится лениво вместе с кодом редактора.
 *
 *  • Коакс (`config.coax`): разъём А и разъём В, кабель — один провод с центрального контакта на центральный (контакт 1),
 *    длина — длина по чертежу; ТУТ на концах — у каждого разъёма. Сторона В без разъёма (голый конец) — не рисуется:
 *    чертёж строится от разъёма до разъёма.
 *  • Ручная сборка: соединения по распиновке, с которыми сборку открыли из типовой («Поправить вручную»,
 *    `config.connections`), провода и свивка — из той же конфигурации; ручная сборка коакса — как коакс.
 */
import { buildHarnessDocument, resolveConnectorSources, wireColorHex, wireSectionOf, type GeneratorDrawing, type GeneratorDrawingPlan, type PlanCable, type PlanConnector, type PlanCovering, type PlanWire } from "./generatorToHarness";
import type { ConnectorHousing } from "./connectorArt";
import type { ComponentTemplateApi } from "./harness/component-library/component-template-api";
import { coreColor } from "./pinoutCheck";
import { parseDecimal_, type Side } from "./tcGenLogic";
import { planTwist } from "./twistGroups";
import { configDrawingProblem } from "./GenDrawing";

type Obj = Record<string, unknown>;
const obj = (value: unknown): Obj => (value && typeof value === "object" && !Array.isArray(value) ? value as Obj : {});
const list = (value: unknown): Obj[] => (Array.isArray(value) ? value.map(obj) : []);
const str = (value: unknown) => (typeof value === "string" ? value : value == null ? "" : String(value));
const positive = (value: unknown): number | null => {
  const number = parseDecimal_(value);
  return number > 0 ? Math.round(number * 1000) / 1000 : null;
};
/** «#222» → «#222222», #RRGGBB как есть, иное — "" (как цвет хранит чертёж). */
const hex6 = (value: string | null | undefined) => {
  const color = (value ?? "").toUpperCase();
  return /^#[0-9A-F]{3}$/.test(color) ? `#${[...color.slice(1)].map((digit) => digit + digit).join("")}` : /^#[0-9A-F]{6}$/.test(color) ? color : "";
};

/** Коакс: разъём А → разъём В одним проводом (центральный контакт 1 ↔ 1). */
function coaxPlan(config: Obj): GeneratorDrawingPlan {
  const coax = obj(config.coax);
  const sideA = obj(coax.sideA), sideB = obj(coax.sideB);
  const mark = str(coax.cableArt) || str(coax.cableName) || "Коакс-кабель";
  const connectors: PlanConnector[] = [
    { key: "A", side: "A", designation: "XS1", article: str(sideA.article), terminal: "" },
    { key: "B", side: "B", designation: "XS2", article: str(sideB.article), terminal: "" },
  ];
  const lengthMm = positive(coax.cableLength);
  const wires: PlanWire[] = [{
    key: "coax", group: 0, unit: 0, mark, section: wireSectionOf(mark), color: wireColorHex(mark), lengthMm,
    from: { connector: "A", pin: 1 }, to: { connector: "B", pin: 1 },
  }];
  const ops = list(config.ops).map((op) => str(op.type));
  const tut = ops.includes("coaxInsTut") || ops.includes("coaxCutTut");
  const tutName = str(coax.tutArt) || str(coax.tutName);
  const coverings: PlanCovering[] = tut ? connectors.map((c) => ({
    kind: "tut", name: tutName ? `${tutName} на ${c.designation}` : `ТУТ на ${c.designation}`, lengthMm: positive(coax.tutLength), at: c.key, coveringKind: "heat-shrink",
  })) : [];
  return { connectors, wires, cables: [], pairs: [], twistPitchMm: null, coverings, warnings: lengthMm === null ? ["Длина по чертежу не задана — провод без длины."] : [] };
}

/** Ручная сборка: соединения конфигурации — провод с контакта на контакт; разъёмы — по концам соединений, А раньше В. */
function connectionsPlan(config: Obj): GeneratorDrawingPlan {
  const links = list(config.connections);
  const configWires = list(config.wires);
  const ends = new Map<string, { side: Side; conn: string; label: string; order: number }>();
  links.forEach((link) => [obj(link.from), obj(link.to)].forEach((end) => {
    const key = str(end.end);
    if (!key || ends.has(key)) return;
    const side: Side = /^[ВB]/i.test(key) ? "B" : "A";
    ends.set(key, { side, conn: str(end.conn), label: str(end.label).trim(), order: Number(key.replace(/\D+/g, "")) || 0 });
  }));
  const ordered = [...ends].sort(([, a], [, b]) => (a.side === b.side ? a.order - b.order : a.side === "A" ? -1 : 1));
  const connectors: PlanConnector[] = ordered.map(([key, end], index) => ({
    key, side: end.side, designation: end.label || `XS${index + 1}`, article: end.conn, terminal: "",
  }));
  const groupOf = (mark: string) => configWires.findIndex((wire) => str(wire.art) === mark || str(wire.name) === mark);
  const seen = new Map<number, number>();
  const wires: PlanWire[] = [];
  links.forEach((link, index) => {
    const mark = str(link.wire);
    const group = groupOf(mark);
    const unit = seen.get(group) ?? 0;
    seen.set(group, unit + 1);
    const source = configWires[group] ?? {};
    const colors = Array.isArray(source.coreColors) ? source.coreColors.map(str) : [];
    const from = obj(link.from), to = obj(link.to);
    wires.push({
      key: `c${index + 1}`, group, unit, mark, section: wireSectionOf(mark),
      color: hex6(coreColor(colors[unit])?.hex) || wireColorHex(mark), lengthMm: positive(source.length),
      from: { connector: str(from.end), pin: Number(from.pin) || 0 }, to: { connector: str(to.end), pin: Number(to.pin) || 0 },
    });
  });
  const cables: PlanCable[] = [];
  configWires.forEach((source, group) => {
    const per = Math.round(Number(source.cores) || 1);
    if (per <= 1) return;
    const mine = wires.filter((wire) => wire.group === group);
    for (let c = 0; c * per < mine.length; c++) {
      const keys = mine.filter((wire) => Math.floor(wire.unit / per) === c).map((wire) => wire.key);
      if (keys.length) cables.push({ key: `${group}#${c}`, mark: str(source.art) || str(source.name), wireKeys: keys, lengthMm: positive(source.length), stripMm: null });
    }
  });
  const twistCfg = obj(config.twist);
  const mode = twistCfg.mode === "all" ? "all" : "pairs";
  const groups = mode === "all"
    ? [Array.isArray(twistCfg.wireIndices) ? twistCfg.wireIndices.map(Number) : []]
    : (Array.isArray(twistCfg.pairs) ? twistCfg.pairs : []).filter(Array.isArray).map((group) => (group as unknown[]).map(Number));
  const twist = twistCfg.enabled ? planTwist(mode, groups, (pos) => wires.filter((wire) => wire.group === pos).map((wire) => wire.key)) : { pairs: [], groups: [] };
  const warnings = wires.some((wire) => wire.lengthMm !== null)
    ? ["Длины проводов — длины реза из сборки (с припусками на концы); проверьте их по КД."]
    : ["Длины проводов в сборке не заданы — провода без длины."];
  return {
    connectors, wires, cables, pairs: twist.pairs, ...(twist.groups.length ? { twistGroups: twist.groups } : {}),
    twistPitchMm: positive(twistCfg.pitch), coverings: [], warnings,
  };
}

/** План чертежа по конфигурации сборки; не из чего строить — ошибка с причиной (та же, что под кнопкой). */
export function planFromConfig(config: Record<string, unknown>): GeneratorDrawingPlan {
  const problem = configDrawingProblem(config);
  if (problem) throw new Error(`${problem}.`);
  return list(config.connections).length ? connectionsPlan(config) : coaxPlan(config);
}

export async function buildConfigDrawing(
  config: Record<string, unknown>,
  housings: Readonly<Record<string, ConnectorHousing | null | undefined>>,
  templates: Pick<ComponentTemplateApi, "list" | "getVersion"> | null,
): Promise<GeneratorDrawing> {
  const plan = planFromConfig(config);
  const { sources, warnings } = await resolveConnectorSources(plan, housings, templates);
  const built = buildHarnessDocument(plan, sources);
  return { ...built, warnings: [...warnings, ...built.warnings] };
}
