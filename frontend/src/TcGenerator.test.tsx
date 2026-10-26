import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { pressBuild } from "./test/pressBuild";

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

function kitOf(name: string, components: Array<{ type: string; name: string; art: string; kd?: string; qty: number }>, calc_ops: KitsetBom["calc_ops"] = []): KitsetBom {
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
  expect((await screen.findAllByText("Резка — МГТФ 0,12"))[0]).toBeInTheDocument();
  expect(screen.getAllByText("Опрессовка терминалов А1")[0]).toBeInTheDocument();
  expect(await screen.findAllByText(/Подбор по сечению: 0,12 мм² → S12/)).toHaveLength(2);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();

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
  // Подсказка про стыковку по жилам и текстовая сводка «провод · А1 → В1: 1→2» убраны (владелец 14.10): схема связей показывает то же.
  expect(screen.queryByText(/Контакты стыкуются по жилам/)).toBeNull();
  fireEvent.change(pinsA, { target: { value: "1-2" } });
  fireEvent.change(pinsB, { target: { value: "2, 1" } });
  const links = await screen.findByLabelText("Соединения по распиновке");
  expect(links).not.toHaveTextContent("А1 → В1");
  expect(links.querySelectorAll("b")).toHaveLength(0);
  expect(links.querySelector(".pin-routes")).toBeNull();
  const svg = within(links).getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect([...svg.querySelectorAll(".pin-links-line")].map((l) => `${l.getAttribute("data-a")}→${l.getAttribute("data-b")}`)).toEqual(["А1·1→В1·2", "А1·2→В1·1"]);
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

it("chooses coax stripping on the rotary machine or separately and keeps the choice in the saved form (owner 14.10)", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kitOf("Сборка RG316 150mm", [
    { type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ])} />);

  const mode = await screen.findByRole("radiogroup", { name: "Разделка" });
  expect(within(mode).getByRole("radio", { name: "Резка с разделкой на станке" })).toHaveAttribute("aria-checked", "true");
  // Ø кабеля неизвестен — большой станок; выбор технолога сохраняется.
  const machine = screen.getByRole("radiogroup", { name: "Станок" });
  expect(within(machine).getByRole("radio", { name: /BZW-50X \(большой\)/ })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(within(machine).getByRole("radio", { name: /BZW-886\+Q2 \(малый\)/ }));
  expect(screen.getAllByText("Резка с разделкой (А и В)")[0]).toBeInTheDocument();
  expect(screen.queryByText("Разделка коаксиала (А)")).toBeNull();

  fireEvent.click(within(mode).getByRole("radio", { name: "Резка, затем разделка А и В" }));
  expect(screen.queryByRole("radiogroup", { name: "Станок" })).toBeNull();
  expect(screen.getAllByText("Разделка коаксиала (А)")[0]).toBeInTheDocument();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  // В моке только карта CUT_WIRE_auto — операции без карты в сборку не идут.
  expect(config.ops.map((op: { opKey: string }) => op.opKey)).toEqual(["coaxCut"]);
  expect(config.ops[0]).not.toHaveProperty("machine");
  expect(config.gen_state.S.coax).toMatchObject({ stripMode: "separate", machine: "small" });
});

it("a collet centre contact from БД.КОАКС selects «Цанга, без пайки» and drops the pin soldering (16.10)", async () => {
  mockApi((path) => (path.startsWith("/api/references/tech-operations?") && path.includes("tab=coax") ? json({ metadata: null, columns: [],
    records: [{ id: "c1", tab: "coax", display_text: "N-111C/8D", source_sheet: "", fields: {
      coaxArticle: "N-111C/8D", coaxWire: "8D-FB", "Тип экрана": "обжим", "Центральный контакт": "цанговый, не требует пайки", L1: "9" } }] }) : undefined));
  render(<TcGenerator card={{ ...card, product_name: "Сборка 8D-FB 1m" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Сборка 8D-FB 1m", [
    { type: "Кабель", name: "8D-FB", art: "8D-FB", qty: 1 },
    { type: "Соед", name: "N", art: "N-111C/8D", qty: 1 },
  ])} />);

  const pin = await screen.findByRole("radiogroup", { name: "Тип пина А" });
  await waitFor(() => expect(within(pin).getByRole("radio", { name: "Цанга, без пайки" })).toHaveAttribute("aria-checked", "true"));
  expect(screen.queryByText("Пайка центрального контакта (А)")).toBeNull();
  expect(screen.getAllByText("Монтаж корпуса (А) — цанговый контакт, без пайки")[0]).toBeInTheDocument();
});

it("a crimped centre contact from БД.КОАКС selects «Обжим» and the pin is pressed, not soldered (19.10)", async () => {
  mockApi((path) => (path.startsWith("/api/references/tech-operations?") && path.includes("tab=coax") ? json({ metadata: null, columns: [],
    records: [{ id: "c1", tab: "coax", display_text: "SMA-JW3", source_sheet: "", fields: {
      coaxArticle: "SMA-JW3", coaxWire: "RG-316", "Тип пина": "обжим", "Тип экрана": "обжим", L1: "2,6" } }] }) : undefined));
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG-316 0,3m" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Сборка RG-316 0,3m", [
    { type: "Кабель", name: "RG-316", art: "RG-316", qty: 1 },
    { type: "Соед", name: "SMA", art: "SMA-JW3", qty: 1 },
  ])} />);

  const pin = await screen.findByRole("radiogroup", { name: "Тип пина А" });
  expect(within(pin).getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Пайка", "Пайка внутри корпуса", "Обжим", "Цанга, без пайки"]);
  await waitFor(() => expect(within(pin).getByRole("radio", { name: "Обжим" })).toHaveAttribute("aria-checked", "true"));
  expect(screen.getAllByText("Опрессовка центрального контакта (А)")[0]).toBeInTheDocument();
  expect(screen.queryByText("Пайка центрального контакта (А)")).toBeNull();
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
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].wires[0].length).toBe(160);
});

it("works off the wire replaced in the calc and shows the КД article under it", async () => {
  const resolved: unknown[] = [];
  mockApi((path, init) => {
    if (path === "/api/references/technology/resolve-terminal") resolved.push(JSON.parse(String(init?.body)));
    return undefined;
  });
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", [
    { type: "Провод", name: "Силиконовый 30AWG черный", art: "Силиконовый 30AWG черный", kd: "Силиконовый 28AWG черный", qty: 0.3 },
    ...XH.slice(1),
  ])} />);

  const sheet = await screen.findByRole("region", { name: "Данные из листа" });
  expect(sheet).toHaveTextContent("Силиконовый 30AWG черныйпо КД: Силиконовый 28AWG черный");
  expect((await screen.findAllByText("Резка — Силиконовый 30AWG черный"))[0]).toBeInTheDocument();
  await waitFor(() => expect(resolved.length).toBeGreaterThan(0));
  expect(resolved.map((body) => (body as { wires: string[] }).wires)).toContainEqual(["Силиконовый 30AWG черный"]);
  expect(JSON.stringify(resolved)).not.toContain("28AWG");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0].wires[0]).toMatchObject({ name: "Силиконовый 30AWG черный", art: "Силиконовый 30AWG черный" });
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

  expect((await screen.findAllByText("Резка — МГТФ 0,12"))[0]).toBeInTheDocument();
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

it("«Маркировка по КД»: fills marks by connectors and from the harness drawing and sends them to the build", async () => {
  mockApi((path) => {
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "mark", code: "INS_MARK_HARNESS", side: "", title: "INS_MARK_HARNESS | Маркировка жгута", name: "Маркировка жгута", category: "Монтаж маркировки", steps: 1, tooling: 1 },
    ] });
    if (path === `/api/techcards/kitsets/${encodeURIComponent("P-100")}/harness/${encodeURIComponent("КБ1")}`) return json({
      document: null, version: "v1", warnings: [],
      design: { connectors: [{ id: "x1", designation: "X1", partNumber: "XHP-2" }], wires: [{ id: "w1", circuit: "708К.4КБ1" }] },
    });
    return undefined;
  });
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()}
    kit={kitOf("Жгут XHP 150мм", [...XH, { type: "Маркировка", name: "Маркировка", art: "Маркировка", qty: 1 }])} />);

  expect(await screen.findByText("бирки из состава: Маркировка × 1")).toBeInTheDocument();
  expect(screen.getByText(/Метки не заданы — в маркировку пойдут бирки из состава/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "По разъёмам" }));
  expect(screen.getByLabelText("Надпись 1")).toHaveValue("А1");
  expect(screen.getByLabelText("Материал 1")).toHaveValue("Маркировка");
  fireEvent.click(screen.getByRole("button", { name: "Из чертежа жгута" }));
  expect(await screen.findByDisplayValue("708К.4КБ1")).toBeInTheDocument();
  expect(screen.getByLabelText("Материал 4")).toHaveValue("Маркировка");
  expect(screen.getAllByRole("option", { name: "Хлястик" }).length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText("Материал 4"), { target: { value: "Хлястик" } });
  expect(screen.getByText("4 метки · 5 шт. на изделие")).toBeInTheDocument();

  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const op = onBuild.mock.calls[0][0].ops.find((item: { type: string }) => item.type === "insMark");
  expect(op.templateId).toBe("mark");
  expect(op.marks.map((mark: { text: string; art: string; place: string; qty: number }) => [mark.text, mark.art, mark.place, mark.qty])).toEqual([
    ["А1", "Маркировка", "у разъёма", 1], ["В1", "Маркировка", "у разъёма", 1], ["X1", "Маркировка", "у разъёма", 1],
    ["708К.4КБ1", "Хлястик", "на обоих концах", 2],
  ]);
});

it("«Термоусадка на жгут»: без ТТК/ТУТ в составе включённый переключатель говорит об этом, а не рисует пустую полоску", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);

  (await screen.findAllByText("Резка — МГТФ 0,12"))[0];
  fireEvent.click(screen.getByRole("switch", { name: /Термоусадка на жгут/ }));
  expect(screen.getByText(/В составе проекта нет термоусадки \(ТТК\/ТУТ\)/)).toBeInTheDocument();
});

it("«Термоусадка на жгут»: ТТК из состава даёт поля отрезка и их количество", async () => {
  mockApi();
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()}
    kit={kitOf("Жгут XHP 150мм", [...XH, { type: "Трубка", name: "ТТК 6/3", art: "ТТК 6/3", qty: 2 }])} />);

  (await screen.findAllByText("Резка — МГТФ 0,12"))[0];
  const toggle = screen.getByRole("switch", { name: /Термоусадка на жгут/ });
  if (!(toggle as HTMLInputElement).checked) fireEvent.click(toggle);
  expect(await screen.findByLabelText("ТТК 6/3: отрезок")).toHaveValue("2000");
  expect(screen.getByLabelText("ТТК 6/3: отрезков на изделие")).toHaveValue("1");
  expect(screen.queryByText(/В составе проекта нет термоусадки/)).toBeNull();
});

it("«Маркировать изделие»: одна метка «На изделии» с хлястиком, повторное нажатие дубль не добавляет", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kitOf("Жгут XHP 150мм", XH)} />);

  (await screen.findAllByText("Резка — МГТФ 0,12"))[0];
  fireEvent.click(screen.getByRole("switch", { name: /Маркировка жгута/ }));
  fireEvent.click(screen.getByRole("button", { name: "+ Метка" }));
  expect(screen.getByLabelText("Материал 1")).toHaveValue("Термотрубка");
  const product = screen.getByRole("button", { name: "Маркировать изделие" });
  fireEvent.click(product);
  expect(screen.getByLabelText("Что маркируем 2")).toHaveValue("Изделие КБ1");
  expect(screen.getByLabelText("Надпись 2")).toHaveValue("КБ1");
  expect(screen.getByLabelText("Материал 2")).toHaveValue("Хлястик");
  expect(screen.getByLabelText("Где 2")).toHaveValue("item");
  expect(screen.getByLabelText("На изделие 2")).toHaveValue("1");
  expect(product).toBeDisabled();
  expect(screen.queryByLabelText("Надпись 3")).toBeNull();
});

it("adds a technologist's component to the kit without reseeding the form and removes it again", async () => {
  mockApi();
  const tube = { id: "kit-3", type: "ТТК", name: "Трубка ТТК 4,8/1,6", art: "ТТК 4,8/1,6мм", kd: "", qty: "0.04", manual: true };
  const start = kitOf("Жгут XHP 150мм", XH);
  const add = vi.fn(async () => ({ bom: { ...start, components: [...start.components, tube] }, note: "КБ1 уже отправлена на комплектацию — в её заявку строка не попадёт" }));
  const remove = vi.fn(async () => start);
  function Pane() {
    const [kit, setKit] = useState(start);
    return <TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} kitEdit={{ add, remove, apply: setKit }} />;
  }
  render(<Pane />);
  const sheet = await screen.findByRole("region", { name: "Данные из листа" });
  fireEvent.click(await screen.findByRole("tab", { name: "Сложная сборка" }));
  fireEvent.click(within(sheet).getByRole("button", { name: "+ Комплектующее" }));
  const form = within(sheet).getByRole("form", { name: "Добавить комплектующее" });
  const submit = within(form).getByRole("button", { name: "Добавить в комплектацию" });
  expect(submit).toBeDisabled();
  fireEvent.change(within(form).getByLabelText("Тип"), { target: { value: "ТТК" } });
  fireEvent.change(within(form).getByLabelText("Название"), { target: { value: "Трубка ТТК 4,8/1,6" } });
  fireEvent.change(within(form).getByLabelText("Артикул"), { target: { value: "ТТК 4,8/1,6мм" } });
  fireEvent.change(within(form).getByLabelText("Кол-во на изделие"), { target: { value: "0,04" } });
  fireEvent.click(submit);

  await waitFor(() => expect(add).toHaveBeenCalledWith({ type: "ТТК", name: "Трубка ТТК 4,8/1,6", art: "ТТК 4,8/1,6мм", per: "0,04" }));
  expect(await within(sheet).findByText("добавил технолог")).toBeInTheDocument();
  expect(within(sheet).getByRole("status")).toHaveTextContent("уже отправлена на комплектацию");
  // Состав сменился, а форма не пересеяна: выбранная вкладка осталась.
  expect(screen.getByRole("tab", { name: "Сложная сборка", selected: true })).toBeInTheDocument();

  fireEvent.click(within(sheet).getByRole("button", { name: "Убрать ТТК 4,8/1,6мм из комплектации" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("ТТК 4,8/1,6мм"));
  await waitFor(() => expect(within(sheet).queryByText("добавил технолог")).toBeNull());
});

it("coax shield is a select per side: one «обе стороны» while A and B match, split on demand, state shape unchanged", async () => {
  mockApi();
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf("Сборка RG316 150mm", [
    { type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ])} />);

  const both = await screen.findByRole("combobox", { name: "Экран, обе стороны" });
  expect(screen.queryByRole("radiogroup", { name: /экран/i })).toBeNull();
  expect(within(both).getAllByRole("option")).toHaveLength(3);
  expect(within(both).getAllByRole("option").map((option) => option.textContent)).toEqual(["Обжим втулки", "Пайка экрана", "Прижимной (зажим)"]);
  expect(both).toHaveValue("обжим");
  expect(screen.queryByRole("combobox", { name: "Экран, сторона А" })).toBeNull();

  fireEvent.change(both, { target: { value: "пайка" } });
  expect(screen.getByRole("combobox", { name: "Экран, обе стороны" })).toHaveValue("пайка");

  fireEvent.click(screen.getByRole("button", { name: "разные для А и В" }));
  expect(screen.getByRole("combobox", { name: "Экран, сторона А" })).toHaveValue("пайка");
  fireEvent.change(screen.getByRole("combobox", { name: "Экран, сторона В" }), { target: { value: "прижим" } });
  expect(screen.getByRole("combobox", { name: "Экран, сторона А" })).toHaveValue("пайка");
  expect(screen.getByRole("combobox", { name: "Экран, сторона В" })).toHaveValue("прижим");
  expect(screen.queryByRole("button", { name: "как у А" })).toBeNull();

  // Вернули В к А — появляется «как у А», по клику снова один выбор.
  fireEvent.change(screen.getByRole("combobox", { name: "Экран, сторона В" }), { target: { value: "пайка" } });
  fireEvent.click(screen.getByRole("button", { name: "как у А" }));
  expect(screen.getByRole("combobox", { name: "Экран, обе стороны" })).toHaveValue("пайка");
});
