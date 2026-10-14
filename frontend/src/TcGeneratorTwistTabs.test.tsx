import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// «Построить чертёж» грузит редактор жгута лениво: в холодном прогоне первая сборка дольше 5 с.
vi.setConfig({ testTimeout: 45_000 });
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

/* Свивка трёх проводов в форме и «Построить чертёж» на вкладках «КОАКС» и «Ручная сборка». */

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000031", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Жгут 300мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-13T10:00:00+03:00", updated_at: "2026-10-13T10:00:00+03:00", issued_at: null,
};
const HARNESS = "/api/techcards/kitsets/621%D0%9A.2/harness/%D0%9A%D0%911";
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const kitOf = (name: string, components: Array<{ type: string; name: string; art: string; qty: number }>): KitsetBom => ({
  project: "621К.2", index: "КБ1", name, run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: components.map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
});

let saved: Array<Record<string, unknown>>;

beforeEach(() => {
  resetPinoutHousings();
  window.localStorage.clear();
  saved = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path === "/api/techcards/harness-components") return json([]);
    if (path.startsWith("/api/techcards/assembly/drafts?")) return json({ items: [] });
    if (path === HARNESS && method === "GET") return json({ document: null, design: null, revision: null, warnings: [] });
    if (path === HARNESS && method === "PUT") {
      const body = JSON.parse(String(init!.body)) as { design: Record<string, unknown> };
      saved.push(body.design);
      return json({ document: null, design: body.design, revision: saved.length, warnings: [], created: true });
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
});
afterEach(() => vi.restoreAllMocks());

it("twists three wires as one group from the form", async () => {
  const onConfig = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} onConfig={onConfig} kit={kitOf("Жгут 300мм", [
    { type: "Провод", name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 белый", art: "НВ4 0,2 белый", qty: 0.35 },
    { type: "Соед", name: "Колодка PH 3", art: "PHR-3", qty: 1 },
    { type: "Соед", name: "Колодка XH 3", art: "XHP-3", qty: 1 },
  ])} />);
  fireEvent.click(await screen.findByRole("switch", { name: /^Свивка/ }));
  const row = await screen.findByRole("group", { name: "Свивка 1" });
  fireEvent.change(within(row).getByLabelText("Свивка 1, провод 1"), { target: { value: "0" } });
  fireEvent.change(within(row).getByLabelText("Свивка 1, провод 2"), { target: { value: "1" } });
  fireEvent.click(within(row).getByRole("button", { name: "Добавить провод в свивку 1" }));
  fireEvent.change(screen.getByLabelText("Свивка 1, провод 3"), { target: { value: "2" } });
  await waitFor(() => expect((onConfig.mock.calls.at(-1)![0] as { twist: { pairs: number[][] } }).twist.pairs).toEqual([[0, 1, 2]]));
  // Тот же провод во второй свивке — стоп с причиной под строками.
  fireEvent.click(screen.getByRole("button", { name: "+ Добавить пару" }));
  fireEvent.change(screen.getByLabelText("Свивка 2, провод 1"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Свивка 2, провод 2"), { target: { value: "0" } });
  expect(await screen.findByText(/Провод свивается только в одной группе\.$/)).toHaveTextContent("Свивка: «НВ4 0,2 красный» — в свивках 1 и 2; «НВ4 0,2 белый» — в свивках 1 и 2.");
  // Третий провод убирается из тройки — снова пара.
  fireEvent.click(screen.getByRole("button", { name: "Убрать свивку 2" }));
  fireEvent.change(screen.getByLabelText("Свивка 1, провод 3"), { target: { value: "" } });
  expect(screen.queryByLabelText("Свивка 1, провод 3")).toBeNull();
  await waitFor(() => expect((onConfig.mock.calls.at(-1)![0] as { twist: { pairs: number[][] } }).twist.pairs).toEqual([[0, 1]]));
});

it("builds the drawing on the coax tab: connector to connector by the coax cable", async () => {
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} onOpenDrawing={vi.fn()}
    kit={kitOf("Сборка RG316 150mm", [{ type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 }, { type: "Соед", name: "SMA", art: "SMA-J", qty: 2 }])} />);
  expect(await screen.findByRole("tab", { name: "КОАКС", selected: true })).toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Коакс-кабель" });
  fireEvent.click(await within(section).findByRole("button", { name: "Построить чертёж" }));
  expect(await screen.findByText("Чертёж КБ1 построен: 2 разъёма, 1 провод.", {}, { timeout: 20_000 })).toBeTruthy();
  const design = saved[0] as { connectors: Array<{ partNumber: string }>; wires: Array<{ lengthMm: number }> };
  expect(design.connectors.map((c) => c.partNumber)).toEqual(["SMA-J", "SMA-J"]);
  expect(design.wires).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Открыть чертёж КБ1" })).toBeInTheDocument();
});

it("says why a coax with a bare end is not drawn", async () => {
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()}
    kit={kitOf("Сборка RG316 150mm", [{ type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 }, { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 }])} />);
  expect(await screen.findByRole("tab", { name: "КОАКС", selected: true })).toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Коакс-кабель" });
  expect(within(section).getByText("Сторона В без разъёма — чертёж строится от разъёма до разъёма")).toBeInTheDocument();
  expect(await within(section).findByRole("button", { name: "Построить чертёж" })).toBeDisabled();
});

const end = (endKey: string, conn: string, pin: number) => ({ end: endKey, conn, label: "", pin });
const manualConfig = {
  mode: "manual", assemblyIndex: "КБ1", assemblyName: "Жгут 300мм", partQty: 1,
  ops: [{ type: "customOp", seq: 1, label: "Пайка", qty: "1", tPrep: "60", tOp: "30", steps: [], tooling: [] }],
  route: { blocks: [{ key: "b1", name: "Жгут", ops: [1], final: true }], edges: [] },
  wires: [{ art: "НВ4 0,2 красный", name: "НВ4 0,2 красный", qty: 1, length: 310 }, { art: "НВ4 0,2 черный", name: "НВ4 0,2 черный", qty: 1, length: 310 }],
  connections: [
    { wire: "НВ4 0,2 красный", from: end("А1", "PHR-2", 1), to: end("В1", "XHP-2", 1) },
    { wire: "НВ4 0,2 черный", from: end("А1", "PHR-2", 2), to: end("В1", "XHP-2", 2) },
  ],
};
const manualKit = kitOf("Жгут 300мм", [
  { type: "Провод", name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 0.35 },
  { type: "Провод", name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 0.35 },
  { type: "Соед", name: "Колодка PH 2", art: "PHR-2", qty: 1 },
  { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 1 },
]);
const builtBy = (config: Record<string, unknown>): ProjectTechcard => ({
  ...card, version: 2, document: { kit: { part_quantity: "1", mode: "manual" }, operations: [], warnings: [], build_config: config },
});

it("builds the drawing on the manual tab by the connections of the assembly", async () => {
  render(<TcGenerator card={builtBy(manualConfig)} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={manualKit} />);
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Чертёж жгута" });
  fireEvent.click(await within(section).findByRole("button", { name: "Построить чертёж" }));
  expect(await screen.findByText("Чертёж КБ1 построен: 2 разъёма, 2 провода.", {}, { timeout: 20_000 })).toBeTruthy();
  const design = saved[0] as { connectors: Array<{ designation: string; partNumber: string }>; wires: unknown[] };
  expect(design.connectors.map((c) => [c.designation, c.partNumber])).toEqual([["XS1", "PHR-2"], ["XS2", "XHP-2"]]);
  expect(design.wires).toHaveLength(2);
});

it("says why a manual assembly without connections is not drawn", async () => {
  const { connections: _links, wires: _wires, ...bare } = manualConfig;
  render(<TcGenerator card={builtBy(bare)} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={manualKit} />);
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Чертёж жгута" });
  expect(within(section).getByText("В сборке нет соединений по распиновке")).toBeInTheDocument();
  expect(within(section).getByRole("button", { name: "Построить чертёж" })).toBeDisabled();
});
