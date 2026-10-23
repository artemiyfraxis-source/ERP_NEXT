import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { pressBuild } from "./test/pressBuild";

/* «Норма заказчика (КД)» на вкладке КОАКС под «Контролем КСВН» (владелец 23.10: «норма заказчика — на этапе ТК; я
   главный на производстве по технологии, я задаю»). */

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000009", version: 2, project: "P-100", item_index: "КБ1", product_name: "Сборка RG316 150mm",
  source_calculation: "", deadline: null, status: "Выдана", revision: 1, manual_operation_keys: [],
  created_at: "2026-10-23T10:00:00+03:00", updated_at: "2026-10-23T10:00:00+03:00", issued_at: "2026-10-23T10:00:00+03:00",
  document: {
    operations: [],
    build_config: { mode: "coax", tab: "coax", ops: [], coax: {
      sideA: { article: "SMA-J" }, sideB: { article: "SMA-K" }, vswrControl: { mode: "all", every: null },
      vswrCustomer: { vswrMax: 1.5, fromMhz: 700, toMhz: 2700, basis: "ТУ 16-505.123, п. 3.4" },
    } },
    coaxVswrNorm: { status: "ok", vswrMax: 1.5, bandMhz: [700, 2700], source: "customer", basis: "ТУ 16-505.123, п. 3.4",
      components: { status: "ok", vswrMax: 2.06, bandMhz: [1, 6000] } },
  },
};

const kit: KitsetBom = {
  project: "P-100", index: "КБ1", name: "Сборка RG316 150mm", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { id: "k", kd: "", type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { id: "a", kd: "", type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { id: "b", kd: "", type: "Соед", name: "SMA", art: "SMA-K", qty: 1 },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "vswr", code: "CTRL_VSWR_coax", side: "", title: "CTRL_VSWR_coax | Контроль КСВН", name: "Контроль КСВН", category: "Контроль", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/kitsets/") || path.startsWith("/api/techcards/assembly/")) return json({ items: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

const group = () => screen.findByRole("group", { name: "Норма заказчика (КД)" });

it("a built card reopens with its customer norm, shows the components norm and warns when it is stricter", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);

  const norm = await group();
  expect(within(norm).getByLabelText("Норма заказчика: КСВН не более")).toHaveValue("1,5");
  expect(within(norm).getByLabelText("Норма заказчика: полоса от, МГц")).toHaveValue("700");
  expect(within(norm).getByLabelText("Норма заказчика: полоса до, МГц")).toHaveValue("2700");
  expect(within(norm).getByLabelText("Норма заказчика: основание")).toHaveValue("ТУ 16-505.123, п. 3.4");
  expect(within(norm).getByText("по комплектующим ≤ 2,06")).toBeInTheDocument();
  expect(within(norm).getByText("Норма заказчика строже возможностей разъёмов (по паспорту до 2,06) — ожидайте брак")).toBeInTheDocument();

  fireEvent.change(within(norm).getByLabelText("Норма заказчика: КСВН не более"), { target: { value: "2,5" } });
  expect(within(norm).queryByText(/строже возможностей разъёмов/)).toBeNull();
});

it("validates the fields, stops «Пересобрать» on an error and sends the norm to the build", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit} />);

  const norm = await group();
  fireEvent.change(within(norm).getByLabelText("Норма заказчика: основание"), { target: { value: "" } });
  expect(within(norm).getByRole("alert")).toHaveTextContent("Норма заказчика: укажите основание — ТУ, КД или заказ");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild("Пересобрать");
  expect(await screen.findByText("Норма заказчика: укажите основание — ТУ, КД или заказ", { selector: ".gen-error" })).toBeInTheDocument();
  expect(onBuild).not.toHaveBeenCalled();

  fireEvent.change(within(norm).getByLabelText("Норма заказчика: основание"), { target: { value: "заказ 77" } });
  fireEvent.change(within(norm).getByLabelText("Норма заказчика: полоса до, МГц"), { target: { value: "500" } });
  expect(within(norm).getByRole("alert")).toHaveTextContent("Норма заказчика: начало полосы должно быть больше 0 и меньше конца");
  fireEvent.change(within(norm).getByLabelText("Норма заказчика: полоса до, МГц"), { target: { value: "3000" } });
  expect(within(norm).queryByRole("alert")).toBeNull();
  pressBuild("Пересобрать");
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].coax.vswrCustomer).toEqual({ vswrMax: 1.5, fromMhz: 700, toMhz: 3000, basis: "заказ 77" });
});

it("«Не проверять» hides the customer norm", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);

  await group();
  fireEvent.click(within(screen.getByRole("radiogroup", { name: "Контроль КСВН" })).getByRole("radio", { name: "Не проверять" }));
  expect(screen.queryByRole("group", { name: "Норма заказчика (КД)" })).toBeNull();
});
