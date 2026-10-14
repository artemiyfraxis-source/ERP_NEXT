import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";

// «Чертёж» в схеме связей грузит редактор жгута лениво: в холодном прогоне первая сборка дольше 5 с.
vi.setConfig({ testTimeout: 45_000 });
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { resetPreviewTemplates } from "./GenDrawingPreview";
import type { KitsetBom, ProjectTechcard } from "./api";

// Схема связей генератора (владелец 14.10: «что бы и чертёжик был и схема связей была»): провода ставят на торцах, под ними
// сразу — таблицы разъёмов как на схеме Э4 и линии с контакта на контакт; вкладка «Чертёж» — чертёж по форме, без записи.

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000041", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Жгут интерфейсный 300мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-14T10:00:00+03:00", updated_at: "2026-10-14T10:00:00+03:00", issued_at: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const housing = (art: string, title: string, n: number) => ({ art, family: title, title, pitch: 2, rows: 1, cols: n, n, kind: "grid", color: "#efe6cf", latch: "windows",
  grid: [Array.from({ length: n }, (_, i) => i + 1)] });
const HOUSINGS: Record<string, unknown> = {
  "I-DS1071-SCB01X4": housing("I-DS1071-SCB01X4", "Connfly DS1071", 4), "PHR-4": housing("PHR-4", "JST PH", 4),
  "PHR-6": housing("PHR-6", "JST PH", 6), "XHP-6": housing("XHP-6", "JST XH", 6),
};
let calls: Array<{ method: string; path: string }>;

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterAll(() => { vi.unstubAllGlobals(); });
beforeEach(() => {
  resetPinoutHousings();
  resetPreviewTemplates();
  window.localStorage.clear();
  calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path });
    const family = /connector-families\?article=([^&]+)/.exec(path);
    if (family) return json({ items: [], match: HOUSINGS[decodeURIComponent(family[1])] ?? null });
    if (path.startsWith("/api/references/tech-operations?tab=prov")) return json({ metadata: null, columns: [], records: [
      { id: "p1", tab: "prov", display_text: "LiYCY 4x0,25", source_sheet: "БД.ПРОВ",
        fields: { "Марка": "LiYCY 4x0,25", "Число жил": "4", "Цвета": "белый, коричневый, зелёный, жёлтый", "Экран": "оплётка луж. медь" } },
    ] });
    if (path === "/api/techcards/harness-components") return json([]);
    if (path.includes("/harness/") && method === "GET") return json({ document: null, design: null, revision: null, warnings: [] });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (method !== "GET") throw new Error(`Unexpected write: ${method} ${path}`);
    return json({ metadata: null, items: [], columns: [], records: [] });
  });
});

const kitOf = (components: Array<[string, string, number]>): KitsetBom => ({
  project: "621К.2", index: "КБ1", name: card.product_name, run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: components.map(([type, art, qty], i) => ({ id: `kit-${i}`, kd: "", type, name: art, art, qty })),
});
const USB = kitOf([
  ["Провод", "НВ4 0,2 красный", 0.3], ["Провод", "НВ4 0,2 черный", 0.3], ["Провод", "НВ4 0,2 белый", 0.3], ["Провод", "НВ4 0,2 зеленый", 0.3],
  ["Соед", "I-DS1071-SCB01X4", 1], ["Соед", "PHR-4", 1], ["Терминал", "SPH-002T-P0.5S", 4],
]);

const scheme = () => screen.getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
const table = (end: string) => scheme().querySelector(`.pin-links-table[data-end="${end}"]`)!;
const row = (end: string, pin: number) => table(end).querySelector(`[data-pin="${pin}"]`)!;
const lines = () => [...scheme().querySelectorAll(".pin-links-line")].map((l) => `${l.getAttribute("data-a")}→${l.getAttribute("data-b")}`);
const chip = (wire: RegExp) => within(screen.getByRole("group", { name: "Провода" })).getAllByRole("button", { name: wire })[0];
const cell = (end: string, pin: number) => screen.getByRole("button", { name: new RegExp(`^${end}, контакт ${pin}:`) });
/** Вкладка и кабели формы: новая карта «Сложной сборки» открывается без кабелей (владелец 13.10) — набираем их из состава. */
async function form(tab: string, wires: string[]) {
  fireEvent.click(await screen.findByRole("tab", { name: tab }));
  wires.forEach((wire, i) => {
    if (!screen.queryByLabelText(`Кабель ${i + 1}`)) fireEvent.click(screen.getByRole("button", { name: "+ Добавить кабель" }));
    const select = screen.getByLabelText(`Кабель ${i + 1}`) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === wire)!.value } });
  });
}

it("«Межплатная» I-DS1071 ↔ PHR-4: E4 tables for both connectors, a line appears the moment a wire is placed on a face, twisted pair drawn twisted", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={USB} />);
  await form("Межплатная", ["НВ4 0,2 красный", "НВ4 0,2 черный", "НВ4 0,2 белый", "НВ4 0,2 зеленый"]);
  await screen.findByLabelText("Контакты А1: НВ4 0,2 красный");
  // Оба корпуса известны — провода встали пин-в-пин сами.
  await waitFor(() => expect(lines()).toHaveLength(4));
  expect(lines()).toEqual(["А1·1→В1·1", "А1·2→В1·2", "А1·3→В1·3", "А1·4→В1·4"]);
  const a1 = table("А1");
  expect(a1).toHaveTextContent("XS1");
  for (const head of ["Цвет", "Сечение", "Марка", "Терминал", "№"]) expect(a1).toHaveTextContent(head);
  expect(a1).toHaveTextContent("Connfly DS1071 I-DS1071-SCB01X4");
  expect(table("В1")).toHaveTextContent("JST PH PHR-4");
  expect(row("А1", 1)).toHaveTextContent("0,2 мм²НВ4 0,2 красныйSPH-002T-P0.5S1");
  expect(row("А1", 1).querySelector(".pin-links-swatch")).toHaveAttribute("fill", expect.stringMatching(/^#/));
  // Перекрёст на торце: зелёный — с В1·4 на В1·3 (через руку) — линия сразу идёт на новый контакт.
  fireEvent.click(cell("В1", 4));
  fireEvent.click(cell("В1", 3));
  expect(lines()).toEqual(["А1·1→В1·1", "А1·2→В1·2", "А1·3→В1·4", "А1·4→В1·3"]);
  expect(row("В1", 3)).toHaveAttribute("data-wires", "4");
  // Снять провод с контакта — линия к нему пропадает, остаётся «не задан» от другого конца.
  fireEvent.click(cell("В1", 1));
  fireEvent.click(screen.getByRole("button", { name: "Снять с контакта" }));
  expect(row("В1", 1)).not.toHaveAttribute("data-wires");
  expect(scheme().querySelector('.pin-links-line[data-wire="1"]')).toHaveAttribute("data-free", "open");
  // Поставить его обратно с палитры — линия снова с контакта на контакт.
  fireEvent.click(chip(/^1\s*НВ4 0,2 красный/));
  fireEvent.click(cell("В1", 1));
  expect(lines()).toContain("А1·1→В1·1");

  // Свивка красного с чёрным: их линии свиты, подпись «свитая пара».
  const twist = screen.getByRole("switch", { name: /Свивка/ });
  if (!(twist as HTMLInputElement).checked) fireEvent.click(twist);
  fireEvent.change(screen.getByLabelText("Свивка 1, провод 1"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("Свивка 1, провод 2"), { target: { value: "1" } });
  await waitFor(() => expect([...scheme().querySelectorAll('.pin-links-line[data-twisted="true"]')].map((l) => l.getAttribute("data-wire"))).toEqual(["1", "2"]));
  expect(scheme()).toHaveTextContent("свитая пара");

  // «Чертёж» — тот же чертёж по форме, только просмотр: редактор открылся, на сервер ничего не записано.
  fireEvent.click(screen.getByRole("tab", { name: "Чертёж" }));
  const view = await screen.findByRole("group", { name: "Чертёж по форме" }, { timeout: 30_000 });
  await waitFor(() => expect(view.querySelector(".harness-root .he-host")).not.toBeNull(), { timeout: 30_000 });
  expect(screen.getByText("По форме · в проект не сохранён")).toBeInTheDocument();
  expect(calls.filter((call) => call.method !== "GET" && call.path.includes("/harness"))).toEqual([]);
  // Обратно на схему — таблицы на месте.
  fireEvent.click(screen.getByRole("tab", { name: "Схема связей" }));
  expect(lines()).toHaveLength(4);
});

it("a multicore cable with a shield: cores in their colours, the shield on its own contact, the drawing builds", async () => {
  const kit = kitOf([["Кабель", "LiYCY 4x0,25", 0.3], ["Соед", "PHR-6", 1], ["Соед", "XHP-6", 1]]);
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  expect(await screen.findByLabelText("Жил в кабеле 1")).toHaveValue(4);
  // Оба корпуса известны — жилы встали пин-в-пин сами; экран ещё не на контакте.
  await waitFor(() => expect(lines()).toEqual(["А1·1→В1·1", "А1·2→В1·2", "А1·3→В1·3", "А1·4→В1·4"]));
  fireEvent.change(screen.getByLabelText("Контакты А1: LiYCY 4x0,25"), { target: { value: "1-5" } });
  fireEvent.change(screen.getByLabelText("Контакты В1: LiYCY 4x0,25"), { target: { value: "1-5" } });
  await waitFor(() => expect(lines()).toEqual(["А1·1→В1·1", "А1·2→В1·2", "А1·3→В1·3", "А1·4→В1·4", "А1·5→В1·5"]));
  // Жилы — цветами из БД.ПРОВ, экран — пятым контактом, без сечения.
  expect(row("А1", 2)).toHaveAttribute("data-wires", "1.2");
  expect(row("А1", 2).querySelector(".pin-links-swatch")!.getAttribute("fill")).not.toBe(row("А1", 1).querySelector(".pin-links-swatch")!.getAttribute("fill"));
  expect(row("А1", 5)).toHaveAttribute("data-wires", "1.Э");
  expect(row("А1", 5)).toHaveTextContent("экран LiYCY 4x0,25");
  expect(row("А1", 5)).not.toHaveTextContent("мм²");
  expect(row("А1", 6)).not.toHaveAttribute("data-wires");
  // Цвет жилы с палитры — сразу в таблице.
  const before = row("А1", 1).querySelector(".pin-links-swatch")!.getAttribute("fill");
  fireEvent.click(screen.getByRole("button", { name: "Цвет жилы 1.1" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Цвет жилы 1.1" })).getByRole("button", { name: "Цвет: красный" }));
  expect(row("А1", 1).querySelector(".pin-links-swatch")!.getAttribute("fill")).not.toBe(before);

  fireEvent.click(screen.getByRole("tab", { name: "Чертёж" }));
  const view = await screen.findByRole("group", { name: "Чертёж по форме" }, { timeout: 30_000 });
  await waitFor(() => expect(view.querySelector(".harness-root .he-host")).not.toBeNull(), { timeout: 30_000 });
  expect(calls.filter((call) => call.method !== "GET" && call.path.includes("/harness"))).toEqual([]);
});

it("«Сложная сборка»: three connectors on A, two on B — every table and line, a branch to the second B connector drawn too", async () => {
  const kit = kitOf([
    ["Провод", "НВ4 0,2 красный", 0.3], ["Провод", "НВ4 0,2 черный", 0.3], ["Провод", "НВ4 0,2 белый", 0.3],
    ["Соед", "PHR-6", 3], ["Соед", "XHP-6", 2],
  ]);
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  await form("Сложная сборка", ["НВ4 0,2 красный", "НВ4 0,2 черный", "НВ4 0,2 белый"]);
  for (const [side, count, art] of [["А", 3, "PHR-6"], ["В", 2, "XHP-6"]] as const) {
    for (let n = 1; n <= count; n++) {
      if (!screen.queryByLabelText(`Разъём ${side}${n}`)) fireEvent.click(screen.getByRole("button", { name: `Добавить разъём на сторону ${side}` }));
      const select = screen.getByLabelText(`Разъём ${side}${n}`) as HTMLSelectElement;
      fireEvent.change(select, { target: { value: [...select.options].find((o) => o.textContent === art)!.value } });
    }
  }
  await waitFor(() => expect(scheme().querySelectorAll(".pin-links-table")).toHaveLength(5));
  const place = (wire: RegExp, end: string, pin: number) => { fireEvent.click(chip(wire)); fireEvent.click(cell(end, pin)); fireEvent.keyDown(window, { key: "Escape" }); };
  place(/^1\s*НВ4 0,2 красный/, "А1", 1); place(/^1\s*НВ4 0,2 красный/, "В1", 1);
  place(/^2\s*НВ4 0,2 черный/, "А2", 1); place(/^2\s*НВ4 0,2 черный/, "В1", 2);
  place(/^3\s*НВ4 0,2 белый/, "А3", 1); place(/^3\s*НВ4 0,2 белый/, "В2", 1);
  expect(lines()).toEqual(["А1·1→В1·1", "А2·1→В1·2", "А3·1→В2·1"]);
  expect([...scheme().querySelectorAll(".pin-links-table")].map((t) => t.getAttribute("data-end"))).toEqual(["А1", "А2", "А3", "В1", "В2"]);
  // Ответвление красного с А1·1 на В2·2 — сростком: вторая линия того же провода.
  fireEvent.click(cell("А1", 1));
  fireEvent.click(cell("В2", 2));
  fireEvent.click(within(screen.getByRole("group", { name: "Ответвление" })).getByRole("button", { name: "Добавить ответвление" }));
  await waitFor(() => expect(lines()).toContain("А1·1→В2·2"));
  expect(row("В2", 2)).toHaveAttribute("data-wires");
});
