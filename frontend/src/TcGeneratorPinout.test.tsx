import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, onTestFinished, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000009", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Жгут интерфейсный 300мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-10T10:00:00+03:00", updated_at: "2026-10-10T10:00:00+03:00", issued_at: null,
};

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const PHR8 = { art: "PHR-8", family: "jst-ph", title: "JST PH", pitch: 2, rows: 1, cols: 8, n: 8, kind: "grid", color: "#efe6cf", latch: "windows", grid: [[1, 2, 3, 4, 5, 6, 7, 8]] };

beforeEach(() => {
  resetPinoutHousings();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/connector-families?article=PHR-8")) return json({ items: [], match: PHR8 });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

const kit: KitsetBom = {
  project: "621К.2", index: "КБ1", name: "Жгут интерфейсный 300мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { type: "Провод", name: "НВ4 0,2 белый", art: "НВ4 0,2 белый", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 зеленый", art: "НВ4 0,2 зеленый", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 0.35 },
    { type: "Соед", name: "Колодка PH 8", art: "PHR-8", qty: 1 },
    { type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
    { type: "Терминал", name: "Контакт PH", art: "SPH-002T-P0.5S", qty: 4 },
    { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
};

const USB: Record<string, [string, string]> = {
  "НВ4 0,2 красный": ["1", "1"], "НВ4 0,2 белый": ["3", "2"], "НВ4 0,2 зеленый": ["5", "3"], "НВ4 0,2 черный": ["7", "4"],
};
async function fillUsb() {
  await screen.findByLabelText("Контакты А1: НВ4 0,2 белый");
  Object.entries(USB).forEach(([wire, [a, b]]) => {
    fireEvent.change(screen.getByLabelText(`Контакты А1: ${wire}`), { target: { value: a } });
    fireEvent.change(screen.getByLabelText(`Контакты В1: ${wire}`), { target: { value: b } });
  });
}
const mount = (props: Partial<Parameters<typeof TcGenerator>[0]> = {}) => {
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit} {...props} />);
  return onBuild;
};
const line = (from: string, to: string) => document.querySelector(`.pin-wire[data-from="${from}"][data-to="${to}"]`);

it("draws one line per wire from the A contact to the B contact in the wire's colour, free contacts grey", async () => {
  mount();
  await fillUsb();
  const figure = screen.getByRole("figure", { name: "Схема соединений" });
  expect(figure.querySelectorAll(".pin-wire")).toHaveLength(4);
  const stroke = (from: string, to: string) => line(from, to)?.querySelector(".pin-line")?.getAttribute("stroke");
  expect(stroke("А1.3", "В1.2")).toBe("#f4f4f4");
  expect(stroke("А1.5", "В1.3")).toBe("#2e9e4f");
  expect(stroke("А1.1", "В1.1")).toBe("#d7322d");
  expect(stroke("А1.7", "В1.4")).toBe("#222");
  // Белая линия — с тёмной обводкой, тёмная — без.
  expect(line("А1.3", "В1.2")!.querySelectorAll("path")).toHaveLength(2);
  expect(line("А1.7", "В1.4")!.querySelectorAll("path")).toHaveLength(1);
  expect(line("А1.3", "В1.2")!.querySelector("title")).toHaveTextContent("НВ4 0,2 белый: А1 конт. 3 → В1 конт. 2");
  // А1 — PHR-8 из базы разъёмов: 8 контактов, 2, 4, 6, 8 свободны.
  await waitFor(() => expect(figure.querySelectorAll('[data-end="А1"] .pin-cell')).toHaveLength(8));
  const used = (end: string) => [...figure.querySelectorAll(`[data-end="${end}"] .pin-cell.is-used`)].map((cell) => cell.getAttribute("data-pin"));
  expect(used("А1")).toEqual(["1", "3", "5", "7"]);
  expect(used("В1")).toEqual(["1", "2", "3", "4"]);

  // Контакт, занятый двумя проводами, — красный.
  fireEvent.change(screen.getByLabelText("Контакты А1: НВ4 0,2 черный"), { target: { value: "3" } });
  expect(figure.querySelector('[data-end="А1"] .pin-cell.is-dup')).toHaveAttribute("data-pin", "3");
  expect(screen.getByText(/Контакт 3 занят двумя проводами/)).toBeInTheDocument();
});

it("warns at the connector and at «Соединения», and «Создать» asks about unchecked connectors first", async () => {
  const scrolled = vi.fn();
  const original = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = scrolled;
  onTestFinished(() => { Element.prototype.scrollIntoView = original; });
  const onBuild = mount();
  const sideA = await screen.findByRole("region", { name: "Сторона А" });
  expect(within(sideA).getAllByText("Распиновка не задана").length).toBeGreaterThan(0);
  expect(within(screen.getByRole("region", { name: "Соединения по распиновке" })).getByText("Распиновка не задана: А1, В1")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  let ask = screen.getByRole("dialog", { name: "Распиновка не проверена" });
  expect(within(ask).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    expect.stringContaining("А1PHR-8контакты не заданы"), expect.stringContaining("В1XHP-4контакты не заданы"),
  ]);
  expect(onBuild).not.toHaveBeenCalled();
  // «Проверить» — к панели контактов разъёма.
  fireEvent.click(within(within(ask).getAllByRole("listitem")[1]).getByRole("button", { name: "Проверить" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(scrolled).toHaveBeenCalled());
  expect(scrolled.mock.contexts.at(-1)).toHaveAttribute("id", expect.stringMatching(/^pinout-/));
  expect(document.activeElement).toHaveAccessibleName("Контакты В1: НВ4 0,2 белый");

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  ask = screen.getByRole("dialog", { name: "Распиновка не проверена" });
  fireEvent.click(within(ask).getByRole("button", { name: "Создать всё равно" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).not.toHaveProperty("pinout");
});

it("confirmed pinout collapses to a summary, builds without asking, survives a reload and resets on a change", async () => {
  const onBuild = mount();
  await fillUsb();
  for (const button of screen.getAllByRole("button", { name: "Подтвердить распиновку" })) fireEvent.click(button);
  expect(screen.getAllByText("✓ Проверена: 4 провода, перекрёст 3→2, 5→3, 7→4")).toHaveLength(2);
  expect(screen.queryByLabelText("Контакты А1: НВ4 0,2 белый")).not.toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Соединения по распиновке" })).getByText("Распиновка проверена")).toBeInTheDocument();
  // Список соединений — под схемой, как раньше.
  expect(screen.getByRole("region", { name: "Соединения по распиновке" })).toHaveTextContent("НВ4 0,2 белый · А1 → В1: 3→2");

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.pinout.ends.map((end: { end: string; checked: boolean }) => [end.end, end.checked])).toEqual([["А1", true], ["В1", true]]);

  // Перезагрузка: форма из состава + распиновка и проверка из build_config.
  cleanup();
  const built = { ...card, version: 2, document: { kit: {}, operations: [], warnings: [], build_config: config } } as ProjectTechcard;
  mount({ card: built });
  expect(await screen.findAllByText("✓ Проверена: 4 провода, перекрёст 3→2, 5→3, 7→4")).toHaveLength(2);
  expect(line("А1.3", "В1.2")).not.toBeNull();
  // Правка контакта снимает проверку с обоих концов провода.
  fireEvent.click(screen.getAllByRole("button", { name: "Изменить" })[1]);
  fireEvent.change(screen.getByLabelText("Контакты В1: НВ4 0,2 белый"), { target: { value: "4" } });
  expect(screen.getAllByRole("button", { name: "Подтвердить распиновку" })).toHaveLength(2);
  expect(within(screen.getByRole("region", { name: "Соединения по распиновке" })).getByText("Распиновка не проверена: А1, В1")).toBeInTheDocument();
});
