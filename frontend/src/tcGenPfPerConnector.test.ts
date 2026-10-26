// @vitest-environment node
import { expect, it } from "vitest";
import { addEndpoint, complexByHand, initState, makeGen, setSplMethod, toggleLengthMode, updateEndpoint, type Comp, type GenData, type GenState } from "./tcGenLogic";
import { assignPinJoined, joinSplice } from "./tcGenSplices";
import { newMarkRow } from "./tcGenMarks";
import { DSA, end, id } from "./tcGenRealHarness.fixture";

/*
 * «Сложная сборка», жгут без ветвей (владелец 26.10, 708К.5 КБ1 «Жгут подключения Orange Pi + FAN»): каждый разъём — свой ПФ
 * «Разъём X собран» (обжим → монтаж → своя усадка и метки), сростки — свой ПФ, в изделии остаётся сборка (лента на ствол, метки на
 * изделии). Каждая метка по КД (хлястик) печатается в ПФ «Маркировка» и навешивается своей операцией «Монтаж маркировки» на месте метки.
 */
const TAPE: Comp = { id: "tape19", type: "Лента", name: "Лента тканевая 19мм x 15мм", art: "Лента тканевая 19мм x 15мм", qty: 1 };
// Карты библиотеки: монтаж хлястика (INS_MARK), печать на флажковом маркираторе (INS_MARK_AUTO), монтаж ленты (INS_WRAP).
const CARDS = [["ins-mark-хлястик", "INS_MARK | Монтаж маркировочного хлястика", "Монтаж"], ["ins-mark-auto-печать", "INS_MARK_AUTO | Печать хлястиков", "Монтаж маркировки"],
  ["ins-wrap-лента", "INS_WRAP | Монтаж ленты", "Монтаж"]].map(([id, title, category]) => ({ id, title, category }));
const D: GenData = { ...DSA, components: [...DSA.components, TAPE], templates: [...DSA.templates, ...CARDS] };

/** Три разъёма (XS1, XF1 — сторона А, XOP1 — В), два сростка С1 и С2, лента на ствол, четыре хлястика. */
function kb1(): GenState {
  let S = complexByHand(initState(D));
  S = { ...S, wrap: { enabled: true, slots: S.wrap.slots.map((slot, i) => (i === 0 ? { mat: "tape19", kind: "tape" as const, len: "480" } : { ...slot, mat: "" })) }, hs: { ...S.hs, enabled: false } };
  S = addEndpoint(S, "A");
  const [xs1, xf1, xop1] = [id(S, "A", 1), id(S, "A", 2), id(S, "B", 1)];
  [[xs1, "csmr4", "XS1"], [xf1, "csmr2", "XF1"], [xop1, "c01", "XOP1"]].forEach(([ep, connId, label]) => {
    S = updateEndpoint(D, S, ep, { connId }); S = updateEndpoint(D, S, ep, { label });
  });
  S = toggleLengthMode({ ...S, wires: [["w2", 200], ["g2", 200], ["y2", 200], ["r2", 200], ["k2", 200]].map(([compId, length], idx) => ({ idx, compId: compId as string, length: length as number, qty: 1 })), nextWireIdx: 5 }, 0);
  S = assignPinJoined(D, S, xs1, 0, 0, 1); S = assignPinJoined(D, S, xop1, 0, 0, 1);
  S = assignPinJoined(D, S, xs1, 1, 0, 2); S = assignPinJoined(D, S, xf1, 2, 0, 1);
  S = assignPinJoined(D, S, xs1, 3, 0, 3); S = assignPinJoined(D, S, xf1, 4, 0, 2);
  S = joinSplice(D, S, end(1, 0, "B"), "new"); S = joinSplice(D, S, end(2, 0, "B"), S.splices!.at(-1)!.id);
  S = joinSplice(D, S, end(3, 0, "B"), "new"); S = joinSplice(D, S, end(4, 0, "B"), S.splices!.at(-1)!.id);
  S = setSplMethod(S, "ultrasonic");
  const mark = (target: string, text: string, place: "dist" | "item") => newMarkRow({ target, text, material: "Хлястик", place, mm: place === "dist" ? "50" : "", qty: "1" });
  return { ...S, mark: true, markRows: [
    mark("Разъём XOP1 (I-DS1071-SCB01X3)", "XOP1", "dist"), mark("Разъём XS1 (SMR-04V-B)", "XS1", "dist"),
    mark("Разъём XF1 (SMR-02V-B)", "XF1", "dist"), mark("Изделие КБ1", "КБ1", "item"),
  ] };
}
type Route = { blocks: Array<{ key: string; name?: string; ops: number[]; final: boolean; kit?: boolean }>; edges: Array<{ from: string; to: string }> };
const view = (S: GenState) => {
  const gen = makeGen(D, S);
  const ops = gen.getActiveOps();
  const route = (gen.buildConfig() as { route: Route }).route;
  // Номера операций в блоках — по операциям с картой (без карты операции в сборке нет).
  const carded = ops.filter((o) => gen.templateIdFor(o));
  return { ops, route, of: (key: string) => route.blocks.find((b) => b.key === key)!.ops.map((p) => carded[p - 1].opKey) };
};

it("708К.5-like: each connector is its own ПФ, splices keep theirs, the product keeps only assembly ops", () => {
  const { ops, route, of } = view(kb1());
  const names = Object.fromEntries(route.blocks.map((b) => [b.key, b.name]));
  expect(names["pf-A1"]).toBe("Разъём X1 «XS1» собран");
  expect(names["pf-A2"]).toBe("Разъём X2 «XF1» собран");
  expect(names["pf-B1"]).toBe("Разъём X3 «XOP1» собран");
  expect(route.blocks.filter((b) => b.key.startsWith("pf-spl")).length).toBe(1);
  // Опрессовка и монтаж — в блоке своего разъёма, а не кучей в изделии.
  for (const key of ["pf-A1", "pf-A2", "pf-B1"]) expect(of(key).some((k) => k.startsWith("insTerm"))).toBe(true);
  const finalOps = of("pf-final");
  expect(finalOps.some((k) => k.startsWith("insTerm") || k.startsWith("prsTerm"))).toBe(false);
  expect(finalOps).toEqual(expect.arrayContaining(["insWrap", "insMark"]));
  expect(ops.filter((o) => o.pf === "pf-final").length).toBe(finalOps.length);
  // Провод — из резки в блок разъёма, оттуда в сросток, из сростка в изделие.
  const pairs = route.edges.map((e) => `${e.from}>${e.to}`);
  expect(pairs).toEqual(expect.arrayContaining(["pf-A1>pf-spl1", "pf-A2>pf-spl1", "pf-spl1>pf-final", "pf-B1>pf-final"]));
});

it("708К.5-like: every хлястик is printed in «Маркировка» and has its own install op at its connector or on the product", () => {
  const { ops, route, of } = view(kb1());
  const print = ops.find((o) => o.opKey === "printMarks")!;
  expect(print).toMatchObject({ type: "insMark", pf: "pf-mark", markPhase: "print", label: "Печать маркировки — «XOP1», «XS1», «XF1», «КБ1»" });
  expect(print.marks).toHaveLength(4);
  expect(route.blocks.find((b) => b.key === "pf-mark")!.kit).toBe(true);
  expect(of("pf-A1")).toContain("insMarkA1");
  expect(of("pf-A2")).toContain("insMarkA2");
  expect(of("pf-B1")).toContain("insMarkB1");
  expect(of("pf-final")).toContain("insMark");
  const installs = ops.filter((o) => o.markPhase === "install");
  expect(installs.map((o) => [o.pf, o.marks!.length])).toEqual([["pf-A1", 1], ["pf-A2", 1], ["pf-B1", 1], ["pf-final", 1]]);
  expect(installs.map((o) => o.label)).toEqual([
    "Монтаж маркировки X1 «XS1» — «XS1»", "Монтаж маркировки X2 «XF1» — «XF1»", "Монтаж маркировки X3 «XOP1» — «XOP1»", "Монтаж маркировки на изделие — «КБ1»"]);
  // Каждая метка встала ровно один раз.
  expect(installs.flatMap((o) => o.marks!.map((m) => ("text" in m ? m.text : ""))).sort()).toEqual(["XF1", "XOP1", "XS1", "КБ1"]);
  // Печать — раньше монтажа, стрелки из «Маркировки» — в блоки мест.
  expect(ops.indexOf(print)).toBeLessThan(ops.findIndex((o) => o.markPhase === "install"));
  expect(route.edges.filter((e) => e.from === "pf-mark").map((e) => e.to).sort()).toEqual(["pf-A1", "pf-A2", "pf-B1", "pf-final"]);
  // В конфиге — фаза и свои метки; ключи ЭТК различны (ветвь в ключе).
  const cfg = makeGen(D, kb1()).buildConfig() as { ops: Array<Record<string, unknown>> };
  expect(cfg.ops.filter((o) => o.type === "insMark").map((o) => [o.markPhase, o.branch])).toEqual([
    ["print", "печать"], ["install", "X1 «XS1»"], ["install", "X2 «XF1»"], ["install", "X3 «XOP1»"], ["install", "на изделие"]]);
});

it("a simple two-connector cable without splices keeps one product block: nothing changes", () => {
  let S = complexByHand(initState(D));
  const [a, b] = [id(S, "A", 1), id(S, "B", 1)];
  S = updateEndpoint(D, S, a, { connId: "csmr2" }); S = updateEndpoint(D, S, b, { connId: "c01" });
  S = toggleLengthMode({ ...S, wires: [{ idx: 0, compId: "w2", length: 200, qty: 1 }], nextWireIdx: 1 }, 0);
  S = assignPinJoined(D, S, a, 0, 0, 1); S = assignPinJoined(D, S, b, 0, 0, 1);
  const { route } = view(S);
  expect(route.blocks.some((x) => /собран$/.test(x.name || ""))).toBe(false);
});

it("branches are not touched: a harness with a branch keeps its «Ветвь» block and the other connectors stay in the product", () => {
  const S = kb1();
  const withBranch = { ...S, endpoints: S.endpoints.map((e) => (e.side === "A" && e.label === "XS1" ? { ...e, shrinkOn: true, shrinkMat: "ttk8", shrinkLen: "15" } : e)) };
  const { route } = view(withBranch);
  const names = route.blocks.map((b) => b.name);
  expect(names).toContain("Ветвь X1 «XS1», сторона А");
  expect(names.some((n) => /собран$/.test(n || ""))).toBe(false);
});
