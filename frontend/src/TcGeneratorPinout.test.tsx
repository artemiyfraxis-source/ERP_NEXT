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

// ── Редактор на схеме (владелец 12.10) ──
const chip = (wire: string) => within(screen.getByRole("group", { name: "Провода" })).getByRole("button", { name: new RegExp(wire) });
const cell = (end: string, pin: number) => screen.getByRole("button", { name: new RegExp(`^${end}, контакт ${pin}:`) });
const pins = (end: string, wire: string) => (screen.getByLabelText(`Контакты ${end}: ${wire}`) as HTMLInputElement).value;
/** Ответы API теста + корпуса разъёмов из базы разъёмов. */
function withHousings(extra: Record<string, unknown>) {
  const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    const m = /connector-families\?article=([^&]+)/.exec(String(input));
    const hs = m && extra[decodeURIComponent(m[1])];
    return hs ? json({ items: [], match: hs }) : base(input, init);
  });
}

it("click a wire, then a pin: the wire goes there, stays in hand for the other connector, Esc drops it", async () => {
  mount();
  await screen.findByLabelText("Контакты А1: НВ4 0,2 белый");
  fireEvent.click(chip("НВ4 0,2 белый"));
  expect(chip("НВ4 0,2 белый")).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(cell("А1", 3));
  expect(pins("А1", "НВ4 0,2 белый")).toBe("3");
  // На В1 контакта ещё нет — провод в руке; клавиатурой: Enter на контакте.
  expect(chip("НВ4 0,2 белый")).toHaveAttribute("aria-pressed", "true");
  fireEvent.keyDown(cell("В1", 2), { key: "Enter" });
  expect(pins("В1", "НВ4 0,2 белый")).toBe("2");
  expect(line("А1.3", "В1.2")).not.toBeNull();
  expect(chip("НВ4 0,2 белый")).toHaveAttribute("aria-pressed", "false");
  expect(cell("А1", 3)).toHaveAccessibleName("А1, контакт 3: 1 · НВ4 0,2 белый");

  fireEvent.click(chip("НВ4 0,2 красный"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(chip("НВ4 0,2 красный")).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(chip("НВ4 0,2 красный"));
  fireEvent.click(chip("НВ4 0,2 красный"));
  expect(chip("НВ4 0,2 красный")).toHaveAttribute("aria-pressed", "false");
});

it("click an occupied pin, then another pin of the same connector: the wires swap; «Снять с контакта» frees it", async () => {
  mount();
  await fillUsb();
  // А1: красный 1, белый 3 → поменять местами.
  fireEvent.click(cell("А1", 1));
  expect(screen.getByRole("status")).toHaveTextContent("НВ4 0,2 красный · А1, конт. 1");
  fireEvent.click(cell("А1", 3));
  expect([pins("А1", "НВ4 0,2 красный"), pins("А1", "НВ4 0,2 белый")]).toEqual(["3", "1"]);
  expect(line("А1.3", "В1.1")).not.toBeNull();
  // Свободный контакт — перенос.
  fireEvent.click(cell("А1", 7));
  fireEvent.click(cell("А1", 8));
  expect(pins("А1", "НВ4 0,2 черный")).toBe("8");
  fireEvent.click(cell("А1", 8));
  fireEvent.click(screen.getByRole("button", { name: "Снять с контакта" }));
  expect(pins("А1", "НВ4 0,2 черный")).toBe("");
});

it("changes the wire colour from the pin: the cables trade colours, the pins stay", async () => {
  mount();
  await fillUsb();
  fireEvent.click(cell("А1", 3));
  fireEvent.change(screen.getByLabelText("Провод на контакте"), { target: { value: "kit-1" } });
  // Белый кабель стал зелёным, зелёный — белым: на А1 3 теперь зелёный провод.
  expect(cell("А1", 3)).toHaveAccessibleName(/НВ4 0,2 зеленый/);
  expect(cell("А1", 5)).toHaveAccessibleName(/НВ4 0,2 белый/);
  expect(screen.getByLabelText("Кабель 1")).toHaveValue("kit-1");
});

it("«Заполнить автоматически»: pin to pin, asks before overwriting; fills by itself when both housings are known", async () => {
  mount();
  await fillUsb();
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  fireEvent.click(screen.getByRole("button", { name: "Заполнить автоматически" }));
  expect(pins("А1", "НВ4 0,2 белый")).toBe("3");
  fireEvent.click(screen.getByRole("button", { name: "Заполнить автоматически" }));
  expect(window.confirm).toHaveBeenLastCalledWith(expect.stringContaining("Заменить распиновку автоматической"));
  expect(["белый", "зеленый", "красный", "черный"].map((c) => [pins("А1", `НВ4 0,2 ${c}`), pins("В1", `НВ4 0,2 ${c}`)])).toEqual([["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"]]);
  expect(screen.getByText("А1 (PHR-8): 4 провода на 8 контактов, свободны 5–8.")).toBeInTheDocument();
  expect(screen.getByText("В1 (XHP-4): число контактов разъёма неизвестно — 4 провода по порядку на 1–4.")).toBeInTheDocument();

  // Оба корпуса в базе: пустая распиновка заполняется сама, но остаётся «не проверена».
  cleanup();
  resetPinoutHousings();
  withHousings({ "XHP-4": { ...PHR8, art: "XHP-4", family: "jst-xh", cols: 3, n: 3, grid: [[1, 2, 3]] } });
  mount();
  // В XHP-3 4 провода не помещаются — само не заполняет, говорит почему.
  expect(await screen.findByText("В1 (XHP-4): 3 контакта, а 4 провода — всем контакта не хватит.")).toBeInTheDocument();
  expect(pins("А1", "НВ4 0,2 белый")).toBe("");
  cleanup();
  resetPinoutHousings();
  withHousings({ "XHP-4": { ...PHR8, art: "XHP-4", family: "jst-xh", cols: 4, n: 4, grid: [[1, 2, 3, 4]] } });
  mount();
  await waitFor(() => expect(pins("В1", "НВ4 0,2 черный")).toBe("4"));
  expect(pins("А1", "НВ4 0,2 черный")).toBe("4");
  expect(within(screen.getByRole("region", { name: "Соединения по распиновке" })).getByText("Распиновка не проверена: А1, В1")).toBeInTheDocument();
});

it("one connector: each wire runs to its free end, which shows tinning, stripping or no processing live", async () => {
  const SHR = { ...PHR8, art: "SHR-02V-S-B", family: "jst-sh", cols: 2, n: 2, grid: [[2, 1]] };
  withHousings({ "SHR-02V-S-B": SHR });
  const openEnd: KitsetBom = { ...kit, name: "Кабельная сборка SHR-02V-S-B to Open-End 30AWG 100mm", components: [
    { type: "Провод", name: "Силиконовый 30AWG черный", art: "Силиконовый 30AWG черный", qty: 0.2 },
    { type: "Соед", name: "Колодка SH 2", art: "SHR-02V-S-B", qty: 1 },
    { type: "Терминал", name: "Контакт SH", art: "SSH-003T-P0.2-H", qty: 2 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })) };
  mount({ kit: openEnd });
  const figure = await screen.findByRole("figure", { name: "Схема соединений" });
  // Провод ×2 сам встал на контакты 1 и 2.
  await waitFor(() => expect(figure.querySelectorAll(".pin-free")).toHaveLength(2));
  expect(pins("А1", "Силиконовый 30AWG черный")).toBe("1, 2");
  expect(figure).toHaveTextContent("Сторона В — свободные концы");
  expect(figure).toHaveTextContent("1.1 · Силиконовый 30AWG черный · длина 100 мм");
  expect(figure).not.toHaveTextContent("Линии появятся");
  const kinds = () => [...figure.querySelectorAll(".pin-free")].map((g) => `${g.getAttribute("data-free")}: ${g.querySelector(".pin-free-label")!.textContent}`);
  const toggle = (name: RegExp, on: boolean) => {
    const sw = screen.getByRole("switch", { name });
    if ((sw as HTMLInputElement).checked !== on) fireEvent.click(sw);
  };
  toggle(/Лужение свободных концов/, false);
  toggle(/Зачистка свободных концов/, false);
  expect(kinds()).toEqual(["none: без обработки", "none: без обработки"]);
  toggle(/Зачистка свободных концов/, true);
  fireEvent.change(screen.getByLabelText("Длина зачистки"), { target: { value: "4" } });
  expect(kinds()).toEqual(["strip: зачистка L=4 мм", "strip: зачистка L=4 мм"]);
  toggle(/Лужение свободных концов/, true);
  fireEvent.change(screen.getByLabelText("Зачистка под лужение"), { target: { value: "5" } });
  expect(kinds()).toEqual(["tin: лужение L=5 мм", "tin: лужение L=5 мм"]);
});
