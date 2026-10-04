import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QualityPage } from "./QualityPage";
import type { Principal, ProductionInstrument, ProductionOperation, ProductionTask } from "./api";

const user = { id: "10000000-0000-0000-0000-000000000001", email: "u@example.test", name: "Сотрудник", department: "Производство" };
const qc: Principal = { user, roles: ["qc"], permissions: { production: "edit" } };
const master: Principal = { user, roles: ["master"], permissions: { production: "edit" } };
const admin: Principal = { user, roles: ["admin"], permissions: { production: "edit" } };

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
  expect(headings()).toEqual(["Очередь ОТК", "Средства измерения"]);
  expect(requested).not.toContain("/api/production/defects");
  expect(requested).not.toContain("/api/production/qc/rejections");
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

  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: /Поверка до/ })).toBeInTheDocument();
  expect(within(table).getByText("СИ-014")).toBeInTheDocument();
  expect(within(table).getByText("Микрометр МК-25")).toBeInTheDocument();
  expect(within(table).getByText("01.09.2027 09:00")).toBeInTheDocument();
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
  expect(headings()).toEqual(["Очередь ОТК", "Брак ОТК", "Брак цеха", "Средства измерения"]);
});

it("shows the load failure instead of a blank section", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Нет связи с ERP"));
  render(<QualityPage principal={qc} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
});
