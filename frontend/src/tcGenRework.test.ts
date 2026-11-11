import { describe, expect, it } from "vitest";
import { emptyRework, emptyState, makeGen, type GenData, type ReworkState } from "./tcGenLogic";
import { newMarkRow } from "./tcGenMarks";

const D: GenData = {
  components: [{ id: "incoming", art: "ДИ-1", name: "Давальческое изделие", type: "Изделие", qty: 2.5 },
    { id: "unused", art: "XHP-2", name: "Разъём", type: "Соед", qty: 20 }],
  assemblyInfo: { "Индекс": "КБ13", "Наименование": "Доработанное изделие" }, partQty: 99,
  templates: ["CUT_WIRE_hand", "STRIP_WIRE", "SDR_tin", "INS_MARK"].map((code) => ({ id: code, title: `${code} | Операция`, category: "Операция" })),
  ops: [{ opNumber: "CUT_WIRE_hand", opName: "Резка", tOp: "6", tPrep: "30", tMachine: "" }],
  terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
const state = (patch: Partial<ReworkState> = {}) => ({ ...emptyState(), tab: "rework" as const,
  rework: { ...emptyRework(), compId: "incoming", length: "250", quantity: "10", ...patch } });
const build = (patch: Partial<ReworkState> = {}) => makeGen(D, state(patch)).validate();

describe("customer-supplied product rework", () => {
  it("cuts one incoming product and counts finished pieces independently of the kit batch", () => {
    const { cfg, error } = build();
    expect(error).toBe("");
    expect(cfg).toMatchObject({ mode: "rework", tab: "rework", partQty: 10,
      rework: { input: { id: "incoming", art: "ДИ-1", unit: "М." }, cutLength: 250 },
      wires: [{ art: "ДИ-1", qty: 1, length: 250 }], ops: [{ type: "cutWire", tOp: "6" }] });
    expect((cfg.ops as unknown[])).toHaveLength(1);
    expect(cfg).not.toHaveProperty("connections");
  });
  it.each(["", "0", "-1", "NaN", "Infinity", "250мм", "1e3"])("rejects length %s", (length) => {
    expect(build({ length }).error).toContain("длину реза");
  });
  it.each(["", "0", "-1", "1.5", "Infinity", "9007199254740992"])("rejects quantity %s", (quantity) => {
    expect(build({ quantity }).error).toContain("количество");
  });
  it("accepts decimal commas and refuses missing product, oversized strip and missing templates", () => {
    expect(build({ length: "250,5" }).cfg.rework).toMatchObject({ cutLength: 250.5 });
    expect(build({ compId: "gone" }).error).toContain("входное");
    expect(build({ treatment: "strip", ends: "AB", stripLen: "126" }).error).toContain("зачистки");
    expect(makeGen({ ...D, templates: D.templates.slice(0, 1) }, state({ treatment: "tin", stripLen: "5" })).validate().error).toContain("каждой операции");
  });
  it("adds only selected end treatments, leaving cutting and marking unchanged", () => {
    const common: Partial<ReworkState> = { treatment: "tin", stripLen: "5", mark: true,
      markRows: [newMarkRow({ text: "ДИ-1", material: "Бирка", place: "item" })] };
    const one = build(common).cfg.ops as Array<Record<string, unknown>>;
    const both = build({ ...common, ends: "AB" }).cfg.ops as Array<Record<string, unknown>>;
    expect(one.map((op) => op.type)).toEqual(["cutWire", "stripCable", "tin", "insMark"]);
    expect(both.map((op) => op.type)).toEqual(["cutWire", "stripCable", "tin", "stripCable", "tin", "insMark"]);
    expect(both[0]).toEqual(one[0]);
    expect(both.at(-1)).toEqual(one.at(-1));
    expect(both.slice(1, 3)).toEqual(one.slice(1, 3));
  });
  it("requires complete marking and keeps unrelated saved manufacturing settings out", () => {
    expect(build({ mark: true }).error).toContain("метки");
    const s = state();
    s.twist.enabled = true; s.spl.enabled = true; s.mark = true; s.hs.enabled = true;
    s.opOrder = ["reworkTinA", "reworkCut"];
    expect(makeGen(D, s).getActiveOps().map((op) => op.type)).toEqual(["cutWire"]);
    const legacy = { ...s, tab: "inter" as const };
    expect(makeGen(D, legacy).buildConfig()).toEqual(makeGen(D, { ...legacy, rework: undefined }).buildConfig());
  });
  it("rejects a saved combined manufacturing card for the cut-only choice", () => {
    const s = state();
    s.selectedTemplates.reworkCut = "combined";
    s.manualTemplates = ["reworkCut"];
    const templates = [...D.templates, { id: "combined", title: "CUT_WIRE+2strip | Резка с разделкой", category: "Резка" }];
    expect(makeGen({ ...D, templates }, s).validate().error).toContain("не соответствует");
  });
});
