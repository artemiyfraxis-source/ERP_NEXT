import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EtkRoute, EtkRouteBlock, Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import { etkRouteBranches, etkRouteChips, etkRouteLine, etkRouteOf, etkRouteSummary, pluralBlocks } from "./etkRoute";

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
    expect(chips[2].feeds).toEqual([{ number: 1, name: "Резка провода", times: "" }]);
    expect(chips[3].feeds).toEqual([{ number: 2, name: "Резка ТУТ", times: "×2" }, { number: 3, name: "Опрессовка", times: "" }]);
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

describe("etkRouteLine (print)", () => {
  it("is one text line for a branched scheme and empty for a linear one", () => {
    expect(etkRouteLine(linear)).toBe("");
    expect(etkRouteLine(null)).toBe("");
    expect(etkRouteLine(branched)).toBe(
      "Маршрут полуфабрикатов (4 блока · на изделие 4,1 мин · 5 оп.): 1 Резка провода (1 оп. · 0,5 мин/шт); 2 Резка ТУТ (1 оп. · 0,2 мин/шт); " +
      "3 Опрессовка (2 оп. · 1,2 мин/шт · из 1); 4 Изделие (1 оп. · 2 мин/шт · из 2 (×2), 3 · итог)",
    );
  });
});

function techcard(route?: EtkRoute): ProjectTechcard {
  return { id: "t", project: "P-1", item_index: "КБ1", product_name: "Жгут", revision: 1, document: {
    operations: [1, 2, 3, 4, 5].map((position) => ({ position, code: "CUT_WIRE_auto", type: "cutWire", title: `Операция ${position}` })),
    ...(route ? { route } : {}),
  } } as unknown as ProjectTechcard;
}

describe("etkPrintPages with the block scheme", () => {
  it("adds the scheme line under the header of the cover only when the route branches", () => {
    const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard(branched), []));
    expect(html).toContain("Маршрут полуфабрикатов (4 блока");
    expect(html.indexOf("Маршрут полуфабрикатов")).toBeGreaterThan(html.indexOf("НОМЕР ПРОЕКТА"));
    expect(html.indexOf("Маршрут полуфабрикатов")).toBeLessThan(html.indexOf("<h2>Маршрут</h2>"));
    expect(html.match(/Маршрут полуфабрикатов/g)).toHaveLength(1);
  });

  it("prints a linear ETK exactly as one without a scheme", () => {
    expect(etkPrintPages(techcard(linear), [])).toEqual(etkPrintPages(techcard(), []));
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard(linear), []))).not.toContain("Маршрут полуфабрикатов");
  });
});

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

async function renderSheet(route?: EtkRoute) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const view = render(<EtkSheet card={techcard(route)} principal={principal} onChange={() => undefined} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2)); // журнал ревизий и эскизы
  await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
  return view;
}

afterEach(() => vi.restoreAllMocks());

describe("ETK strip «Маршрут»", () => {
  it("is absent for a linear route and the ETK renders byte for byte as without a scheme", async () => {
    const withLinear = await renderSheet(linear);
    expect(withLinear.container.querySelector(".etk-route")).toBeNull();
    expect(screen.queryByRole("region", { name: "Маршрут полуфабрикатов" })).toBeNull();
    const html = withLinear.container.innerHTML;
    withLinear.unmount();
    vi.restoreAllMocks();
    const without = await renderSheet();
    expect(without.container.innerHTML).toBe(html);
    expect(html).toContain("etk-nav-route");
  });

  it("shows chips with name, operations, time and what feeds each block, final marked, above the route list", async () => {
    const { container } = await renderSheet(branched);
    const strip = screen.getByRole("region", { name: "Маршрут полуфабрикатов" });
    expect(strip).toHaveTextContent("Маршрут");
    expect(strip).toHaveTextContent("4 блока · на изделие 4,1 мин · 5 оп.");
    const chips = within(strip).getAllByRole("button");
    expect(chips).toHaveLength(4);
    expect(chips[0]).toHaveTextContent("1Резка провода · 1 оп. · 0,5 мин/шт");
    expect(chips[0].querySelector(".etk-route-feed")).toBeNull();
    expect(chips[2]).toHaveTextContent("3Опрессовка · 2 оп. · 1,2 мин/шт← 1 Резка провода");
    expect(chips[3]).toHaveTextContent("4Изделие · 1 оп. · 2 мин/шт← 2 Резка ТУТ (×2) · 3 Опрессовкаитог");
    expect(within(strip).getAllByText("итог")).toHaveLength(1);
    expect(chips[3]).toContainElement(within(strip).getByText("итог"));
    expect(strip.compareDocumentPosition(container.querySelector(".etk-wrap") as Element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("opens the block's first operation on click and highlights the block of the open operation", async () => {
    await renderSheet(branched);
    const strip = screen.getByRole("region", { name: "Маршрут полуфабрикатов" });
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 1");
    expect(within(strip).getAllByRole("button")[0]).toHaveAttribute("aria-current", "step");
    fireEvent.click(within(strip).getByRole("button", { name: /Опрессовка/ }));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 3");
    expect(within(strip).getByRole("button", { name: /Опрессовка/ })).toHaveAttribute("aria-current", "step");
    expect(within(strip).getByRole("button", { name: /Резка провода/ })).not.toHaveAttribute("aria-current");
    fireEvent.click(within(strip).getByRole("button", { name: /Изделие/ }));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Операция 5");
  });
});
