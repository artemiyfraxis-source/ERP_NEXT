import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { pressBuild } from "./test/pressBuild";

/* Схема полуфабрикатов в генераторе (ARCH-TRACK-1): XH-жгут — цепочка «резка → опрессовка А → опрессовка В». */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? [{ id: "t1", tab: "ter", display_text: "XH", source_sheet: "", fields: { terArticle: "SXH-001T-P0.6", terLPlus: "5", terStep: "2.50", terApplicator: "S12" } }] : [] });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S12", crimp_height: "0,8", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

const scheme = () => screen.getByRole("group", { name: "Карточки схемы" });
const schemeCards = () => [...scheme().querySelectorAll("article.rc-card")] as HTMLElement[];
const cardTitles = () => schemeCards().map((item) => item.querySelector(".rc-title")?.textContent);
const schemeOp = (name: RegExp) => within(scheme()).getByRole("button", { name });

const XH = [
  { type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
  { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
  { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
];
const kit = (): KitsetBom => ({
  project: "P-100", index: "КБ1", name: "Жгут XHP 150мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: XH.map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
});
const mount = (props: Partial<Parameters<typeof TcGenerator>[0]> = {}) => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} {...props} />);
  return onBuild;
};
const build = async (name: "Создать" | "Пересобрать" = "Создать") => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild(name);
};

const builtCard = (route?: unknown, types = ["cutWire", "prsTerm", "prsTerm"]): ProjectTechcard => ({
  ...card, version: 2, status: "Выдана", revision: 1,
  document: {
    kit: { part_quantity: "1" },
    operations: types.map((type, index) => ({ type, time: [{ name: `Операция ${index + 1}`, norm: String(index + 1) }] })),
    warnings: [], build_config: { partQty: 1, ops: [], ...(route ? { route } : {}) },
  },
});
const savedRoute = {
  blocks: [{ key: "b1", ops: [1], final: false }, { key: "b3", name: "Жгут готов", ops: [2, 3], final: true }],
  edges: [{ from: "b1", to: "b3", per_unit: 2 }],
};

it("draws the scheme as semi-finished cards and sends the hand-edited quantity as build_config.route", async () => {
  const onBuild = mount();
  expect(await screen.findByText("Схема полуфабрикатов")).toBeInTheDocument();
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  // Резка — «Нарезанные провода», опрессовка А1 — сторона А, опрессовка В1 — готовый кабель.
  expect(cardTitles()).toEqual(["Нарезанные провода", "Сторона А собрана", "Готовый кабель"]);
  expect(screen.getByText("3 карточки · 3 операции")).toBeInTheDocument();
  // «Монтаж в разъём» (3 и 5 в списке) без шаблона в сборку не идёт — схема о них говорит
  expect(screen.getByText("Без шаблона, в схему не входят: 3, 5")).toBeInTheDocument();

  fireEvent.click(schemeOp(/Опрессовка терминалов А1/));
  fireEvent.change(within(screen.getByRole("region", { name: "Блок «Опрессовка терминалов А1»" }))
    .getByLabelText("Штук из «Резка — МГТФ 0,12» на один «Опрессовка терминалов А1»"), { target: { value: "2" } });
  expect(screen.getByText("правлена вручную")).toBeInTheDocument();
  await build();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const { route, ops } = onBuild.mock.calls[0][0];
  expect(ops).toHaveLength(3);
  expect(route).toEqual({
    blocks: [{ key: "b1", ops: [1], final: false }, { key: "b2", ops: [2], final: false }, { key: "b3", ops: [3], final: true }],
    edges: [{ from: "b1", to: "b2", per_unit: 2 }, { from: "b2", to: "b3", per_unit: 1 }],
  });

  // «Сбросить к автоматической» — в сборку схема не уходит, сервер выведет её сам.
  fireEvent.click(screen.getByRole("button", { name: "Сбросить к автоматической" }));
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalledTimes(2));
  expect(onBuild.mock.calls[1][0]).not.toHaveProperty("route");
});

it("does not send a scheme nobody edited", async () => {
  const onBuild = mount();
  await screen.findByText("Схема полуфабрикатов");
  await build();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).not.toHaveProperty("route");
});

it("picks up the hand-edited scheme saved with the card, with times from its last build, and sends it back", async () => {
  const onBuild = mount({ card: builtCard(savedRoute) });
  expect(await screen.findByText("правлена вручную")).toBeInTheDocument();
  // Ручная схема — карточка на блок (блоки правит мышь), а не по стадиям.
  expect(cardTitles()).toEqual(["Резка — МГТФ 0,12", "Жгут готов"]);
  // операции 2 и 3 — 2 + 3 мин на партию из одной штуки; вход — две штуки резки по 1 мин, «2 шт» на связи
  const final = schemeCards()[1];
  expect(final.querySelector(".rc-meta")).toHaveTextContent("2 оп. · 5 мин/шт");
  expect(scheme().querySelector(".rc-qty")).toHaveTextContent("2 шт");
  expect(screen.getByText(/на изделие/)).toHaveTextContent("на изделие 7 мин");
  await build("Пересобрать");
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].route).toEqual(savedRoute);
});

it("a scheme the generator drew itself (source «generated») is not a hand edit: the card reopens with the automatic one", async () => {
  // «Сложная сборка» пишет свою схему ПФ в build_config.route (владелец 16.10) — генератор строит её заново, а не держит
  // как ручную правку, которая перекрыла бы его.
  const onBuild = mount({ card: builtCard({ ...savedRoute, source: "generated" }) });
  expect(await screen.findByText("автоматическая")).toBeInTheDocument();
  expect(screen.queryByText("правлена вручную")).not.toBeInTheDocument();
  await build("Пересобрать");
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).not.toHaveProperty("route");
});

it("drops a saved scheme that was made for other operations and says so", async () => {
  const onBuild = mount({ card: builtCard(savedRoute, ["cutWire", "prsTerm", "prsTerm", "insHousing"]) });
  expect(await screen.findByText(/Операции изменились — схема собрана заново/)).toBeInTheDocument();
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  await build("Пересобрать");
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).not.toHaveProperty("route");
});

it("shows the server's «Схема полуфабрикатов: …» refusal in an error banner of the scheme", async () => {
  const onBuild = mount();
  onBuild.mockRejectedValue(new Error("Схема полуфабрикатов: блок «Резка» не доходит до итогового блока"));
  await screen.findByText("Схема полуфабрикатов");
  await build();
  const banner = await screen.findByRole("alert");
  expect(banner).toHaveClass("error-banner");
  expect(banner).toHaveTextContent("Схема полуфабрикатов: блок «Резка» не доходит до итогового блока");
  expect(scheme()).toBeInTheDocument(); // с ошибкой схема раскрыта
});

it("opens a block with the same operation editor the list has, so its template and times are set in place", async () => {
  mount();
  await screen.findByText("Схема полуфабрикатов");
  expect(screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12")).toHaveLength(1);
  fireEvent.click(schemeOp(/Резка — МГТФ 0,12/));
  const panel = screen.getByRole("region", { name: "Блок «Резка — МГТФ 0,12»" });
  expect(screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12")).toHaveLength(2);
  fireEvent.change(within(panel).getByLabelText("Карточка: Резка — МГТФ 0,12"), { target: { value: "prsA" } });
  // одно состояние формы: тот же шаблон стоит и в общем списке операций
  await waitFor(() => expect((screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12") as HTMLSelectElement[]).map((select) => select.value)).toEqual(["prsA", "prsA"]));
});

/* Кабель USB «Сибирь»: обжим обеих сторон при резке (600 проводов в партии), карты CUT_WIRE+2crimp в библиотеке нет —
   резка по CUT_WIRE+1crimp; монтаж А1 и В1. Владелец: «неправильный порядок … не могу его редактировать … мне нужны коды». */
const USB = [
  { type: "Провод", name: "Провод НВ4 0,2", art: "НВ4 0,2", qty: 0.3 },
  { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6N", qty: 6 },
  { type: "Соед", name: "Розетка XH", art: "I-DS1071-SCB02X5", qty: 2 },
];
const USB_NAME = "Кабель USB «Сибирь» ТСЦМ.685621.047 150мм";
const usbKit = (): KitsetBom => ({
  project: "P-100", index: "КБ1", name: USB_NAME, run: "200", part_qty: "200", calc_source: "", calc_ops: [],
  components: USB.map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
});
function mockUsb() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cwc", code: "CUT_WIRE+1crimp", side: "", title: "CUT_WIRE+1crimp | Автомат резка + опрессовка", name: "", category: "Автомат резка + опрессовка", steps: 1, tooling: 1 },
      { id: "insA", code: "INS_terminal_2.50", side: "A", title: "INS_terminal_2.50 | Монтаж терминалов (А)", name: "", category: "Монтаж терминалов", steps: 1, tooling: 1 },
      { id: "insB", code: "INS_terminal_2.50", side: "B", title: "INS_terminal_2.50 | Монтаж терминалов (В)", name: "", category: "Монтаж терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? [{ id: "t1", tab: "ter", display_text: "XH", source_sheet: "", fields: { terArticle: "SXH-001T-P0.6N", terLPlus: "5", terStep: "2.50", terStrip: "2", terApplicator: "S5/M5/A2" } }] : [] });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}
const mountUsb = (cardPatch: Partial<ProjectTechcard> = {}) => {
  mockUsb();
  const onBuild = vi.fn();
  const view = render(<TcGenerator card={{ ...card, product_name: USB_NAME, ...cardPatch }} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={usbKit()} />);
  return { onBuild, view };
};
const opList = () => (within(screen.getByRole("group", { name: "Операции" })).getAllByLabelText(/^Карточка: /) as HTMLSelectElement[])
  .map((select) => select.getAttribute("aria-label")!.replace("Карточка: ", ""));
const schemeNames = () => [...scheme().querySelectorAll(".rc-op-name")].map((name) => name.textContent);

it("USB: cards name the operations in Russian, codes stay in the hint, the replaced card is noted and В1 is in the finished cable", async () => {
  mountUsb();
  await screen.findByText("Схема полуфабрикатов");
  expect(screen.getAllByText("Резка с обжимом А и В (SXH-001T-P0.6N) — Провод НВ4 0,2")).toHaveLength(2); // список и схема
  expect(cardTitles()).toEqual(["Нарезанные и обжатые провода", "Сторона А собрана", "Готовый кабель"]);
  expect(scheme().textContent).not.toMatch(/CUT_WIRE|INS_terminal/);
  const cut = schemeOp(/Резка с обжимом/);
  expect(cut.getAttribute("title")).toContain("Карта CUT_WIRE+1crimp");
  expect(cut.getAttribute("title")).toContain("вместо CUT_WIRE+2crimp — нет карты");
  expect(cut).toHaveTextContent("заменена");
  expect(schemeCards()[2].querySelector(".rc-op-name")).toHaveTextContent("Монтаж в разъём В1 — I-DS1071-SCB02X5");
  expect(schemeCards()[2].querySelector(".rc-sub")).toHaveTextContent(USB_NAME);
  expect(screen.getByText("3 карточки · 3 операции")).toBeInTheDocument();
  // Внутренние имена типов операций на экран не попадают.
  expect(document.body.textContent).not.toMatch(/cutWireCrimp|insTerm/);
});

it("USB: «Выше» on a block reorders the operations, the build sends that order and the card reopens with it", async () => {
  const { onBuild, view } = mountUsb();
  await screen.findByText("Схема полуфабрикатов");
  fireEvent.click(screen.getByRole("button", { name: "Изменить схему" }));
  fireEvent.click(schemeOp(/Монтаж в разъём В1/));
  fireEvent.click(within(screen.getByRole("region", { name: "Блок «Монтаж в разъём В1 — I-DS1071-SCB02X5»" })).getByRole("button", { name: "↑ Выше" }));
  // Список операций и схема — в новом порядке; шаблоны операций остались при них. Сторона В собирается раньше,
  // готовый кабель — после стороны А.
  expect(opList()).toEqual(["Резка с обжимом А и В (SXH-001T-P0.6N) — Провод НВ4 0,2", "Монтаж в разъём В1 — I-DS1071-SCB02X5", "Монтаж в разъём А1 — I-DS1071-SCB02X5"]);
  // «Изменить схему» рисует карточкой каждый блок; готово — карточки по стадиям.
  expect(cardTitles()).toEqual(["Резка с обжимом А и В (SXH-001T-P0.6N) — Провод НВ4 0,2", "Монтаж в разъём В1 — I-DS1071-SCB02X5", "Монтаж в разъём А1 — I-DS1071-SCB02X5"]);
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));
  expect(cardTitles()).toEqual(["Нарезанные и обжатые провода", "Сторона В собрана", "Готовый кабель"]);
  expect(schemeNames()).toEqual([
    "Резка с обжимом А и В (SXH-001T-P0.6N) — Провод НВ4 0,2", "Монтаж в разъём В1 — I-DS1071-SCB02X5", "Монтаж в разъём А1 — I-DS1071-SCB02X5",
  ]);
  expect(screen.getByText("свой порядок операций")).toBeInTheDocument();
  await build();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.ops.map((op: { type: string; side?: string; templateId: string }) => [op.type, op.side ?? "", op.templateId])).toEqual([
    ["cutWireCrimp", "A", "cwc"], ["insTerm", "B", "insB"], ["insTerm", "A", "insA"],
  ]);
  expect(config.opOrder).toEqual(["cutWire", "insTermB1", "insTermA1"]);
  expect(config).not.toHaveProperty("route");

  // Карта, собранная с этим порядком, открывается с ним же; «Вернуть порядок генератора» возвращает правило.
  view.unmount();
  vi.restoreAllMocks();
  mountUsb({ version: 2, document: { kit: { part_quantity: "200" }, operations: [], warnings: [], build_config: config } });
  await screen.findByText("Схема полуфабрикатов");
  expect(opList()[1]).toBe("Монтаж в разъём В1 — I-DS1071-SCB02X5");
  fireEvent.click(screen.getByRole("button", { name: "Вернуть порядок генератора" }));
  expect(opList()[1]).toBe("Монтаж в разъём А1 — I-DS1071-SCB02X5");
  expect(screen.queryByText("свой порядок операций")).not.toBeInTheDocument();
});
