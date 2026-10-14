import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

vi.setConfig({ testTimeout: 30_000 });

// Владелец 14.10: «цвета многожильного так и не добавил». «Межплатная» КБ1 «Кабель TC-BLS-5-BLS-5-400-RLB»: UL2575 26AWG
// без записи БД.ПРОВ — один кабель на 4 жилы, а цвета жил видно и задают в строке «Кабели» («Цвета жил»).
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000077", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Кабель TC-BLS-5-BLS-5-400-RLB",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-14T10:00:00+03:00", updated_at: "2026-10-14T10:00:00+03:00", issued_at: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const DS5 = { art: "I-DS1071-SCB01X5", family: "Connfly DS1071", title: "Connfly DS1071", pitch: 2, rows: 1, cols: 5, n: 5, kind: "grid", color: "#efe6cf",
  latch: "windows", grid: [[1, 2, 3, 4, 5]] };
let prov: unknown[] = [];

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterAll(() => { vi.unstubAllGlobals(); });
beforeEach(() => {
  resetPinoutHousings();
  window.localStorage.clear();
  prov = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: path.includes("I-DS1071-SCB01X5") ? DS5 : null });
    if (path.startsWith("/api/references/tech-operations?tab=prov")) return json({ metadata: null, columns: [], records: prov });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.includes("/harness/") && method === "GET") return json({ document: null, design: null, revision: null, warnings: [] });
    if (method !== "GET") throw new Error(`Unexpected write: ${method} ${path}`);
    return json({ metadata: null, items: [], columns: [], records: [] });
  });
});

const kitOf = (wire: string): KitsetBom => ({
  project: "621К.2", index: "КБ1", name: card.product_name, run: "50", part_qty: "50", calc_source: "", calc_ops: [],
  components: ([
    ["Соед", "I-DS1071-SCB01X5", 2], ["Терминал", "T-DS1071-SC600", 8], ["Провод", wire, 0.42], ["ТУТ", "ТУТ 6/3мм", 0.06],
  ] as Array<[string, string, number]>).map(([type, art, qty], i) => ({ id: `kit-${i}`, kd: "", type, name: art, art, qty })),
});
const palette = () => screen.getByRole("group", { name: "Провода" });
const dot = (num: string) => within(palette()).getByRole("button", { name: `Цвет жилы ${num}` });
const fill = (pin: number) => document.querySelector(`.pin-links-table[data-end="А1"] [data-pin="${pin}"] .pin-links-swatch`)?.getAttribute("fill");

it("UL2575 26AWG without a БД.ПРОВ record: 1 cable × 4 cores, uncoloured cores ask «?»", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("UL2575 26AWG")} />);
  expect(await screen.findByLabelText("Жил в кабеле 1")).toHaveValue(4);
  expect(screen.getByLabelText("Кол-во кабеля 1")).toHaveValue(1);
  await waitFor(() => expect(within(palette()).getAllByRole("button", { name: /Цвет жилы/ })).toHaveLength(4));
  expect(dot("1.3")).toHaveAttribute("title", "выбрать цвет жилы");
  expect(dot("1.3")).toHaveTextContent("?");
  expect(screen.getByRole("button", { name: "Цвета жил: кабель 1" })).toHaveTextContent(/Цвета жил1\?2\?3\?4\?/);
});

it("НВ4 0,2 stays a single wire ×4: no «Цвета жил»", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("НВ4 0,2")} />);
  expect(await screen.findByLabelText("Жил в кабеле 1")).toHaveValue(1);
  expect(screen.getByLabelText("Кол-во кабеля 1")).toHaveValue(4);
  expect(screen.queryByRole("button", { name: /^Цвета жил/ })).toBeNull();
});

it("«Цвета жил»: core 3 white shows on the chip and in the scheme row; the 4-core preset fills all", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("UL2575 26AWG")} />);
  const open = await screen.findByRole("button", { name: "Цвета жил: кабель 1" });
  await waitFor(() => expect(fill(3)).toBeTruthy());
  fireEvent.click(open);
  const pop = screen.getByRole("dialog", { name: "Цвета жил кабель 1" });
  fireEvent.click(within(pop).getByRole("button", { name: "Жила 3: белый" }));
  expect(dot("1.3")).toHaveAttribute("title", "белый");
  expect(within(palette()).getAllByRole("button", { name: /^1\.3/ })[0]).toHaveTextContent("белый");
  expect(fill(3)).toBe("#ffffff");
  expect(within(pop).getByRole("group", { name: "Жила 3" })).toHaveTextContent("белый");
  // Типовой порядок — по щелчку, все четыре.
  fireEvent.click(within(pop).getByRole("button", { name: "Типовой порядок: красный, чёрный, белый, зелёный" }));
  expect(["1.1", "1.2", "1.3", "1.4"].map((n) => dot(n).getAttribute("title"))).toEqual(["красный", "чёрный", "белый", "зелёный"]);
  expect(open).toHaveTextContent(/1красный2чёрный3белый4зелёный/);
  // Сбросить жилу — снова «?».
  fireEvent.click(within(pop).getByRole("button", { name: "Сбросить цвет жилы 2" }));
  expect(dot("1.2")).toHaveAttribute("title", "выбрать цвет жилы");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Цвета жил кабель 1" })).toBeNull();
});

it("a БД.ПРОВ record with colours is the default; a pick overrides it, a reset returns it", async () => {
  prov = [{ id: "p1", tab: "prov", display_text: "UL2575 26AWG", source_sheet: "БД.ПРОВ",
    fields: { "Марка": "UL2575 26AWG", "Число жил": "4", "Цвета": "белый, коричневый, зелёный, жёлтый" } }];
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("UL2575 26AWG")} />);
  const open = await screen.findByRole("button", { name: "Цвета жил: кабель 1" });
  await waitFor(() => expect(open).toHaveTextContent(/1белый2коричневый3зелёный4жёлтый/));
  expect(screen.getByLabelText("Кол-во кабеля 1")).toHaveValue(1);
  fireEvent.click(open);
  const pop = screen.getByRole("dialog", { name: "Цвета жил кабель 1" });
  fireEvent.click(within(pop).getByRole("button", { name: "Жила 2: красный" }));
  expect(dot("1.2")).toHaveAttribute("title", "красный");
  fireEvent.click(within(pop).getByRole("button", { name: "Сбросить цвет жилы 2" }));
  expect(dot("1.2")).toHaveAttribute("title", "коричневый");
});
