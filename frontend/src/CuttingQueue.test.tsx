import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CuttingQueue } from "./CuttingQueue";
import type { CuttingQueueRow } from "./cuttingQueueApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

function row(overrides: Partial<CuttingQueueRow>): CuttingQueueRow {
  return {
    key: "k1", task_id: "t1", operation_id: "o1", task_number: "ПЗ-1", project: "708К.", item: "КБ3", group_number: null,
    wire: "RTK031", wire_name: "RTK 031", length_mm: "120", planned: "120", done: "0", left: "120",
    strip_a: "3/7,6", strip_a_mode: "full", strip_b: null, strip_b_mode: null, laser_mark: null, marking_mode: "ends",
    operator_names: [], warnings: [], blocked: null, setup_change: false, setup_text: "", ...overrides,
  };
}

const MACHINES = { machines: [
  { machine_id: "ОБ.095", machine_name: "BZW-50X", rows: 2, left: "170", projects: 2 },
  { machine_id: null, machine_name: "Станок не указан", rows: 1, left: "5", projects: 1 },
] };
const ROWS = [
  row({ setup_change: true, setup_text: "Заправьте RTK 031 · без разделки B" }),
  row({ key: "k2", task_id: "t2", operation_id: "o2", project: "801К.1", item: "КБ1", left: "50", length_mm: "300",
    warnings: ["нет в ЭТК: зачистка Б"], blocked: "Группа РЗ-1 начата на карточке группы" }),
];

function mockApi(onDone?: (init: RequestInit) => Response) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/cutting-queue/machines")) return json(MACHINES);
    if (url.includes("/cutting-queue/done")) return onDone ? onDone(init as RequestInit) : json({ notice: "Готово: 708К. · КБ3 · 120 шт", assigned: false, output_id: "x" });
    if (url.includes("machine_id=none")) return json({ machine_id: null, version: "v", rows: [row({ key: "n1", project: "999К.", warnings: ["нет в ЭТК: станок"] })] });
    return json({ machine_id: "ОБ.095", version: "v", rows: ROWS });
  });
}

it("renders rows in API order with the setup banner, warnings and the current row marked", async () => {
  mockApi();
  render(<CuttingQueue />);
  await screen.findByText("Заправьте RTK 031 · без разделки B");
  const lines = screen.getAllByRole("row").slice(1);
  expect(lines.map((line) => line.textContent)).toEqual([
    "Заправьте RTK 031 · без разделки B",
    expect.stringContaining("708К."),
    expect.stringContaining("801К.1"),
  ]);
  expect(lines[1]).toHaveAttribute("aria-current", "true");
  expect(lines[2]).not.toHaveAttribute("aria-current");
  expect(within(lines[2]).getByText("нет в ЭТК: зачистка Б")).toBeInTheDocument();
  expect(screen.getByText(/Сейчас/)).toBeInTheDocument();
});

it("posts «Готово» with an Idempotency-Key, shows the notice and reloads", async () => {
  const fetchMock = mockApi();
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  expect(await screen.findByText("Готово: 708К. · КБ3 · 120 шт")).toBeInTheDocument();
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/cutting-queue/done"))!;
  const init = call[1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({ task_id: "t1", operation_id: "o1", quantity: 120 });
  expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/cutting-queue/machines")).length).toBeGreaterThan(1));
});

it("disables «Готово» of a blocked row with the reason as tooltip", async () => {
  mockApi();
  render(<CuttingQueue />);
  await screen.findByText("801К.1");
  const button = screen.getAllByRole("button", { name: "Готово" })[1];
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "Группа РЗ-1 начата на карточке группы");
});

it("shows a server refusal as returned", async () => {
  mockApi(() => json({ detail: "Этап 1: уже сдано 10 из 10, осталось 0." }, 422));
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  expect(await screen.findByRole("alert")).toHaveTextContent("уже сдано 10 из 10");
});

it("selects the «Станок не указан» bucket", async () => {
  const fetchMock = mockApi();
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.change(screen.getByLabelText("Станок"), { target: { value: "none" } });
  await screen.findByText("999К.");
  expect(screen.getByText("нет в ЭТК: станок")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("machine_id=none"))).toBe(true);
});

it("shows the blocked reason as text, skips blocked rows for the current mark and posts on behalf", async () => {
  const blocked = [row({ key: "b", blocked: "Группа РЗ-1 начата на карточке группы" }), row({ key: "c", task_id: "t3", operation_id: "o3", project: "801К.1" })];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/machines")) return json(MACHINES);
    if (url.includes("/done")) return json({ notice: "Готово: 801К.1 · КБ3 · 120 шт", assigned: true, output_id: "x" });
    return json({ machine_id: "ОБ.095", version: "v", rows: blocked });
  });
  render(<CuttingQueue onBehalfOf="u-7" />);
  await screen.findByText("Группа РЗ-1 начата на карточке группы");
  const lines = screen.getAllByRole("row").slice(1);
  expect(lines[0]).not.toHaveAttribute("aria-current");
  expect(lines[1]).toHaveAttribute("aria-current", "true");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[1]);
  await screen.findByText("Готово: 801К.1 · КБ3 · 120 шт");
  expect(String(fetchMock.mock.calls.find(([url]) => String(url).includes("/done"))![0])).toContain("on_behalf_of=u-7");
});

it("read-only mode has no «Готово» and says where it is submitted", async () => {
  mockApi();
  render(<CuttingQueue readOnly />);
  await screen.findByText("708К.");
  expect(screen.queryByRole("button", { name: "Готово" })).toBeNull();
  expect(screen.getByText("«Готово» сдаёт оператор в «Заданиях»")).toBeInTheDocument();
});

it("reloads the queue after a refused «Готово» and keeps the server text", async () => {
  const fetchMock = mockApi(() => json({ detail: "Этап 1: уже сдано 10 из 10, осталось 0." }, 422));
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  const before = fetchMock.mock.calls.filter(([url]) => String(url).includes("/machines")).length;
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/machines")).length).toBeGreaterThan(before));
  expect(await screen.findByRole("alert")).toHaveTextContent("уже сдано 10 из 10");
});
