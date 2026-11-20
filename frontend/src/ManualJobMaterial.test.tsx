import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ManualJobMaterial } from "./ManualJobMaterial";
import { ManualJobsPage } from "./ManualJobsPage";
import { ManualJobWindow } from "./ManualJobWindow";
import type { ManualJob, ManualJobLot, ManualJobMaterialView } from "./manualJobsApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

const JOB: ManualJob = {
  id: "j3", number: "Р-000003", plc_number: "R-000003", equipment_id: "u19", inventory: "ОБ.019", machine: "Автомат резки",
  status: "done", status_text: "Завершена", tone: "ok", purpose: "Проба на образцах", wire: "ПВАМ 0,35", contact: "",
  length_mm: "500", quantity_planned: null, reference_id: null, count_source: "manual", count_text: "Вручную",
  good: 18, defect: 2, defect_reason: "надрез", machine_count: null, material: "pending", material_text: "Ждёт решения склада",
  material_tone: "warn", material_note: "", cancel_reason: "", operator: "Слава", finished_by: "Слава",
  started_at: "2026-11-20T10:00:00+03:00", finished_at: "2026-11-20T11:00:00+03:00", version: 2,
};
const lot = (id: string, grn: string, available: string, extra: Partial<ManualJobLot> = {}): ManualJobLot => ({
  id, version: 1, warehouse: "ЦАР", grn, package: "", article: "ПВАМ 0,35", name: "Провод ПВАМ 0,35 красный", unit: "м",
  storage_location: "12C-3", balance: available, reserved: "0", available, here: true, match: "exact", ...extra });
const VIEW: ManualJobMaterialView = {
  job: JOB, site: "ЦАР", pieces: 20, length_mm: "500",
  wire: { label: "ПВАМ 0,35", lots: [lot("a", "Г-001", "6"), lot("a2", "Г-004", "50"),
    lot("b", "Г-002", "50", { warehouse: "ЖУК", here: false }), lot("f", "Г-005", "80", { article: "ПВАМ 0,35 син", match: "fuzzy" })] },
  contact: { label: "", lots: [] }, found: [], issues: [], can_issue: true, can_decide: true, can_search: true, search_note: "",
};
const ISSUED: ManualJobMaterialView = {
  ...VIEW, job: { ...JOB, material: "issued", material_text: "Списано с партии", material_tone: "ok", version: 3 },
  wire: { label: "ПВАМ 0,35", lots: [] },
  issues: [{ lot_id: "a", writeoff_id: "w1", quantity: "6", warehouse: "ЦАР", grn: "Г-001", package: "", name: "Провод ПВАМ 0,35 красный",
    unit: "м", by: "Кладовщик", at: "2026-11-20T12:00:00+03:00" }],
};

type Call = { url: string; method: string; body?: unknown };

function serve(calls: Call[], view: ManualJobMaterialView = VIEW, answer: Response | (() => Response) = () => json(ISSUED)) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.includes("/material?q=син")) return json({ ...view, found: [lot("c", "Г-009", "30", { article: "ПВАМ 0,35 син", match: "" })] });
    if (url.includes("/material?q=")) return json({ ...view, found: [lot("d", "Г-010", "30", { article: "ПВАМ 0,35 бел", match: "" })] });
    if (url.endsWith("/material")) return json(view);
    if (url.endsWith("/material/issue") || url.endsWith("/material/not-issued")) return typeof answer === "function" ? answer() : answer;
    if (url.endsWith("/finish")) return json(JOB);
    if (url.includes("/manual-jobs?")) {
      return json({ date_from: "2026-11-20", date_to: "2026-11-20", rows: [JOB], can_edit: false, can_issue: true, can_decide: true, waiting_count: 1 });
    }
    return json({});
  });
}

it("suggests the wire used, spreads it over the lots and writes it off in one request", async () => {
  const calls: Call[] = [];
  serve(calls);
  const saved = vi.fn();
  render(<ManualJobMaterial job={JOB} close={() => undefined} onSaved={saved} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  expect(await within(dialog).findByText(/Провод: 20 шт × 500 мм =/)).toBeTruthy();
  const value = (grn: string) => (within(dialog).getByLabelText(`Списать с ${grn}`) as HTMLInputElement).value;
  // 10 m over the exact lots of the machine's warehouse: all of Г-001, the rest from Г-004; the other warehouse's lot
  // (Г-002) and the look-alike (Г-005) are left for the storekeeper.
  expect(["Г-001", "Г-004", "Г-002", "Г-005"].map(value)).toEqual(["6", "4", "", ""]);
  fireEvent.change(within(dialog).getByLabelText("Отходы наладки, м"), { target: { value: "0,5" } });
  fireEvent.click(within(dialog).getByRole("button", { name: /Подставить 10,5/ }));
  expect(["Г-001", "Г-004", "Г-002", "Г-005"].map(value)).toEqual(["6", "4,5", "", ""]);
  const submit = within(dialog).getByRole("button", { name: "Списать с партии (2 партии)" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(false);
  fireEvent.click(submit);
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/manual-jobs/j3/material/issue"))).toBe(true));
  expect(calls.find((call) => call.url.endsWith("/material/issue"))?.body).toEqual({ version: 2, lines: [
    { lot_id: "a", lot_version: 1, quantity: "6" }, { lot_id: "a2", lot_version: 1, quantity: "4.5" }] });
  expect(await within(dialog).findByRole("group", { name: "Списано с партий" })).toBeTruthy();
  expect(within(dialog).getByText("Списано с партии")).toBeTruthy();
  expect(within(dialog).queryByRole("button", { name: /Списать с партии/ })).toBeNull();
  expect(saved).toHaveBeenCalledWith(ISSUED.job);
});

it("refuses more than a lot has before sending and shows the server's refusal", async () => {
  const calls: Call[] = [];
  serve(calls, VIEW, () => json({ detail: "Ручную работу уже изменили — обновите и проверьте ещё раз" }, 409));
  render(<ManualJobMaterial job={JOB} close={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  const first = await within(dialog).findByLabelText("Списать с Г-001");
  fireEvent.change(first, { target: { value: "7" } });
  const submit = within(dialog).getByRole("button", { name: /Списать с партии/ }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  expect(within(dialog).getByText(/Г-001: доступно 6/)).toBeTruthy();
  fireEvent.change(first, { target: { value: "6" } });
  fireEvent.click(submit);
  expect(await within(dialog).findByRole("alert")).toHaveProperty("textContent", "Ручную работу уже изменили — обновите и проверьте ещё раз");
  // A lot found by search can be written off too; its typed quantity survives the next search.
  fireEvent.change(within(dialog).getByRole("searchbox"), { target: { value: "син" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Найти" }));
  fireEvent.change(await within(dialog).findByLabelText("Списать с Г-009"), { target: { value: "3" } });
  expect(calls.some((call) => call.url.endsWith("/material?q=син"))).toBe(true);
  fireEvent.change(within(dialog).getByRole("searchbox"), { target: { value: "бел" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Найти" }));
  expect(await within(dialog).findByLabelText("Списать с Г-010")).toBeTruthy();
  expect((within(dialog).getByLabelText("Списать с Г-009") as HTMLInputElement).value).toBe("3");
  expect(within(dialog).getByRole("button", { name: "Списать с партии (3 партии)" })).toBeTruthy(); // Г-001, Г-004, Г-009
});

it("lets the master say «Не списывать» with a reason but not write off", async () => {
  const calls: Call[] = [];
  serve(calls, { ...VIEW, can_issue: false, can_search: false }, () => json({ ...VIEW, job: { ...JOB, material: "not_issued", material_text: "Не списывать",
    material_tone: "neutral", material_note: "обрезки", version: 3 }, wire: { label: "ПВАМ 0,35", lots: [] } }));
  render(<ManualJobMaterial job={JOB} close={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  expect(await within(dialog).findByText("Списывает склад — нужна правка раздела «Склад»")).toBeTruthy();
  expect((within(dialog).getByRole("button", { name: /Списать с партии/ }) as HTMLButtonElement).disabled).toBe(true);
  expect((within(dialog).getByLabelText("Списать с Г-001") as HTMLInputElement).disabled).toBe(true);
  expect(within(dialog).queryByRole("searchbox")).toBeNull(); // the free stock search is the warehouse's
  fireEvent.click(within(dialog).getByRole("button", { name: "Не списывать…" }));
  const refuse = within(dialog).getByRole("button", { name: "Не списывать" }) as HTMLButtonElement;
  expect(refuse.disabled).toBe(true);
  fireEvent.change(within(dialog).getByLabelText("Почему не списывать"), { target: { value: "обрезки" } });
  fireEvent.click(refuse);
  await waitFor(() => expect(calls.find((call) => call.url.endsWith("/material/not-issued"))?.body).toEqual({ version: 2, note: "обрезки" }));
  expect(await within(dialog).findByText("обрезки")).toBeTruthy();
});

it("counts contacts by the ends of a piece", async () => {
  serve([], { ...VIEW, job: { ...JOB, wire: "", contact: "DJ621-E2.8B" }, wire: { label: "", lots: [] },
    contact: { label: "DJ621-E2.8B", lots: [lot("k", "Г-050", "100", { unit: "шт", article: "DJ621-E2.8B" })] } });
  render(<ManualJobMaterial job={{ ...JOB, wire: "", contact: "DJ621-E2.8B" }} close={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  expect(((await within(dialog).findByLabelText("Списать с Г-050")) as HTMLInputElement).value).toBe("20");
  fireEvent.change(within(dialog).getByLabelText("Концов на изделие"), { target: { value: "2" } });
  fireEvent.click(within(dialog).getByRole("button", { name: /Подставить 40/ }));
  expect((within(dialog).getByLabelText("Списать с Г-050") as HTMLInputElement).value).toBe("40");
});

it("lists the jobs waiting for the warehouse and opens the material window from the row", async () => {
  const calls: Call[] = [];
  serve(calls);
  render(<ManualJobsPage />);
  const waiting = await screen.findByRole("button", { name: /Ждёт решения склада/ });
  expect(waiting.textContent).toContain("1");
  fireEvent.click(waiting);
  await waitFor(() => expect(calls.some((call) => call.url.includes("/manual-jobs?") && call.url.endsWith("waiting=true"))).toBe(true));
  expect(await screen.findByText(/Ждёт решения склада за все даты: 1 работа/)).toBeTruthy();
  fireEvent.click(screen.getAllByText("Р-000003")[0]);
  expect(await screen.findByRole("dialog", { name: "Материал Р-000003" })).toBeTruthy();
});

it("finishes with «Списать с партии» as waiting for the warehouse and goes on to the lots", async () => {
  const calls: Call[] = [];
  serve(calls);
  const running: ManualJob = { ...JOB, status: "running", status_text: "Идёт", tone: "info", good: null, defect: null, material_text: "",
    finished_at: null, version: 1 };
  render(<ManualJobWindow job={running} canEdit canIssue close={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Годные, шт"), { target: { value: "18" } });
  fireEvent.click(screen.getByRole("radio", { name: "Списать с партии" }));
  fireEvent.click(screen.getByRole("button", { name: "Завершить работу" }));
  await waitFor(() => expect(calls.find((call) => call.url.endsWith("/finish"))?.body).toEqual({ version: 1, good: 18, defect: 0,
    defect_reason: "", material: "pending", material_note: "" }));
  expect(await screen.findByRole("dialog", { name: "Материал Р-000003" })).toBeTruthy();
});

it("pre-fills nothing when the machine's warehouse is not known", async () => {
  serve([], { ...VIEW, site: "", wire: { label: "ПВАМ 0,35", lots: VIEW.wire.lots.map((item) => ({ ...item, here: false })) } });
  render(<ManualJobMaterial job={JOB} close={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  const inputs = await within(dialog).findAllByLabelText(/^Списать с Г-/);
  expect(inputs.map((input) => (input as HTMLInputElement).value)).toEqual(["", "", "", ""]);
  expect(within(dialog).queryByRole("button", { name: /Подставить/ })).toBeNull();
});

it("says «5 партий» on the button", async () => {
  const five = ["1", "2", "3", "4", "5"].map((n) => lot(`p${n}`, `Г-10${n}`, "2"));
  serve([], { ...VIEW, wire: { label: "ПВАМ 0,35", lots: five } });
  render(<ManualJobMaterial job={JOB} close={() => undefined} />);
  const dialog = await screen.findByRole("dialog", { name: "Материал Р-000003" });
  expect(await within(dialog).findByRole("button", { name: "Списать с партии (5 партий)" })).toBeTruthy();
});
