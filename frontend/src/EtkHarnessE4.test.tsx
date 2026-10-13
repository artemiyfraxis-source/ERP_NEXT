import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EtkHarnessE4, EtkWireMap, wireMapModel } from "./EtkHarnessE4";
import { focusIndex } from "./ProjectEtk";

// Редактор чертежа — отдельный тяжёлый чанк; здесь важно только, что схема открывается в нём на Э4 и только для просмотра.
vi.mock("./harness", () => ({
  HarnessEditorLazy: (props: { initialView: string; readOnly?: boolean; phoneView?: boolean; harnessId: string }) =>
    <div data-testid="e4-editor" data-view={props.initialView} data-readonly={String(Boolean(props.readOnly))}
      data-phone={String(Boolean(props.phoneView))}>{props.harnessId}</div>,
  loadHarnessDrawing: () => Promise.resolve({
    harnessDrawing: (options: { readOnly?: boolean }) => ({ api: { readOnly: options.readOnly }, placements: {}, revision: () => 1, mine: () => null }),
    createErpHarnessEditorCatalog: () => ({ templates: {} }),
  }),
}));

afterEach(() => { vi.restoreAllMocks(); });

function answer(status: number, body: unknown) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

const topology = {
  nodes: [
    { id: "a", conn: "Molex 39012140", side: "A", depth: 0, housing: { title: "Molex Mini-Fit Jr.", n: 14 } },
    { id: "b", conn: "Molex 39012141", side: "B", depth: 1, housing: { title: "Molex Mini-Fit Jr.", n: 14 } },
  ],
  edges: [{ from: "a", to: "b", wires: ["ПВАМ 1,5 белый", "ПВАМ 1,5 черный", "ПВАМ 1,5 белый"], count: 3, length: 264 }],
} as unknown as Parameters<typeof wireMapModel>[1];

describe("плоская схема Э4 по ЭТК", () => {
  it("по таблице соединений: слева «откуда», справа «куда», контакты по возрастанию, линия на каждый провод", () => {
    const model = wireMapModel([
      { wire: "НВ4 0,2 белый", from: { end: "A1", conn: "DS1071", pin: 3 }, to: { end: "B1", conn: "XHP-4", pin: 2 } },
      { wire: "НВ4 0,2 красный", from: { end: "A1", conn: "DS1071", pin: 1 }, to: { end: "B1", conn: "XHP-4", pin: 1 } },
    ], topology)!;
    expect(model.columns.map((column) => column.map((item) => item.title))).toEqual([["A1"], ["B1"]]);
    expect(model.columns[0][0].sub).toBe("DS1071");
    expect(model.columns[0][0].rows.map((row) => row.pin)).toEqual(["1", "3"]);
    expect(model.columns[1][0].rows.map((row) => row.pin)).toEqual(["1", "2"]);
    // Белый провод: A1 контакт 3 (вторая строка) → B1 контакт 2 (вторая строка).
    expect(model.links[0]).toMatchObject({ from: ["A1", 1], to: ["B1", 1], wire: "НВ4 0,2 белый", color: "#f4f4f4" });
    expect(model.links[1]).toMatchObject({ from: ["A1", 0], to: ["B1", 0], color: "#d7322d" });
    expect(model.bundles).toEqual([]);
  });

  it("без распиновки — по топологии: разъёмы с проводами по цветам и пучок «N пров. · длина», без объёмных картинок", () => {
    const model = wireMapModel([], topology)!;
    expect(model.columns.map((column) => column.map((item) => item.title))).toEqual([["Molex 39012140"], ["Molex 39012141"]]);
    expect(model.columns[0][0].sub).toBe("Molex Mini-Fit Jr. · 14 конт. · сторона А");
    expect(model.columns[0][0].rows).toEqual([
      { pin: "", wire: "ПВАМ 1,5 белый", color: "#f4f4f4", count: 2 },
      { pin: "", wire: "ПВАМ 1,5 черный", color: "#222", count: 1 },
    ]);
    expect(model.bundles).toEqual([{ from: "a", to: "b", label: "3 пров. · 264 мм", colors: ["#f4f4f4", "#222"] }]);
    const { container } = render(<EtkWireMap model={model} />);
    expect(container.querySelector("svg.etk-wiremap")).not.toBeNull();
    expect(screen.getByText("3 пров. · 264 мм")).toBeInTheDocument();
    expect(container.querySelector("image, foreignObject")).toBeNull();
  });

  it("распиновка операций монтажа даёт номера контактов и без таблицы соединений", () => {
    const model = wireMapModel(undefined, topology, { "Molex 39012140::A": { 2: "ПВАМ 1,5 черный", 1: "ПВАМ 1,5 белый" } })!;
    expect(model.columns[0][0].rows.map((row) => `${row.pin} ${row.wire}`)).toEqual(["1 ПВАМ 1,5 белый", "2 ПВАМ 1,5 черный"]);
  });

  it("нечего рисовать — схемы нет", () => {
    expect(wireMapModel([], null)).toBeNull();
    expect(wireMapModel([{ wire: "x", from: { end: "", pin: 1 }, to: { end: "B", pin: 1 } }], { nodes: [] })).toBeNull();
  });
});

describe("схема Э4 в задании", () => {
  const model = wireMapModel([], topology);

  it("сохранённый чертёж КБ — открывается в редакторе на «Схема Э4» только для просмотра", async () => {
    const fetch = answer(200, { document: { id: "d" }, design: { objects: [] }, revision: 3 });
    render(<EtkHarnessE4 project="614К.1" index="КБ1" model={model} />);
    const editor = await screen.findByTestId("e4-editor");
    expect(editor).toHaveAttribute("data-view", "e4");
    expect(editor).toHaveAttribute("data-readonly", "true");
    expect(editor).toHaveAttribute("data-phone", "true"); // поле и масштаб, без панелей правки
    expect(String(fetch.mock.calls[0][0])).toBe("/api/techcards/kitsets/614%D0%9A.1/harness/%D0%9A%D0%911");
    expect(document.querySelector(".etk-wiremap")).toBeNull();
  });

  it("чертежа нет — плоская схема по ЭТК", async () => {
    answer(200, { document: null, design: null, revision: null });
    render(<EtkHarnessE4 project="614К.1" index="КБ1" model={model} />);
    expect(await screen.findByRole("img", { name: /Схема Э4: Molex 39012140, Molex 39012141/ })).toBeInTheDocument();
    expect(screen.queryByTestId("e4-editor")).toBeNull();
  });

  it("чертёж не прочитан (нет доступа) — плоская схема, без ошибки на экране", async () => {
    answer(403, { detail: "Раздел «Проекты» вам не открыт." });
    render(<EtkHarnessE4 project="614К.1" index="КБ1" model={model} />);
    expect(await screen.findByRole("img", { name: /Схема Э4/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ни чертежа, ни данных ЭТК — короткая строка вместо схемы", async () => {
    answer(200, { document: null, design: null, revision: null });
    render(<EtkHarnessE4 project="614К.1" index="КБ1" model={null} />);
    expect(await screen.findByText("Схема жгута не задана: нет ни чертежа, ни таблицы соединений.")).toBeInTheDocument();
  });
});

describe("операция задания в ЭТК", () => {
  const list = [
    { position: 1, code: "CUT" }, { position: 2, code: "INS" }, { position: 3, code: "PRS" }, { position: 4, code: "INS" },
  ];
  it("один код у двух операций — берётся та, у которой совпал и номер", () => {
    expect(focusIndex(list, { code: "INS", position: 4 })).toBe(3);
    expect(focusIndex(list, { code: "INS", position: 2 })).toBe(1);
  });
  it("номер не совпал — по коду, кода нет — по номеру, ничего — первая", () => {
    expect(focusIndex(list, { code: "PRS", position: 9 })).toBe(2);
    expect(focusIndex(list, { code: "", position: 4 })).toBe(3);
    expect(focusIndex(list, { code: "NONE", position: 9 })).toBe(0);
    expect(focusIndex(list)).toBe(0);
  });
});
