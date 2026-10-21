import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* Длина зачистки у разъёма (владелец 14.10: «под каждый разъём длину зачистки, если записана — из БД») и снимок формы в
   собранной карте (14.10: «когда пересобираю, прошлые настройки должны сохраняться»). */

const card = (build_config: Record<string, unknown> | null = null): ProjectTechcard => ({
  id: "40000000-0000-0000-0000-000000000014", version: 1, project: "P-14", item_index: "КБ1", product_name: "Жгут PicoBlade",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: build_config ? { build_config } : null, manual_operation_keys: [],
  created_at: "2026-10-14T10:00:00+03:00", updated_at: "2026-10-14T10:00:00+03:00", issued_at: null,
});

const kit = (extra: KitsetBom["components"] = [], drop: string[] = []): KitsetBom => ({
  project: "P-14", index: "КБ1", name: "Жгут PicoBlade", run: "1", part_qty: "1", calc_source: "",
  components: [
    { id: "kit-0", type: "Провод", name: "МГТФ 0,07", art: "МГТФ 0,07", kd: "", qty: 1.4 },
    { id: "kit-1", type: "Терминал", name: "Контакт PicoBlade", art: "500798000", kd: "", qty: 8 },
    { id: "kit-2", type: "Соед", name: "Колодка PicoBlade 4", art: "510210400", kd: "", qty: 2 },
    ...extra,
  ].filter((c) => !drop.includes(c.art)),
  calc_ops: [],
});

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

function mockApi(strip: string) {
  const patches: Array<{ edits: Array<{ field: string; old: string; value: string }> }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? [{ id: "t1", tab: "ter", display_text: "Molex", source_sheet: "", fields: { terArticle: "500798000", terLPlus: "4", terStep: "1,25", terApplicator: "S19", terStrip: strip } }] : [] });
    }
    if (path === "/api/references/tech-operations/t1" && init?.method === "PATCH") {
      patches.push(JSON.parse(String(init.body)));
      return json({ id: "t1", tab: "ter", display_text: "Molex", source_sheet: "", fields: { terArticle: "500798000", terStrip: "2,5" } });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S19", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/references/")) return json({ metadata: null, columns: [], records: [], series: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  return patches;
}

beforeEach(() => { window.localStorage.clear(); vi.spyOn(window, "confirm").mockReturnValue(true); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const norms = (end: string) => within(screen.getByLabelText(`Нормы терминала ${end}`));

async function open(buildConfig: Record<string, unknown> | null = null, components = kit()) {
  const onBuild = vi.fn(async () => true);
  render(<TcGenerator card={card(buildConfig)} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={components} />);
  await screen.findByLabelText("Нормы терминала А1");
  return onBuild;
}

it("«Зачистка» разъёма: длина терминала из БД — в подсказке «из БД», своя длина её заменяет", async () => {
  mockApi("1,8");
  await open();
  const field = norms("А1").getByLabelText(/^Зачистка/);
  expect(field).toHaveValue("");
  expect(field).toHaveAttribute("placeholder", "1,8");
  expect(screen.getAllByText("из БД").length).toBeGreaterThan(0);
  fireEvent.change(field, { target: { value: "3" } });
  expect(norms("А1").getByLabelText(/^Зачистка/)).toHaveValue("3");
  expect(within(screen.getByLabelText("Нормы терминала А1")).queryByText("из БД")).toBeNull();
  expect(norms("В1").getByLabelText(/^Зачистка/)).toHaveAttribute("placeholder", "1,8");
});

it("в БД.ТЕР нет длины зачистки: поле просит её, и «Записать в БД» пишет введённую в terStrip", async () => {
  const patches = mockApi("");
  await open();
  expect(norms("А1").getByLabelText(/^Зачистка/)).toHaveAttribute("placeholder", "—");
  expect(norms("А1").getByText("нет в БД")).toBeInTheDocument();
  fireEvent.change(norms("А1").getByLabelText(/^Зачистка/), { target: { value: "2,5" } });
  fireEvent.click(norms("А1").getByRole("button", { name: "Записать в БД" }));
  expect(await screen.findByText(/Записано в БД\.ТЕР · 500798000: Зачистка 2,5 мм/)).toBeInTheDocument();
  expect(patches).toEqual([{ edits: [{ field: "terStrip", old: "", value: "2,5" }] }]);
  // Записанная длина теперь в БД, своя у разъёма снята.
  await waitFor(() => expect(norms("А1").getByLabelText(/^Зачистка/)).toHaveValue(""));
});

async function builtConfig() {
  mockApi("1,8");
  const onBuild = await open();
  fireEvent.click(screen.getByRole("radio", { name: /От краёв разъёмов/ }));
  fireEvent.change(screen.getByLabelText("Длина по чертежу"), { target: { value: "350" } });
  fireEvent.change(norms("В1").getByLabelText(/^Зачистка/), { target: { value: "3,5" } });
  fireEvent.change(norms("А1").getByLabelText("L−"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  const anyway = screen.queryByRole("button", { name: "Создать всё равно" });
  if (anyway) fireEvent.click(anyway);
  await waitFor(() => expect(onBuild, screen.queryByRole("alert")?.textContent || "no alert").toHaveBeenCalled());
  cleanup();
  window.localStorage.clear();
  return (onBuild.mock.calls[0] as unknown as [Record<string, unknown>])[0];
}

it("сборка кладёт снимок формы в build_config, а открытие собранной карты возвращает все настройки", async () => {
  const config = await builtConfig();
  expect(config.gen_state).toBeTruthy();
  vi.restoreAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi("1,8");
  await open(config);
  expect(screen.getByRole("radio", { name: /От краёв разъёмов/ })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByLabelText("Длина по чертежу")).toHaveValue(350);
  expect(norms("В1").getByLabelText(/^Зачистка/)).toHaveValue("3,5");
  expect(norms("А1").getByLabelText(/^Зачистка/)).toHaveValue("");
  expect(norms("А1").getByLabelText("L−")).toHaveValue("2");
  expect(screen.queryByText(/Состав изменился/)).toBeNull();
});

it("состав набора изменился: убранное уходит, новое добавляется, прошлые настройки остаются, строка «Состав изменился»", async () => {
  const config = await builtConfig();
  vi.restoreAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi("1,8");
  // Строка проводов сдвинула номера (kit-N), добавлен ещё провод.
  const changed = kit([{ id: "kit-3", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", kd: "", qty: 0.5 }]);
  await open(config, changed);
  expect(screen.getByText("Состав изменился: добавлено 1, убрано 0 — проверьте")).toBeInTheDocument();
  expect(screen.getByLabelText("Длина по чертежу")).toHaveValue(350);
  expect(norms("В1").getByLabelText(/^Зачистка/)).toHaveValue("3,5");
});

it("карта без снимка (собрана до 14.10) открывается, как раньше: форма засевается заново", async () => {
  const config = await builtConfig();
  const old = { ...config }; delete old.gen_state;
  vi.restoreAllMocks();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  mockApi("1,8");
  await open(old);
  expect(norms("В1").getByLabelText(/^Зачистка/)).toHaveValue("");
  expect(screen.queryByText(/Состав изменился/)).toBeNull();
});
