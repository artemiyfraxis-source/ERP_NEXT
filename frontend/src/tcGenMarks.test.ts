import { expect, it } from "vitest";
import { initState, makeGen, type GenData } from "./tcGenLogic";
import {
  markLabel, markMaterialSource, markMatsFromRecords, markPlaceText, markRowsByConnectors, markRowsFromConfig, markRowsFromHarness,
  markSpecs, markTargets, mergeMarkRows, newMarkRow, productMarkRow,
} from "./tcGenMarks";
import { DSA, fullHarness } from "./tcGenRealHarness.fixture";

const templates = [
  { id: "cut", title: "CUT_WIRE_auto | Резка", category: "Резка" },
  { id: "mark", title: "INS_MARK_HARNESS | Маркировка жгута", category: "Монтаж маркировки" },
];

function data(components: GenData["components"]): GenData {
  return {
    components, assemblyInfo: { "Индекс": "КБ1", "Наименование": "Жгут 150мм" }, templates,
    ops: [{ opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "12", tPrep: "60", tMachine: "3" }],
    terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
    markMats: [{ name: "Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая", group: "бирка", unit: "шт" }],
  };
}

const kit = [
  { id: "w", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
  { id: "x1", type: "Соед", name: "Розетка DB-9F", art: "DB-9F", qty: 1 },
  { id: "x2", type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
  { id: "m", type: "Маркировка", name: "Маркировка", art: "Маркировка", qty: 1 },
];

const markOp = (cfg: Record<string, unknown>) => (cfg.ops as Array<Record<string, unknown>>).find((op) => op.type === "insMark");

it("bare toggle without marks by КД keeps the old marking operation: composition tags, the same config as before", () => {
  const D = data(kit);
  const S = initState(D);
  expect(S.mark).toBe(true);
  expect(S.markRows).toEqual([]);
  const op = markOp(makeGen(D, S).buildConfig() as Record<string, unknown>);
  expect(op).toMatchObject({ type: "insMark", templateId: "mark", marks: [{ art: "Маркировка", name: "Маркировка", qty: 1 }] });
  expect(Object.keys((op!.marks as object[])[0]).sort()).toEqual(["art", "name", "qty"]);
  const spec = makeGen(D, S).getActiveOps().find((item) => item.type === "insMark")!;
  expect(spec.label).toBe("Маркировка — Маркировка");
  // Пустые строки таблицы не меняют старое поведение.
  const blank = makeGen(D, { ...S, markRows: [newMarkRow()] }).buildConfig() as Record<string, unknown>;
  expect(markOp(blank)!.marks).toEqual([{ art: "Маркировка", name: "Маркировка", qty: 1 }]);
});

it("marks by КД replace the composition tags: every row goes to the operation with text, material, place and qty", () => {
  const D = data(kit);
  const S = {
    ...initState(D),
    markRows: [
      newMarkRow({ target: "Разъём X1 (DB-9F)", text: "X1", material: "Маркировка", place: "conn", qty: "1" }),
      newMarkRow({ target: "Провод W1", text: "708К.4КБ1", material: "Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая", place: "dist", mm: "50", qty: "2" }),
      newMarkRow({ target: "Жгут целиком", text: "КБ1 №‹номер›", material: "", place: "free", where: "на оплётке у X2", qty: "1" }),
    ],
  };
  const gen = makeGen(D, S);
  const spec = gen.getActiveOps().find((item) => item.type === "insMark")!;
  expect(spec.label).toBe("Маркировка — X1, 708К.4КБ1, КБ1 №‹номер›");
  expect(markOp(gen.buildConfig() as Record<string, unknown>)!.marks).toEqual([
    { art: "Маркировка", name: "Маркировка", qty: 1, target: "Разъём X1 (DB-9F)", text: "X1", place: "у разъёма", placeKind: "conn", placeMm: "" },
    { art: "Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая", name: "Бирка маркировочная TMS-SCE-1/4-2.0-4 жёлтая", qty: 2,
      target: "Провод W1", text: "708К.4КБ1", place: "50 мм от конца", placeKind: "dist", placeMm: "50" },
    { art: "", name: "", qty: 1, target: "Жгут целиком", text: "КБ1 №‹номер›", place: "на оплётке у X2", placeKind: "free", placeMm: "" },
  ]);
  // Выключенная маркировка — операции нет.
  expect(markOp(makeGen(D, { ...S, mark: false }).buildConfig() as Record<string, unknown>)).toBeUndefined();
});

it("marks by КД work without marking tags in the composition", () => {
  const D = data(kit.filter((item) => item.id !== "m"));
  const S = { ...initState(D), mark: true, markRows: [newMarkRow({ text: "X1", material: "Бирка ТМ" })] };
  expect(markOp(makeGen(D, S).buildConfig() as Record<string, unknown>)!.marks).toMatchObject([{ art: "Бирка ТМ", text: "X1", qty: 1 }]);
});

it("quick fills: by the form's connectors and from the harness drawing, without duplicates", () => {
  const D = data(kit);
  const S = initState(D);
  const connectors = makeGen(D, S).markConnectors_();
  expect(connectors).toEqual([{ name: "А1", art: "DB-9F" }, { name: "В1", art: "XHP-4" }]);
  const byConn = markRowsByConnectors(connectors, "Маркировка");
  expect(byConn.map((row) => [row.target, row.text, row.material, row.place, row.qty])).toEqual([
    ["Разъём А1 (DB-9F)", "А1", "Маркировка", "conn", "1"], ["Разъём В1 (XHP-4)", "В1", "Маркировка", "conn", "1"],
  ]);
  expect(markTargets(connectors, ["МГТФ 0,12"])).toEqual(["Жгут целиком", "Разъём А1 (DB-9F)", "Разъём В1 (XHP-4)", "Провод МГТФ 0,12"]);

  const design = { content: {
    connectors: [{ id: "x1", designation: "X1", partNumber: "DB-9F" }, { id: "x2", designation: "", partNumber: "XHP-4" }],
    wires: [{ id: "w1", circuit: "708К.4КБ1-1" }, { id: "w2", circuit: "" }, { id: "w3", circuit: "708К.4КБ1-3" }],
  } };
  const drawn = markRowsFromHarness(design, "");
  expect(drawn.map((row) => [row.target, row.text, row.place, row.qty])).toEqual([
    ["Разъём X1 (DB-9F)", "X1", "conn", "1"], ["Провод W1", "708К.4КБ1-1", "ends", "2"], ["Провод W3", "708К.4КБ1-3", "ends", "2"],
  ]);
  // Чертёж без обёртки content читается так же; мусор — пусто.
  expect(markRowsFromHarness(design.content, "")).toHaveLength(3);
  expect(markRowsFromHarness(null, "")).toEqual([]);
  // Повторное заполнение не дублирует, пустые строки уходят.
  const merged = mergeMarkRows([newMarkRow(), ...drawn], [...drawn, ...byConn]);
  expect(merged.map((row) => row.text)).toEqual(["X1", "708К.4КБ1-1", "708К.4КБ1-3", "А1", "В1"]);
});

it("saved build config gives the rows back; an old config gives none", () => {
  const D = data(kit);
  const S = { ...initState(D), markRows: [
    newMarkRow({ target: "Провод W1", text: "W1", material: "Маркировка", place: "dist", mm: "40", qty: "2" }),
    newMarkRow({ target: "Жгут целиком", text: "КБ1", place: "free", where: "у X2" }),
  ] };
  const cfg = makeGen(D, S).buildConfig();
  expect(markRowsFromConfig(cfg)).toEqual(S.markRows);
  expect(markRowsFromConfig(makeGen(D, initState(D)).buildConfig())).toEqual([]);
  expect(markRowsFromConfig(null)).toEqual([]);
});

it("places, labels, materials from БД.МАТ and the «нет в справочнике» flag", () => {
  expect(markPlaceText(newMarkRow({ place: "ends" }))).toBe("на обоих концах");
  expect(markPlaceText(newMarkRow({ place: "dist", mm: "" }))).toBe("от конца, мм по КД");
  expect(markPlaceText(newMarkRow({ place: "kd" }))).toBe("по КД");
  expect(markPlaceText(newMarkRow({ place: "free", where: "" }))).toBe("по КД");
  const specs = markSpecs(["A", "B", "C", "D", "E", "F"].map((text) => newMarkRow({ text })), []);
  expect(markLabel(specs)).toBe("Маркировка — A, B, C, D и ещё 2");
  expect(markSpecs([newMarkRow({ text: "X1", qty: "0" }), newMarkRow({ text: "X2", qty: "1,5" })], []).map((spec) => spec.qty)).toEqual([1, 1.5]);

  const mats = markMatsFromRecords([
    { "Наименование": "ТУТнг 4/2 черная", "Группа": "ТУТ", "Ед. изм.": "м" },
    { "Наименование": "Бирка маркировочная TMS", "Группа": "бирка", "Ед. изм.": "шт" },
    { "Наименование": "Кембрик ПВХ 3 мм", "Группа": "кембрик", "Ед. изм.": "м" },
    { "Наименование": "Стяжка 100", "Группа": "стяжка", "Ед. изм.": "шт" },
  ]);
  expect(mats.map((mat) => mat.name)).toEqual(["Бирка маркировочная TMS", "Кембрик ПВХ 3 мм", "ТУТнг 4/2 черная"]);
  expect(markMaterialSource("бирка маркировочная  tms", mats, [])).toBe("ref");
  expect(markMaterialSource("Маркировка", mats, [{ art: "Маркировка" }])).toBe("kit");
  expect(markMaterialSource("Самоклейка неведомая", mats, [])).toBeNull();
  expect(markMaterialSource("", mats, [])).toBe("ref");
});

it("«Маркировать изделие»: метка на изделие с хлястиком, место «на изделии» уходит в операцию по-русски", () => {
  const row = productMarkRow("КБ1");
  expect(row).toMatchObject({ target: "Изделие КБ1", text: "КБ1", material: "Хлястик", place: "item", qty: "1" });
  expect(markPlaceText(row)).toBe("на изделии");
  const [spec] = markSpecs([row], []);
  expect(spec).toMatchObject({ art: "Хлястик", name: "Хлястик", place: "на изделии", placeKind: "item", qty: 1 });
  const op = { type: "insMark", marks: [spec] };
  expect(markRowsFromConfig({ ops: [op] })[0]).toMatchObject({ place: "item", material: "Хлястик", target: "Изделие КБ1" });
});

it("вид маркировки Термотрубка / Хлястик не считается «нет в справочнике»", () => {
  expect(markMaterialSource("Термотрубка", [], [])).toBe("ref");
  expect(markMaterialSource("Хлястик", [], [])).toBe("ref");
  expect(markMaterialSource("Самоклейка неведомая", [], [])).toBeNull();
});

it("tube length goes to the operation as a number (comma accepted), only for tubes and only when > 0; saved config gives it back", () => {
  const specs = markSpecs([
    newMarkRow({ text: "X1", material: "Термотрубка", len: "25,5" }), newMarkRow({ text: "X2", material: "Термотрубка", len: "0" }),
    newMarkRow({ text: "X3", material: "Термотрубка" }), newMarkRow({ text: "КБ1", material: "Хлястик", len: "30" }),
  ], []);
  expect(specs.map((spec) => spec.lengthMm)).toEqual([25.5, undefined, undefined, undefined]);
  expect("lengthMm" in specs[1]).toBe(false);
  expect(markRowsFromConfig({ ops: [{ type: "insMark", marks: [specs[0], specs[2]] }] }).map((row) => row.len ?? "")).toEqual(["25,5", ""]);
});

it("«Сложная сборка»: a branch's marking tube is put on and shrunk by its own operations; a connector without a label is named by its marking", () => {
  // 713К.4 КБ1 (владелец 16.10): у разъёма «A6» нет подписи — ветвь и её операции называет надпись маркировки.
  const S0 = fullHarness();
  const a4 = S0.endpoints.filter((e) => e.side === "A")[3];
  const S = { ...S0, endpoints: S0.endpoints.map((e) => (e.id === a4.id ? { ...e, label: "" } : e)),
    markRows: S0.markRows.map((row) => (row.text === "A6" ? { ...row, target: "Разъём А4 (SMR-04V-B)" } : row)) };
  const gen = makeGen(DSA, S);
  const ops = gen.getActiveOps().filter((o) => o.pf === "pf-A4");
  expect(ops.map((o) => o.label)).toEqual(["Монтаж контактов в разъём X4 «A6» — SMR-04V-B", "Надеть оплётку X4 «A6» — XP-3, 120 мм",
    "Надеть маркировку X4 «A6» — «A6»", "Усадка маркировки X4 «A6» — «A6»"]);
  expect(ops.filter((o) => o.markTube).map((o) => [o.opKey, o.tmplHint, o.tutCount])).toEqual([["onMarkA4", "INS_TUT", 1], ["heatMarkA4", "HEAT_TYT_short", 1]]);
  expect(gen.pfGraph()!.blocks.find((b) => b.key === "pf-A4")!.name).toBe("Ветвь X4 «A6», сторона А");
});

it("«По разъёмам» on 713К.4 КБ1: each mark gets its own kit tube by the quoted text, 15 mm by default", () => {
  const gen = makeGen(DSA, fullHarness());
  const rows = markRowsByConnectors(gen.markConnectors_(), "Маркировка \"5\" 4/2мм", gen.markComps_());
  expect(rows.map((row) => [row.text, row.material, row.len])).toEqual([
    ["5", "Маркировка \"5\" 4/2мм", "15"], ["I3", "Маркировка \"I3\" 4/2мм", "15"], ["C", "Маркировка \"C\" 4/2мм", "15"],
    ["A6", "Маркировка \"A6\" 4/2мм", "15"], ["В1", "Термотрубка", "15"], ["В2", "Термотрубка", "15"],
  ]);
  // Метки состава без надписи — как раньше: один материал на все строки, у бирки длины нет.
  const tags = markRowsByConnectors([{ name: "X1", art: "DB-9F" }], "Бирка ТМ", [{ art: "Бирка ТМ", name: "Бирка ТМ" }]);
  expect(tags.map((row) => [row.material, row.len])).toEqual([["Бирка ТМ", undefined]]);
});
