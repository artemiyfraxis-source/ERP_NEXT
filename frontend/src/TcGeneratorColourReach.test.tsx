import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

vi.setConfig({ testTimeout: 30_000 });

// Владелец 15.10, второй раз: «почему я до сих пор не могу выбрать цвет жил у многожильного». Карта «Межплатная» КБ1 с
// UL2575 26AWG, сохранённая до распознавания кабеля, — 4 провода × 1 жила. Цвет выбирался только крошечным «?» на чипе,
// а там, куда технолог смотрит (провод в руке, «Цвет» в таблице схемы, строка «Кабели»), выбора не было.
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000078", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Кабель TC-BLS-5-BLS-5-400-RLB",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-15T10:00:00+03:00", updated_at: "2026-10-15T10:00:00+03:00", issued_at: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const DS5 = { art: "I-DS1071-SCB01X5", family: "Connfly DS1071", title: "Connfly DS1071", pitch: 2, rows: 1, cols: 5, n: 5, kind: "grid", color: "#efe6cf",
  latch: "windows", grid: [[1, 2, 3, 4, 5]] };

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterAll(() => { vi.unstubAllGlobals(); });
beforeEach(() => {
  resetPinoutHousings();
  window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: path.includes("I-DS1071-SCB01X5") ? DS5 : null });
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
/** Щелчок как у человека: указатель вниз и вверх, затем click — обработчики mousedown/pointerdown срабатывают по-настоящему. */
function press(el: Element) {
  fireEvent.pointerDown(el, { button: 0, pointerId: 1 });
  fireEvent.mouseDown(el, { button: 0 });
  fireEvent.pointerUp(el, { button: 0, pointerId: 1 });
  fireEvent.mouseUp(el, { button: 0 });
  fireEvent.click(el, { button: 0 });
}
/** Карта до распознавания UL2575: 4 провода × 1 жила. */
async function oldCard(wire = "UL2575 26AWG") {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf(wire)} />);
  await waitFor(() => expect(within(palette()).getAllByRole("button", { name: /^Цвет жилы/ })).toHaveLength(4));
  await waitFor(() => expect(fill(4)).toBeTruthy());
  fireEvent.change(screen.getByLabelText("Жил в кабеле 1"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Кол-во кабеля 1"), { target: { value: "4" } });
  await waitFor(() => expect(within(palette()).getAllByRole("button", { name: /^Цвет жилы/ })).toHaveLength(4));
  await waitFor(() => expect(fill(4)).toBeTruthy());
}

it("the «?» on the chip: a real pointer press opens the palette, a colour shows on the chip and in the scheme", async () => {
  await oldCard();
  press(dot("1.1"));
  const pop = screen.getByRole("dialog", { name: "Цвет жилы 1.1" });
  press(within(pop).getByRole("button", { name: "Цвет: красный" }));
  expect(dot("1.1")).toHaveAttribute("title", "красный");
  expect(fill(1)).toBe("#d93a32");
  expect(screen.queryByRole("dialog", { name: "Цвет жилы 1.1" })).toBeNull();
});

it("a wire in hand: «Цвет» next to «Цепь» opens the same palette", async () => {
  await oldCard();
  press(within(palette()).getAllByRole("button", { name: /^1\.3/ })[0]);
  const hand = screen.getByRole("status");
  const pick = within(hand).getByRole("button", { name: "Цвет жилы 1.3: не задан" });
  press(pick);
  press(within(screen.getByRole("dialog", { name: "Цвет жилы 1.3" })).getByRole("button", { name: "Цвет: белый" }));
  expect(dot("1.3")).toHaveAttribute("title", "белый");
  expect(fill(3)).toBe("#ffffff");
  expect(within(hand).getByRole("button", { name: "Цвет жилы 1.3: белый" })).toBeInTheDocument();
});

it("the scheme table «Цвет» cell opens the palette for the wire on that contact", async () => {
  await oldCard();
  const cell = screen.getByRole("button", { name: "Цвет, А1 контакт 4: не задан. Изменить" });
  press(cell);
  press(within(screen.getByRole("dialog", { name: "Цвет жилы 1.4" })).getByRole("button", { name: "Цвет: зелёный" }));
  expect(dot("1.4")).toHaveAttribute("title", "зелёный");
  expect(fill(4)).toBe(document.querySelector(`.pin-links-table[data-end="В1"] [data-pin="4"] .pin-links-swatch`)?.getAttribute("fill"));
  expect(screen.getByRole("button", { name: "Цвет, А1 контакт 4: зелёный. Изменить" })).toBeInTheDocument();
});

it("«Кабели»: UL2575 entered as 4 single wires offers «Это один кабель на 4 жилы»; colours and contacts survive", async () => {
  await oldCard();
  press(dot("1.1"));
  press(within(screen.getByRole("dialog", { name: "Цвет жилы 1.1" })).getByRole("button", { name: "Цвет: красный" }));
  expect(screen.queryByRole("button", { name: /^Цвета жил/ })).toBeNull();
  press(screen.getByRole("button", { name: "Это один кабель на 4 жилы" }));
  expect(screen.getByLabelText("Кол-во кабеля 1")).toHaveValue(1);
  expect(screen.getByLabelText("Жил в кабеле 1")).toHaveValue(4);
  expect(screen.getByRole("button", { name: "Цвета жил: кабель 1" })).toHaveTextContent(/1красный2\?3\?4\?/);
  expect(dot("1.1")).toHaveAttribute("title", "красный");
  expect(fill(1)).toBe("#d93a32");
  expect(screen.queryByRole("button", { name: /Это один кабель/ })).toBeNull();
});

it("single wires (НВ4 0,2 ×4) get no «один кабель» offer", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("НВ4 0,2")} />);
  expect(await screen.findByLabelText("Кол-во кабеля 1")).toHaveValue(4);
  await waitFor(() => expect(within(palette()).getAllByRole("button", { name: /^Цвет жилы/ })).toHaveLength(4));
  expect(screen.queryByRole("button", { name: /Это один кабель/ })).toBeNull();
});
