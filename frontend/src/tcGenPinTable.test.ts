import { describe, expect, it } from "vitest";
import { pinoutEnds, pinUnits } from "./pinoutCheck";
import { applyEnd, firstFreePin, pinConns, pinRows, spliceLines } from "./tcGenPinTable";
import { makeGen, type GenState, type Side } from "./tcGenLogic";
import { COMPONENTS_663, D663, W, harness663 } from "./tcGenComplex663.fixture";

/* Таблица «Распиновка и сростки»: 663К.1 КБ2 (XS1/XS2/XS3 на стороне А, XP1 на стороне В, сростки X1/X2 в гильзах ГС-2,5). */
const COUNTS: Record<string, number> = { a1: 2, b1: 2, e2: 3, e3: 6 };
function view(S: GenState) {
  const gen = makeGen(D663, S);
  const ends = pinoutEnds(gen, S);
  const units = pinUnits(gen, S);
  const conns = pinConns(ends, S, COUNTS);
  return { ends, units, conns, rows: pinRows(conns, units, S, ends), lines: spliceLines(S, units, conns) };
}
const set = (S: GenState, idx: number, unit: number, side: Side, value: string) => {
  const v = view(S);
  return applyEnd(D663, S, v.conns, v.units, { idx, unit }, side, value);
};
const row = (S: GenState, idx: number, unit = 0) => view(S).rows.find((r) => r.idx === idx && r.unit === unit)!;

describe("таблица распиновки: чтение жгута 663К.1 КБ2", () => {
  const S = harness663();
  it("одна строка на жилу, оба конца в строке, замечаний нет", () => {
    const { rows } = view(S);
    expect(rows).toHaveLength(12);
    expect(rows.every((r) => r.problems.length === 0)).toBe(true);
    expect(row(S, W.xs1r).cells.A.text).toBe("XS1·1");
    expect(row(S, W.xs1r).cells.B.text).toBe("С1");
    expect(row(S, W.xp1b).cells.A.text).toBe("С2");
    expect(row(S, W.xp1b).cells.B.text).toBe("XP1·2");
    expect(row(S, W.mgtf, 1).cells.A.text).toBe("XS3·4");
    expect(row(S, W.mgtf, 1).cells.B.text).toBe("хвост");
    expect(row(S, W.xs3r, 1).num).toBe("7.2");
  });
  it("сросток — первый класс: состав и обратные ссылки на контакты", () => {
    const { lines } = view(S);
    expect(lines.map((l) => [l.label, l.ends.length])).toEqual([["С1", 5], ["С2", 5]]);
    expect(lines[0].ends.map((e) => e.where)).toContain("→ XP1·1");
    expect(lines[0].problem).toBe("");
  });
});

describe("таблица распиновки: правки концов", () => {
  it("«не задан» снимает конец со сростка и показывает замечание по-русски", () => {
    const S = set(harness663(), W.xs2r, 0, "B", "");
    expect(row(S, W.xs2r).cells.B.value).toBe("");
    expect(row(S, W.xs2r).problems).toEqual(["Конец В не задан: выберите контакт, сросток или свободный конец"]);
    expect(S.splices![0].ends).toHaveLength(4);
  });
  it("тот же сросток у другого конца соединяет их; контакт этого конца снимается", () => {
    let S = set(harness663(), W.mgtf, 0, "B", "sp:s1");
    expect(row(S, W.mgtf).cells.B.text).toBe("С1");
    expect(S.splices![0].ends).toHaveLength(6);
    S = set(S, W.xs1r, 0, "A", "sp:s1");
    expect(row(S, W.xs1r).cells.A.text).toBe("С1");
    expect(view(S).conns.find((c) => c.designation === "XS1")).toBeTruthy();
    expect(row(S, W.xs1r).problems).toEqual(["Оба конца провода в одном сростке"]);
  });
  it("«Новый сросток» заводит С3 с этим концом", () => {
    const S = set(harness663(), W.mgtf, 0, "B", "new");
    expect(S.splices!.map((sp) => sp.id)).toEqual(["s1", "s2", "s3"]);
    expect(row(S, W.mgtf).cells.B.text).toBe("С3");
    expect(view(S).lines[2].problem).toBe("В сростке меньше двух концов");
  });
  it("контакт: конкретный, первый свободный; занятый переставляет жилы местами", () => {
    let S = set(harness663(), W.xs1r, 0, "A", "sp:s1"); // XS1·1 освободился
    S = set(S, W.xs1r, 0, "A", "pin:a1:auto");
    expect(row(S, W.xs1r).cells.A.text).toBe("XS1·1");
    S = set(S, W.xs1r, 0, "A", "pin:a1:2"); // контакт 2 занят чёрным — чёрный уходит на прежний контакт 1
    expect(row(S, W.xs1r).cells.A.text).toBe("XS1·2");
    expect(row(S, W.xs1b).cells.A.text).toBe("XS1·1");
  });
  it("«первый свободный»: все заняты — выбор не меняет жгут", () => {
    const S = harness663();
    const v = view(S);
    expect(firstFreePin(v.conns.find((c) => c.id === "a1")!, v.units)).toBeNull();
    expect(set(S, W.xs2r, 0, "A", "pin:a1:auto")).toBe(S);
  });
  it("хвост и возврат на контакт", () => {
    let S = set(harness663(), W.mgtf, 1, "A", "tail");
    expect(row(S, W.mgtf, 1).cells.A.text).toBe("хвост");
    S = set(S, W.mgtf, 1, "A", "pin:e3:4");
    expect(row(S, W.mgtf, 1).cells.A.text).toBe("XS3·4");
    expect(S.tails!.some((e) => e.idx === W.mgtf && e.unit === 1 && e.side === "A")).toBe(false);
  });
  it("контакт, занятый двумя проводами при двойном вводе, помечается замечанием", () => {
    // Два провода вручную в один контакт без двойного обжима: правка текстом контактов разъёма (как в карточке разъёма).
    const S0 = harness663();
    const S = { ...S0, endpoints: S0.endpoints.map((e) => (e.id === "a1" ? { ...e, pins: { ...e.pins, [W.xs1b]: "1" } } : e)) };
    expect(row(S, W.xs1r).problems[0]).toContain("Контакт 1 разъёма XS1 занят и другим проводом");
  });
  it("корпус без известного числа контактов: список контактов — до занятых плюс один", () => {
    const S = harness663();
    const v = view(S);
    const conn = { ...v.conns.find((c) => c.id === "e3")!, count: 0 };
    expect(firstFreePin(conn, v.units)).toBe(7);
  });
});

describe("старые черновики", () => {
  it("состояние без splices/tails читается таблицей без ошибок", () => {
    const { splices: _s, tails: _t, ...rest } = harness663();
    const rows = view(rest as GenState).rows;
    expect(rows.some((r) => r.cells.A.value === "")).toBe(true);
  });
  it("состав kit-N совпадает с фикстурой", () => {
    expect(COMPONENTS_663).toHaveLength(17);
  });
});
