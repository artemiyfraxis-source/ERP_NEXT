// @vitest-environment node
import { describe, expect, it } from "vitest";
import { autoConfig, generatorData, refsFromRecords, type BatchBom, type BatchRefs } from "./tcGenBatch";
import { initState, makeGen } from "./tcGenLogic";

// Прогон 25.10 по 95 производственным сборкам без техкарт (tcGenBatch.autoConfig — то же, что «Создать»): состав
// приходит как GET /api/techcards/kitsets/{project}/bom/{index}, со справочным названием `title` у каждой позиции.
const REFS: BatchRefs = { templates: [], op: [], ter: [], coax: [], wire_dia: [], kab_coax: [] };
const comp = (id: string, type: string, art: string, qty: number | string, title = "") => ({ id, type, name: art, art, qty, ...(title ? { title } : {}) });
const bom = (name: string, components: BatchBom["components"], calc_ops: BatchBom["calc_ops"] = []): BatchBom =>
  ({ index: "КБ1", name, run: "1", part_qty: "1", components, calc_ops });
const run = (b: BatchBom, refs: BatchRefs = REFS) => {
  const D = generatorData(refsFromRecords(refs), b);
  return { D, S: initState(D), ...autoConfig(D) };
};
/** Конфиг и предупреждения без выбора шаблонов (справочник шаблонов в тестах пуст). */
const built = (b: BatchBom, refs: BatchRefs = REFS) => {
  const { D, S } = run(b, refs);
  const g = makeGen(D, S), cfg = g.buildConfig();
  return { S, cfg, warnings: g.bomWarnings_(cfg) };
};

describe("название из справочника — запасной ход при распознавании", () => {
  const usb = bom("Кабельная сборка USB-AM to MicroUSB-m 100mm", [
    comp("k0", "Соед", "DS1107-W", 1, "USB-A вилка под пайку"),
    comp("k1", "Соед", "microUSB 5BM", 1, "Корпус micro-USB B 5 конт."),
    comp("k2", "Провод", "CCCU-4G", 0.11),
  ]);

  it("USB-A вилка с артикулом DS1107-W паяется — по названию «USB-A вилка под пайку»", () => {
    const { S } = run(usb);
    const ep = S.endpoints.find((e) => e.connId === "k0")!;
    expect(ep.method).toBe("solder");
    expect(ep.sdrTmpl).toBe("SDR_USB_A_2.0");
  });

  it("без названия артикул не опознан — как было (название только запасной ход)", () => {
    const bare = bom(usb.name, usb.components.map(({ title: _title, ...rest }) => rest));
    expect(run(bare).S.endpoints.find((e) => e.connId === "k0")!.method).toBe("crimp");
  });

  it("артикул сильнее названия", () => {
    const b = bom(usb.name, [comp("k0", "Соед", "GX12-4", 1, "USB-A вилка под пайку"), usb.components[2]]);
    expect(run(b).S.endpoints.find((e) => e.connId === "k0")!.sdrTmpl).toBe("SDR_GX12");
  });

  it("наконечник без «НШВИ» в артикуле опознан по названию «наконечник втулочный»", () => {
    const b = bom("Кабельная сборка X", [comp("a", "Др", "КВТ-77", 2, "наконечник втулочный"), comp("w", "Провод", "ПВАМ 0,35", 0.3)]);
    const { D, S } = run(b);
    expect(makeGen(D, S).bomLists().terms.map((c) => c.id)).toEqual(["a"]);
  });
});

describe("коаксиал: длина кабеля без длины в наименовании", () => {
  const parts = [
    comp("k0", "Соед", "T-A112/5D", 1, "RP-TNC-male 5D-FB"), comp("k1", "Соед", "T-212/5D", 1, "TNC-female 5D-FB"),
    comp("k2", "Провод", "5D-FB", "1.05", "Кабель 5D-FB"), comp("k3", "ТУТ", "ТУТ 10/5мм, черная", "0.06"),
  ];
  const NAME = "Кабельная сборка RP-TNC-Male to TNC-Female 5D-FB";
  const cutRow = (l: string, req: string) => ({ code: "CUT_COAX+2strip", comp: "5D-FB", n: "1", l, req });
  const coaxRefs: BatchRefs = { ...REFS, coax: [
    { "Артикул": "T-A112/5D", "Тип/Серия": "RP-TNC", "Провод": "5D-FB", "L+": "10" },
    { "Артикул": "T-212/5D", "Тип/Серия": "TNC", "Провод": "5D-FB", "L+": "10" },
  ] };
  const coaxOf = (cfg: Record<string, unknown>) => (cfg.coax || {}) as { cableLength?: unknown };

  it("447К.9 КБ2: длина резки — из строки CUT_COAX расчёта, L+ не прибавляется", () => {
    const r = built(bom(NAME, parts, [cutRow("1,05", "L=1050мм")]), coaxRefs);
    expect(coaxOf(r.cfg).cableLength).toBe(1050);
    expect(r.S.coax.len).toBe("1050");
    expect(r.warnings).toContain("Длина кабеля взята из расчёта (в наименовании сборки её нет) — сверьте с КД.");
    expect(r.warnings.join("|")).not.toContain("Не указана длина кабеля");
  });

  it("расчёт сильнее состава: резка 1000 мм при метраже 1,05 м — берётся 1000", () => {
    expect(built(bom(NAME, parts, [cutRow("1", "L=1000мм")]), coaxRefs).S.coax.len).toBe("1000");
  });

  it("расчёта нет — метраж кабеля в составе: 1,05 м → 1050 мм (800К. КБ1: 10,05 м → 10050)", () => {
    const r = built(bom(NAME, parts), coaxRefs);
    expect(coaxOf(r.cfg).cableLength).toBe(1050);
    expect(r.warnings).toContain("Длина кабеля взята из состава (в наименовании сборки её нет) — сверьте с КД.");
    const long = built(bom(NAME, parts.map((c) => (c.id === "k2" ? { ...c, qty: "10.05" } : c))), coaxRefs);
    expect(long.S.coax.len).toBe("10050");
  });

  it("длина есть в наименовании — как раньше: длина + L+ разъёмов, без предупреждения", () => {
    const r = built(bom(NAME + " 1m", parts, [cutRow("5", "L=5000мм")]), coaxRefs);
    expect(Number(r.S.coax.len)).toBeGreaterThan(1000);
    expect(r.S.coax.len).not.toBe("5000");
    expect(r.S.coaxLenFrom).toBeUndefined();
    expect(r.warnings.join("|")).not.toContain("Длина кабеля взята");
  });
});

describe("наконечники на свободных концах, а не гильзы сростков", () => {
  const labels = (b: BatchBom) => { const r = built(b); const g = makeGen(run(b).D, r.S); return { r, ops: g.getActiveOps() }; };

  it("533К.13 КБ1: НШВИ 0.34-8 ×2 обжимаются на свободных концах (В), предупреждения про сростки нет", () => {
    const b = bom("Кабельная сборка CCW 0.4", [
      comp("k0", "Соед", "XHP-2", 1), comp("k1", "Терминал", "SXH-001T-P0.6", 2), comp("k2", "Провод", "ПВВС 2x0,35", "0.45"),
      comp("k3", "ТУТ", "ТУТ 6/3мм, черная", "0.08"), comp("k4", "Др", "НШВИ 0.34-8", 2, "наконечник втулочный"),
    ]);
    const { r, ops } = labels(b);
    expect(r.S.endpoints.map((e) => [e.side, e.connId, e.termId])).toEqual([["A", "k0", "k1"], ["B", "", "k4"]]);
    expect(ops.filter((o) => o.type === "prsTerm").map((o) => o.label)).toEqual(["Опрессовка терминалов А1", "Опрессовка наконечников В1"]);
    expect(r.warnings.join("|")).not.toContain("Гильза");
  });

  it("663К.1 КБ10: паяный разъём А не берёт наконечник терминалом; НШВИ, РПИ-П, РПИ-М — свои точки на свободных концах", () => {
    const b = bom("Кабельная сборка БЦТС.002.01.03400.222.00", [
      comp("k0", "Др", "НШВИ 0.5-8", 2), comp("k1", "Др", "РПИ-П 1.5-(4.8)", 1), comp("k2", "Др", "РПИ-М 1.5-(4.8)", 1),
      comp("k3", "Соед", "SA812/S4", 1), comp("k4", "Провод", "ПВАМ 0,35 красный", "0.2"), comp("k5", "Провод", "ПВАМ 0,35 черный", "0.4"),
    ]);
    const { r, ops } = labels(b);
    expect(r.S.endpoints.map((e) => [e.side, e.connId, e.termId])).toEqual([["A", "k3", ""], ["B", "", "k0"], ["B", "", "k1"], ["B", "", "k2"]]);
    expect(ops.filter((o) => o.type === "prsTerm").map((o) => o.label)).toEqual(
      ["Опрессовка наконечников В1", "Опрессовка наконечников В2", "Опрессовка наконечников В3"]);
    expect(ops.some((o) => o.type === "solderConn")).toBe(true);
  });

  it("с разъёмом на обеих сторонах свободных концов нет — лишних точек не появляется", () => {
    const b = bom("Кабель X", [comp("k0", "Соед", "XHP-2", 2), comp("k1", "Терминал", "SXH-001T-P0.6", 4), comp("k2", "Провод", "ПВАМ 0,35", "0.3"),
      comp("k3", "Др", "НШВИ 0.34-8", 2)]);
    expect(run(b).S.endpoints.every((e) => e.connId)).toBe(true);
  });

  it("сростки есть — НШВИ по-прежнему гильза сростка", () => {
    const b = bom("Кабель X", [comp("k0", "Соед", "XHP-2", 1), comp("k1", "Терминал", "SXH-001T-P0.6", 2), comp("k2", "Провод", "ПВАМ 0,35", "0.3"),
      comp("k3", "Др", "НШВИ 0.34-8", 2)]);
    const { D, S } = run(b);
    expect(makeGen(D, S).spliceSleeveComps_()).toEqual([]);
    const spliced = { ...S, spl: { ...S.spl, enabled: true, count: "2" } };
    expect(makeGen(D, spliced).spliceSleeveComps_().map((c) => c.art)).toEqual(["НШВИ 0.34-8"]);
  });
});
