// @vitest-environment node
import { expect, it } from "vitest";
import golden from "./fixtures/gen713k4Rev17.json";
import { restoreFromSnapshot, snapshotFromConfig } from "./genBuildState";
import { ensureSeparateLengths, initState, makeGen, type Comp, type GenData } from "./tcGenLogic";
import { DSA } from "./tcGenRealHarness.fixture";
import { etkCutLengths, etkDrawWires } from "./etkDraw";

/*
 * 713К.4 КБ1 с прода (техкарта ревизия 17, 17.10): снимок формы `build_config.gen_state`, состав набора и то, что из них
 * собрала сборка — операции (подписи, карты библиотеки) и схема ПФ (16 ПФ). Генератор должен пересобрать то же самое
 * из снимка: всё, что владелец набрал в форме, повторяется без ручного порядка операций и без ручного выбора карт.
 * Справочники (карты библиотеки, БД.ТЕР, БД.ОП) — как в рабочей базе (tcGenRealHarness.fixture.ts).
 */
const data: GenData = { ...DSA, components: golden.components as Comp[] };
const snap = snapshotFromConfig({ gen_state: golden.gen_state })!;

function rebuilt() {
  const { S } = restoreFromSnapshot(data.components, initState(data), snap);
  const gen = makeGen(data, ensureSeparateLengths(S));
  const stuck = gen.stickTemplates();
  return makeGen(data, stuck ? { ...ensureSeparateLengths(S), selectedTemplates: stuck } : ensureSeparateLengths(S));
}

it("the prod snapshot carries no hand-set operation order and no hand-picked cards", () => {
  expect(snap.S.opOrder ?? []).toEqual([]);
  expect(snap.S.manualTemplates ?? []).toEqual([]);
});

it("rebuilds prod rev 17 from its form snapshot: the same 44 operations, labels and library cards", () => {
  const cfg = rebuilt().buildConfig() as { ops: Array<{ opKey: string; type: string; label: string; templateId: string; opNum: string }> };
  const pick = (o: { opKey: string; type: string; label: string; templateId: string }) => `${o.opKey} | ${o.type} | ${o.label} | ${o.templateId}`;
  expect(cfg.ops).toHaveLength(golden.expected.ops.length);
  expect(cfg.ops.map(pick)).toEqual(golden.expected.ops.map(pick));
  // Код карты операции: у прода — из БД.ОП (opNum), здесь — из заголовка выбранной карты библиотеки.
  const code = (id: string) => String(data.templates.find((t) => t.id === id)?.title ?? "").split("|")[0].trim();
  // Карта маркировочной трубки переименована 25.10 (INS_MARK_TUBE_AUTO → PRINT_MARK_TUBE, id шаблона тот же): снимок прода хранит прежний код.
  const renamed = (num: string) => (num === "INS_MARK_TUBE_AUTO" ? "PRINT_MARK_TUBE" : num);
  expect(cfg.ops.map((o) => code(o.templateId))).toEqual(golden.expected.ops.map((o) => renamed(o.opNum)));
});

it("the X5/X6 cut batch keeps every wire's length: 180 мм ×3 and 200 мм ×1 on the ETK sketch", () => {
  // Владелец 17.10 (ПФ 1.5, оп. 05): эскиз показывал одну «L = 180 мм» на все 4 провода, «тут один чёрный 200 мм».
  const cfg = rebuilt().buildConfig() as { ops: Array<{ type: string; label: string; opNum: string; side?: string; wires: Array<{ art: string; length: number; qty: number }>;
    strip: unknown }> };
  const op = cfg.ops.find((o) => o.label.startsWith("Резка с обжимом В (T-DS1071-SC600)"))!;
  expect(op.wires.map((w) => [w.art, w.length])).toEqual([
    ["НВ4 0,2 красный", 180], ["НВ4 0,2 красный", 180], ["НВ4 0,2 черный", 180], ["НВ4 0,2 черный", 200]]);
  expect(etkCutLengths({ wires: op.wires })).toEqual([{ length: 180, count: 3 }, { length: 200, count: 1 }]);
  const sketch = etkDrawWires({ type: op.type, code: op.opNum, side: op.side, fields: { wires: op.wires, strip: op.strip, cutTolerance: "±3 мм" } });
  expect(sketch).toContain("L = 180 мм ±3 мм · 3 шт.");
  expect(sketch).toContain("L = 200 мм ±3 мм · 1 шт.");
});

it("rebuilds prod rev 17's ПФ schema: 16 ПФ with the same names, operations and links", () => {
  const route = (rebuilt().buildConfig() as { route: { source: string; blocks: Array<{ key: string; name?: string; ops: number[]; kit?: boolean; final: boolean }>;
    edges: unknown[] } }).route;
  expect(route.source).toBe(golden.expected.route.source);
  expect(route.blocks).toHaveLength(16);
  expect(route.blocks.map((b) => ({ key: b.key, name: b.name, ops: b.ops, kit: !!b.kit, final: !!b.final }))).toEqual(golden.expected.route.blocks);
  expect(route.edges).toEqual(golden.expected.route.edges);
});
