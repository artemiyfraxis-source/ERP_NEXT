import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QualityPage } from "./QualityPage";
import type { Principal, ProductionInstrument, ProductionOperation, ProductionTask } from "./api";

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
  get?: (path: string) => Response | null;
}

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
    if (path === "/api/production/instruments") return json({ instruments: data.instruments ?? [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: data.rejections ?? [] });
    if (path === "/api/production/defects") return json({ defects: data.defects ?? [] });
    const handled = data.get?.(path);
    if (handled) return handled;
    throw new Error(`Unexpected request: ${path}`);
  });
  return requested;
}

afterEach(() => { vi.restoreAllMocks(); });

const headings = () => screen.queryAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);

it("shows the QC inspector the queue and the instruments, not the master's reject decisions", async () => {
  const requested = qualityFetch({ instruments: [micrometer] });
  render(<QualityPage principal={qc} />);

  expect(await screen.findByRole("heading", { name: "Очередь ОТК" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "ОТК", level: 1 })).toBeInTheDocument();
  expect(headings()).toEqual(["Очередь ОТК", "Замеры КСВН", "Средства измерения"]);
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
  expect(await screen.findByRole("button", { name: "Принять ОТК" })).toBeInTheDocument();
});

it("tells the QC inspector when no box waits for inspection", async () => {
  qualityFetch();
  render(<QualityPage principal={qc} />);
  expect(await screen.findByText(/Коробок на проверку нет/)).toBeInTheDocument();
});

it("sends to QC only the box of the final operation of the scheme", async () => {
  qualityFetch({ boxes: [box("000001-8", 1, cutA), box("000002-6", 2, cutB), box("000003-4", 3, assembly)] });
  render(<QualityPage principal={qc} />);
  expect(await screen.findByLabelText("Годные ОТК 000003-4")).toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000001-8")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000002-6")).not.toBeInTheDocument();
});

// Подтверждает только ОТК (13.10.2026): сданная монтажником коробка последней операции — сразу в очереди.
const handed = (number: string, position: number, operationId: string) => ({
  ...box(number, position, operationId), status: "Закрыта оператором", confirmed_at: null, confirmed_by: null,
});

it("queues the box of the final operation as soon as the fitter hands it in, and old master-confirmed ones too", async () => {
  qualityFetch({ boxes: [handed("000001-8", 1, cutA), handed("000003-4", 3, assembly), box("000004-2", 3, assembly), { ...handed("000005-0", 3, assembly), status: "Возвращена" }] });
  render(<QualityPage principal={qc} />);
  expect(await screen.findByLabelText("Годные ОТК 000003-4")).toBeInTheDocument();
  expect(screen.getByLabelText("Годные ОТК 000004-2")).toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000001-8")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Годные ОТК 000005-0")).not.toBeInTheDocument();
});

it("returns a handed box to its fitter from the QC queue with a reason", async () => {
  let returned: { path: string; body: unknown } | null = null;
  qualityFetch({ boxes: [handed("000003-4", 3, assembly)] }, (path, body) => {
    if (path.endsWith("/return")) { returned = { path, body }; return json({ box: { ...handed("000003-4", 3, assembly), status: "Возвращена" } }); }
    return null;
  });
  render(<QualityPage principal={qc} />);

  fireEvent.click(await screen.findByRole("button", { name: "Вернуть…" }));
  const send = screen.getByRole("button", { name: "Вернуть монтажнику" });
  expect(send).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина возврата 000003-4"), { target: { value: " Пересчитать " } });
  fireEvent.click(send);

  await waitFor(() => expect(returned).toEqual({
    path: "/api/production/boxes/74000000-0000-0000-0000-000000000103/return", body: { version: 1, reason: "Пересчитать" },
  }));
});

it("accepts a box at QC against the registered instruments that are still valid", async () => {
  let payload: unknown;
  qualityFetch(
    { boxes: [box("000003-4", 3, assembly)], instruments: [micrometer, { ...micrometer, id: "75000000-0000-0000-0000-000000000002", inventory_number: "СИ-015", name: "Штангенциркуль", status: "Выведено" }] },
    (path, body) => { if (path.endsWith("/qc")) { payload = body; return json({ box: box("000003-4", 3, assembly), inspection_id: "1", defect_number: null, serials: [] }); } return null; },
  );
  render(<QualityPage principal={qc} />);

  const picker = await screen.findByLabelText("Средства измерения 000003-4");
  expect(within(picker).getAllByRole("option")).toHaveLength(1);
  (within(picker).getByRole("option") as HTMLOptionElement).selected = true;
  fireEvent.change(picker);
  fireEvent.click(screen.getByRole("button", { name: "Принять ОТК" }));

  await waitFor(() => expect(payload).toMatchObject({ version: 1, accepted: 5, rejected: 0, instrument_ids: [micrometer.id] }));
});

it("keeps the instrument form closed until «+ Добавить СИ» and lists the instruments in a table", async () => {
  qualityFetch({ instruments: [micrometer] });
  render(<QualityPage principal={qc} />);

  const table = (await screen.findByRole("heading", { name: "Средства измерения" })).closest(".panel") as HTMLElement;
  expect(within(table).getByRole("columnheader", { name: /Поверка до/ })).toBeInTheDocument();
  expect(within(table).getByText("СИ-014")).toBeInTheDocument();
  expect(within(table).getByText("Микрометр МК-25")).toBeInTheDocument();
  expect(within(table).getByText("01.09.2027")).toBeInTheDocument();
  expect(within(table).queryByText(/09:00/)).not.toBeInTheDocument(); // a verification date has no time (audit М-7)
  expect(screen.queryByRole("button", { name: "Сохранить СИ" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Инв. №")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "+ Добавить СИ" }));
  expect(screen.getByRole("button", { name: "Сохранить СИ" })).toBeDisabled();
});

it("registers an instrument and closes the form", async () => {
  let payload: unknown;
  qualityFetch({}, (path, body) => {
    if (path === "/api/production/instruments") { payload = body; return json({ instrument: micrometer }); }
    return null;
  });
  render(<QualityPage principal={qc} />);

  fireEvent.click(await screen.findByRole("button", { name: "+ Добавить СИ" }));
  const form = screen.getByRole("button", { name: "Сохранить СИ" }).closest(".instrument-form") as HTMLElement;
  const fill = (label: string, value: string) => fireEvent.change(within(form).getByLabelText(label), { target: { value } });
  fill("Инв. №", "СИ-014");
  fill("Наименование", "Микрометр МК-25");
  fill("Тип", "Микрометр");
  fill("№ поверки", "П-2026/118");
  fill("Поверено", "2026-09-01T09:00");
  fill("Поверка до", "2027-09-01T09:00");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить СИ" }));

  await waitFor(() => expect(payload).toEqual({
    inventory_number: "СИ-014", name: "Микрометр МК-25", instrument_type: "Микрометр",
    verification_number: "П-2026/118", verified_at: "2026-09-01T09:00", verification_due: "2027-09-01T09:00",
  }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Сохранить СИ" })).not.toBeInTheDocument());
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

it("shows the administrator all four blocks of the section", async () => {
  qualityFetch({ instruments: [micrometer], rejections: [rejection], defects: [defect] });
  render(<QualityPage principal={admin} />);
  expect(await screen.findByRole("heading", { name: "Средства измерения" })).toBeInTheDocument();
  expect(headings()).toEqual(["Очередь ОТК", "Замеры КСВН", "Брак ОТК", "Брак цеха", "Средства измерения"]);
});

it("shows the load failure instead of a blank section", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Нет связи с ERP"));
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
});

// --- Замеры КСВН ------------------------------------------------------------------------------------------------------

const vswrBase = `/api/production/vswr/tasks/${branched.id}`;
const trace = [{ mhz: 1700, vswr: 1.3 }, { mhz: 1800, vswr: 1.6 }, { mhz: 1900, vswr: 1.45 }];
const measurement = (verdict: "pass" | "fail", extra: object = {}) => ({
  id: "78000000-0000-0000-0000-000000000001", serial_number: "ПЗ-0001-01", attempt: 1, band_mhz: [1700, 1900], max_vswr: 1.6, max_vswr_mhz: 1800,
  norm: {}, norm_vswr_max: 2.06, verdict, verdict_name: verdict === "pass" ? "Годно" : "Не годно", verdict_manual: false, verdict_reason: "",
  inspector: { id: "u", name: "Контролёр Иванов" }, measured_at: "2026-10-22T10:00:00+03:00", file_name: "a.s1p", points: 3, file_meta: {}, trace, ...extra,
});
const overview = (norm: object, latest: unknown = null, control: { mode: string; every: number | null } = { mode: "all", every: null }) => ({
  task: { id: branched.id, number: "ПЗ-0001", name: "Жгут", quantity: 10 }, norm, control,
  required: control.mode === "sample" ? { total: 1, measured: latest ? 1 : 0 } : { total: 10, measured: latest ? 1 : 0 },
  serials: [
    { position: 1, serial_id: "s1", serial_number: "ПЗ-0001-01", status: "В работе", attempts: latest ? 1 : 0, in_sample: true, latest },
    { position: 2, serial_id: "s2", serial_number: "ПЗ-0001-02", status: "В работе", attempts: 0, in_sample: control.mode !== "sample", latest: null },
  ],
});
const okNorm = { status: "ok", reason: "", vswr_max: 2.06, band_mhz: [1700, 1900], source: "SMA-J 1,25, SMA-J 1,3" };
const noNorm = { status: "undefined", reason: "у разъёма нет КСВ в справочнике", vswr_max: null, band_mhz: null };
const vna = { ...micrometer, name: "Анализатор цепей", instrument_type: "ВАЦ" };

async function openVswr(norm: object, latest: unknown = null, extra: Partial<Data> = {}) {
  qualityFetch({ instruments: [vna], get: (path) => (path === vswrBase ? json(overview(norm, latest)) : null), ...extra });
  render(<QualityPage principal={qc} />);
  fireEvent.change(await screen.findByLabelText("Задание"), { target: { value: branched.id } });
}

// После выбора задания подменяем ответы: POST замера и тот же обзор.
function postVswr(posted: Record<string, unknown>[], norm: object, answer: unknown) {
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posted.push(JSON.parse(String(init.body))); return json({ measurement: answer, position: 1 }); }
    if (path === vswrBase) return json(overview(norm));
    if (path === "/api/production/instruments") return json({ instruments: [vna] });
    if (path === "/api/production/tasks") return json({ tasks: [branched], next_cursor: null });
    return json({ boxes: [], operators: [], rejections: [], defects: [] });
  });
}

it("shows the vswr section: the norm line and a dense table of units with their latest measurement", async () => {
  await openVswr(okNorm, measurement("pass"));
  expect(await screen.findByText(/Норма ≤ 2,06 по комплектующим: SMA-J 1,25, SMA-J 1,3/)).toBeInTheDocument();
  const table = (await screen.findByRole("heading", { name: "Замеры КСВН" })).closest(".panel") as HTMLElement;
  for (const title of ["Серийный номер", "Попыток", "Макс. КСВН", "На частоте, МГц", "Вердикт", "Контролёр", "Дата"]) {
    expect(within(table).getAllByText(title).length).toBeGreaterThan(0);
  }
  expect(within(table).getAllByText("Годно").length).toBeGreaterThan(0);
  expect(within(table).getByText("Контролёр Иванов")).toBeInTheDocument();
  expect(within(table).getAllByRole("button", { name: "Загрузить замер" })).toHaveLength(2);
  expect(within(table).getByRole("link", { name: "Файл" })).toHaveAttribute("href", "/api/production/vswr/measurements/78000000-0000-0000-0000-000000000001/file");
});

it("sample 1 of N: marks the units in and out of the sample, counts the required ones, still lets measure the rest", async () => {
  qualityFetch({ instruments: [vna], get: (path) => (path === vswrBase ? json(overview(okNorm, measurement("pass"), { mode: "sample", every: 50 })) : null) });
  render(<QualityPage principal={qc} />);
  fireEvent.change(await screen.findByLabelText("Задание"), { target: { value: branched.id } });
  expect(await screen.findByText("· выборочно 1 из 50")).toBeInTheDocument();
  expect(screen.getByText("замерено 1 из 1 требуемых")).toBeInTheDocument();
  const table = screen.getByRole("heading", { name: "Замеры КСВН" }).closest(".panel") as HTMLElement;
  const [first, second] = ["ПЗ-0001-01", "ПЗ-0001-02"].map((serial) => within(table).getByText(serial).closest("tr") as HTMLElement);
  expect(within(first).getByText("в выборке")).toBeInTheDocument();
  expect(within(second).getByText("вне выборки")).toBeInTheDocument();
  expect(within(second).getByRole("button", { name: "Загрузить замер" })).toBeEnabled();
  expect(within(second).getByRole("button", { name: "Паспорт" })).toBeEnabled(); // паспорт ссылается на выборку партии
});

it("every unit: the norm line says so and every unit is required", async () => {
  await openVswr(okNorm);
  expect(await screen.findByText("· контроль: каждое изделие")).toBeInTheDocument();
  expect(screen.getByText("замерено 0 из 10 требуемых")).toBeInTheDocument();
  expect(screen.queryByText("вне выборки")).not.toBeInTheDocument();
});

it("says why the norm is undefined", async () => {
  await openVswr(noNorm);
  expect(await screen.findByText("Норма не определена — у разъёма нет КСВ в справочнике")).toBeInTheDocument();
});

it("validates the upload form: needs a file and the band must be ordered", async () => {
  await openVswr(okNorm);
  fireEvent.click((await screen.findAllByRole("button", { name: "Загрузить замер" }))[0]);
  const dialog = await screen.findByRole("dialog");
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
  fireEvent.click((await screen.findAllByRole("button", { name: "Загрузить замер" }))[0]);
  const dialog = await screen.findByRole("dialog");
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
  expect(within(dialog).getByText("Не годно")).toBeInTheDocument();
});

it("sends no verdict when the norm is defined: the server judges", async () => {
  const posted: Record<string, unknown>[] = [];
  await openVswr(okNorm);
  postVswr(posted, okNorm, measurement("pass"));
  fireEvent.click((await screen.findAllByRole("button", { name: "Загрузить замер" }))[0]);
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Файл замера"), { target: { files: [new File(["x"], "a.s1p")] } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить замер" }));
  await within(dialog).findByRole("img", { name: /норма не более 2,06/ });
  expect(posted[0]).not.toHaveProperty("verdict");
  expect(posted[0]).toMatchObject({ band_from_mhz: 1700, band_to_mhz: 1900 });
});

it("leads a failed unit into the QC queue with the reject prefilled", async () => {
  await openVswr(okNorm, measurement("fail", { max_vswr: 2.4 }), { boxes: [box("000003-4", 3, assembly)] });
  fireEvent.click(await screen.findByRole("button", { name: "В брак ОТК" }));
  await waitFor(() => expect(screen.getByLabelText("Брак ОТК 000003-4")).toHaveValue(1));
  expect(screen.getByLabelText("Годные ОТК 000003-4")).toHaveValue(4);
  expect(screen.getByDisplayValue("КСВН выше нормы")).toBeInTheDocument();
  expect(screen.getByDisplayValue(/Изделие ПЗ-0001-01: макс\. КСВН 2,4 на/)).toBeInTheDocument();
});

it("offers no way into rejects for a failed unit when no box of the task waits in the queue", async () => {
  await openVswr(okNorm, measurement("fail"));
  await screen.findAllByText("Не годно");
  expect(screen.queryByRole("button", { name: "В брак ОТК" })).not.toBeInTheDocument();
});
