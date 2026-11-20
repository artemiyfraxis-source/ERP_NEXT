import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { QualityPage } from "./QualityPage";
import { resolveScan, vswrTone } from "./VswrSection";
import { productionApi, type CableAssemblyPassport, type Principal, type ProductionInstrument, type ProductionOperation, type ProductionTask, type QcPlan, type VswrTaskListItem } from "./api";

const user = { id: "10000000-0000-0000-0000-000000000001", email: "u@example.test", name: "Сотрудник", department: "Производство" };
// Access levels: the section «ОТК» (`quality`) holds the inspection and the instrument registry, «Ресурсное планирование»
// (`production`) the decisions on rejects; the reads of both come with a view of `production`.
const as = (level: string, permissions: Principal["permissions"]): Principal => ({ user, roles: [level], access_level: level, permissions });
const qc = as("foreman", { quality: "edit", production: "view" });
const master = as("foreman", { production: "edit", quality: "view" });
const admin = as("admin", {});

const cutA = "71000000-0000-0000-0000-0000000000a1";
const cutB = "71000000-0000-0000-0000-0000000000b2";
const assembly = "71000000-0000-0000-0000-0000000000c3";
const stageOp = (id: string, position: number, stage: string, extra: Partial<ProductionOperation>): ProductionOperation => ({
  id, position, code: `OP-0${position}`, stage, description: "", seconds_per_unit: "60", setup_seconds: "0", norm_seconds: 600,
  operator_ids: [], planned_start: null, planned_finish: null, assigned_by: "Мастер", ...extra,
});
// Сетевой график: две нарезки идут в сборку, сборка — финал.
const branched: ProductionTask = {
  id: "70000000-0000-0000-0000-000000000001", number: "ПЗ-0001", version: 2, created_at: "2026-09-22T09:00:00+03:00",
  project: "591K.3", item: "591K.3.КБ1", name: "Жгут", quantity: 10, fulfillment_request_id: null, deadline: "2026-10-01",
  status: "Запущено", launched_by: "Мастер", planned_start: null, planned_finish: null, assigned: 0,
  operations: [
    stageOp(cutA, 1, "Нарезка А", { inputs: [], first: true, final: false, required_quantity: "10" }),
    stageOp(cutB, 2, "Нарезка Б", { inputs: [], first: true, final: false, required_quantity: "20" }),
    stageOp(assembly, 3, "Сборка", {
      inputs: [
        { operation_id: cutA, position: 1, per_unit: "1", transfer_batch: null },
        { operation_id: cutB, position: 2, per_unit: "2", transfer_batch: null },
      ],
      first: false, final: true, required_quantity: "10",
    }),
  ],
};

const box = (number: string, position: number, operationId: string) => ({
  id: `74000000-0000-0000-0000-00000000010${position}`, number, version: 1, task_id: branched.id, operation_id: operationId, item: branched.item,
  operation_position: position, operator_id: "10000000-0000-0000-0000-000000000002", quantity: "5", consumed: "0", reserved: "0", rejected: "0",
  available: "5", status: "Подтверждена", created_at: "2026-09-22T09:00:00+03:00", closed_at: null, closed_by: null,
  confirmed_at: "2026-09-22T10:00:00+03:00", confirmed_by: "Мастер", return_reason: "",
});

const micrometer: ProductionInstrument = {
  id: "75000000-0000-0000-0000-000000000001", version: 1, inventory_number: "СИ-014", name: "Микрометр МК-25",
  instrument_type: "Микрометр", verification_number: "П-2026/118", verified_at: "2026-09-01T09:00:00",
  verification_due: "2027-09-01T09:00:00", status: "Действует",
  kind: "Средство измерений", parent_id: null, serial_number: "МК-25-14", manufacturer: "", doc_designation: "", quantity: 1, storage_location: "ОТК", warehouse_grn: "", files: [],
};
const unverifiedInstrument: ProductionInstrument = {
  ...micrometer, id: "unverified", inventory_number: "СИ-099", name: "Тестовый прибор",
  status: "Поверка не подтверждена", verification_number: null, verified_at: null, verification_due: null,
};

const rejection = {
  id: "73000000-0000-0000-0000-000000000001", box_id: "74000000-0000-0000-0000-000000000001", box_number: "000002-4", item: "591K.3.КБ1",
  inspected_at: "2026-09-22T09:00:00+03:00", rejected: 1, defect_number: "Б-000001-7", defect_character: "Обрыв",
  defect_description: "Нет контакта", status: "Ожидает решения", decision: null,
};

const defect = {
  id: "76000000-0000-0000-0000-000000000001", number: "Б-000009-1", version: 1, created_at: "2026-09-22T09:00:00+03:00",
  output_id: "77000000-0000-0000-0000-000000000001", source_box_id: "74000000-0000-0000-0000-000000000001", task_id: branched.id,
  operation_id: cutA, operator_id: "10000000-0000-0000-0000-000000000002", quantity: "2", character: "Царапина",
  description: "Повреждена изоляция", status: "Ожидает решения", replaced_by_output_id: null,
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

interface Data {
  boxes?: unknown[];
  instruments?: ProductionInstrument[];
  rejections?: unknown[];
  defects?: unknown[];
  vswrTasks?: unknown[];
  /** Ответ окна ОТК `/qc/plan/…`: план или готовый ответ (ошибка); по умолчанию — ЭТК без проверок. */
  plan?: QcPlan | Response;
  get?: (path: string) => Response | null;
}

const emptyPlan: QcPlan = {
  task: { id: "", number: "ПЗ-0001", project: "591K.3", item: "591K.3.КБ1", name: "Жгут", quantity: 10 },
  etk: { item_index: "КБ1", product_name: "Жгут", revision: 3, current_revision: 3 },
  checks: [], instruments: [], missing_instruments: [], vswr: null, defects: [],
  sto: { designation: "СТО ФРАК-001-2026", revision: "3", title: "Типовые дефекты" },
};
// План трёхоперационной ЭТК коаксиальной сборки — как его собирает сервер (test_qc_plan_api).
const richPlan: QcPlan = {
  ...emptyPlan,
  checks: [
    { kind: "visual", title: "Внешний осмотр", method: "СТО ФРАК-001-2026, разд. 4.1, 4.4.5", tools: ["Лупа 4×", "Визуально"],
      items: [{ operation: "2. Пайка центрального контакта", text: "Галтель ровная, без наплывов", tolerance: "" }] },
    { kind: "dimensions", title: "Размеры", method: "СТО ФРАК-001-2026, разд. 4.1", tools: ["Штангенциркуль"],
      items: [{ operation: "1. Резка кабеля", text: "Длина отрезка по ЭТК", tolerance: "500 ± 5 мм" }] },
    { kind: "vswr", title: "КСВН", method: "СТО ФРАК-002-2026; СТО ФРАК-001-2026, разд. 4.8", tools: ["ВАЦ (S11, S22)"], required: true,
      items: [{ operation: "3. Контроль КСВН", text: "Измерить КСВН с двух концов", tolerance: "≤ 1,35" }] },
    { kind: "continuity", title: "Прозвонка", method: "СТО ФРАК-001-2026, разд. 4.4.5", tools: ["Тестер цепей"], items: [] },
  ],
  instruments: [{ id: "75000000-0000-0000-0000-000000000001", inventory_number: "СИ-014", name: "Микрометр МК-25", purpose: "Размеры" }],
  missing_instruments: [{ tool: "Тестер цепей", purpose: "Прозвонка" }],
  vswr: { mode: "all", every: null, total: 20, measured: 12, failed: 1, max_vswr: 1.42, norm_vswr_max: 1.35, band_mhz: [1000, 3000],
    norm_status: "ok", norm_reason: "", units: [
      { position: 1, serial_number: "ПЗ-0001-0001", verdict: "pass", verdict_name: "Годно", max_vswr: 1.2, instrument_id: "vna" },
      { position: 5, serial_number: "ПЗ-0001-0005", verdict: "fail", verdict_name: "Не годно", max_vswr: 1.42, instrument_id: "vna" },
    ] },
  defects: [
    { code: "4.1.17", section: "4.1", defect: "Длина вне допуска", criterion: "Длина не по КД", class: "Значительный" },
    { code: "4.8.16", section: "4.8", defect: "КСВН вне нормы", criterion: "КСВН сборки больше нормы КД", class: "Критический" },
  ],
};

function qualityFetch(data: Data = {}, post: (path: string, body: unknown) => Response | null = () => null) {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(path);
    if (init?.method === "POST") {
      const handled = post(path, JSON.parse(String(init.body)));
      if (handled) return handled;
      throw new Error(`Unexpected request: POST ${path}`);
    }
    if (path === "/api/production/tasks") return json({ tasks: [branched], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [{ id: "10000000-0000-0000-0000-000000000002", name: "Оператор", site: "ЦАР" }] });
    if (path === "/api/production/boxes") return json({ boxes: data.boxes ?? [] });
    if (path === "/api/production/instruments/next-number") return json({ inventory_number: "СИ.004" });
    if (path === "/api/production/instruments") return json({ instruments: data.instruments ?? [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: data.rejections ?? [] });
    if (path === "/api/production/defects") return json({ defects: data.defects ?? [] });
    if (path === "/api/production/vswr/tasks") return json({ tasks: data.vswrTasks ?? [] });
    if (path.startsWith("/api/production/qc/plan/")) return data.plan instanceof Response ? data.plan.clone() : json(data.plan ?? emptyPlan);
    const handled = data.get?.(path);
    if (handled) return handled;
    throw new Error(`Unexpected request: ${path}`);
  });
  return requested;
}

beforeEach(() => { window.history.replaceState({}, "", "/?section=quality"); localStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); });

/** Окно ОТК коробки: щелчок по её строке в очереди. */
async function openBox(number: string) {
  fireEvent.click(await screen.findByRole("row", { name: new RegExp(number) }));
  return screen.findByRole("dialog", { name: `ОТК · ${number}` });
}
function decide(dialog: HTMLElement, accepted: string, rejected: string) {
  const number = within(dialog).getByLabelText(/^Годные ОТК/).getAttribute("aria-label")!.replace("Годные ОТК ", "");
  fireEvent.change(within(dialog).getByLabelText(`Годные ОТК ${number}`), { target: { value: accepted } });
  fireEvent.change(within(dialog).getByLabelText(`Брак ОТК ${number}`), { target: { value: rejected } });
}

const headings = () => screen.queryAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);

it("shows the QC inspector the queue and the instruments, not the master's reject decisions", async () => {
  const requested = qualityFetch({ instruments: [micrometer] });
  render(<QualityPage principal={qc} />);

  expect(await screen.findByRole("heading", { name: "Очередь ОТК" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "ОТК", level: 1 })).toBeInTheDocument();
  expect(headings()).toEqual(["Очередь ОТК"]);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Очередь ОТК · 0", "Замеры КСВН", "Средства измерения"]);
  expect(screen.queryByRole("tab", { name: /Брак/ })).not.toBeInTheDocument();
  expect(requested).not.toContain("/api/production/defects");
  expect(requested).not.toContain("/api/production/qc/rejections");
});

it("gives the QC actions only with edit on «ОТК»: a view grant on it shows no queue and no registry", async () => {
  const requested = qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer] });
  render(<QualityPage principal={as("foreman", { quality: "view", production: "view" })} />);

  expect(await screen.findByText("ОТК открыт вам только на просмотр.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Принять ОТК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Очередь ОТК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Средства измерения" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Замеры КСВН" })).not.toBeInTheDocument();
  expect(requested).not.toContain("/api/production/boxes");
  expect(requested).not.toContain("/api/production/instruments");
});

it("does not give the QC actions to edit on planning alone: that key decides rejects only", async () => {
  const requested = qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer], defects: [defect] });
  render(<QualityPage principal={as("tech_lead", { production: "edit" })} />);

  expect(await screen.findByRole("heading", { name: "Брак цеха" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Принять ОТК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Очередь ОТК" })).not.toBeInTheDocument();
  expect(requested).not.toContain("/api/production/boxes");
});

it("gives the QC actions by edit on «ОТК» whatever the level is called", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)] });
  render(<QualityPage principal={as("operator", { quality: "edit", production: "view" })} />);
  await openBox("000003-4");
  expect(screen.getByRole("button", { name: "Принять ОТК" })).toBeInTheDocument();
});

it("tells the QC inspector when no box waits for inspection", async () => {
  qualityFetch();
  render(<QualityPage principal={qc} />);
  expect(await screen.findByText(/Коробок на проверку нет/)).toBeInTheDocument();
});

const queued = () => screen.getAllByRole("row").map((row) => row.textContent ?? "");

it("sends to QC only the box of the final operation of the scheme", async () => {
  qualityFetch({ boxes: [box("000001-8", 1, cutA), box("000002-6", 2, cutB), box("000003-4", 3, assembly)] });
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("row", { name: /000003-4/ })).toBeInTheDocument();
  expect(queued().some((text) => /000001-8|000002-6/.test(text))).toBe(false);
});

// Подтверждает только ОТК (13.10.2026): сданная монтажником коробка последней операции — сразу в очереди.
const handed = (number: string, position: number, operationId: string) => ({
  ...box(number, position, operationId), status: "Закрыта оператором", confirmed_at: null, confirmed_by: null,
});

it("queues the box of the final operation as soon as the fitter hands it in, and old master-confirmed ones too", async () => {
  qualityFetch({ boxes: [handed("000001-8", 1, cutA), handed("000003-4", 3, assembly), box("000004-2", 3, assembly), { ...handed("000005-0", 3, assembly), status: "Возвращена" }] });
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("row", { name: /000003-4.*сдана монтажником/ })).toBeInTheDocument();
  expect(screen.getByRole("row", { name: /000004-2.*подтверждена мастером/ })).toBeInTheDocument();
  expect(queued().some((text) => /000001-8|000005-0/.test(text))).toBe(false);
});

it("returns a handed box to its fitter from the OTK window with a reason", async () => {
  let returned: { path: string; body: unknown } | null = null;
  qualityFetch({ boxes: [handed("000003-4", 3, assembly)] }, (path, body) => {
    if (path.endsWith("/return")) { returned = { path, body }; return json({ box: { ...handed("000003-4", 3, assembly), status: "Возвращена" } }); }
    return null;
  });
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");

  fireEvent.click(within(dialog).getByRole("button", { name: "Вернуть монтажнику…" }));
  const send = within(dialog).getByRole("button", { name: "Вернуть монтажнику" });
  expect(send).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Причина возврата 000003-4"), { target: { value: " Пересчитать " } });
  fireEvent.click(send);

  await waitFor(() => expect(returned).toEqual({
    path: "/api/production/boxes/74000000-0000-0000-0000-000000000103/return", body: { version: 1, reason: "Пересчитать" },
  }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("shows what to check from the ETK with the methodology, the VSWR status and passports, and fills the planned instruments", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer, vna], plan: richPlan });
  render(<QualityPage principal={admin} />);
  const dialog = await openBox("000003-4");

  const checks = await within(dialog).findByRole("table", { name: "Проверки ОТК по ЭТК" });
  const dims = within(checks).getByRole("row", { name: /Размеры/ });
  expect(within(dims).getByText("1. Резка кабеля")).toBeInTheDocument();
  expect(within(dims).getByText("500 ± 5 мм")).toBeInTheDocument();
  expect(within(dims).getByText("СТО ФРАК-001-2026, разд. 4.1")).toBeInTheDocument();
  expect(within(checks).getByRole("row", { name: /Прозвонка.*Цепи и подключение экрана/ })).toBeInTheDocument();
  expect(within(dialog).getByText("померено 12 из 20 · брак 1 · макс. КСВН 1,42 при норме ≤ 1,35")).toHaveClass("tone-danger");
  expect(within(dialog).getByRole("button", { name: "№ 1" })).toHaveAttribute("title", "Паспорт ПЗ-0001-0001");
  expect(within(dialog).getByRole("button", { name: "Убрать СИ-014 Микрометр МК-25" })).toBeInTheDocument();
  expect(within(dialog).getByText(/Нет действующего СИ в реестре: тестер цепей \(прозвонка\)/)).toBeInTheDocument();
  // Годные не подставлены: контролёр вводит оба числа.
  expect(within(dialog).getByLabelText("Годные ОТК 000003-4")).toHaveValue(null);
  expect(within(dialog).getByRole("button", { name: "Принять ОТК" })).toBeDisabled();
});

it("accepts a box at QC against the registered instruments that are still valid", async () => {
  let payload: unknown;
  qualityFetch(
    { boxes: [box("000003-4", 3, assembly)], instruments: [micrometer, unverifiedInstrument, { ...micrometer, id: "accessory", name: "Переходник", kind: "Принадлежность", parent_id: micrometer.id }, { ...micrometer, id: "75000000-0000-0000-0000-000000000002", inventory_number: "СИ-015", name: "Штангенциркуль", status: "Не используется" }] },
    (path, body) => { if (path.endsWith("/qc")) { payload = body; return json({ box: box("000003-4", 3, assembly), inspection_id: "1", defect_number: null, serials: [] }); } return null; },
  );
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");

  const picker = await within(dialog).findByLabelText("Средства измерения 000003-4");
  expect(within(picker).getAllByRole("button")).toHaveLength(1);
  expect(within(picker).getByText("не выбраны")).toBeInTheDocument();
  fireEvent.click(within(picker).getByRole("button", { name: "+ СИ" }));
  const popup = screen.getByRole("dialog", { name: "Выбрать СИ · 000003-4" });
  expect(within(popup).getAllByRole("checkbox")).toHaveLength(1);
  const toggle = within(popup).getByRole("checkbox", { name: "СИ-014 Микрометр МК-25" });
  fireEvent.click(toggle);
  expect(within(picker).getByRole("button", { name: "Убрать СИ-014 Микрометр МК-25" })).toHaveAttribute("title", expect.stringContaining("поверка до 01.09.2027"));
  fireEvent.keyDown(toggle, { key: "Escape" });
  decide(dialog, "5", "0");
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 5" }));

  await waitFor(() => expect(payload).toMatchObject({ version: 1, accepted: 5, rejected: 0, defect_character: "", instrument_ids: [micrometer.id] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("removes a selected chip and restores the empty selection with focus on the add button", async () => {
  localStorage.setItem("erp-quality-last-instruments", JSON.stringify([micrometer.id]));
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer] });
  render(<QualityPage principal={qc} />);
  await openBox("000003-4");
  fireEvent.click(await screen.findByRole("button", { name: "Убрать СИ-014 Микрометр МК-25" }));
  expect(screen.queryByRole("button", { name: /^Убрать/ })).not.toBeInTheDocument();
  expect(screen.getByText("не выбраны")).toBeInTheDocument();
  const add = screen.getByRole("button", { name: "+ СИ" });
  expect(add).toHaveFocus();
  fireEvent.click(add);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.queryByRole("searchbox", { name: "Найти СИ" })).not.toBeInTheDocument();
});

it("searches more than eight active instruments, preserves selection across searches and returns focus on Escape", async () => {
  const instruments = Array.from({ length: 9 }, (_, index) => ({ ...micrometer, id: `si-${index}`, inventory_number: `СИ.${index + 1}`, name: `Микрометр ${index + 1} · полное наименование` }));
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments });
  render(<QualityPage principal={qc} />);
  await openBox("000003-4");
  const add = await screen.findByRole("button", { name: "+ СИ" });
  fireEvent.click(add);
  const search = screen.getByRole("searchbox", { name: "Найти СИ" });
  expect(search).toHaveFocus();
  expect(add).toHaveAttribute("aria-expanded", "true");
  expect(screen.getAllByRole("checkbox")).toHaveLength(9);
  fireEvent.change(search, { target: { value: "микрометр 9" } });
  expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "Убрать СИ.9 Микрометр 9 · полное наименование" })).toHaveTextContent("СИ.9 Микрометр 9×");
  fireEvent.change(search, { target: { value: "СИ.1" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.change(search, { target: { value: "нет такого прибора" } });
  expect(screen.getByText("СИ не найдены")).toBeInTheDocument();
  fireEvent.keyDown(search, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: /Выбрать СИ/ })).not.toBeInTheDocument();
  expect(add).toHaveFocus();
  expect(add).toHaveAttribute("aria-expanded", "false");
  expect(screen.getAllByRole("button", { name: /^Убрать/ })).toHaveLength(2);
  fireEvent.click(add);
  expect(screen.getByRole("searchbox", { name: "Найти СИ" })).toHaveValue("");
  expect(screen.getByRole("checkbox", { name: "СИ.9 Микрометр 9 · полное наименование" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть выбор СИ" }));
  expect(add).toHaveFocus();
});

it("dismisses the instrument popup on outside pointer or focus and offers an honest empty list", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)] });
  render(<QualityPage principal={qc} />);
  await openBox("000003-4");
  const add = await screen.findByRole("button", { name: "+ СИ" });
  fireEvent.click(add);
  expect(screen.getByText("Нет действующих СИ")).toBeInTheDocument();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("dialog", { name: /Выбрать СИ/ })).not.toBeInTheDocument();
  fireEvent.click(add);
  fireEvent.focusIn(screen.getByLabelText("Годные ОТК 000003-4"));
  expect(screen.queryByRole("dialog", { name: /Выбрать СИ/ })).not.toBeInTheDocument();
});

it("opens the instrument editor in a modal and lists verification dates without time", async () => {
  qualityFetch({ instruments: [micrometer] });
  render(<QualityPage principal={qc} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Средства измерения" }));
  const table = (await screen.findByRole("heading", { name: "Средства измерения" })).closest(".panel") as HTMLElement;
  expect(within(table).getByRole("columnheader", { name: /Поверка до/ })).toBeInTheDocument();
  expect(within(table).getByText("01.09.2027")).toBeInTheDocument();
  expect(within(table).queryByText(/09:00/)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(within(table).getByText("СИ-014"));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ-014");
});
it("records the master's append-only decision for a QC rejection and for a shop defect", async () => {
  const posted: Array<{ path: string; body: unknown }> = [];
  const requested = qualityFetch({ rejections: [rejection], defects: [defect] }, (path, body) => {
    posted.push({ path, body });
    return json({ decision: {} });
  });
  render(<QualityPage principal={master} />);

  expect(await screen.findByRole("heading", { name: "Брак ОТК" })).toBeInTheDocument();
  expect(headings()).toEqual(["Брак ОТК", "Брак цеха"]);
  expect(requested).not.toContain("/api/production/instruments");
  expect(requested).not.toContain("/api/production/boxes");

  fireEvent.change(screen.getByLabelText("Акт ОТК для Б-000001-7"), { target: { value: "Акт НС-12" } });
  fireEvent.click(within(screen.getByRole("heading", { name: "Брак ОТК" }).closest(".panel") as HTMLElement).getByRole("button", { name: "Принять решение" }));
  await waitFor(() => expect(posted).toContainEqual({
    path: "/api/production/qc/rejections/73000000-0000-0000-0000-000000000001/decide",
    body: { disposition: "Доработка", act_reference: "Акт НС-12" },
  }));

  fireEvent.change(screen.getByLabelText("Акт для Б-000009-1"), { target: { value: "Акт Б-9" } });
  fireEvent.click(within(screen.getByRole("heading", { name: "Брак цеха" }).closest(".panel") as HTMLElement).getByRole("button", { name: "Принять решение" }));
  await waitFor(() => expect(posted).toContainEqual({
    path: "/api/production/defects/76000000-0000-0000-0000-000000000001/decide",
    body: { version: 1, disposition: "Доработка", act_reference: "Акт Б-9" },
  }));
});

it("says there is no reject to decide instead of leaving the master a blank page", async () => {
  qualityFetch();
  render(<QualityPage principal={master} />);
  expect(await screen.findByText(/Брака ОТК на решение нет/)).toBeInTheDocument();
  expect(screen.getByText(/Брака цеха на решение нет/)).toBeInTheDocument();
});

it("shows the administrator all four tabs, with only the selected job visible", async () => {
  qualityFetch({ instruments: [micrometer], rejections: [rejection], defects: [defect] });
  render(<QualityPage principal={admin} />);
  expect(await screen.findByRole("heading", { name: "Очередь ОТК" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab")).toHaveLength(4);
  fireEvent.click(screen.getByRole("tab", { name: "Брак · 2" }));
  expect(headings()).toEqual(["Брак ОТК", "Брак цеха"]);
  fireEvent.click(screen.getByRole("tab", { name: "Средства измерения" }));
  expect(headings()).toEqual(["Средства измерения"]);
});

it("leaves boxes of a fully shipped КБ out of the QC queue", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly), { ...box("000004-2", 4, assembly), kb_shipped: true }] });
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("row", { name: /000003-4/ })).toBeInTheDocument();
  expect(queued().some((text) => text.includes("000004-2"))).toBe(false);
  expect(screen.getByRole("tab", { name: "Очередь ОТК · 1" })).toBeInTheDocument();
});

it("shows the load failure instead of a blank section", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Нет связи с ERP"));
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
});

it("keeps tab URLs, the box draft and loaded data when switching jobs", async () => {
  const requested = qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer] });
  render(<QualityPage principal={qc} />);
  let dialog = await openBox("000003-4");
  fireEvent.change(within(dialog).getByLabelText("Брак ОТК 000003-4"), { target: { value: "1" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "+ СИ" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "СИ-014 Микрометр МК-25" }));
  fireEvent.keyDown(document, { key: "Escape" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  const before = [...requested];
  fireEvent.click(screen.getByRole("tab", { name: "Средства измерения" }));
  expect(new URL(window.location.href).searchParams.get("tab")).toBe("instruments");
  expect(screen.queryByRole("heading", { name: "Очередь ОТК" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Очередь ОТК · 1" }));
  expect(requested).toEqual(before);
  dialog = await openBox("000003-4");
  expect(within(dialog).getByLabelText("Брак ОТК 000003-4")).toHaveValue(1);
  expect(within(dialog).getByRole("button", { name: "Убрать СИ-014 Микрометр МК-25" })).toBeInTheDocument();
});

it("falls back to the first permitted tab for a forbidden deep link", async () => {
  window.history.replaceState({}, "", "/?section=quality&tab=instruments");
  qualityFetch();
  render(<QualityPage principal={master} />);
  expect(await screen.findByRole("tab", { name: "Брак · 0", selected: true })).toBeInTheDocument();
  expect(new URL(window.location.href).searchParams.get("tab")).toBe("rejects");
});

it("validates quantities, takes the reject reason from the STO list and sends its details", async () => {
  let payload: unknown;
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], plan: richPlan }, (path, body) => {
    if (!path.endsWith("/qc")) return null;
    payload = body; return json({ box: box("000003-4", 3, assembly) });
  });
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  await within(dialog).findByRole("table", { name: "Проверки ОТК по ЭТК" });
  decide(dialog, "", "1");
  expect(within(dialog).getByRole("button", { name: "Принять ОТК" })).toBeDisabled();
  expect(within(dialog).getByText(/Введите годные и брак/)).toBeInTheDocument();
  decide(dialog, "3", "1");
  expect(within(dialog).getByText(/Годные и брак вместе — 5 шт, сейчас 4/)).toBeInTheDocument();
  expect(within(dialog).getByText("В коробке 5 шт · не распределено 1")).toBeInTheDocument();
  decide(dialog, "4", "1");
  const reason = within(dialog).getByLabelText(/Причина брака \(СТО ФРАК-001-2026\)/);
  expect(within(reason).getByRole("group", { name: "Раздел 4.8" })).toBeInTheDocument();
  fireEvent.change(reason, { target: { value: "4.8.16" } });
  expect(within(dialog).getByLabelText("Описание брака")).toHaveValue("КСВН сборки больше нормы КД");
  fireEvent.change(within(dialog).getByLabelText("Описание брака"), { target: { value: "Изделие 0005: КСВН 1,42" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 4, брак 1" }));
  await waitFor(() => expect(payload).toMatchObject({ accepted: 4, rejected: 1, defect_character: "4.8.16 КСВН вне нормы", defect_description: "Изделие 0005: КСВН 1,42" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("takes another reject reason by hand when the STO list has none", async () => {
  let payload: unknown;
  qualityFetch({ boxes: [box("000003-4", 3, assembly)] }, (path, body) => (path.endsWith("/qc") ? (payload = body, json({})) : null));
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  decide(dialog, "4", "1");
  fireEvent.change(within(dialog).getByLabelText(/Причина брака/), { target: { value: "other" } });
  fireEvent.change(within(dialog).getByLabelText("Характер брака"), { target: { value: "Обрыв" } });
  fireEvent.change(within(dialog).getByLabelText("Описание брака"), { target: { value: "Нет контакта" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 4, брак 1" }));
  await waitFor(() => expect(payload).toMatchObject({ accepted: 4, rejected: 1, defect_character: "Обрыв", defect_description: "Нет контакта" }));
});

it("remembers successfully accepted instruments and ignores inactive saved IDs", async () => {
  localStorage.setItem("erp-quality-last-instruments", JSON.stringify([micrometer.id, unverifiedInstrument.id]));
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer, unverifiedInstrument] }, (path) => path.endsWith("/qc") ? json({}) : null);
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  expect(await within(dialog).findByRole("button", { name: "Убрать СИ-014 Микрометр МК-25" })).toBeInTheDocument();
  decide(dialog, "5", "0");
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 5" }));
  await waitFor(() => expect(JSON.parse(localStorage.getItem("erp-quality-last-instruments")!)).toEqual([micrometer.id]));
});

it("accepts a box when browser storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage disabled"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage disabled"); });
  let payload: unknown;
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [micrometer] }, (path, body) => {
    if (!path.endsWith("/qc")) return null;
    payload = body; return json({});
  });
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  fireEvent.click(await within(dialog).findByRole("button", { name: "+ СИ" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "СИ-014 Микрометр МК-25" }));
  fireEvent.keyDown(document, { key: "Escape" });
  decide(dialog, "5", "0");
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 5" }));
  await waitFor(() => expect(payload).toMatchObject({ accepted: 5, rejected: 0, instrument_ids: [micrometer.id] }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("keeps a failed rejection in the open OTK window", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)] }, (path) => path.endsWith("/qc") ? new Response(JSON.stringify({ detail: "Коробка изменена" }), { status: 400 }) : null);
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  decide(dialog, "4", "1");
  fireEvent.change(within(dialog).getByLabelText(/Причина брака/), { target: { value: "other" } });
  fireEvent.change(within(dialog).getByLabelText("Характер брака"), { target: { value: "Обрыв" } });
  fireEvent.change(within(dialog).getByLabelText("Описание брака"), { target: { value: "Нет контакта" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Принять ОТК: годных 4, брак 1" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Коробка изменена");
  expect(dialog).toBeInTheDocument();
});

it("says so when the checks cannot be loaded, and still lets the inspector decide", async () => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], plan: new Response(JSON.stringify({ detail: "ЭТК недоступна" }), { status: 500 }) });
  render(<QualityPage principal={qc} />);
  const dialog = await openBox("000003-4");
  expect(await within(dialog).findByText(/ЭТК недоступна — проверьте по ЭТК задания/)).toBeInTheDocument();
  decide(dialog, "5", "0");
  expect(within(dialog).getByRole("button", { name: "Принять ОТК: годных 5" })).toBeEnabled();
});

// --- Замеры КСВН ------------------------------------------------------------------------------------------------------

const vswrBase = `/api/production/vswr/tasks/${branched.id}`;
const trace = [{ mhz: 1700, vswr: 1.3 }, { mhz: 1800, vswr: 1.6 }, { mhz: 1900, vswr: 1.45 }];
const measurement = (verdict: "pass" | "fail" | "border", extra: object = {}) => ({
  id: "78000000-0000-0000-0000-000000000001", serial_number: "ПЗ-0001-01", attempt: 1, band_mhz: [1700, 1900], max_vswr: 1.6, max_vswr_mhz: 1800,
  norm: {}, norm_vswr_max: 2.06, verdict, verdict_name: verdict === "pass" ? "Годно" : "Не годно", verdict_manual: false, verdict_reason: "",
  inspector: { id: "u", name: "Контролёр Иванов" }, measured_at: "2026-10-22T10:00:00+03:00", file_name: "a.s1p", points: 3, file_meta: {}, trace, ...extra,
});
const overview = (norm: object, latest: unknown = null, control: { mode: string; every: number | null } = { mode: "all", every: null }) => ({
  task: { id: branched.id, number: "ПЗ-0001", name: "Жгут", quantity: 10, project: "591K.3", item: "591K.3.КБ1" },
  etk: { item_index: "КБ1", revision: 3, current_revision: 3 }, norm, control,
  required: control.mode === "sample" ? { total: 1, measured: latest ? 1 : 0 } : { total: 10, measured: latest ? 1 : 0 },
  serials: [
    { position: 1, serial_id: "s1", serial_number: "ПЗ-0001-01", status: "В работе", attempts: latest ? 1 : 0, in_sample: true, latest },
    { position: 2, serial_id: "s2", serial_number: "ПЗ-0001-02", status: "В работе", attempts: 0, in_sample: control.mode !== "sample", latest: null },
  ],
});
const okNorm = { status: "ok", reason: "", vswr_max: 2.06, band_mhz: [1700, 1900], source: "SMA-J 1,25, SMA-J 1,3" };
const noNorm = { status: "undefined", reason: "у разъёма нет КСВ в справочнике", vswr_max: null, band_mhz: null };
const listed = (extra: object = {}) => ({
  id: branched.id, number: "ПЗ-0001", name: "Жгут", item: branched.item, project: "591K.3", quantity: 10, status: "Запущено", etk_revision: 3,
  norm: { status: "ok", vswr_max: 2.06, source: null, band_mhz: [1700, 1900] }, control: { mode: "all", every: null },
  required: { total: 10, measured: 3, failed: 0, max_vswr: null }, ...extra,
});
const vna = { ...micrometer, id: "75000000-0000-0000-0000-0000000000aa", inventory_number: "СИ-020", name: "Анализатор цепей", instrument_type: "ВАЦ" };

// Задания коаксиальных сборок — таблица: щелчок по строке показывает изделия задания.
async function pickTask() {
  fireEvent.click(await screen.findByRole("tab", { name: "Замеры КСВН" }));
  const tasks = await screen.findByRole("region", { name: "Задания коаксиальных сборок" });
  fireEvent.click(await within(tasks).findByText("ПЗ-0001"));
  return screen.findByRole("region", { name: "Изделия задания ПЗ-0001" });
}

async function openVswr(norm: object, latest: unknown = null, extra: Partial<Data> = {}) {
  qualityFetch({ instruments: [vna], vswrTasks: [listed()], get: (path) => (path === vswrBase ? json(overview(norm, latest)) : null), ...extra });
  render(<QualityPage principal={qc} />);
  return pickTask();
}
const fromFile = async (serial = "ПЗ-0001-01") => {
  fireEvent.click(await screen.findByRole("button", { name: `Замер из файла ${serial}` }));
  return screen.findByRole("dialog", { name: `Замер КСВН ${serial}` });
};

// После выбора задания подменяем ответы: POST замера и тот же обзор.
function postVswr(posted: Record<string, unknown>[], norm: object, answer: unknown) {
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posted.push(JSON.parse(String(init.body))); return json({ measurement: answer, position: 1 }); }
    if (path === vswrBase) return json(overview(norm));
    if (path === "/api/production/instruments/next-number") return json({ inventory_number: "СИ.004" });
    if (path === "/api/production/instruments") return json({ instruments: [vna] });
    if (path === "/api/production/tasks") return json({ tasks: [branched], next_cursor: null });
    return json({ boxes: [], operators: [], rejections: [], defects: [] });
  });
}

it("groups the measurements by task: project, КБ, ETK revision, norm, band and progress", async () => {
  qualityFetch({ instruments: [vna], vswrTasks: [listed({ required: { total: 10, measured: 3, failed: 1, max_vswr: 2.4 } }), listed({ id: "done", number: "ПЗ-0002", required: { total: 2, measured: 2, failed: 0, max_vswr: 1.2 } })] });
  render(<QualityPage principal={qc} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Замеры КСВН" }));
  const tasks = await screen.findByRole("region", { name: "Задания коаксиальных сборок" });
  const row = within(tasks).getByText("ПЗ-0001").closest("tr") as HTMLElement;
  for (const text of ["591K.3", "591K.3.КБ1", "ред. 3", "≤ 2,06", "1 700 … 1 900", "каждое", "3 из 10", "2,4"]) expect(within(row).getByText(text)).toBeInTheDocument();
  expect(within(row).getByText("1")).toHaveClass("tone-danger");
  fireEvent.click(screen.getByRole("button", { name: /Не померены · 1/ }));
  expect(within(tasks).queryByText("ПЗ-0002")).not.toBeInTheDocument();
});

it("shows no measurement columns of dashes before anything is measured", async () => {
  const group = await openVswr(okNorm);
  expect(within(group).getByRole("heading", { name: "591K.3 · 591K.3.КБ1 · ПЗ-0001" })).toBeInTheDocument();
  expect(within(group).getByText("ЭТК КБ1, ред. 3")).toBeInTheDocument();
  expect(within(group).getByText("померено 0 из 10")).toBeInTheDocument();
  for (const title of ["Макс. КСВН", "На частоте, МГц", "Контролёр", "Дата"]) expect(within(group).queryByRole("columnheader", { name: title })).not.toBeInTheDocument();
  expect(within(group).getAllByText("Не измерено").length).toBeGreaterThan(0);
});

it("shows the units with their latest measurement and verdict colours", async () => {
  const group = await openVswr(okNorm, measurement("pass"));
  expect(within(group).getByText(/Норма ≤ 2,06 по комплектующим: SMA-J 1,25, SMA-J 1,3/)).toBeInTheDocument();
  for (const title of ["Серийный номер", "Попыток", "Макс. КСВН", "На частоте, МГц", "Вердикт", "Контролёр", "Дата"]) {
    expect(within(group).getAllByText(title).length).toBeGreaterThan(0);
  }
  const first = within(group).getByText("ПЗ-0001-01").closest("tr") as HTMLElement;
  expect(within(first).getByText("Годен")).toHaveAttribute("data-tone", "success");
  expect(within(group).getByText("Контролёр Иванов")).toBeInTheDocument();
  expect(within(group).getAllByRole("button", { name: /^Снять с ВАЦ / })).toHaveLength(2);
  expect(within(group).getByRole("link", { name: "Файл" })).toHaveAttribute("href", "/api/production/vswr/measurements/78000000-0000-0000-0000-000000000001/file");
});

it("takes «на границе» from the server decision, never from its own margin", () => {
  expect(vswrTone(null)).toBe("none");
  expect(vswrTone({ verdict: "fail" })).toBe("fail");
  expect(vswrTone({ verdict: "pass" })).toBe("pass");
  // 1,34 при норме 1,35 без решения сервера (ВАЦ без известной погрешности) — годен, экран границу не придумывает.
  expect(vswrTone({ verdict: "pass", decision: { status: "pass", uncertainty: null, text: "" } })).toBe("pass");
  expect(vswrTone({ verdict: "fail", decision: { status: "border", uncertainty: 0.022, text: "На границе допуска" } })).toBe("edge");
});

it("shows the server's border decision and why a unit is rejected", async () => {
  const border = measurement("fail", { max_vswr: 2.07, verdict_reason: "Конец А: КСВН выше нормы",
    decision: { status: "border", uncertainty: 0.022, text: "На границе допуска — нужен повторный замер (3 подключения с поворотом 120°)" } });
  const group = await openVswr(okNorm, border);
  const row = within(group).getByText("ПЗ-0001-01").closest("tr") as HTMLElement;
  expect(within(row).getByText("На границе")).toHaveAttribute("title", "На границе допуска — нужен повторный замер (3 подключения с поворотом 120°)");
  expect(within(row).getByText("Конец А: КСВН выше нормы")).toBeInTheDocument();
  expect(within(group).getByRole("columnheader", { name: "Причина" })).toBeInTheDocument();
});

it("resolves a scanned serial: the ERP number, the factory FRX label within the project, or a position", () => {
  const tasks = [listed(), listed({ id: "t2", number: "ПЗ-0002", project: "708К" })] as VswrTaskListItem[];
  expect(resolveScan(" пз-0002-0004 ", tasks, branched.id)).toEqual({ taskId: "t2", position: 4 });
  expect(resolveScan("FRX.708K.120907/AA.7", tasks, branched.id)).toEqual({ taskId: "t2", position: 7 });
  expect(resolveScan("3", tasks, branched.id)).toEqual({ taskId: branched.id, position: 3 });
  expect(resolveScan("11", tasks, branched.id)).toBeNull();
  expect(resolveScan("ПЗ-0009-0001", tasks, branched.id)).toBeNull();
});

it("flow mode: a scanned serial opens its measurement with the VNA capture", async () => {
  await openVswr(okNorm);
  fireEvent.click(screen.getByRole("button", { name: "Поток: скан → замер → следующий" }));
  const scan = screen.getByLabelText("Серийный номер");
  await waitFor(() => expect(scan).toHaveFocus());
  fireEvent.change(scan, { target: { value: "ПЗ-0001-0002" } });
  fireEvent.click(screen.getByRole("button", { name: "Замерить" }));
  expect(await screen.findByRole("dialog", { name: "Замер КСВН ПЗ-0001-02" })).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Снять с ВАЦ" })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Снять с ВАЦ" }), { key: "Escape" });
  fireEvent.keyDown(screen.getByRole("dialog", { name: "Замер КСВН ПЗ-0001-02" }), { key: "Escape" });
  fireEvent.change(screen.getByLabelText("Серийный номер"), { target: { value: "чужой" } });
  fireEvent.click(screen.getByRole("button", { name: "Замерить" }));
  expect(screen.getByRole("alert")).toHaveTextContent("«чужой» — не серийный номер заданий на замер КСВН.");
});

it("does not offer an unverified instrument for VSWR measurements", async () => {
  await openVswr(okNorm, null, { instruments: [unverifiedInstrument, { ...vna, id: "adapter", kind: "Принадлежность", name: "Переходник", parent_id: vna.id }, { ...vna, id: "inactive", name: "Старый анализатор", status: "Не используется" }, vna] });
  await fromFile();
  const picker = screen.getByLabelText("Прибор (средство измерения)");
  expect(within(picker).queryByRole("option", { name: /Тестовый прибор/ })).not.toBeInTheDocument();
  expect(within(picker).queryByRole("option", { name: /Переходник|Старый анализатор/ })).not.toBeInTheDocument();
  expect(within(picker).getByRole("option", { name: /Анализатор цепей/ })).toBeInTheDocument();
});

it("sample 1 of N: marks the units in and out of the sample, counts the required ones, still lets measure the rest", async () => {
  qualityFetch({ instruments: [vna], vswrTasks: [listed()], get: (path) => (path === vswrBase ? json(overview(okNorm, measurement("pass"), { mode: "sample", every: 50 })) : null) });
  render(<QualityPage principal={admin} />);
  const group = await pickTask();
  expect(within(group).getByText("· выборочно 1 из 50")).toBeInTheDocument();
  expect(within(group).getByText("померено 1 из 1")).toBeInTheDocument();
  const [first, second] = ["ПЗ-0001-01", "ПЗ-0001-02"].map((serial) => within(group).getByText(serial).closest("tr") as HTMLElement);
  expect(within(first).getByText("в выборке")).toBeInTheDocument();
  expect(within(second).getByText("вне выборки")).toBeInTheDocument();
  expect(within(second).getByRole("button", { name: "Снять с ВАЦ ПЗ-0001-02" })).toBeEnabled();
  expect(within(second).getByRole("button", { name: "Паспорт" })).toBeEnabled(); // паспорт ссылается на выборку партии
});

it("every unit: the norm line says so and every unit is required", async () => {
  const group = await openVswr(okNorm);
  expect(within(group).getByText("· контроль: каждое изделие")).toBeInTheDocument();
  expect(within(group).queryByText("вне выборки")).not.toBeInTheDocument();
});

it("lists only the coax tasks from the vswr endpoint and says so when there are none", async () => {
  qualityFetch({ instruments: [vna], vswrTasks: [] });
  render(<QualityPage principal={qc} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Замеры КСВН" }));
  expect(await screen.findByText(/Нет заданий на коаксиальные сборки/)).toBeInTheDocument();
});

it("a computed norm shows its basis instead of the service word", async () => {
  const computed = { ...okNorm, source: "computed", basis: "рассчитана по справочникам на 23.10.2026 — ЭТК выпущена до нормы КСВН" };
  await openVswr(computed);
  expect(await screen.findByText(/Норма ≤ 2,06 по комплектующим \(рассчитана по справочникам на 23\.10\.2026 — ЭТК выпущена до нормы КСВН\)/)).toBeInTheDocument();
  expect(screen.queryByText(/computed/)).not.toBeInTheDocument();
});

it("a customer norm shows its band, basis and the components norm for reference; the band is locked in the upload form", async () => {
  const customer = { ...okNorm, vswr_max: 1.5, band_mhz: [700, 2700], source: "customer", basis: "ТУ 16-505.123, п. 3.4; норма заказчика из ревизии 4 ЭТК",
    components: { status: "ok", reason: "", vswr_max: 2.06, band_mhz: [1, 6000] } };
  await openVswr(customer);
  const line = await screen.findByText(/^Норма заказчика ≤ 1,5 в полосе 700/);
  expect(line.textContent!.replace(/\s+/g, " ")).toBe("Норма заказчика ≤ 1,5 в полосе 700 … 2 700 МГц (ТУ 16-505.123, п. 3.4; норма заказчика из ревизии 4 ЭТК); по комплектующим ≤ 2,06 — справочно");
  const dialog = await fromFile();
  expect(within(dialog).getByLabelText("Полоса от, МГц")).toHaveValue("700");
  expect(within(dialog).getByLabelText("Полоса до, МГц")).toHaveAttribute("readonly");
});

it("says why the norm is undefined", async () => {
  await openVswr(noNorm);
  expect(await screen.findByText("Норма не определена — у разъёма нет КСВ в справочнике")).toBeInTheDocument();
});

it("validates the upload form: needs a file and the band must be ordered", async () => {
  await openVswr(okNorm);
  const dialog = await fromFile();
  const submit = within(dialog).getByRole("button", { name: "Загрузить замер" });
  expect(submit).toBeDisabled();
  expect(within(dialog).getByText(/Приложите файл замера/)).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Полоса от, МГц")).toHaveValue("1700");
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  expect(submit).toBeEnabled();
  fireEvent.change(within(dialog).getByLabelText("Полоса от, МГц"), { target: { value: "2000" } });
  expect(submit).toBeDisabled();
  expect(within(dialog).getByText(/Полоса «от» должна быть меньше «до»/)).toBeInTheDocument();
});

it("requires a verdict and a reason when the norm is undefined, and sends them", async () => {
  const posted: Record<string, unknown>[] = [];
  await openVswr(noNorm);
  postVswr(posted, noNorm, measurement("fail", { verdict_manual: true, verdict_reason: "Нет КСВ", norm_vswr_max: null }));
  const dialog = await fromFile();
  const submit = within(dialog).getByRole("button", { name: "Загрузить замер" });
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  expect(submit).toBeDisabled();
  expect(within(dialog).getByText(/Норма не определена — выберите вердикт/)).toBeInTheDocument();
  expect(within(dialog).getByText(/укажите причину вердикта/)).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Вердикт"), { target: { value: "fail" } });
  expect(submit).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Причина вердикта"), { target: { value: "Нет КСВ" } });
  expect(submit).toBeEnabled();
  fireEvent.click(submit);
  expect(await within(dialog).findByRole("img", { name: /График КСВН/ })).toBeInTheDocument();
  expect(posted).toMatchObject([{ task_id: branched.id, position: 1, instrument_id: vna.id, file_name: "a.s1p", verdict: "fail", reason: "Нет КСВ" }]);
  expect(within(dialog).getByText("Брак")).toBeInTheDocument();
});

it("sends no verdict when the norm is defined, prints the passport of a pass at once and offers the next unit", async () => {
  const posted: Record<string, unknown>[] = [];
  await openVswr(okNorm);
  postVswr(posted, okNorm, measurement("pass"));
  const passport = vi.spyOn(productionApi, "vswrPassport").mockResolvedValue({} as CableAssemblyPassport);
  const dialog = await fromFile();
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить замер" }));
  await within(dialog).findByRole("img", { name: /норма не более 2,06/ });
  expect(posted[0]).not.toHaveProperty("verdict");
  expect(posted[0]).toMatchObject({ band_from_mhz: 1700, band_to_mhz: 1900 });
  expect(within(dialog).getByText("Годен")).toBeInTheDocument();
  await waitFor(() => expect(passport).toHaveBeenCalledWith(branched.id, 1));
  expect(within(dialog).getByRole("button", { name: "Следующий: ПЗ-0001-02" })).toHaveFocus();
});

it("a border result offers the repeat connection of the same unit instead of the passport", async () => {
  const posted: Record<string, unknown>[] = [];
  await openVswr(okNorm);
  postVswr(posted, okNorm, measurement("border", { max_vswr: 2.07, verdict_name: "На границе допуска",
    verdict_reason: "На границе допуска — нужен повторный замер (3 подключения с поворотом 120°)",
    decision: { status: "border", uncertainty: 0.022, text: "На границе допуска — нужен повторный замер (3 подключения с поворотом 120°)" } }));
  const passport = vi.spyOn(productionApi, "vswrPassport");
  const dialog = await fromFile();
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить замер" }));
  const repeat = await within(dialog).findByRole("button", { name: "Повторный замер (поворот 120°)" });
  expect(within(dialog).getByText("На границе")).toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Паспорт" })).not.toBeInTheDocument();
  expect(passport).not.toHaveBeenCalled();
  fireEvent.click(repeat);
  expect(await screen.findByRole("dialog", { name: "Замер КСВН ПЗ-0001-01" })).toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: "Снять с ВАЦ" })).toBeInTheDocument();
});

it("leads a failed unit into the OTK window of its box with the reject prefilled", async () => {
  const group = await openVswr(okNorm, measurement("fail", { max_vswr: 2.4 }), { boxes: [box("000003-4", 3, assembly)], plan: richPlan });
  fireEvent.click(within(group).getByRole("button", { name: "В брак ОТК" }));
  const dialog = await screen.findByRole("dialog", { name: "ОТК · 000003-4" });
  await waitFor(() => expect(within(dialog).getByLabelText(/Причина брака/)).toHaveValue("4.8.16"));
  expect(within(dialog).getByLabelText("Брак ОТК 000003-4")).toHaveValue(1);
  expect(within(dialog).getByLabelText("Годные ОТК 000003-4")).toHaveValue(null);
  expect(within(dialog).getByDisplayValue(/Изделие ПЗ-0001-01: макс\. КСВН 2,4 на/)).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Очередь ОТК · 1", selected: true })).toBeInTheDocument();
});

it("offers no way into rejects for a failed unit when no box of the task waits in the queue", async () => {
  const group = await openVswr(okNorm, measurement("fail"));
  expect(within(group).getAllByText("Брак").length).toBeGreaterThan(0);
  expect(within(group).queryByRole("button", { name: "В брак ОТК" })).not.toBeInTheDocument();
});

it("links the queued box to its coax task, preferring it to the first task, without refetching on tab switches", async () => {
  const requested = qualityFetch({ boxes: [box("000003-4", 3, assembly)], plan: richPlan,
    vswrTasks: [listed({ id: "other", number: "ПЗ-0002" }), listed()],
    get: (path) => path === vswrBase ? json({ ...overview(okNorm), required: { measured: 3, total: 10 } }) : null });
  render(<QualityPage principal={qc} />);
  const row = await screen.findByRole("row", { name: /000003-4/ });
  expect(within(row).getByText("померено 3 из 10")).toHaveClass("tone-warn");
  await waitFor(() => expect(requested).toContain(vswrBase));
  const dialog = await openBox("000003-4");
  fireEvent.click(within(dialog).getByRole("button", { name: "Протокол замеров" }));
  expect(screen.getByRole("tab", { name: "Замеры КСВН", selected: true })).toBeInTheDocument();
  expect(await screen.findByRole("region", { name: "Изделия задания ПЗ-0001" })).toBeInTheDocument();
  expect(new URL(window.location.href).searchParams.get("tab")).toBe("vswr");
  const before = [...requested];
  fireEvent.click(screen.getByRole("tab", { name: "Очередь ОТК · 1" }));
  fireEvent.click(screen.getByRole("tab", { name: "Замеры КСВН" }));
  expect(requested).toEqual(before);
});

it.each([
  [{ mode: "all", every: null }, { measured: 10, total: 10 }, "померено 10 из 10", "tone-ok"],
  [{ mode: "none", every: null }, { measured: 0, total: 0 }, "контроль не проводится", ""],
])("shows the completed or unnecessary VSWR state of a queued coax task", async (control, required, label, tone) => {
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], vswrTasks: [listed({ control, required })],
    get: (path) => path === vswrBase ? json({ ...overview(okNorm), control, required }) : null });
  render(<QualityPage principal={qc} />);
  const row = await screen.findByRole("row", { name: /000003-4/ });
  const cell = await within(row).findByText(label as string);
  if (tone) expect(cell).toHaveClass(tone as string);
});

it("updates the queue measurement count after uploading a trace", async () => {
  const saved = measurement("pass");
  let uploaded = false;
  qualityFetch({ boxes: [box("000003-4", 3, assembly)], instruments: [vna], vswrTasks: [listed()],
    get: (path) => path === vswrBase ? json(overview(okNorm, uploaded ? saved : null)) : null }, (path) => {
    if (path !== "/api/production/vswr") return null;
    uploaded = true; return json({ measurement: saved, position: 1 });
  });
  render(<QualityPage principal={qc} />);
  await pickTask();
  const dialog = await fromFile();
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить замер" }));
  await within(dialog).findByRole("img", { name: /График КСВН/ });
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  fireEvent.click(screen.getByRole("tab", { name: "Очередь ОТК · 1" }));
  const row = await screen.findByRole("row", { name: /000003-4/ });
  expect(await within(row).findByText("померено 1 из 10")).toBeInTheDocument();
});

it("opens a deep-linked tab and follows keyboard and browser navigation", async () => {
  window.history.replaceState({}, "", "/?section=quality&tab=instruments");
  qualityFetch(); render(<QualityPage principal={admin} />);
  expect(await screen.findByRole("tab", { name: "Средства измерения", selected: true })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("tab", { name: "Средства измерения" }), { key: "Home" });
  expect(screen.getByRole("tab", { name: "Очередь ОТК · 0" })).toHaveFocus();
  window.history.replaceState({}, "", "/?section=quality&tab=rejects");
  fireEvent.popState(window);
  expect(screen.getByRole("tab", { name: "Брак · 0", selected: true })).toBeInTheDocument();
});

it("has no «ВАЦ — рабочее место» tab: the workplace lives in the analyser card of «Оборудование» (owner 20.11.2026)", async () => {
  qualityFetch();
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("tab", { name: "Средства измерения" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Очередь ОТК · 0", "Замеры КСВН", "Средства измерения"]);
  expect(vi.mocked(globalThis.fetch).mock.calls.some(([input]) => String(input).includes("/vna-bench/"))).toBe(false);
});
