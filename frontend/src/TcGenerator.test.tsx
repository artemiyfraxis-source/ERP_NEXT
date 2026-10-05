import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const records = (tab: string) => ({
  metadata: null, columns: [],
  records: tab === "ter" ? [{ id: "t1", tab: "ter", display_text: "XH", source_sheet: "", fields: { terArticle: "SXH-001T-P0.6", terLPlus: "5", terStep: "2.50", terApplicator: "S12" } }] : [],
});

function mockApi(extra?: (path: string, init?: RequestInit) => Response | undefined) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const custom = extra?.(path, init);
    if (custom) return custom;
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json(records(new URLSearchParams(path.split("?")[1]).get("tab") || ""));
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S12", crimp_height: "0,8", pull_force: "", by_section: [{ section: "0,12 мм²", crimp_height: "0,8", pull_force: "", applicator: "S12", range_only: false, found: true }] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

function kitOf(name: string, components: Array<{ type: string; name: string; art: string; qty: number }>, calc_ops: KitsetBom["calc_ops"] = []): KitsetBom {
  return {
    project: "P-100", index: "КБ1", name, run: "1", part_qty: "1", calc_source: "https://docs.google.com/spreadsheets/d/calc/edit",
    components: components.map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })), calc_ops,
  };
}

const XH = [
  { type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
  { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
  { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
];

it("seeds the form from the kit and sends the SUPPLYMAP config to the build", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);

  expect(await screen.findByRole("tab", { name: "Межплатная", selected: true })).toBeInTheDocument();
  expect(await screen.findByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  expect(screen.getByText("Опрессовка терминалов А1")).toBeInTheDocument();
  expect(await screen.findAllByText(/Подбор по сечению: 0,12 мм² → S12/)).toHaveLength(2);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));

  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.wires).toEqual([{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }]);
  expect(config.ops.map((op: { type: string }) => op.type)).toEqual(["cutWire", "prsTerm", "prsTerm"]);
  expect(config.partQty).toBe(1);
  expect(config).not.toHaveProperty("tcgen");
});

it("pairs the pin lists of both ends in order and shows the crossover under the connectors", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);
  const [pinsA, pinsB] = await screen.findAllByPlaceholderText("1, 3 или 1-2");
  expect(screen.getAllByText(/Контакты стыкуются по порядку: k-й здесь ↔ k-й на другой стороне/)).toHaveLength(1);
  expect(screen.queryByLabelText("Соединения по распиновке")).not.toBeInTheDocument();
  fireEvent.change(pinsA, { target: { value: "1-2" } });
  fireEvent.change(pinsB, { target: { value: "2, 1" } });
  const links = await screen.findByLabelText("Соединения по распиновке");
  expect(links).toHaveTextContent("МГТФ 0,12 · А1 → В1: 1→2, 2→1");
  expect(links.querySelectorAll("b")).toHaveLength(2);
});

it("adds a missing coax connector to БД.КОАКС through the references record", async () => {
  let created: unknown;
  mockApi((path, init) => {
    if (path === "/api/references/tech-operations" && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return json({ id: "c1", tab: "coax", display_text: "SMA-J", source_sheet: "", fields: {} }, 201);
    }
    return undefined;
  });
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Сборка RG316 150mm", [
    { type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ])} />);

  expect(await screen.findByRole("tab", { name: "КОАКС", selected: true })).toBeInTheDocument();
  expect(screen.getByText("⚠ «SMA-J» нет в БД.КОАКС — добавьте ниже")).toBeInTheDocument();
  expect(screen.queryByLabelText("d1 A")).toBeNull();
  fireEvent.change(screen.getByLabelText("l1 A"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить в БД.КОАКС" }));

  await waitFor(() => expect(created).toEqual({ tab: "coax", fields: {
    coaxArticle: "SMA-J", coaxWire: "RG316", l1: "4", pinType: "пайка", shieldType: "обжим", connArticle: "SMA-J",
  } }));
});

it("takes cut lengths and tinning from the КБn sheet of the calculation (tcCalcOps_)", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kitOf("Жгут XHP", XH, [
    { code: "CUT_WIRE", comp: "МГТФ 0,12", n: "2", l: "0,16", req: "L=150мм" },
  ])} />);

  await waitFor(() => expect(screen.getByLabelText("Длина по чертежу")).toHaveValue(150));
  expect(screen.queryByLabelText("Строки расчёта КБn")).not.toBeInTheDocument();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].wires[0].length).toBe(160);
});

it("shows the kit error instead of a hand-made composition", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kitError="Карточка набора «P-100» не найдена" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Карточка набора «P-100» не найдена");
  expect(screen.queryByRole("button", { name: "Создать" })).not.toBeInTheDocument();
});

it("resets hand-typed times to БД.ОП on the next form change, as renderOpsCard does", async () => {
  mockApi((path) => (path.startsWith("/api/references/tech-operations?") && path.includes("tab=op")
    ? json({ metadata: null, columns: [], records: [{ id: "o1", tab: "op", display_text: "", source_sheet: "", fields: { opNumber: "CUT_WIRE_auto", opName: "Резка", tPrep: "30", tOp: "5", tMachine: "" } }] })
    : undefined));
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);

  expect(await screen.findByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  const prep = () => screen.getAllByLabelText(/^Подготовка, сек: /)[0];
  await waitFor(() => expect(prep()).toHaveValue(30));
  fireEvent.change(prep(), { target: { value: "45" } });
  expect(prep()).toHaveValue(45);
  fireEvent.change(screen.getByLabelText("Длина по чертежу"), { target: { value: "200" } });
  expect(prep()).toHaveValue(30);
});

it("has one main button: «Создать» before the first issue, «Пересобрать» after", async () => {
  mockApi();
  const { unmount } = render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);
  expect(await screen.findByRole("button", { name: "Создать" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Удалить созданные" })).not.toBeInTheDocument();
  unmount();
  render(<TcGenerator card={{ ...card, status: "Выдана", revision: 1 }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);
  expect(await screen.findByRole("button", { name: "Пересобрать" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Создать" })).not.toBeInTheDocument();
});
