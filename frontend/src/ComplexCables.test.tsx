import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ComplexCables } from "./ComplexCables";
import { duplicateWire, ensureSeparateLengths, fillLengthBelow, initState, makeGen, switchGenTab, type GenData, type GenState, type WireEntry } from "./tcGenLogic";

const D: GenData = {
  components: [
    { id: "w1", type: "Провод", name: "МГТФ 0,12 красный", art: "МГТФ 0,12 красный", qty: 0.3 },
    { id: "w2", type: "Провод", name: "МГТФ 0,12 черный", art: "МГТФ 0,12 черный", qty: 0.3 },
    { id: "w3", type: "Провод", name: "МГТФ 0,12 белый", art: "МГТФ 0,12 белый", qty: 0.3 },
  ],
  assemblyInfo: {}, templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, calcOps: [],
};
const row = (idx: number, compId: string, length: number, qty = 1): WireEntry => ({ idx, compId, length, qty });
const stateOf = (wires: WireEntry[], extra: Partial<GenState> = {}): GenState =>
  ({ ...initState(D), tab: "complex", wires, nextWireIdx: wires.length, ...extra });
const lens = (S: GenState) => S.wires.map((w) => w.length);

afterEach(cleanup);

/** Рисует панель над настоящим состоянием, чтобы кнопки меняли его так же, как в генераторе. */
function Harness({ start, spy }: { start: GenState; spy?: (S: GenState) => void }) {
  const [S, setS] = useState(start);
  spy?.(S);
  return <ComplexCables wires={S.wires} anyCores={false} coresOf={() => 1} update={(fn) => setS((c) => fn(c))}
    select={(w, n) => <select aria-label={`Кабель ${n + 1}`} value={w.compId} onChange={() => undefined}><option value={w.compId}>{w.compId}</option></select>} />;
}

it("shows a length field in every row and no common field or toggle", () => {
  render(<Harness start={stateOf([row(0, "w1", 100), row(1, "w2", 0)])} />);
  expect(screen.getByLabelText("Длина кабеля 1")).toHaveValue(100);
  expect(screen.getByLabelText("Длина кабеля 2")).toHaveValue(null);
  expect(screen.queryByLabelText("Длина по чертежу")).toBeNull();
  expect(screen.queryByText(/раздельная длина/)).toBeNull();
});

it("«⧉» duplicates the row right below with the same cable, length and quantity", () => {
  let last!: GenState;
  render(<Harness start={stateOf([row(0, "w1", 120, 3), row(1, "w2", 50)])} spy={(S) => { last = S; }} />);
  fireEvent.click(screen.getByLabelText("Копировать кабель 1"));
  expect(last.wires.map((w) => [w.compId, w.length, w.qty])).toEqual([["w1", 120, 3], ["w1", 120, 3], ["w2", 50, 1]]);
  expect(new Set(last.wires.map((w) => w.idx)).size).toBe(3);
});

it("«Длину всем ниже» fills only empty rows below and leaves filled ones and rows above", () => {
  const S = fillLengthBelow(stateOf([row(0, "w1", 0), row(1, "w2", 200), row(2, "w3", 0), row(3, "w1", 70), row(4, "w2", 0)]), 1);
  expect(lens(S)).toEqual([0, 200, 200, 70, 200]);
  expect(lens(fillLengthBelow(stateOf([row(0, "w1", 0), row(1, "w2", 0)]), 0))).toEqual([0, 0]);
});

it("the «ниже» button is off when there is nothing to fill", () => {
  render(<Harness start={stateOf([row(0, "w1", 100), row(1, "w2", 90)])} />);
  expect(screen.getByLabelText("Длину всем ниже кабеля 1")).toBeDisabled();
});

it("Enter in a length moves focus to the next row's length", () => {
  render(<Harness start={stateOf([row(0, "w1", 100), row(1, "w2", 0), row(2, "w3", 0)])} />);
  screen.getByLabelText("Длина кабеля 1").focus();
  fireEvent.keyDown(screen.getByLabelText("Длина кабеля 1"), { key: "Enter" });
  expect(screen.getByLabelText("Длина кабеля 2")).toHaveFocus();
});

it("an old card with a common length gets it copied into every empty row on open", () => {
  const S = ensureSeparateLengths(stateOf([row(0, "w1", 0), row(1, "w2", 80), row(2, "w3", 0)], { advancedLength: false, drawingLength: 300, sharedLengthText: "300" }));
  expect(S.advancedLength).toBe(true);
  expect(lens(S)).toEqual([300, 80, 300]);
  // На других вкладках ничего не меняется.
  const inter = stateOf([row(0, "w1", 0)], { tab: "inter", advancedLength: false, drawingLength: 300 });
  expect(ensureSeparateLengths(inter)).toBe(inter);
  expect(ensureSeparateLengths(switchGenTab(inter, "complex")).wires[0].length).toBe(300);
});

it("the config cut lengths use each row's own length", () => {
  const S = stateOf([row(0, "w1", 100), row(1, "w2", 250, 2), row(2, "w3", 400)], { advancedLength: true });
  const wires = makeGen(D, S).buildConfig().wires as Array<{ art: string; length: number; qty: number }>;
  expect(wires.map((w) => [w.art, w.length, w.qty])).toEqual([["МГТФ 0,12 красный", 100, 1], ["МГТФ 0,12 черный", 250, 2], ["МГТФ 0,12 белый", 400, 1]]);
});

it("duplicateWire on an unknown row changes nothing", () => {
  const S = stateOf([row(0, "w1", 100)]);
  expect(duplicateWire(S, 9)).toBe(S);
  vi.restoreAllMocks();
});
