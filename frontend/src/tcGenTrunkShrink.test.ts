// @vitest-environment node
import { expect, it } from "vitest";
import { addHsRow, makeGen, REF_TUBE_, removeHsRow, setHsRow, type GenData, type GenState } from "./tcGenLogic";
import { DSA, fullHarness, harness } from "./tcGenRealHarness.fixture";

/*
 * «Термоусадка на жгут» (владелец 17.10, 713К.4 КБ1: «что всё, что мы делали, я смогу повторить сам»). Строки засеваются
 * из состава при первом открытии — все ТТК набора, по длине из состава. ТТК 8/2 владелец потом ставит поверх разъёмов
 * X1–X3, ТТК 3/1 — на сростки; засеянные строки этих трубок на ствол не идут (раньше резался лишний «ТТК 8/2 1×100 мм» и
 * усадка на жгуте была на 3 шт вместо 2). Строку, которую владелец задал сам, генератор считает всегда.
 */
const labels = (S: GenState, data: GenData = DSA) => makeGen(data, S).getActiveOps().map((o) => o.label);
const row = (S: GenState, compId: string) => S.hs.rows.findIndex((r) => r.compId === compId);

it("seeds a row per kit tube, marked as the seed", () => {
  const S = harness(DSA);
  expect(S.hs.rows.map((r) => `${r.compId} ${r.len}×${r.cnt} ${r.auto}`)).toEqual(["ttk3 60×1 true", "ttk8 100×1 true", "ttk12 50×1 true"]);
});

it("a seeded tube already over the connectors or on the splices stays off the trunk; the owner's ТТК 12/4 goes on", () => {
  const S = fullHarness();
  const gen = makeGen(DSA, S);
  expect(S.hs.rows.map((r) => [r.compId, gen.hsRowSkip_(r)])).toEqual([["ttk3", "уже на сростках"], ["ttk8", "уже на разъёмах"], ["ttk12", ""]]);
  const ops = labels(S);
  expect(ops).toContain("Резка трубок — ТТК 8/2 3×15 мм, ТТК 12/4 2×25 мм, ТТК 3/1 4×20 мм");
  expect(ops).toContain("Надеть трубки: ТТК 12/4мм, черная на жгут — 2 шт");
  expect(ops).toContain("Усадка термоусадки на жгуте — 2 шт");
  expect(ops.some((label) => /100 мм/.test(label))).toBe(false);
});

it("a row the owner sets counts even when the tube is on the connectors too", () => {
  const S0 = fullHarness();
  const S = setHsRow(S0, row(S0, "ttk8"), { len: "30", cnt: "1" });
  expect(makeGen(DSA, S).hsRowSkip_(S.hs.rows[row(S, "ttk8")])).toBe("");
  const ops = labels(S);
  expect(ops).toContain("Резка трубок — ТТК 8/2 3×15 мм, ТТК 8/2 1×30 мм, ТТК 12/4 2×25 мм, ТТК 3/1 4×20 мм");
  expect(ops).toContain("Усадка термоусадки на жгуте — 3 шт");
});

it("a row saved before 17.10 (no seed mark) keeps the old rule: a connector tube counts, the splice tube does not", () => {
  const S0 = fullHarness();
  const rows = S0.hs.rows.map(({ auto: _auto, ...r }) => (r.compId === "ttk12" ? r : { ...r, cnt: "1" }));
  const S = { ...S0, hs: { ...S0.hs, rows } };
  const gen = makeGen(DSA, S);
  expect(S.hs.rows.map((r) => gen.hsRowSkip_(r))).toEqual(["уже на сростках", "", ""]);
  expect(labels(S)).toContain("Усадка термоусадки на жгуте — 3 шт");
});

it("«Добавить трубку»: a kit tube or a БД.МАТ tube becomes the owner's row, «×» takes it off", () => {
  const data: GenData = { ...DSA, markMats: [{ name: "ТТК 16/4мм, черная", group: "ТТК", unit: "м" }, { name: "ТТК 8/2мм, черная", group: "ТТК", unit: "м" },
    { name: "Бирка ТМ", group: "Маркировка", unit: "шт" }] };
  let S: GenState = { ...fullHarness(), hs: { ...fullHarness().hs, rows: [] } };
  const options = makeGen(data, S).hsTubeOptions_();
  expect(options.kit.map((c) => c.id)).toEqual(["ttk3", "ttk8", "ttk12"]);
  // Трубка, что есть в составе, второй раз из БД.МАТ не предлагается; бирки — не трубки.
  expect(options.ref.map((m) => m.name)).toEqual(["ТТК 16/4мм, черная"]);
  S = addHsRow(S, `${REF_TUBE_}ТТК 16/4мм, черная`);
  S = setHsRow(S, 0, { len: "40" });
  expect(S.hs.rows).toEqual([{ compId: `${REF_TUBE_}ТТК 16/4мм, черная`, len: "40", cnt: "1", auto: false }]);
  expect(makeGen(data, S).hsTubeOptions_().ref).toEqual([]);
  expect(labels(S, data)).toContain("Надеть трубки: ТТК 16/4мм, черная на жгут — 1 шт");
  S = removeHsRow(S, 0);
  expect(labels(S, data).some((label) => /на жгут/.test(label))).toBe(false);
});
