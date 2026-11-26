import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import { manualJobsApi, type ManualJob, type ManualJobMaterialView } from "./manualJobsApi";

const JOB: ManualJob = {
  id: "job", number: "Р-000003", plc_number: "R-000003", equipment_id: "u19", inventory: "ОБ.019", machine: "Автомат резки",
  status: "done", status_text: "Завершена", tone: "ok", purpose: "Образцы", wire: "Провод", contact: "", length_mm: "500",
  quantity_planned: null, reference_id: null, count_source: "manual", count_text: "Вручную", good: 20, defect: 0,
  defect_reason: "", machine_count: null, material: "pending", material_text: "Ждёт решения склада", material_tone: "warn",
  material_note: "", cancel_reason: "", operator: "Монтажник", finished_by: "Монтажник", started_at: "2020-01-01T10:00:00+03:00",
  finished_at: "2020-01-01T11:00:00+03:00", version: 7,
};
const VIEW: ManualJobMaterialView = {
  job: JOB, site: "ЦАР", pieces: 20, length_mm: "500", wire: { label: "Провод", lots: [{ id: "lot", version: 9,
    warehouse: "ЦАР", grn: "Г-001", package: "", article: "Провод", name: "Провод", unit: "м", storage_location: "А1",
    balance: "30", reserved: "0", available: "30", here: true, match: "exact" }] },
  contact: { label: "", lots: [] }, found: [], issues: [], can_issue: true, can_decide: true, can_search: true, search_note: "",
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

function setup({ edit = false, rows = [JOB] }: { edit?: boolean; rows?: ManualJob[] } = {}) {
  window.history.replaceState({}, "", "/?section=stock&view=work-materials");
  vi.spyOn(manualJobsApi, "list").mockResolvedValue({ rows, date_from: "2026-11-23", date_to: "2026-11-29",
    can_edit: false, can_issue: edit, can_decide: edit, waiting_count: rows.length });
  vi.spyOn(manualJobsApi, "material").mockResolvedValue({ ...VIEW, can_issue: edit, can_decide: edit });
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("{}", { status: 404, headers: { "Content-Type": "application/json" } }));
  const issue = vi.spyOn(manualJobsApi, "issue").mockResolvedValue({ ...VIEW, job: { ...JOB, material: "issued", material_text: "Списано с партии", version: 8 } });
  const notIssued = vi.spyOn(manualJobsApi, "notIssued").mockResolvedValue({ ...VIEW, job: { ...JOB, material: "not_issued", material_text: "Не списывать", material_note: "Обрезки", version: 8 } });
  return { issue, notIssued };
}

it("shows unresolved material from any date to a warehouse reader without write controls", async () => {
  const { issue, notIssued } = setup();
  render(<WarehousePage readOnly />);
  await screen.findByText("Р-000003");
  expect(screen.getByRole("tab", { name: "Материал работ" })).toHaveAttribute("aria-selected", "true");
  expect(manualJobsApi.list).toHaveBeenCalledWith(expect.any(String), expect.any(String), "", expect.any(AbortSignal), true);
  expect(screen.getByLabelText("Начало периода")).toBeDisabled();
  expect(screen.getByText(/Ждёт решения склада за все даты: 1/)).toBeTruthy();
  fireEvent.click(screen.getByText("Р-000003"));
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  await within(dialog).findByText("Материал ждёт решения склада — у вас только просмотр");
  expect(within(dialog).queryByRole("button", { name: /Списать с партии|Не списывать/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Начать работу" })).toBeNull();
  expect(issue).not.toHaveBeenCalled();
  expect(notIssued).not.toHaveBeenCalled();
});

it("lets the warehouse write off selected lots with job and lot versions once", async () => {
  const { issue } = setup({ edit: true });
  render(<WarehousePage />);
  fireEvent.click(await screen.findByText("Р-000003"));
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  const quantity = await within(dialog).findByLabelText("Списать с Г-001");
  fireEvent.change(quantity, { target: { value: "12" } });
  fireEvent.click(within(dialog).getByRole("button", { name: /Списать с партии/ }));
  await waitFor(() => expect(issue).toHaveBeenCalledExactlyOnceWith("job", 7, [{ lot_id: "lot", lot_version: 9, quantity: "12" }]));
  await waitFor(() => expect(within(dialog).queryByRole("button", { name: /Списать с партии/ })).toBeNull());
  await waitFor(() => expect(manualJobsApi.list).toHaveBeenCalledTimes(2));
});

it("requires a reason for «Не списывать» from the stock view", async () => {
  const { notIssued, issue } = setup({ edit: true });
  render(<WarehousePage />);
  fireEvent.click(await screen.findByText("Р-000003"));
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  fireEvent.click(await within(dialog).findByRole("button", { name: "Не списывать…" }));
  expect(within(dialog).getByRole("button", { name: "Не списывать" })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Почему не списывать"), { target: { value: "Обрезки" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Не списывать" }));
  await waitFor(() => expect(notIssued).toHaveBeenCalledExactlyOnceWith("job", 7, "Обрезки"));
  expect(issue).not.toHaveBeenCalled();
});

it("keeps work editing closed for a warehouse editor reopening a running job", async () => {
  setup({ edit: true, rows: [{ ...JOB, status: "running", status_text: "Идёт", finished_at: null }] });
  render(<WarehousePage />);
  fireEvent.click(screen.getByRole("button", { name: /Ждёт решения склада/ }));
  fireEvent.click(await screen.findByText("Р-000003"));
  const dialog = await screen.findByRole("dialog", { name: "Работа Р-000003" });
  expect(within(dialog).queryByRole("button", { name: /Завершить работу|Отменить работу/ })).toBeNull();
});

it("opens historical material and exports the selected period", async () => {
  setup();
  vi.mocked(manualJobsApi.list).mockResolvedValue({ rows: [{ ...JOB, material: "not_issued", material_text: "Не списывать" }],
    date_from: "2020-01-01", date_to: "2020-01-31", can_edit: false, can_decide: false, can_issue: false });
  const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:journal");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<WarehousePage readOnly />);
  fireEvent.click(screen.getByRole("button", { name: /Ждёт решения склада/ }));
  fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2020-01-01" } });
  fireEvent.change(screen.getByLabelText("Конец периода"), { target: { value: "2020-01-31" } });
  await screen.findByText("Р-000003");
  expect(manualJobsApi.list).toHaveBeenLastCalledWith("2020-01-01", "2020-01-31", "", expect.any(AbortSignal), false);
  fireEvent.click(screen.getByRole("button", { name: "Excel" }));
  expect(create).toHaveBeenCalledWith(expect.any(Blob));
  expect((click.mock.instances[0] as HTMLAnchorElement).download).toBe("Материал работ 01.01.2020 – 31.01.2020.xls");
  fireEvent.click(screen.getByText("Р-000003"));
  await screen.findByRole("dialog", { name: "Материал Р-000003" });
  const params = new URLSearchParams(window.location.search);
  expect(params.get("waiting")).toBe("false");
  expect(params.get("date_from")).toBe("2020-01-01");
});

it("provides the material tab from ordinary stock even without equipment or shipping access", async () => {
  setup();
  window.history.replaceState({}, "", "/?section=stock");
  // This case opens the stock page before switching to the journal.
  vi.mocked(globalThis.fetch).mockImplementation(async input => new Response(JSON.stringify(String(input).split("?")[0] === "/api/stock"
    ? { lots: [], total: 0, next_cursor: null } : {}), { headers: { "Content-Type": "application/json" } }));
  render(<WarehousePage readOnly />);
  fireEvent.click(screen.getByRole("tab", { name: "Материал работ" }));
  await screen.findByRole("heading", { name: "Материал работ" });
  expect(new URLSearchParams(window.location.search).get("view")).toBe("work-materials");
  expect(screen.queryByRole("tab", { name: "Оборудование" })).toBeNull();
});
