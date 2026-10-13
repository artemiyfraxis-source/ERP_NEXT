import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EtkRoute, EtkRouteBlock, Principal, ProjectTechcard } from "./api";
import { EtkSheet, focusOperations } from "./ProjectEtk";
import { pfPayload } from "./EtkPfList";
import { etkPfGroups } from "./etkRoute";
import { mergeBlocks, placeOp, splitBlock, type Route } from "./routeLogic";

function block(key: string, name: string, ops: number[], extra: Partial<EtkRouteBlock> = {}): EtkRouteBlock {
  return { key, name, ops, final: false, components: [], outputs: 1, inputs: 0, minutes: 0, unit_minutes: 0, rolled_minutes: 0, rolled_ops: ops.length, ...extra };
}

/** 01 Резка → ПФ-1; 02 Опрессовка → ПФ-2; 04 Скрутка → ПФ-3; 05, 03 Монтаж (в таком порядке работы) → ПФ-4, изделие. */
const pf: EtkRoute = {
  source: "manual",
  final: "mount",
  blocks: [
    block("cut", "Провод отрезанный", [1]),
    block("crimp", "Провод с терминалом", [2]),
    block("mount", "Жгут", [5, 3], { final: true }),
    block("twist", "Пара скрученная", [4]),
  ],
  edges: [
    { from: "cut", to: "crimp", per_unit: 1 },
    { from: "crimp", to: "mount", per_unit: 2 },
    { from: "twist", to: "mount", per_unit: 1 },
  ],
};

const operations = [
  { position: 1, code: "CUT", type: "cutWire", title: "Резка", time: [{ name: "Провод", norm: "10" }],
    components: [{ art: "ПВ3 0,5", name: "Провод ПВ3", norm: "20", unit: "м" }], result: [{ name: "Провод отрезанный", norm: "10", unit: "шт" }] },
  { position: 2, code: "PRS", type: "prsTerm", title: "Опрессовка", time: [{ name: "Провод", norm: "5" }],
    components: [{ art: "T-1", name: "Терминал", norm: "20", unit: "шт" }], result: [{ name: "Провод с терминалом", norm: "10", unit: "шт" }] },
  { position: 3, code: "INS", type: "insHousing", title: "Монтаж в колодку", time: [{ name: "Жгут", norm: "7,5" }],
    components: [{ art: "H-4", name: "Колодка", norm: "10", unit: "шт" }, { art: "T-1", name: "Терминал", norm: "2", unit: "шт" }],
    result: [{ name: "Жгут", norm: "10", unit: "шт" }] },
  { position: 4, code: "TWS", type: "twist", title: "Скрутка", time: [{ name: "Пара", norm: "3" }], result: [{ name: "Пара скрученная", norm: "10", unit: "шт" }] },
  { position: 5, code: "MNT", type: "insTerm", title: "Монтаж", time: [{ name: "Жгут", norm: "4" }],
    components: [{ art: "T-1", name: "Терминал", norm: "1,5", unit: "шт" }], result: [{ name: "Жгут собранный", norm: "10", unit: "шт" }] },
];

function techcard(route: EtkRoute | null = pf, status = "Выдана"): ProjectTechcard {
  return {
    id: "t", version: 4, project: "P-1", item_index: "КБ1", product_name: "Жгут", revision: 2, status,
    document: { kit: { part_quantity: "10", name: "Жгут" }, operations, ...(route ? { route } : {}) },
  } as unknown as ProjectTechcard;
}

const route: Route = { blocks: pf.blocks.map(({ key, name, ops, final, components }) => ({ key, name, ops, final, components })), edges: pf.edges };

describe("etkPfGroups", () => {
  it("groups the ETK by ПФ in the scheme's order, keeps the order of work and adds up what each ПФ takes", () => {
    const groups = etkPfGroups(pf, operations, 10);
    expect(groups.map((group) => [group.number, group.key, group.ops])).toEqual([
      [1, "cut", [1]], [2, "crimp", [2]], [3, "mount", [5, 3]], [4, "twist", [4]],
    ]);
    const mount = groups[2];
    // Терминал из 05 и 03 сложен в одну строку; колодка — своя.
    expect(mount.components).toEqual([{ art: "T-1", name: "Терминал", norm: "3,5", unit: "шт" }, { art: "H-4", name: "Колодка", norm: "10", unit: "шт" }]);
    expect(mount.inputs).toEqual([{ number: 2, name: "Провод с терминалом", times: "×2" }, { number: 4, name: "Пара скрученная", times: "" }]);
    // Выход — результат последней операции по порядку работы (03), а не по номеру.
    expect(mount.output).toEqual([{ name: "Жгут", norm: "10", unit: "шт" }]);
    expect(mount.minutes).toBe(11.5);
    expect(groups[0].inputs).toEqual([]);
    expect(etkPfGroups(null, operations)).toEqual([]);
  });
});

describe("ПФ editing (routeLogic)", () => {
  it("reorders inside a ПФ and moves an operation to a place in another ПФ, keeping the hand order", () => {
    expect(placeOp(route, 3, "mount", 0).blocks.find((item) => item.key === "mount")?.ops).toEqual([3, 5]);
    const moved = placeOp(route, 5, "crimp", 0);
    expect(moved.blocks.map((item) => [item.key, item.ops])).toEqual([["cut", [1]], ["crimp", [5, 2]], ["mount", [3]], ["twist", [4]]]);
    expect(moved.edges).toEqual(route.edges);
  });

  it("a sole operation takes its ПФ along — the ПФ merges into the target; «Новый ПФ» feeds the old one", () => {
    const merged = placeOp(route, 4, "mount", 1);
    expect(merged.blocks.map((item) => [item.key, item.ops, item.final])).toEqual([["cut", [1], false], ["crimp", [2], false], ["mount", [5, 4, 3], true]]);
    expect(merged.edges).toEqual([{ from: "cut", to: "crimp", per_unit: 1 }, { from: "crimp", to: "mount", per_unit: 2 }]);
    const split = placeOp(route, 5, null);
    expect(split.blocks.map((item) => [item.key, item.ops])).toEqual([["cut", [1]], ["crimp", [2]], ["mount", [3]], ["twist", [4]], ["b1", [5]]]);
    expect(split.edges).toContainEqual({ from: "b1", to: "mount", per_unit: 1 });
    expect(placeOp(route, 1, null)).toBe(route);
  });

  it("merge and split keep the order of work when asked, and the default stays ascending", () => {
    expect(mergeBlocks(route, "twist", "mount", true).blocks.find((item) => item.key === "mount")?.ops).toEqual([4, 5, 3]);
    expect(mergeBlocks(route, "twist", "mount").blocks.find((item) => item.key === "mount")?.ops).toEqual([3, 4, 5]);
    const split = splitBlock(route, "mount", 5, true);
    expect(split.blocks.map((item) => [item.key, item.ops])).toEqual([["cut", [1]], ["crimp", [2]], ["mount", [3]], ["twist", [4]], ["b1", [5]]]);
  });

  it("the saved body drops the old name of a ПФ whose operations changed", () => {
    const body = pfPayload(placeOp(route, 5, "crimp", 0), route) as { blocks: Array<{ key: string; name?: string; ops: number[] }> };
    expect(body.blocks.map((item) => [item.key, item.name ?? "", item.ops])).toEqual([
      ["cut", "Провод отрезанный", [1]], ["crimp", "", [5, 2]], ["mount", "", [3]], ["twist", "Пара скрученная", [4]],
    ]);
  });
});

const editor: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: { techcards: "edit" } };

function mockFetch(saved?: (body: Record<string, unknown>) => void) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/route") && init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      saved?.(body);
      return new Response(JSON.stringify({ ...techcard({ ...pf, blocks: [] }), version: 5, revision: 3 }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

afterEach(() => vi.restoreAllMocks());

describe("ETK list by ПФ", () => {
  it("shows each ПФ with its input, operations in the order of work, output and norm", async () => {
    mockFetch();
    render(<EtkSheet card={techcard()} principal={editor} onChange={() => undefined} />);
    const nav = await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
    const groups = within(nav).getAllByRole("region");
    expect(groups.map((group) => group.getAttribute("aria-label"))).toEqual(["ПФ-1 · Резка", "ПФ-2 · Опрессовка", "ПФ-3 · Монтаж", "ПФ-4 · Скрутка"]);
    const mount = groups[2];
    expect(within(mount).getAllByRole("button", { name: /^\d\d/ }).map((button) => button.textContent?.slice(0, 2))).toEqual(["05", "03"]);
    expect(within(mount).getByRole("list", { name: "Вход ПФ-3" })).toHaveTextContent("← ПФ-2 Провод с терминалом×2");
    expect(within(mount).getByRole("list", { name: "Вход ПФ-3" })).toHaveTextContent("T-13,5");
    expect(within(mount).getByLabelText("Выход ПФ-3")).toHaveTextContent("→ Жгут");
    expect(mount).toHaveTextContent("11,5 мин");
    expect(mount).toHaveTextContent("изделие");
    fireEvent.click(within(mount).getByRole("button", { name: /^05/ }));
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Монтаж");
  });

  it("moves an operation to another ПФ by drag and by the select, then saves the scheme as a new revision", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    mockFetch((body) => bodies.push(body));
    const changed = vi.fn();
    render(<EtkSheet card={techcard()} principal={editor} onChange={changed} />);
    const nav = await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
    fireEvent.click(within(nav).getByRole("button", { name: "Править ПФ" }));

    // Перетащить 03 в ПФ-2 перед 02.
    const source = within(nav).getByRole("button", { name: /^03/ }).closest("li")!;
    const target = within(nav).getByRole("button", { name: /^02/ }).closest("li")!;
    const data = { setData: vi.fn(), getData: () => "3", effectAllowed: "" };
    fireEvent.dragStart(source, { dataTransfer: data });
    fireEvent.dragOver(target, { dataTransfer: data });
    fireEvent.drop(target, { dataTransfer: data });
    const crimp = within(nav).getAllByRole("region")[1];
    expect(within(crimp).getAllByRole("button", { name: /^\d\d/ }).map((button) => button.textContent?.slice(0, 2))).toEqual(["03", "02"]);

    // Скрутку (04, единственная в своём ПФ) — в ПФ-3: её ПФ сливается с ним.
    fireEvent.change(within(nav).getByRole("combobox", { name: "Перенести операцию 04 в ПФ" }), { target: { value: "mount" } });
    expect(within(nav).getAllByRole("region")).toHaveLength(3);

    fireEvent.click(within(nav).getByRole("button", { name: "Сохранить ПФ · ревизия 3" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(bodies).toHaveLength(1);
    expect(bodies[0].version).toBe(4);
    const saved = bodies[0].route as { blocks: Array<{ key: string; ops: number[]; final: boolean }>; edges: unknown[] };
    expect(saved.blocks.map((item) => [item.key, item.ops, item.final])).toEqual([["cut", [1], false], ["crimp", [3, 2], false], ["mount", [5, 4], true]]);
    expect(saved.edges).toEqual([{ from: "cut", to: "crimp", per_unit: 1 }, { from: "crimp", to: "mount", per_unit: 2 }]);
    expect(await screen.findByText("ПФ сохранены · ревизия 3")).toBeInTheDocument();
  });

  it("blocks saving a scheme that breaks the rules and says why", async () => {
    mockFetch();
    render(<EtkSheet card={techcard()} principal={editor} onChange={() => undefined} />);
    const nav = await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
    fireEvent.click(within(nav).getByRole("button", { name: "Править ПФ" }));
    // Резка (ПФ-1) — в изделие: стрелка из изделия в «Провод с терминалом» замыкает схему.
    fireEvent.change(within(nav).getByRole("combobox", { name: "Перенести операцию 01 в ПФ" }), { target: { value: "mount" } });
    expect(within(nav).getByRole("status")).toHaveTextContent("Схема полуфабрикатов:");
    expect(within(nav).getByRole("button", { name: /Сохранить ПФ/ })).toBeDisabled();
    fireEvent.click(within(nav).getByRole("button", { name: "Отмена" }));
    expect(within(nav).getAllByRole("region")).toHaveLength(4);
  });

  it("a viewer sees the ПФ but not «Править ПФ»", async () => {
    mockFetch();
    render(<EtkSheet card={techcard()} principal={{ ...editor, permissions: {} }} onChange={() => undefined} />);
    const nav = await screen.findByRole("navigation", { name: "Маршрут ЭТК" });
    expect(within(nav).queryByRole("button", { name: "Править ПФ" })).toBeNull();
  });
});

describe("task mode by ПФ", () => {
  it("shows only the task's operations in the order of work, each collapsed", async () => {
    mockFetch();
    const { container } = render(<EtkSheet card={techcard()} principal={editor} onChange={() => undefined} task focus={{ positions: [5, 3] }} />);
    const list = await screen.findByLabelText("Операции задания");
    const items = Array.from(list.querySelectorAll<HTMLDetailsElement>(":scope > details"));
    expect(items.map((item) => item.querySelector("summary")?.textContent)).toEqual(["05МонтажMNT", "03Монтаж в колодкуINS"]);
    expect(items.every((item) => !item.open)).toBe(true);
    expect(screen.queryByRole("navigation", { name: "Маршрут ЭТК" })).toBeNull();
    expect(container.querySelectorAll(".etk-op")).toHaveLength(2);
  });

  it("one operation in focus stays the single-operation view", async () => {
    mockFetch();
    render(<EtkSheet card={techcard()} principal={editor} onChange={() => undefined} task focus={{ code: "TWS", position: 4 }} />);
    expect(await screen.findByRole("heading", { level: 3 })).toHaveTextContent("Скрутка");
    expect(screen.queryByLabelText("Операции задания")).toBeNull();
    expect(focusOperations(operations, { positions: [2] })).toEqual([]);
    expect(focusOperations(operations, { positions: [2, 9, 2, 1] })).toEqual([2, 1]);
  });
});
