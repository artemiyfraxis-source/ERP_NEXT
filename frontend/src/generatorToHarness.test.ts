// @vitest-environment node
import { expect, it } from "vitest";
import { addEndpoint, initState, makeGen, switchGenTab, updateEndpoint, type GenData, type GenState, type TerRec } from "./tcGenLogic";
import { pinoutRecord, restorePinout, setCoreNet } from "./pinoutCheck";
import {
  buildHarnessDocument, planFromGenerator, resolveConnectorSources, wireSectionOf, type ConnectorSource, type GeneratorDrawingPlan,
} from "./generatorToHarness";
import { parseHarnessDesignDocument, type HarnessDesignDocument } from "./harness/editor/model";
import { addArticleVariantsV3, addContactTypeGroupV3, newTemplateContentV3, setArticleVariantContactGroupV3 } from "./harness/component-library/template-commands-v3";
import { createE4ConnectorSeriesTableFromV3 } from "./harness/component-library/e4-connector-series-table";
import { createTemplateContentV5FromEditor } from "./harness/component-library/template-model-v5";
import type { ComponentTemplate } from "./harness/component-library/component-template-api";
import serverSample from "../../backend/tests/techcards/harness_generated_sample.json";

const ter = (article: string): TerRec => ({ article, step: "", strip: "", lPlus: "", lMinus: "", applicator: "", awgFrom: "", awgTo: "", mm2From: "", mm2To: "" });

const COMPONENTS = [
  { id: "r", type: "Провод", name: "МГТФ 0,12 красный", art: "МГТФ 0,12 красный", qty: 0.3 },
  { id: "k", type: "Провод", name: "МГТФ 0,12 черный", art: "МГТФ 0,12 черный", qty: 0.3 },
  { id: "w", type: "Провод", name: "МГТФ 0,12 белый", art: "МГТФ 0,12 белый", qty: 0.3 },
  { id: "g", type: "Провод", name: "МГТФ 0,12 зеленый", art: "МГТФ 0,12 зеленый", qty: 0.3 },
  { id: "ca", type: "Соед", name: "Колодка PH 4", art: "PHR-4", qty: 1 },
  { id: "cb", type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
  { id: "ta", type: "Терминал", name: "Контакт PH", art: "SPH-002T-P0.5S", qty: 4 },
  { id: "tb", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
];
const D: GenData = {
  components: COMPONENTS, assemblyInfo: { "Индекс": "КБ1" }, templates: [], ops: [], terRecords: [ter("SPH-002T-P0.5S")],
  coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};

const idx = (S: GenState, compId: string) => S.wires.find((w) => w.compId === compId)!.idx;
const pins = (S: GenState, list: Array<[string, string]>) => Object.fromEntries(list.map(([comp, pin]) => [idx(S, comp), pin]));

/** Два разъёма, 4 провода, перекрёст на В: красный 1→1, чёрный 2→3, белый 3→2, зелёный 4→4; длина 300 мм. */
function twoEnded(): GenState {
  const seeded = initState(D);
  let S: GenState = { ...seeded, drawingLength: 300, sharedLengthText: "300", twist: { ...seeded.twist, enabled: false, pairs: [] } };
  S = updateEndpoint(D, S, "a1", { pins: pins(S, [["r", "1"], ["k", "2"], ["w", "3"], ["g", "4"]]) });
  return updateEndpoint(D, S, "b1", { pins: pins(S, [["r", "1"], ["k", "3"], ["w", "2"], ["g", "4"]]) });
}

/** Три конца: А — все 4 провода, В1 (XHP-4) — красный и чёрный, В2 (XHP-4) — белый и зелёный. */
function threeEnded(): GenState {
  let S = switchGenTab(twoEnded(), "complex");
  S = addEndpoint(S, "B");
  const b2 = S.endpoints.at(-1)!.id;
  S = updateEndpoint(D, S, b2, { connId: "cb", termId: "tb", label: "XS3" });
  S = updateEndpoint(D, S, "a1", { label: "XS1" });
  S = updateEndpoint(D, S, "b1", { label: "XS2", wireIdxs: [idx(S, "r"), idx(S, "k")], pins: pins(S, [["r", "1"], ["k", "2"]]) });
  return updateEndpoint(D, S, b2, { wireIdxs: [idx(S, "w"), idx(S, "g")], pins: pins(S, [["w", "1"], ["g", "2"]]) });
}

const free = (contactCount: number | null = null, libraryCode: string | null = null): ConnectorSource => ({ kind: "free", contactCount, libraryCode });
const sourcesOf = (plan: GeneratorDrawingPlan, source: ConnectorSource) => Object.fromEntries(plan.connectors.map((c) => [c.key, source]));
const contactRow = (document: HarnessDesignDocument, designation: string, number: number) =>
  document.connectors.find((c) => c.designation === designation)!.contacts.find((c) => c.number === number)!;
const routeOf = (document: HarnessDesignDocument, wireId: string) =>
  document.physicalTopology!.routes.find((r) => r.wireId === wireId)!.steps.map((s) => s.segmentId);

it("reads a wire section from its mark", () => {
  expect(wireSectionOf("МГТФ 0,12 красный")).toBe("0,12 мм²");
  expect(wireSectionOf("UL1007 24AWG red")).toBe("24 AWG");
  expect(wireSectionOf("ПуГВ 1х0,75")).toBe("0,75 мм²");
  expect(wireSectionOf("Провод")).toBe("");
});

it("builds a two-ended 4-wire harness: tables, wires pin to pin, one trunk with its length", () => {
  const plan = planFromGenerator(D, twoEnded());
  expect(plan.connectors.map((c) => [c.designation, c.article, c.side, c.terminal])).toEqual([
    ["XS1", "PHR-4", "A", "SPH-002T-P0.5S"], ["XS2", "XHP-4", "B", ""],
  ]);
  expect(plan.warnings).toContain("В1: терминала «SXH-001T-P0.6» нет в БД.ТЕР — в таблице контактов он не указан.");
  const { document, wires, connectors } = buildHarnessDocument(plan, sourcesOf(plan, free(4, "JST")));
  expect([connectors, wires]).toEqual([2, 4]);
  // Перекрёст: чёрный с XS1:2 на XS2:3.
  const black = document.wires.find((w) => w.color === "#222222")!;
  const end = (e: typeof black.from) => {
    const c = document.connectors.find((x) => x.id === e.connectorId)!;
    return `${c.designation}:${c.contacts.find((x) => x.id === e.contactId)!.number}`;
  };
  expect([end(black.from), end(black.to)]).toEqual(["XS1:2", "XS2:3"]);
  // Таблица контактов (Э4): марка, сечение, цвет, терминал.
  expect(contactRow(document, "XS1", 1)).toMatchObject({ wire: "МГТФ 0,12 красный", wireSection: "0,12 мм²", color: "#D7322D", terminalArticle: "SPH-002T-P0.5S" });
  expect(contactRow(document, "XS2", 3)).toMatchObject({ wire: "МГТФ 0,12 черный", color: "#222222", terminalArticle: "" });
  const [a, b] = document.connectors;
  expect([a.libraryCode, a.partNumber, b.schematic.orientation]).toEqual(["JST", "PHR-4", "contacts-left"]);
  expect(b.positions.e4.x).toBeGreaterThan(a.positions.e4.x);
  expect(b.positions.drawing.x).toBeGreaterThan(a.positions.drawing.x);
  // Один участок А → В, все провода по нему, размер 300 мм — длина каждого провода.
  const topology = document.physicalTopology!;
  expect(topology.segments).toHaveLength(1);
  expect(document.wires.map((w) => routeOf(document, w.id))).toEqual(document.wires.map(() => ["gen-s1"]));
  expect(document.drawingDocuments!.dimensions).toMatchObject([{ segmentId: "gen-s1", lengthMm: 300 }]);
  expect(document.wires.every((w) => w.lengthMm === 300)).toBe(true);
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("splits a 3-ended harness: trunk from A to a branch node, a branch per B end", () => {
  const plan = planFromGenerator(D, threeEnded());
  expect(plan.connectors.map((c) => c.designation)).toEqual(["XS1", "XS2", "XS3"]);
  expect(plan.wires.map((w) => `${w.mark.split(" ").at(-1)} ${w.from.pin}→${plan.connectors.find((c) => c.key === w.to.connector)!.designation}:${w.to.pin}`))
    .toEqual(["красный 1→XS2:1", "черный 2→XS2:2", "белый 3→XS3:1", "зеленый 4→XS3:2"]);
  const { document, warnings } = buildHarnessDocument(plan, sourcesOf(plan, free(4)));
  const topology = document.physicalTopology!;
  expect(topology.nodes.find((n) => !n.connectorId)?.id).toBe("gen-j1");
  expect(topology.segments.map((s) => [s.id, s.from, s.to])).toEqual([
    ["gen-s1", "gen-n1", "gen-j1"], ["gen-s2", "gen-j1", "gen-n2"], ["gen-s3", "gen-j1", "gen-n3"],
  ]);
  expect(document.wires.map((w) => routeOf(document, w.id))).toEqual([
    ["gen-s1", "gen-s2"], ["gen-s1", "gen-s2"], ["gen-s1", "gen-s3"], ["gen-s1", "gen-s3"],
  ]);
  const [, b1, b2] = document.connectors;
  expect(b1.positions.drawing.y).toBeLessThan(b2.positions.drawing.y);
  expect(document.drawingDocuments).toEqual({ tables: [], leaders: [], bomOrder: [] });
  expect(document.wires.every((w) => w.lengthMm === 300)).toBe(true);
  expect(warnings).toContain("Длины ствола и веток генератор не знает — размеры на чертеже не проставлены, длины проводов перенесены.");
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("draws a twisted pair on the segments both wires run through", () => {
  const S = twoEnded();
  const twisted = { ...S, twist: { ...S.twist, enabled: true, mode: "pairs" as const, pairs: [{ a: "0", b: "1" }], pitch: "25" } };
  const plan = planFromGenerator(D, twisted);
  expect(plan.pairs).toEqual([[plan.wires[0].key, plan.wires[1].key]]);
  const { document } = buildHarnessDocument(plan, sourcesOf(plan, free(4)));
  expect(document.diffPairs).toEqual([{ id: "gen-p1", wireIds: ["gen-w1", "gen-w2"], step: 25, amplitude: 7, variant: 1 }]);
  expect(routeOf(document, "gen-w1")).toEqual(routeOf(document, "gen-w2"));
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
  // «Все вместе» больше двух — не пара, а свитая группа (twistGroups.test.ts, twistDrawing.test.ts).
  const all = planFromGenerator(D, { ...twisted, twist: { ...twisted.twist, mode: "all", wires: null } });
  expect(all.pairs).toEqual([]);
  expect(all.twistGroups).toEqual([plan.wires.map((w) => w.key)]);
});

it("falls back to a generic housing for a connector the library does not know, and flags it", async () => {
  const S = twoEnded();
  const plan = planFromGenerator(D, S);
  // База разъёмов знает XHP-4 (серия и 4 контакта), PHR-4 — нет; библиотека компонентов пуста.
  const { sources } = await resolveConnectorSources(plan, { "XHP-4": { family: "JST XH", n: 4, rows: 1, cols: 4, color: "#fff", grid: [] } },
    { list: async () => [], getVersion: async () => { throw new Error("нет"); } });
  expect(sources).toEqual({ a1: free(null, null), b1: free(4, "JST XH") });
  const { document, warnings } = buildHarnessDocument(plan, sources);
  const [a, b] = document.connectors;
  expect([a.libraryBinding, a.libraryCode, a.contacts.length]).toEqual([{ mode: "free" }, "FREE", 4]);
  expect([b.libraryCode, b.partNumber, b.contacts.length]).toEqual(["JST XH", "XHP-4", 4]);
  expect(warnings).toContain("XS1 (PHR-4): нет в библиотеке компонентов — нарисован свободным корпусом на 4 конт. (число контактов — по распиновке)");
  expect(warnings).toContain("XS2 (XHP-4): нет в библиотеке компонентов — нарисован свободным корпусом на 4 конт.");
});

it("places a connector from the component library by its article", async () => {
  let core = newTemplateContentV3();
  let group: string;
  [core, group] = addContactTypeGroupV3(core, "Сигнал");
  core = addArticleVariantsV3(core, [{ sourceId: "technology-connectors", entityType: "connector", articleKey: "PHR-4" }]);
  core = setArticleVariantContactGroupV3(core, core.articleVariants[0]!.id, group, 4, []);
  const content = createTemplateContentV5FromEditor(core, createE4ConnectorSeriesTableFromV3(core, true), [], []).content;
  const template: ComponentTemplate = { templateId: "tmpl-ph", version: 3, versionSha256: "a".repeat(64), code: "JST PH", name: "Колодки JST PH",
    assets: [], articleBindings: core.articleVariants, createdUtc: "2026-10-12", content };
  const plan = planFromGenerator(D, twoEnded());
  const { sources } = await resolveConnectorSources(plan, {}, { list: async () => [template], getVersion: async () => template });
  expect(sources.a1.kind).toBe("template");
  const { document } = buildHarnessDocument(plan, sources);
  const a = document.connectors[0];
  expect(a.libraryBinding).toMatchObject({ mode: "template", templateId: "tmpl-ph", templateVersion: 3 });
  expect([a.libraryCode, a.partNumber]).toEqual(["JST PH", "PHR-4"]);
  expect(contactRow(document, "XS1", 2)).toMatchObject({ wire: "МГТФ 0,12 черный", color: "#222222" });
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("puts a solder TUT on every wire at its contact (not one tube over the bundle) and the braid over the whole harness", () => {
  const S = threeEnded();
  const solder = updateEndpoint(D, S, "a1", { method: "solder", tutLen: "15" });
  const braided = { ...solder, wrap: { enabled: true, slots: [{ mat: "w", kind: "braid" as const, len: "320" }, ...solder.wrap.slots.slice(1)] } };
  const plan = planFromGenerator(D, braided);
  const { document } = buildHarnessDocument(plan, sourcesOf(plan, free(4)));
  const topology = document.physicalTopology!;
  // У XS1 у каждого контакта свой выход и отрезок до общего узла разъёма gen-n1, дальше — ствол и ветки, как раньше.
  expect(topology.segments.map((s) => [s.id, s.from, s.to])).toEqual([
    ["gen-s1", "gen-n1-1", "gen-n1"], ["gen-s2", "gen-n1-2", "gen-n1"], ["gen-s3", "gen-n1-3", "gen-n1"], ["gen-s4", "gen-n1-4", "gen-n1"],
    ["gen-s5", "gen-n1", "gen-j1"], ["gen-s6", "gen-j1", "gen-n2"], ["gen-s7", "gen-j1", "gen-n3"],
  ]);
  expect(topology.nodes.filter((n) => n.id.startsWith("gen-n1-")).map((n) => n.wireIds)).toEqual([["gen-w1"], ["gen-w2"], ["gen-w3"], ["gen-w4"]]);
  expect(document.wires.map((w) => routeOf(document, w.id))).toEqual([
    ["gen-s1", "gen-s5", "gen-s6"], ["gen-s2", "gen-s5", "gen-s6"], ["gen-s3", "gen-s5", "gen-s7"], ["gen-s4", "gen-s5", "gen-s7"],
  ]);
  const coverings = topology.coverings!;
  expect(coverings.map((c) => [c.name, c.kind, c.lengthMm, c.spans.map((s) => s.segmentId)])).toEqual([
    ["ТУТ на пайку XS1 · конт. 1", "heat-shrink", 15, ["gen-s1"]],
    ["ТУТ на пайку XS1 · конт. 2", "heat-shrink", 15, ["gen-s2"]],
    ["ТУТ на пайку XS1 · конт. 3", "heat-shrink", 15, ["gen-s3"]],
    ["ТУТ на пайку XS1 · конт. 4", "heat-shrink", 15, ["gen-s4"]],
    ["МГТФ 0,12 белый", "braid", 320, ["gen-s5", "gen-s6", "gen-s7"]],
  ]);
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("a two-ended harness with solder TUT keeps one trunk with its length dimension; termoshrink sleeves stay over the bundle", () => {
  const S = updateEndpoint(D, twoEnded(), "b1", { method: "solder", tutLen: "" });
  const sleeved = { ...S, hs: { enabled: true, manual: true, rows: [{ compId: "w", len: "30", cnt: "1" }] } };
  const plan = planFromGenerator(D, sleeved);
  const { document, warnings } = buildHarnessDocument(plan, sourcesOf(plan, free(4)));
  const topology = document.physicalTopology!;
  expect(topology.segments.map((s) => [s.from, s.to])).toEqual([
    ["gen-n2-1", "gen-n2"], ["gen-n2-2", "gen-n2"], ["gen-n2-3", "gen-n2"], ["gen-n2-4", "gen-n2"], ["gen-n1", "gen-n2"],
  ]);
  expect(document.drawingDocuments!.dimensions).toMatchObject([{ segmentId: "gen-s5", lengthMm: 300 }]);
  expect(warnings.some((w) => w.startsWith("Длины ствола и веток"))).toBe(false);
  const coverings = topology.coverings!;
  // ТУТ без длины — длина по чертежу авто; рукав термоусадки — у конца XS1 на стволе, поверх всего пучка.
  expect(coverings.map((c) => [c.name, c.lengthMode, c.spans.map((s) => s.segmentId)])).toEqual([
    ["ТУТ на пайку XS2 · конт. 1", "auto", ["gen-s1"]], ["ТУТ на пайку XS2 · конт. 2", "auto", ["gen-s2"]],
    ["ТУТ на пайку XS2 · конт. 3", "auto", ["gen-s3"]], ["ТУТ на пайку XS2 · конт. 4", "auto", ["gen-s4"]],
    ["МГТФ 0,12 белый", "manual", ["gen-s5"]],
  ]);
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
});

it("carries net names from the pinout onto the drawing: the wire and both contact rows", () => {
  let S = twoEnded();
  S = setCoreNet(D, S, idx(S, "r"), 0, "+5V");
  S = setCoreNet(D, S, idx(S, "k"), 0, " GND ");
  const plan = planFromGenerator(D, S);
  expect(plan.wires.map((w) => w.circuit)).toEqual(["+5V", "GND", "", ""]);
  const { document } = buildHarnessDocument(plan, sourcesOf(plan, free(4)));
  expect(document.wires.map((w) => w.circuit)).toEqual(["+5V", "GND", "", ""]);
  // Перекрёст: чёрный (GND) — XS1:2 → XS2:3.
  expect([contactRow(document, "XS1", 1).circuit, contactRow(document, "XS2", 1).circuit]).toEqual(["+5V", "+5V"]);
  expect([contactRow(document, "XS1", 2).circuit, contactRow(document, "XS2", 3).circuit]).toEqual(["GND", "GND"]);
  expect(contactRow(document, "XS1", 3).circuit).toBe("");
  expect(() => parseHarnessDesignDocument(JSON.parse(JSON.stringify(document)))).not.toThrow();
  // Снять цепь — пустая строка; запись распиновки хранит цепи и возвращает их в свежую форму.
  expect(setCoreNet(D, S, idx(S, "r"), 0, "").wires.find((w) => w.compId === "r")!.nets).toBeUndefined();
  const record = pinoutRecord(makeGen(D, S), S);
  expect(record?.nets).toEqual([{ pos: 0, unit: 0, net: "+5V" }, { pos: 1, unit: 0, net: "GND" }]);
  const restored = restorePinout(D, twoEnded(), { pinout: record });
  expect(planFromGenerator(D, restored).wires.map((w) => w.circuit)).toEqual(["+5V", "GND", "", ""]);
});

it("refuses to draw a harness with no pinout", () => {
  const S = { ...initState(D), drawingLength: 300 };
  const plan = planFromGenerator(D, S);
  expect(plan.wires).toEqual([]);
  expect(() => buildHarnessDocument(plan, {})).toThrow("Нет соединений по распиновке");
});

it("matches the sample the server validator checks (backend/tests/techcards/harness_generated_sample.json)", () => {
  const S = threeEnded();
  const twisted = { ...S, twist: { ...S.twist, enabled: true, mode: "pairs" as const, pairs: [{ a: "0", b: "1" }], pitch: "20" } };
  const plan = planFromGenerator(D, updateEndpoint(D, twisted, "a1", { method: "solder", tutLen: "15" }));
  const { document } = buildHarnessDocument(plan, sourcesOf(plan, free(4, "JST XH")));
  expect(document).toEqual(serverSample);
});
