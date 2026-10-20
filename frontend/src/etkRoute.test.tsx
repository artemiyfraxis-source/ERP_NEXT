import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EtkRoute, EtkRouteBlock, Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { COLUMN_MAX, COLUMN_MIN, GAP, OPEN_MIN, etkDiagramWidths } from "./EtkRouteDiagram";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import { etkDiagramCrossings, etkPartSource, etkSourceLines, etkPfGroups, etkRouteBranches, etkRouteChips, etkRouteDiagram, etkRouteLevels, etkRouteOf, etkRouteSummary, etkSemiSource, pluralBlocks } from "./etkRoute";

function block(key: string, name: string, ops: number[], extra: Partial<EtkRouteBlock> = {}): EtkRouteBlock {
  return { key, name, ops, final: false, components: [], outputs: 1, inputs: 0, minutes: 0, unit_minutes: 0, rolled_minutes: 0, rolled_ops: ops.length, ...extra };
}

/** Резка провода → опрессовка (2 операции) ─┐
 *  Резка ТУТ (×2 на изделие) ───────────────┴→ Изделие. */
const branched: EtkRoute = {
  source: "derived",
  final: "b5",
  blocks: [
    block("b1", "Резка провода", [1], { unit_minutes: 0.5, minutes: 50 }),
    block("b2", "Резка ТУТ", [2], { unit_minutes: 0.2, minutes: 20 }),
    block("b3", "Опрессовка", [3, 4], { unit_minutes: 1.2, minutes: 120, inputs: 1, rolled_minutes: 1.7, rolled_ops: 3 }),
    block("b5", "Изделие", [5], { final: true, unit_minutes: 2, minutes: 200, inputs: 3, rolled_minutes: 4.1, rolled_ops: 5 }),
  ],
  edges: [
    { from: "b1", to: "b3", per_unit: 1 },
    { from: "b3", to: "b5", per_unit: 1 },
    { from: "b2", to: "b5", per_unit: 2 },
  ],
};

const linear: EtkRoute = {
  source: "derived",
  final: "b3",
  blocks: [block("b1", "Резка", [1]), block("b2", "Опрессовка", [2]), block("b3", "Изделие", [3], { final: true })],
  edges: [{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }],
};

describe("etkRouteOf", () => {
  it("is null for an ETK without a scheme and drops edges to unknown blocks", () => {
    expect(etkRouteOf(null)).toBeNull();
    expect(etkRouteOf({ operations: [] })).toBeNull();
    expect(etkRouteOf({ route: { blocks: [] } })).toBeNull();
    const route = etkRouteOf({ route: { source: "manual", final: "a", blocks: [{ key: "a", name: "Сборка", ops: [1], final: true }],
      edges: [{ from: "zz", to: "a", per_unit: 1 }] } });
    expect(route).toMatchObject({ source: "manual", final: "a", edges: [] });
    expect(route?.blocks[0]).toMatchObject({ key: "a", ops: [1], unit_minutes: 0, rolled_ops: 0 });
  });

  it("reads the level label, the kit mark and the arrow items, and leaves them out when not set", () => {
    const route = etkRouteOf({ route: { source: "generated", final: "b", blocks: [
      { key: "a", name: "Резка", ops: [1], final: false, kit: true, label: "1" },
      { key: "c", name: "Трубки", ops: [2], final: false, kit: "yes", label: "" },
      { key: "b", name: "Сборка", ops: [3], final: true, label: "2" },
    ], edges: [{ from: "a", to: "b", per_unit: 1, item: " НВ4 0,12 белый ×2; ТТК 8/2 ×1 " }, { from: "c", to: "b", per_unit: 1, item: 3 }] } });

    expect(route?.source).toBe("generated");
    expect(route?.blocks.map((block) => [block.key, block.kit, block.label])).toEqual([["a", true, "1"], ["c", undefined, undefined], ["b", undefined, "2"]]);
    expect(route?.blocks[1]).not.toHaveProperty("kit");
    expect(route?.edges).toEqual([{ from: "a", to: "b", per_unit: 1, item: "НВ4 0,12 белый ×2; ТТК 8/2 ×1" }, { from: "c", to: "b", per_unit: 1 }]);
  });
});

describe("etkRouteBranches", () => {
  it("is false for a chain, a single block and no scheme", () => {
    expect(etkRouteBranches(null)).toBe(false);
    expect(etkRouteBranches(linear)).toBe(false);
    expect(etkRouteBranches({ ...linear, blocks: [block("a", "Сборка", [1], { final: true })], edges: [] })).toBe(false);
  });

  it("is true when a block has several inputs, several outputs or the graph has several starts", () => {
    expect(etkRouteBranches(branched)).toBe(true);
    const fanOut: EtkRoute = { ...linear, edges: [...linear.edges, { from: "b1", to: "b3", per_unit: 1 }] };
    expect(etkRouteBranches(fanOut)).toBe(true);
    const twoStarts: EtkRoute = { ...linear, edges: [{ from: "b2", to: "b3", per_unit: 1 }] };
    expect(etkRouteBranches(twoStarts)).toBe(true);
  });
});

describe("etkRouteChips", () => {
  it("orders sources before consumers and says what feeds each block, with ×N only when it is not 1", () => {
    const chips = etkRouteChips(branched);
    expect(chips.map((chip) => `${chip.number} ${chip.name}`)).toEqual(["1 Резка провода", "2 Резка ТУТ", "3 Опрессовка", "4 Изделие"]);
    expect(chips.map((chip) => chip.first)).toEqual([1, 2, 3, 5]);
    expect(chips.map((chip) => chip.ops)).toEqual([1, 1, 2, 1]);
    expect(chips.map((chip) => chip.perUnit)).toEqual(["0,5 мин/шт", "0,2 мин/шт", "1,2 мин/шт", "2 мин/шт"]);
    expect(chips[2].feeds).toEqual([{ number: 1, label: "1.1", name: "Резка провода", times: "" }]);
    expect(chips[3].feeds).toEqual([{ number: 2, label: "1.2", name: "Резка ТУТ", times: "×2" }, { number: 3, label: "2", name: "Опрессовка", times: "" }]);
    expect(chips.map((chip) => chip.final)).toEqual([false, false, false, true]);
  });

  it("puts a consumer after its sources even when the scheme lists it first, and keeps blocks of a broken cycle", () => {
    const reversed: EtkRoute = { ...branched, blocks: [...branched.blocks].reverse() };
    const numbers = etkRouteChips(reversed).map((chip) => chip.key);
    expect(numbers.indexOf("b5")).toBe(3);
    expect(numbers.indexOf("b3")).toBeGreaterThan(numbers.indexOf("b1"));
    const cycle: EtkRoute = { ...linear, edges: [...linear.edges, { from: "b3", to: "b1", per_unit: 1 }] };
    expect(etkRouteChips(cycle)).toHaveLength(3);
  });

  it("shows no time when the scheme has none and never an empty name", () => {
    const [chip] = etkRouteChips({ ...linear, blocks: [block("b1", " ", [1]), ...linear.blocks.slice(1)] });
    expect(chip).toMatchObject({ name: "Блок 1", perUnit: "" });
  });
});

describe("etkRouteSummary", () => {
  it("counts blocks and gives the final block's time and operations per piece", () => {
    expect(etkRouteSummary(etkRouteChips(branched))).toBe("4 блока · на изделие 4,1 мин · 5 оп.");
    expect([1, 2, 5, 11, 21, 24].map(pluralBlocks)).toEqual(["1 блок", "2 блока", "5 блоков", "11 блоков", "21 блок", "24 блока"]);
  });
});

function techcard(route?: EtkRoute): ProjectTechcard {
  return { id: "t", project: "P-1", item_index: "КБ1", product_name: "Жгут", revision: 1, document: {
    operations: [1, 2, 3, 4, 5].map((position) => ({ position, code: "CUT_WIRE_auto", type: "cutWire", title: `Операция ${position}` })),
    ...(route ? { route } : {}),
  } } as unknown as ProjectTechcard;
}

describe("etkPrintPages with the ПФ scheme", () => {
  it("prints the ПФ as a compact table «ПФ · операции · вход · выход» under the header of the cover", () => {
    const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard(branched), []));
    expect(html).toContain("<h2>Полуфабрикаты</h2>");
    expect(html.indexOf("<h2>Полуфабрикаты</h2>")).toBeGreaterThan(html.indexOf("НОМЕР ПРОЕКТА"));
    expect(html.indexOf("<h2>Полуфабрикаты</h2>")).toBeLessThan(html.indexOf("<h2>Маршрут</h2>"));
    const pages = etkPrintPages(techcard(branched), []);
    const table = pages[0].blocks.find((block) => "table" in block && block.table.columns[0] === "ПФ") as { table: { columns: string[]; rows: string[][] } };
    expect(table.table.columns).toEqual(["ПФ", "Операции", "Вход", "Выход"]);
    expect(table.table.rows.map((row) => row[0].split("\n")[0])).toEqual(["ПФ 1.1", "ПФ 1.2", "ПФ 2", "ПФ 3 · изделие"]);
    expect(table.table.rows[2][1]).toBe("03 Операция 3\n04 Операция 4");
    expect(table.table.rows[3][2]).toBe("ПФ 1.2: Резка ТУТ ×2\nПФ 2: Опрессовка");
    expect(html).not.toContain("Маршрут полуфабрикатов");
  });

  it("prints no ПФ table without a scheme or with a single ПФ; a linear scheme gets the table too", () => {
    const single: EtkRoute = { ...linear, final: "b1", blocks: [block("b1", "Изделие", [1, 2, 3], { final: true })], edges: [] };
    expect(etkPrintPages(techcard(single), [])).toEqual(etkPrintPages(techcard(), []));
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard(), []))).not.toContain("Полуфабрикаты");
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard(linear), []))).toContain("<h2>Полуфабрикаты</h2>");
  });
});

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

async function renderSheet(route?: EtkRoute) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const view = render(<EtkSheet card={techcard(route)} principal={principal} onChange={() => undefined} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3)); // журнал ревизий, эскизы и выпуски комплекта ТД
  if (route) await screen.findByRole("region", { name: "Схема полуфабрикатов" });
  else await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
  return view;
}

afterEach(() => vi.restoreAllMocks());

const diagramOf = () => screen.getByRole("region", { name: "Схема полуфабрикатов" });
const boxOf = (name: RegExp) => within(diagramOf()).getByRole("region", { name });
/** Операция по номеру в схеме: первый щелчок выбирает её ПФ, второй открывает (владелец 19.10). */
const openOp = (name: RegExp) => {
  fireEvent.click(within(diagramOf()).getByRole("button", { name }));
  fireEvent.click(within(diagramOf()).getByRole("button", { name }));
};

describe("ETK scheme of ПФ in the fitter's task", () => {
  async function renderTask(focus: { position: number; positions?: number[] }) {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const view = render(<EtkSheet card={techcard(branched)} principal={principal} onChange={() => undefined} task focus={focus} />);
    await screen.findByRole("region", { name: "Схема полуфабрикатов" });
    return view;
  }

  it("shows the scheme open with the fitter's ПФ expanded and his operations lit; operations do not open from it", async () => {
    const { container } = await renderTask({ position: 3, positions: [3, 4] });
    const mine = boxOf(/ПФ 2 · Опрессовка/);
    expect(mine).toHaveClass("on");
    expect(within(mine).getByText("ваше задание")).toBeInTheDocument();
    expect([...container.querySelectorAll(".etk-dag-opline.on")].map((line) => line.querySelector(".etk-no")?.textContent)).toEqual(["03", "04"]);
    expect(within(diagramOf()).queryByRole("button", { name: /Операция/ })).toBeNull();
    expect(container.querySelector(".etk-route")?.closest("details")).toBeNull();
    // Щелчок по чужому ПФ только подсвечивает его связи.
    fireEvent.click(boxOf(/ПФ 1.1 · Резка провода/));
    expect(boxOf(/ПФ 1.1 · Резка провода/)).toHaveClass("picked");
    expect(screen.getByLabelText("Операции задания")).toBeInTheDocument();
  });

  it("lights the one operation of a single-operation task", async () => {
    const { container } = await renderTask({ position: 5 });
    expect(boxOf(/ПФ 3 · Изделие/)).toHaveClass("on");
    expect([...container.querySelectorAll(".etk-dag-opline.on")].map((line) => line.querySelector(".etk-no")?.textContent)).toEqual(["05"]);
  });
});

describe("ETK scheme of ПФ — the navigation", () => {
  it("shows a linear route as the same boxes in a chain; an ETK without a scheme keeps the flat list", async () => {
    const withLinear = await renderSheet(linear);
    expect(within(diagramOf()).getAllByRole("region").map((box) => box.getAttribute("aria-label"))).toEqual([
      "ПФ 1 · Резка", "ПФ 2 · Опрессовка", "ПФ 3 · Изделие",
    ]);
    expect(withLinear.container.querySelectorAll(".etk-dag-line")).toHaveLength(2);
    expect(withLinear.container.querySelector(".etk-pf, .etk-nav-route")).toBeNull();
    withLinear.unmount();
    vi.restoreAllMocks();
    const without = await renderSheet();
    expect(without.container.querySelector(".etk-route")).toBeNull();
    expect(without.container.innerHTML).toContain("etk-nav-route");
  });

  it("draws a column per level with a box per ПФ: name, input, numbered operations; the final box marked, above the operation", async () => {
    const { container } = await renderSheet(branched);
    const diagram = diagramOf();
    expect(diagram).toHaveTextContent("Схема ПФ");
    expect(diagram).toHaveTextContent("4 блока · на изделие 4,1 мин · 5 оп.");
    const boxes = within(diagram).getAllByRole("region");
    expect(boxes.map((box) => box.querySelector(".etk-dag-top b")?.textContent)).toEqual(["ПФ 1.1", "ПФ 1.2", "ПФ 2", "ПФ 3"]);
    expect(boxes[2].querySelector(".etk-dag-name")).toHaveTextContent("Опрессовка");
    expect(within(within(boxes[2]).getByRole("list", { name: "Операции ПФ 2" })).getAllByRole("button").map((button) => button.textContent)).toEqual(["03Операция 3", "04Операция 4"]);
    // Что приходит в ПФ — списком внутри блока-потребителя.
    expect(within(boxes[3]).getByLabelText("Вход")).toHaveTextContent("1.2 Резка ТУТ ×22 Опрессовка");
    expect(within(diagram).getAllByText("изделие")).toHaveLength(1);
    // Старая схема без подписей на стрелках: «×N» только там, где на штуку нужно больше одной.
    expect([...container.querySelectorAll(".etk-dag-item")].map((item) => item.textContent)).toEqual(["×2"]);
    expect(diagram.compareDocumentPosition(container.querySelector(".etk-wrap") as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("draws kit arrows thin and unlabelled and lists what comes in inside the consumer box (713К.4: ~28 labelled curves)", async () => {
    const withItems: EtkRoute = { ...branched,
      blocks: branched.blocks.map((item) => (item.key === "b2" || item.key === "b1" ? { ...item, kit: true } : item)),
      edges: branched.edges.map((edge) => (edge.from === "b1" ? { ...edge, item: "НВ4 0,12 белый ×2; ТТК 8/2 ×1" } : edge)) };
    const { container } = await renderSheet(withItems);
    // На стрелках — ничего: ни подписи комплекта, ни «×2» от комплектного источника.
    expect(container.querySelectorAll(".etk-dag-item")).toHaveLength(0);
    expect(container.querySelectorAll(".etk-dag-line.kit")).toHaveLength(2);
    expect(container.querySelectorAll(".etk-dag-line:not(.kit)")).toHaveLength(1);
    expect(container.querySelectorAll(".etk-dag-box.kit")).toHaveLength(2);
    const press = boxOf(/Опрессовка/);
    expect(within(press).getByLabelText("Вход")).toHaveTextContent("1.1 НВ4 0,12 белый ×2, ТТК 8/2");
    expect(press.querySelector(".etk-dag-title")).toHaveAttribute("title", expect.stringContaining("← ПФ 1.1: НВ4 0,12 белый ×2, ТТК 8/2"));
  });

  it("labels a non-kit arrow by count only, and highlights a hovered box's arrows while dimming the rest", async () => {
    const { container } = await renderSheet(branched);
    expect([...container.querySelectorAll(".etk-dag-item")].map((item) => item.textContent)).toEqual(["×2"]);
    fireEvent.mouseEnter(boxOf(/Резка провода/));
    // b1 → b3 горит; остальные стрелки и чужие блоки притушены.
    expect(container.querySelectorAll(".etk-dag-line.on")).toHaveLength(1);
    expect(container.querySelectorAll(".etk-dag-line.dim")).toHaveLength(2);
    expect(boxOf(/Опрессовка/)).toHaveClass("near");
    expect(boxOf(/Резка ТУТ/)).toHaveClass("dim");
    fireEvent.mouseLeave(boxOf(/Резка провода/));
    expect(container.querySelectorAll(".etk-dag-line.on, .etk-dag-line.dim")).toHaveLength(0);
    fireEvent.focus(within(boxOf(/Изделие/)).getByRole("button", { name: /^05/ }));
    expect(container.querySelectorAll(".etk-dag-line.on")).toHaveLength(2);
  });

  it("lists the input of an operation a row per incoming ПФ with its own source, names on one line with the full text in the title", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const card = techcard(branched);
    const operations = (card.document as { operations: Array<Record<string, unknown>> }).operations;
    operations[4] = { ...operations[4], semis: [
      { name: "Резка ТУТ: ТТК 8/2мм, черная — 3 шт по 15 мм; ТТК 12/4мм — 2 шт по 25 мм", norm: "3", unit: "шт." },
      { name: "Провод с терминалом", norm: "10", unit: "шт." },
    ] };
    render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
    await screen.findByRole("region", { name: "Схема полуфабрикатов" });
    openOp(/^05/);
    const table = await screen.findByRole("table", { name: "Что взять" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector(".etk-pick-name")).toHaveAttribute("title", expect.stringContaining("ТТК 12/4мм — 2 шт по 25 мм"));
    expect(rows[0].querySelector(".etk-pick-from")).toHaveTextContent("ПФ 1.2 · коробка оп. 02");
    expect(rows[1].querySelector(".etk-pick-from")).toHaveTextContent("ПФ 2 · коробка оп. 04");
    expect(table).not.toHaveTextContent(";  ");
    // Сервер дал `source` строке — он главнее общей строки схемы.
    cleanup();
    operations[4] = { ...operations[4], semis: [
      { name: "ТТК 8/2 — 1 шт", norm: "500", unit: "шт.", source: "ПФ 1.2" }, { name: "Провод с терминалом", norm: "10", unit: "шт.", source: "операция 09" },
    ] };
    render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
    await screen.findByRole("region", { name: "Схема полуфабрикатов" });
    openOp(/^05/);
    const given = within(await screen.findByRole("table", { name: "Что взять" })).getAllByRole("row").slice(1);
    expect(given.map((row) => row.querySelector(".etk-pick-from")?.textContent)).toEqual(["ПФ 1.2", "операция 09"]);
  });

  it("draws the cut tubes in the result of the operation and in the sketch of the ПФ", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const card = techcard(branched);
    const operations = (card.document as { operations: Array<Record<string, unknown>> }).operations;
    operations[1] = { ...operations[1], type: "cutTut", code: "CUT_TUT_short",
      result: [{ name: "Трубки нарезаны: ТТК 8/2мм, черная — 3 шт по 15 мм; ТТК 12/4мм — 2 шт по 25 мм", norm: "3", unit: "шт." }] };
    render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
    await screen.findByRole("region", { name: "Схема полуфабрикатов" });
    openOp(/^02/);
    const result = await screen.findByRole("region", { name: "Результат" });
    expect(result.querySelector(".etk-result-art svg")?.innerHTML).toContain("ТТК 8/2мм ×3 · 15 мм");
    expect(within(diagramOf()).getByRole("img", { name: "Эскиз ПФ 1.2" }).innerHTML).toContain("ТТК 12/4мм ×2 · 25 мм");
  });

  it("selects a block on the first click (its links stay lit), opens an operation on the next; the open block's operations open at once; a click on the empty field drops the choice", async () => {
    const { container } = await renderSheet(branched);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 1");
    expect(within(diagramOf()).getAllByRole("region")[0]).toHaveAttribute("aria-current", "step");
    // Первый щелчок — где угодно по карточке: выбор, связи горят и без наведения; операция не меняется.
    fireEvent.click(boxOf(/Опрессовка/).querySelector(".etk-dag-name")!);
    expect(boxOf(/Опрессовка/)).toHaveClass("picked");
    expect(container.querySelectorAll(".etk-dag-line.on")).toHaveLength(2);
    expect(boxOf(/Резка ТУТ/)).toHaveClass("dim");
    fireEvent.mouseEnter(boxOf(/Резка ТУТ/));
    expect(boxOf(/Опрессовка/)).toHaveClass("picked");
    expect(container.querySelectorAll(".etk-dag-line.on")).toHaveLength(2);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 1");
    // Щелчок по пустому месту схемы снимает выбор; снова выбор — и щелчок по заголовку открывает первую операцию.
    fireEvent.click(container.querySelector(".etk-dag")!);
    expect(container.querySelector(".etk-dag-box.picked")).toBeNull();
    fireEvent.click(boxOf(/Опрессовка/).querySelector(".etk-dag-title")!);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 1");
    fireEvent.click(boxOf(/Опрессовка/).querySelector(".etk-dag-title")!);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 3");
    expect(boxOf(/Опрессовка/)).toHaveAttribute("aria-current", "step");
    expect(boxOf(/Резка провода/)).not.toHaveAttribute("aria-current");
    fireEvent.click(within(boxOf(/Опрессовка/)).getByRole("button", { name: /^04/ }));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 4");
    fireEvent.click(boxOf(/Изделие/).querySelector(".etk-dag-title")!);
    fireEvent.click(boxOf(/Изделие/).querySelector(".etk-dag-title")!);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 5");
  });

  it("lays the levels out in one row over the whole frame and scrolls it sideways inside its own frame when it does not fit", async () => {
    // 27": колонки делят ширину, раскрытая шире; не измерено — минимальные; узкая рамка — не уже минимума, ряд листается.
    const wide = etkDiagramWidths([false, false, true, false, false], 3600);
    expect(wide[2]).toBeGreaterThan(wide[0]);
    expect(wide.reduce((sum, width) => sum + width, 0) + 4 * GAP + 24).toBeLessThanOrEqual(3600);
    expect(wide[0]).toBeGreaterThan(COLUMN_MIN);
    expect(etkDiagramWidths([false, true], 0)).toEqual([COLUMN_MIN, OPEN_MIN]);
    const narrow = etkDiagramWidths([false, false, true, false, false], 1100);
    expect(narrow).toEqual([COLUMN_MIN, COLUMN_MIN, OPEN_MIN, COLUMN_MIN, COLUMN_MIN]);
    expect(Math.max(...etkDiagramWidths([false, false], 4000))).toBe(COLUMN_MAX);
    const { container } = await renderSheet(branched);
    // Один ряд колонок, без полос ниже; рамка листания своя.
    expect(container.querySelector(".etk-dag-band")).toBeNull();
    const frame = container.querySelector(".etk-dag-scroll") as HTMLElement;
    expect(frame.parentElement).toHaveClass("etk-dag-view");
    // В рамке — стрелки, подписи «×N» и колонки уровней (1.1 и 1.2 — одна колонка), ничего ниже ряда.
    expect([...frame.querySelector(".etk-dag")!.children].every((child) => child.matches("svg, .etk-dag-item, .etk-dag-col"))).toBe(true);
    expect(container.querySelectorAll(".etk-dag-col")).toHaveLength(3);
  });

  it("shows each operation in its box by its own generator label, the card code only under it in the open box", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const card = techcard(branched);
    const labels = ["Резка проводов", "Резка ТУТ", "Надеть ТТК X4 «A6» — ТТК 12/4", "Усадка маркировки X4 «A6» — «A6»", ""];
    card.document = { ...card.document, build_config: { ops: labels.map((label) => ({ type: "insTut", label })) } };
    render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
    await screen.findByRole("region", { name: "Схема полуфабрикатов" });
    const ops = (name: RegExp) => within(within(boxOf(name)).getByRole("list")).getAllByRole("button").map((button) => button.textContent);
    expect(ops(/Опрессовка/)).toEqual(["03Надеть ТТК X4 «A6» — ТТК 12/4", "04Усадка маркировки X4 «A6» — «A6»"]);
    // Пустая строка генератора — название карты.
    expect(ops(/Изделие/)).toEqual(["05Операция 5"]);
    fireEvent.click(boxOf(/Опрессовка/).querySelector(".etk-dag-title")!);
    fireEvent.click(boxOf(/Опрессовка/).querySelector(".etk-dag-title")!);
    expect(within(boxOf(/Опрессовка/)).getByRole("button", { name: /^03/ }).querySelector("small")).toHaveTextContent("CUT_WIRE_auto");
    expect(within(boxOf(/Резка провода/)).getByRole("button", { name: /^01/ }).querySelector("small")).toBeNull();
  });
});

describe("level labels (ПФ 1.1, ПФ 3)", () => {
  it("are computed from an old route without labels: longest path from a source, «N.k» when a level holds several", () => {
    const levels = etkRouteLevels(branched);
    expect([...levels].map(([key, value]) => `${key}:${value.label}`)).toEqual(["b1:1.1", "b2:1.2", "b3:2", "b5:3"]);
    // b2 → b5 напрямую, но b5 стоит ещё и после b3: уровень по самому длинному пути.
    expect(levels.get("b5")?.level).toBe(3);
    expect(etkRouteLevels(linear).get("b2")?.label).toBe("2");
  });

  it("number the ПФ list, the «из ПФ …» sources and the diagram the same way", () => {
    const operations = [1, 2, 3, 4, 5].map((position) => ({ position, result: [{ name: `Рез ${position}` }] }));
    expect(etkPfGroups(branched, operations).map((group) => group.label)).toEqual(["1.1", "1.2", "2", "3"]);
    expect(etkSemiSource(branched, 5)).toBe("из ПФ 1.2, коробка с операции 02; ПФ 2, коробка с операции 04");
    expect(etkSemiSource(branched, 3)).toBe("из ПФ 1.1, коробка с операции 01");
    expect(etkSemiSource(branched, 4)).toBe("с операции 03");
  });

  it("lay out the diagram by level with the arrow text, falling back to ×N", () => {
    const diagram = etkRouteDiagram(branched);
    expect(diagram.columns.map((column) => column.boxes.map((box) => box.label))).toEqual([["1.1", "1.2"], ["2"], ["3"]]);
    expect(diagram.edges.map((edge) => `${edge.from}>${edge.to}:${edge.text}`)).toEqual(["b1>b3:", "b3>b5:", "b2>b5:×2"]);
  });

  it("order the boxes of a column by their neighbours, not by label, so that arrows cross less", () => {
    // Метки по первой операции: a1 (в x2), a2 (в x1) — по метке порядок пересекается, по барицентру — нет.
    const crossed: EtkRoute = { source: "generated", final: "z", blocks: [
      block("a1", "Резка 1", [1]), block("a2", "Резка 2", [2]), block("a3", "Резка 3", [3]),
      block("x1", "Сборка 1", [4]), block("x2", "Сборка 2", [5]), block("z", "Изделие", [6], { final: true }),
    ], edges: [
      { from: "a1", to: "x2", per_unit: 1 }, { from: "a2", to: "x1", per_unit: 1 }, { from: "a3", to: "x2", per_unit: 1 },
      { from: "x1", to: "z", per_unit: 1 }, { from: "x2", to: "z", per_unit: 1 },
    ] };
    const diagram = etkRouteDiagram(crossed);
    expect(etkDiagramCrossings(diagram)).toBe(0);
    expect(diagram.columns[0].boxes.map((box) => box.key)).toEqual(["a2", "a1", "a3"]);
    // Уровень и метки остаются прежними: порядок колонки меняется, названия «ПФ 1.1…» — нет.
    expect(diagram.columns[0].boxes.map((box) => box.label)).toEqual(["1.2", "1.1", "1.3"]);
    const same = etkRouteDiagram(branched);
    expect(etkDiagramCrossings(same)).toBe(0);
  });

  it("give a box its inputs: source label, the item text without «×1», the count when there is no item", () => {
    const withItems: EtkRoute = { ...branched, blocks: branched.blocks.map((item) => (item.key === "b1" ? { ...item, kit: true } : item)),
      edges: branched.edges.map((edge) => (edge.from === "b1" ? { ...edge, item: "НВ4 0,5 красный ×1; НВ4 0,5 черный ×1" } : edge)) };
    const boxes = new Map(etkRouteDiagram(withItems).columns.flatMap((column) => column.boxes).map((box) => [box.key, box]));
    expect(boxes.get("b3")?.inputs).toEqual([{ label: "1.1", text: "НВ4 0,5 красный, НВ4 0,5 черный", times: "", kit: true }]);
    expect(boxes.get("b5")?.inputs).toEqual([{ label: "1.2", text: "Резка ТУТ", times: "×2", kit: false }, { label: "2", text: "Опрессовка", times: "", kit: false }]);
    expect(boxes.get("b1")?.inputs).toEqual([]);
  });

  it("split a combined source string into a line per source", () => {
    expect(etkSourceLines("из ПФ 1.2, коробка с операции 02; ПФ 1.6, коробка с операции 03; ПФ 1.7, коробка с операции 04"))
      .toEqual(["ПФ 1.2 · коробка оп. 02", "ПФ 1.6 · коробка оп. 03", "ПФ 1.7 · коробка оп. 04"]);
    expect(etkSourceLines("с операции 03")).toEqual(["с операции 03"]);
    expect(etkSourceLines("")).toEqual([]);
  });
});

describe("etkPartSource", () => {
  it("names the kit request, joins several, and stays plain without one", () => {
    expect(etkPartSource({ kit_request: "REQ.26.0000123" })).toBe("со склада по комплектации REQ.26.0000123");
    expect(etkPartSource({ kit_request: "REQ.26.0000123, REQ.26.0000130" })).toBe("со склада по комплектации REQ.26.0000123, REQ.26.0000130");
    expect(etkPartSource({})).toBe("со склада по комплектации");
    expect(etkPartSource({ kit_request: " " })).toBe("со склада по комплектации");
  });
});
