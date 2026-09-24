import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionPage } from "./ProductionPage";
import type { Principal, ProductionTask } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "master@example.test",
    name: "Мастер",
    department: "Производство",
  },
  roles: ["master"],
  permissions: { production: "edit" },
};

const task: ProductionTask = {
  id: "70000000-0000-0000-0000-000000000001",
  number: "ПЗ-0001",
  version: 2,
  created_at: "2026-09-22T09:00:00+03:00",
  project: "591K.3",
  item: "591K.3.КБ1",
  name: "Жгут",
  quantity: 10,
  fulfillment_request_id: null,
  deadline: "2026-10-01",
  status: "Запущено",
  launched_by: "Мастер",
  planned_start: "2026-09-22T09:00:00+03:00",
  planned_finish: "2026-09-22T09:12:00+03:00",
  assigned: 1,
  operations: [{
    id: "71000000-0000-0000-0000-000000000001",
    position: 1,
    code: "OP-01",
    stage: "Разделка",
    description: "Подготовить кабель",
    seconds_per_unit: "60",
    setup_seconds: "120",
    norm_seconds: 720,
    operator_ids: ["10000000-0000-0000-0000-000000000002"],
    planned_start: "2026-09-22T09:00:00+03:00",
    planned_finish: "2026-09-22T09:12:00+03:00",
    assigned_by: "Мастер",
  }],
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("launches a task with an explicit immutable route snapshot", async () => {
  let payload: unknown;
  let key = "";
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      key = new Headers(init.headers).get("Idempotency-Key") ?? "";
      return json({ task });
    }
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [] });
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Запустить задание" }));
  fireEvent.change(screen.getByLabelText("Проект"), { target: { value: "591K.3" } });
  fireEvent.change(screen.getByLabelText("Изделие"), { target: { value: "591K.3.КБ1" } });
  fireEvent.change(screen.getByLabelText("Название этапа 1"), { target: { value: "Разделка" } });
  fireEvent.change(screen.getByLabelText("Секунд на штуку 1"), { target: { value: "60" } });
  fireEvent.click(screen.getByRole("button", { name: "Запустить" }));

  await waitFor(() => expect(payload).toEqual({
    project: "591K.3",
    item: "591K.3.КБ1",
    name: "",
    quantity: 1,
    deadline: null,
    fulfillment_request_id: null,
    operations: [{
      position: 1,
      code: "",
      stage: "Разделка",
      description: "",
      seconds_per_unit: "60",
      setup_seconds: "0",
    }],
  }));
  expect(key).not.toBe("");
});

it("assigns all selected operators with optimistic task version", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks") return json({ tasks: [task], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [{
      id: "10000000-0000-0000-0000-000000000002", name: "Оператор", site: "ЦАР",
    }] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [] });
    if (path.endsWith("/assign") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ task, warnings: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Запланировать" }));

  await waitFor(() => expect(payload).toEqual({
    version: 2,
    operator_ids: ["10000000-0000-0000-0000-000000000002"],
    requested_start: "2026-09-22T09:00",
  }));
});

it("saves shop holidays through the production calendar", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [] });
    if (path === "/api/production/calendar" && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return new Response(null, { status: 204 });
    }
    if (path === "/api/production/calendar") return json({ holidays: [], absences: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.click(await screen.findByRole("button", { name: "Календарь цеха" }));
  fireEvent.change(await screen.findByLabelText("Нерабочий день"), { target: { value: "2026-11-04" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить праздник" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить календарь" }));

  await waitFor(() => expect(payload).toEqual({
    holidays: ["2026-11-04"], absences: [],
    shift: {
      weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30",
      breaks: [["13:00", "13:30"]], effective_hours: "8",
    },
  }));
});

it("starts the first operation from an issued GRN and package", async () => {
  let payload: unknown;
  const operatorPrincipal: Principal = {
    user: { id: "10000000-0000-0000-0000-000000000002", email: "operator@example.test", name: "Оператор", department: "Производство" },
    roles: ["staff"], permissions: { production: "view" },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/my") return json({ tasks: [task] });
    if (path === "/api/production/my/execution") return json({ session: null, boxes: [] });
    if (path.endsWith("/inputs")) return json({ boxes: [], materials: [{
      fulfillment_line_id: "72000000-0000-0000-0000-000000000001",
      article: "WIRE-1", name: "Провод", unit: "м", grn: "PPM.26.0000001",
      package: "П1", issued: "10", consumed: "0", reserved: "0", available: "10",
    }] });
    if (path.endsWith("/work/start") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ session: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={operatorPrincipal} mine />);
  fireEvent.change(await screen.findByLabelText("Количество из партии PPM.26.0000001 П1"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать операцию" }));

  await waitFor(() => expect(payload).toEqual({
    inputs: [],
    materials: [{ fulfillment_line_id: "72000000-0000-0000-0000-000000000001", quantity: "3" }],
  }));
});

it("records the master's append-only decision for a QC rejection", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/defects") return json({ defects: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    if (path === "/api/production/qc/rejections") return json({ rejections: [{
      id: "73000000-0000-0000-0000-000000000001",
      box_id: "74000000-0000-0000-0000-000000000001",
      box_number: "000002-4", item: "591K.3.КБ1",
      inspected_at: "2026-09-22T09:00:00+03:00", rejected: 1,
      defect_number: "Б-000001-7", defect_character: "Обрыв",
      defect_description: "Нет контакта", status: "Ожидает решения", decision: null,
    }] });
    if (path.endsWith("/qc/rejections/73000000-0000-0000-0000-000000000001/decide") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ decision: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProductionPage principal={principal} />);
  fireEvent.change(await screen.findByLabelText("Акт ОТК для Б-000001-7"), { target: { value: "Акт НС-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Принять решение" }));

  await waitFor(() => expect(payload).toEqual({ disposition: "Доработка", act_reference: "Акт НС-12" }));
});
