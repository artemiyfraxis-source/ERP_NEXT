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

it("draws each connector's face: wire colour in the cavity, hover «контакт N · провод», free cavities plain, unknown housing numbered boxes", async () => {
  mount();
  await fillUsb();
  const figure = screen.getByRole("figure", { name: "Схема соединений" });
  // А1 — PHR-8 из базы разъёмов: 8 контактов, 2, 4, 6, 8 свободны.
  await waitFor(() => expect(figure.querySelectorAll('[data-end="А1"] .pin-cell')).toHaveLength(8));
  const used = (end: string) => [...figure.querySelectorAll(`[data-end="${end}"] .pin-cell.is-used`)].map((cell) => cell.getAttribute("data-pin"));
  expect(used("А1")).toEqual(["1", "3", "5", "7"]);
  expect(used("В1")).toEqual(["1", "2", "3", "4"]);
  // А1 — торец корпуса из базы: гнёзда ≥ 28 px, подсказка «контакт 3 · провод», цвет жилы в самом рисунке.
  const a1 = figure.querySelector('[data-end="А1"] svg.pin-face')!;
  expect(a1).not.toBeNull();
  expect(figure.querySelector('[data-end="А1"] [data-pin="3"] title')).toHaveTextContent("контакт 3 · НВ4 0,2 белый");
  expect(figure.querySelector('[data-end="А1"] [data-pin="2"] title')).toHaveTextContent("контакт 2 · свободен");
  const hitR = Number(figure.querySelector('[data-end="А1"] .pin-hit')!.getAttribute("r"));
  expect(hitR * 2).toBeGreaterThanOrEqual(28);
  expect(a1.innerHTML).toContain('fill="#f4f4f4"');
  // В1 — XHP-4 нет в базе: квадраты по порядку и пометка.
  expect(figure.querySelector('[data-end="В1"] svg')).toBeNull();
  expect(figure.querySelectorAll('[data-end="В1"] .pin-box')).toHaveLength(4);
  expect(within(figure).getAllByText("вид разъёма неизвестен — номера по порядку")).toHaveLength(1);

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
  // Текстовой сводки «провод · А1 → В1: 3→2» под схемой больше нет (14.10): соединения — в схеме связей.
  expect(screen.getByRole("region", { name: "Соединения по распиновке" })).not.toHaveTextContent("А1 → В1: 3→2");

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
/** Провод с палитры на занятый контакт: «Поставить вместо» (второй вариант — двойной обжим, см. TcGeneratorDoubleCrimp). */
const instead = () => { const offer = screen.queryByRole("group", { name: "Контакт занят" }); if (offer) fireEvent.click(within(offer).getByRole("button", { name: "Поставить вместо" })); };
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
  // Провод ×2 сам встал на контакты 1 и 2; торец SHR-02 нарисован с двумя гнёздами.
  await waitFor(() => expect(figure.querySelectorAll(".pin-free")).toHaveLength(2));
  expect(figure.querySelectorAll('[data-end="А1"] svg .pin-cell')).toHaveLength(2);
  expect(pins("А1", "Силиконовый 30AWG черный")).toBe("1, 2");
  expect(figure).toHaveTextContent("Сторона В — свободные концы");
  expect(figure).toHaveTextContent("1.1 · Силиконовый 30AWG черный · длина 100 мм");
  expect(figure).not.toHaveTextContent("Соединения появятся");
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

// ── Торцы разъёмов (владелец 12.10: «тыкать в контакт разъёма, а не просто так») ──
const XH4 = { art: "XHP-4", family: "jst-xh", title: "JST XH", pitch: 2.5, rows: 1, cols: 4, n: 4, kind: "grid", color: "#efe6cf", latch: "ramp", grid: [[4, 3, 2, 1]] };
const LP4 = { art: "LP-12-J04SX-02-401", family: "cnlinko-lp12", title: "CNLINKO LP-12", rows: 1, cols: 4, n: 4, kind: "round", color: "#222", latch: "none", grid: [],
  round: [{ pin: 1, x: 0, y: -0.4 }, { pin: 2, x: 0.42, y: 0 }, { pin: 3, x: -0.4, y: 0 }, { pin: 4, x: 0, y: 0.41 }] };

it("a rectangular face: click a wire chip, then a cavity assigns it; Enter assigns from the keyboard; occupied cavity swaps", async () => {
  withHousings({ "XHP-4": XH4 });
  mount();
  await screen.findByLabelText("Контакты А1: НВ4 0,2 белый");
  await waitFor(() => expect(document.querySelectorAll('[data-end="В1"] svg .pin-cell')).toHaveLength(4));
  expect(document.querySelector('[data-end="В1"] .pin-box')).toBeNull();
  fireEvent.click(chip("НВ4 0,2 красный"));
  fireEvent.click(cell("В1", 3));
  instead();
  expect(pins("В1", "НВ4 0,2 красный")).toBe("3");
  expect(cell("В1", 3)).toHaveAccessibleName("В1, контакт 3: 3 · НВ4 0,2 красный");
  // Клавиатура: гнездо в порядке Tab, Enter ставит провод из руки.
  expect(cell("В1", 2)).toHaveAttribute("tabindex", "0");
  fireEvent.click(chip("НВ4 0,2 белый"));
  fireEvent.keyDown(cell("В1", 2), { key: "Enter" });
  instead();
  expect(pins("В1", "НВ4 0,2 белый")).toBe("2");
  // Занятое гнездо, затем другое гнездо того же разъёма — провода меняются местами.
  fireEvent.click(cell("В1", 3));
  fireEvent.click(cell("В1", 2));
  expect([pins("В1", "НВ4 0,2 красный"), pins("В1", "НВ4 0,2 белый")]).toEqual(["2", "3"]);
  // Занятое гнездо показывает цвет жилы и номер провода.
  const held = document.querySelector('[data-end="В1"] [data-pin="2"]')!;
  expect(held.querySelector(".pin-badge")).toHaveTextContent("3");
  expect(held).toHaveClass("is-used");
});

it("a round connector draws its pin ring and the pins are clickable too", async () => {
  withHousings({ "XHP-4": LP4 });
  mount();
  await screen.findByLabelText("Контакты А1: НВ4 0,2 белый");
  await waitFor(() => expect(document.querySelectorAll('[data-end="В1"] svg .pin-cell')).toHaveLength(4));
  const hits = [...document.querySelectorAll('[data-end="В1"] .pin-hit')];
  expect(hits.every((c) => Number(c.getAttribute("r")) * 2 >= 28)).toBe(true);
  fireEvent.click(chip("НВ4 0,2 зеленый"));
  fireEvent.click(cell("В1", 4));
  instead();
  expect(pins("В1", "НВ4 0,2 зеленый")).toBe("4");
  expect(document.querySelector('[data-end="В1"] [data-pin="4"] title')).toHaveTextContent("контакт 4 · НВ4 0,2 зеленый");
});

// ── Ответвление и раздача по нескольким разъёмам (владелец 12.10) ──
it("click a connected pin, then a cavity of the second B connector: «Добавить ответвление» — splice or double crimp; the chip shows both ends", async () => {
  withHousings({ "XHP-4": { ...PHR8, art: "XHP-4", family: "jst-xh", cols: 4, n: 4, grid: [[1, 2, 3, 4]] } });
  mount();
  await fillUsb();
  fireEvent.click(screen.getByRole("tab", { name: "Сложная сборка" }));
  fireEvent.click(screen.getByRole("button", { name: "Добавить разъём на сторону В" }));
  const second = screen.getByLabelText("Разъём В2");
  fireEvent.change(second, { target: { value: [...(second as HTMLSelectElement).options].find((o) => o.textContent === "XHP-4")!.value } });
  await waitFor(() => expect(document.querySelectorAll('[data-end="В2"] svg .pin-cell')).toHaveLength(4));
  // Белый: А1·3 → В1·2. Щелчок по контакту А1·3 и затем по гнезду В2·4 — предложение ответвления.
  fireEvent.click(cell("А1", 3));
  fireEvent.click(cell("В2", 4));
  const offer = screen.getByRole("group", { name: "Ответвление" });
  expect(offer).toHaveTextContent("А1, конт. 3 → В2, конт. 4");
  fireEvent.click(within(offer).getByRole("button", { name: "двойной обжим" }));
  fireEvent.click(within(offer).getByRole("button", { name: "Добавить ответвление" }));
  expect(screen.queryByRole("group", { name: "Ответвление" })).not.toBeInTheDocument();
  expect(chip("НВ4 0,2 белый")).toHaveTextContent("А1·3 → В1·2 + В2·4");
  expect(cell("В2", 4)).toHaveAccessibleName("В2, контакт 4: 5 · НВ4 0,2 белый");
  const route = document.querySelector('.pin-route[data-wire="1"]') ?? document.querySelector('.pin-route[data-from="А1·3"]')!;
  // Список «провод → контакты» убран (14.10): в строке ответвления только переключатели режима.
  expect(route.querySelectorAll(".pin-route-to, .pin-glyph")).toHaveLength(0);
  // Режим переключается на самом ответвлении: сросток.
  fireEvent.click(within(route as HTMLElement).getByRole("button", { name: "сросток" }));
  expect(chip("НВ4 0,2 белый")).toHaveTextContent("А1·3 → В1·2 + В2·4");
  expect(within(route as HTMLElement).getByRole("button", { name: "сросток" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(route as HTMLElement).getByRole("button", { name: "Убрать" }));
  expect(chip("НВ4 0,2 белый")).toHaveTextContent("А1·3 → В1·2");
  expect(chip("НВ4 0,2 белый")).not.toHaveTextContent("В2");
});

it("a core colour is picked on the palette: single, two-colour (base + stripe), reset; it reaches the chip, the cavity and the config", async () => {
  const cable: KitsetBom = { ...kit, name: "Кабель Helukabel", components: [
    { type: "Провод", name: "Helukabel 15201", art: "Helukabel 15201", qty: 3 },
    { type: "Провод", name: "Helukabel 15202", art: "Helukabel 15202", qty: 3 },
    { type: "Соед", name: "Колодка PH 8", art: "PHR-8", qty: 1 },
    { type: "Соед", name: "Колодка XH 4", art: "XHP-4", qty: 1 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })) };
  const onBuild = mount({ kit: cable });
  const dot = (num: string) => screen.getByRole("button", { name: `Цвет жилы ${num}` });
  await screen.findByRole("group", { name: "Провода" });
  expect(screen.getByRole("group", { name: "Провода" })).toHaveTextContent("Helukabel 15202");
  // Один цвет: щелчок по кружку, затем по цвету палитры — палитра закрывается, цвет в чипе.
  fireEvent.click(dot("1"));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Цвет жилы 1" })).getByRole("button", { name: "Цвет: красный" }));
  expect(screen.queryByRole("dialog", { name: "Цвет жилы 1" })).not.toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Провода" })).toHaveTextContent("Helukabel 15201 · красный");
  expect(dot("1").querySelector(".pin-swatch")).toHaveStyle({ background: "#d93a32" });
  // Два цвета: основной белый, полоса синяя — «бело-синий».
  fireEvent.click(dot("2"));
  const pop = screen.getByRole("dialog", { name: "Цвет жилы 2" });
  fireEvent.click(within(pop).getByRole("checkbox", { name: "Два цвета" }));
  fireEvent.click(within(pop).getByRole("button", { name: "Основной: белый" }));
  fireEvent.click(within(pop).getByRole("button", { name: "Полоса: синий" }));
  expect(screen.getByRole("group", { name: "Провода" })).toHaveTextContent("Helukabel 15202 · бело-синий");
  expect(dot("2").querySelector(".pin-swatch")).toHaveStyle({ background: "linear-gradient(135deg, #ffffff 50%, #2b5fd9 50%)" });
  // Сброс возвращает цвет из наименования.
  fireEvent.click(within(screen.getByRole("dialog", { name: "Цвет жилы 2" })).getByRole("button", { name: "Сбросить цвет" }));
  expect(screen.getByRole("group", { name: "Провода" })).not.toHaveTextContent("бело-синий");
  expect(screen.getByRole("group", { name: "Провода" })).toHaveTextContent("Helukabel 15201 · красный");
  void onBuild;
});
