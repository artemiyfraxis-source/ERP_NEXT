import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

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
  fireEvent.click(screen.getByRole("button", { name }));
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

it("keeps a linear scheme collapsed, opens it on demand and sends the hand-edited quantity as build_config.route", async () => {
  const onBuild = mount();
  expect(await screen.findByText("Схема полуфабрикатов")).toBeInTheDocument();
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
  // Цепочка свёрнута: названия операций не двоятся, как и раньше.
  expect(screen.getByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  expect(screen.queryByRole("list", { name: "Блоки схемы" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать схему" }));
  const chain = screen.getByRole("list", { name: "Блоки схемы" });
  expect(within(chain).getAllByRole("listitem")).toHaveLength(3);
  expect(screen.getByText("3 блока · 3 оп.")).toBeInTheDocument();
  // «Монтаж в разъём» (3 и 5 в списке) без шаблона в сборку не идёт — схема о них говорит
  expect(screen.getByText("Без шаблона, в схему не входят: 3, 5")).toBeInTheDocument();

  fireEvent.change(within(chain).getByLabelText("Штук из «Резка — МГТФ 0,12» на один «Опрессовка терминалов А1»"), { target: { value: "2" } });
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
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
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
  expect(within(screen.getByRole("list", { name: "Блоки схемы" })).getAllByRole("button")).toHaveLength(2);
  const final = screen.getByRole("button", { name: /Жгут готов/ });
  // операции 2 и 3 — 2 + 3 мин на партию из одной штуки; вход — две штуки резки по 1 мин
  expect(final).toHaveTextContent("2 оп. · входов 2");
  expect(final).toHaveTextContent("5 мин/шт · с входами 7");
  expect(screen.getByText(/на изделие/)).toHaveTextContent("на изделие 7 мин");
  await build("Пересобрать");
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].route).toEqual(savedRoute);
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
  expect(screen.getByRole("list", { name: "Блоки схемы" })).toBeInTheDocument(); // с ошибкой схема раскрыта
});

it("opens a block with the same operation editor the list has, so its template and times are set in place", async () => {
  mount();
  await screen.findByText("Схема полуфабрикатов");
  fireEvent.click(screen.getByRole("button", { name: "Показать схему" }));
  expect(screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12")).toHaveLength(1);
  fireEvent.click(within(screen.getByRole("list", { name: "Блоки схемы" })).getByRole("button", { name: /Резка — МГТФ 0,12/ }));
  const panel = screen.getByRole("region", { name: "Блок «Резка — МГТФ 0,12»" });
  expect(screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12")).toHaveLength(2);
  fireEvent.change(within(panel).getByLabelText("Карточка: Резка — МГТФ 0,12"), { target: { value: "prsA" } });
  // одно состояние формы: тот же шаблон стоит и в общем списке операций
  await waitFor(() => expect((screen.getAllByLabelText("Карточка: Резка — МГТФ 0,12") as HTMLSelectElement[]).map((select) => select.value)).toEqual(["prsA", "prsA"]));
});
