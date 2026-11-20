import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CuttingGroupCard } from "./CuttingGroupCard";
import { ProductionPage, cutGroupInQueue } from "./ProductionPage";
import { cuttingLabels, cuttingLabelsPage, cuttingLabelsTitle, cuttingRows, type CuttingGroup } from "./cuttingGroups";
import type { Principal, ProductionTask } from "./api";
import { buildPrintDocumentHtml, openPrintDocument } from "./print";

vi.mock("./print", async (original) => ({ ...await original<typeof import("./print")>(), openPrintDocument: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

const group: CuttingGroup = {
  id: "group-1", number: "РЗ-0001", version: 1, recipe_version: 1, recipe_key: "recipe", project: "708К.", site: "ЦАР",
  state: "open", membership_frozen: false, planned_start: "2026-11-17T09:00:00+03:00", planned_finish: "2026-11-17T13:00:00+03:00", norm_seconds: 14400,
  rows: Array.from({ length: 4 }, (_, index) => ({
    id: `member-${index}`, task_id: `task-${index}`, operation_id: `op-${index}`, task_version: 1,
    task_number: `ПЗ-${index + 1}`, project: "708К.", item: `КБ${index + 1}`, name: `Кабель ${index + 1}`, position: index,
    assembly_quantity: 15, blank_quantity: "60", operation_quantity: "15", cables_per_assembly: 4, cut_length_mm: 700 + index * 100,
    labels: [
      { text: `FRX.708К.12090${index}/AA.{№}`, material: "Трубка", qty: "1" },
      { text: "Разъём А", material: "Трубка", qty: "1" }, { text: "Разъём В", material: "Трубка", qty: "1" },
    ],
    good: "0", defects: "0", membership_state: "active", state: "assigned", operator_ids: ["operator"],
    planned_start: "2026-11-17T09:00:00+03:00", planned_finish: "2026-11-17T10:00:00+03:00", norm_seconds: 3600,
    boxes: [], etk_url: `/api/production/my/tasks/task-${index}/operations/op-${index}/etk`,
  })),
};
const ranges = Object.fromEntries(group.rows.map((row) => [row.id, { first: 1, last: 15 }]));
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const working: CuttingGroup = { ...group, version: 2, state: "frozen", session: {
  id: "session-1", task_id: "task-0", operation_id: "op-0", operator_id: "operator", cutting_group_id: group.id,
  started_at: new Date(Date.now() - 120000).toISOString(), status: "В работе", pause_started_at: null, pause_seconds: 0,
} };

it("starts one group and renders a working timer with separate PF and blank quantities", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ group: working, outputs: [] }));
  render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("heading", { name: "Резка · 708К. · ПФ-1 Заготовки · 4 КБ" })).toBeTruthy();
  expect(screen.getByText("РЗ-0001 · ЦАР")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Начать" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Начать" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Брак…" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: "Брак…" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Простой" })).toHaveLength(1);
  expect(screen.getAllByRole("row")).toHaveLength(5);
  expect(screen.getAllByText("60")).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Начать" }));
  await screen.findByText(/В работе · время 0:02/);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(String(fetch.mock.calls[0][0])).toContain("/cutting-groups/group-1/start");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ version: 1 });
  fireEvent.click(screen.getByRole("button", { name: "Брак…" }));
  const dialog = screen.getByRole("dialog", { name: "Сдать группу резки" });
  for (const row of group.rows) {
    expect(within(dialog).getByLabelText(`Годные ${row.item}`)).toHaveValue(15);
    expect(within(dialog).getByLabelText(`Брак ${row.item}`)).toHaveValue(0);
  }
  expect(within(dialog).getByRole("button", { name: "Сдать группу" })).toBeEnabled();
});

it("submits all KB quantities atomically and keeps the retry key and form after an error", async () => {
  const answers = [json({ detail: "Повторите запрос" }, 503), json({ group: working, outputs: [] })];
  const all = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).includes("/machines/runs/") ? json({ runs: [] }) : answers.shift()!);
  const submits = () => all.mock.calls.filter(([input]) => !String(input).includes("/machines/runs/"));
  render(<CuttingGroupCard group={working} readOnly={false} onBehalfOf="operator" renderInstruction={() => null} />);
  fireEvent.click(screen.getByRole("button", { name: "Брак…" }));
  fireEvent.change(screen.getByLabelText("Годные КБ1"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("Брак КБ1"), { target: { value: "1" } });
  expect(screen.getByRole("button", { name: "Сдать группу" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Характер брака КБ1"), { target: { value: "Размер" } });
  fireEvent.change(screen.getByLabelText("Описание брака КБ1"), { target: { value: "Короткий рез" } });
  fireEvent.click(screen.getByRole("button", { name: "Сдать группу" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Повторите запрос");
  expect(screen.getByLabelText("Годные КБ1")).toHaveValue(4);
  fireEvent.click(screen.getByRole("button", { name: "Сдать группу" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const fetch = { mock: { calls: submits() } };
  expect(fetch.mock.calls).toHaveLength(2);
  expect(fetch.mock.calls[0][1]?.headers).toEqual(fetch.mock.calls[1][1]?.headers);
  expect(String(fetch.mock.calls[0][0])).toBe("/api/production/cutting-groups/group-1/submit?on_behalf_of=operator");
  const payload = JSON.parse(String(fetch.mock.calls[0][1]?.body));
  expect(payload.rows).toHaveLength(4);
  expect(payload.rows[0]).toMatchObject({ member_id: "member-0", good_quantity: "4", defect_quantity: "1", defect_description: "Короткий рез" });
  expect(payload.rows[1].good_quantity).toBe("15");
});

it("fills «Годные» of the КБ of that length from the ОБ.019 count, wires per ПФ taken into account", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input).includes("/machines/runs/cutting/group-1")
    ? json({ runs: [{ run_id: "w1", inventory: "ОБ.019", length_mm: "700", count: 20, runs: 2, unknown: false,
      closed_at: "2026-11-19T10:05:00+03:00", closed_by: "Слава" }] })
    : json({ group: working, outputs: [] }));
  render(<CuttingGroupCard group={working} readOnly={false} onBehalfOf="operator" renderInstruction={() => null} />);
  fireEvent.click(screen.getByRole("button", { name: "Брак…" }));
  await waitFor(() => expect(screen.getByLabelText("Годные КБ1")).toHaveValue(5));
  expect(screen.getByText(/Счётчик ОБ.019: 20 шт провода 700 мм, прогонов: 2/)).toBeInTheDocument();
  expect(screen.getByLabelText("Годные КБ2")).toHaveValue(15);
});

it("starts and stops group downtime and prevents submission during it", async () => {
  const paused: CuttingGroup = { ...working, version: 3, session: { ...working.session!, status: "Пауза", pause_started_at: new Date().toISOString() },
    downtime: { id: "down-1", reason: "other", reason_name: "Другое", comment: "Настройка", started_at: new Date().toISOString() } };
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(json({ group: paused, outputs: [] })).mockResolvedValueOnce(json({ group: working, outputs: [] }));
  render(<CuttingGroupCard group={working} readOnly={false} renderInstruction={() => null} />);
  fireEvent.click(screen.getByRole("button", { name: "Простой" }));
  fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "other" } });
  expect(screen.getByRole("button", { name: "Начать простой" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Комментарий"), { target: { value: "Настройка" } });
  fireEvent.click(screen.getByRole("button", { name: "Начать простой" }));
  const stop = await screen.findByRole("button", { name: "Закончить простой" });
  expect(screen.getByRole("button", { name: "Брак…" })).toBeDisabled();
  fireEvent.click(stop);
  await waitFor(() => expect(screen.getByRole("button", { name: "Брак…" })).toBeEnabled());
  expect(String(fetch.mock.calls[0][0])).toContain("/downtime/start");
  expect(String(fetch.mock.calls[1][0])).toContain("/downtime/stop");
});

it("blocks execution for partial views, another active session, and a different performer", () => {
  const { rerender } = render(<CuttingGroupCard group={{ ...group, partial_view: true }} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("button", { name: "Начать" })).toBeDisabled();
  rerender(<CuttingGroupCard group={group} activeSession={working.session} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("button", { name: "Начать" })).toBeDisabled();
  rerender(<CuttingGroupCard group={working} performerId="someone-else" readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("button", { name: "Брак…" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Простой" })).toBeDisabled();
});

it("produces 17 label rows per KB in member order, preserving serial presentation and totals", () => {
  const labels = cuttingLabels({ ...group, rows: [...group.rows].reverse() }, ranges);
  expect(labels).toHaveLength(17 * 4);
  expect(labels[0].text).toBe("FRX.708К.120900/AA.1");
  expect(labels[14].text).toBe("FRX.708К.120900/AA.15");
  expect(labels[15]).toMatchObject({ text: "Разъём А", quantity: 15 });
  expect(labels[16]).toMatchObject({ text: "Разъём В", quantity: 15 });
  expect(labels[17].item).toBe("КБ2");
  expect(cuttingLabelsTitle(labels).replaceAll("\u00a0", " ")).toBe("Этикетки: 68 видов, 180 шт.");
  const page = cuttingLabelsPage(group, labels);
  expect(page.subtitle).toBe(`708К. · РЗ-0001 · ${cuttingLabelsTitle(labels)}`);
  expect(buildPrintDocumentHtml("Этикетки группы", [page])).toContain("FRX.708К.120900/AA.1");
  const withPadding = { ...group, rows: [{ ...group.rows[0], labels: [{ text: "КБ-{№:3}", qty: 2 }] }] };
  expect(cuttingLabels(withPadding, ranges)[0]).toMatchObject({ text: "КБ-001", quantity: 2 });
});

it("refuses missing or inconsistent serials and excludes split members", () => {
  expect(() => cuttingLabels(group, {})).toThrow("Нет номеров изделий");
  expect(() => cuttingLabels(group, { ...ranges, "member-0": { first: 2, last: 4 } })).toThrow("Нет номеров изделий");
  expect(cuttingRows({ ...group, rows: [{ ...group.rows[0], membership_state: "split" }] })).toHaveLength(0);
});

it("loads the combined labels on demand and keeps the complete paper list separate", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ serial: { first: 1, last: 15 } }));
  render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Этикетки группы" }));
  expect(await screen.findByText("Этикетки: 68 видов, 180 шт.")).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(4);
  fireEvent.click(screen.getByRole("button", { name: "Список / PDF" }));
  expect(openPrintDocument).toHaveBeenCalledTimes(1);
  const pages = vi.mocked(openPrintDocument).mock.calls[0][1];
  expect(pages[0].blocks[0]).toHaveProperty("table.rows.length", 68);
});

it("blocks printing when one KB cannot load and respects a partial read-only view", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Нет доступа" }, 403));
  render(<CuttingGroupCard group={{ ...group, partial_view: true }} readOnly renderInstruction={() => null} />);
  expect(screen.queryByRole("button", { name: "Начать" })).toBeNull();
  expect(screen.getByText(/только назначенные вам КБ/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Этикетки группы" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Печатать все" })).toBeDisabled();
});

it("uses real groups instead of duplicate child cards in MyQueue and counts a group once", async () => {
  const principal: Principal = {
    user: { id: "operator", name: "Монтажник", email: "fitter@example.test", department: "Производство" },
    roles: [], permissions: { myops: "edit" },
  };
  const duplicates = group.rows.map((row): ProductionTask => ({
    id: row.task_id, number: row.task_number, version: 1, created_at: "2026-11-17", project: row.project,
    item: row.item, name: row.name, quantity: 15, fulfillment_request_id: null, deadline: null, status: "Запущено", launched_by: "Мастер",
    planned_start: row.planned_start, planned_finish: row.planned_finish, assigned: 1,
    operations: [{ id: row.operation_id, position: 1, code: "PF", stage: "Заготовки", description: "", seconds_per_unit: "1", setup_seconds: "0", norm_seconds: 15,
      operator_ids: ["operator"], planned_start: row.planned_start, planned_finish: row.planned_finish, assigned_by: "Мастер" }],
  }));
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url === "/api/production/my") return json({ tasks: duplicates, cutting_groups: [group] });
    if (url === "/api/production/my/execution") return json({ session: null, boxes: [] });
    throw new Error(`Unexpected request: ${url}`);
  });
  const { container } = render(<ProductionPage principal={principal} mine />);
  await screen.findByRole("region", { name: "Резка · 708К. · ПФ-1 Заготовки · 4 КБ" });
  expect(container.querySelectorAll(".mo-card")).toHaveLength(1);
  expect(container.querySelector(".page-count")?.textContent).toBe("1");
  expect(screen.queryByText(/Резка списком/)).toBeNull();
  expect(screen.queryByText(/Операции вам пока не назначены/)).toBeNull();
});

it("shows «Обновить из ЭТК» only for a stale open group and re-pins it", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ group: { ...group, version: 5 } }));
  const stale = { ...group, etk_stale: true };
  const { unmount } = render(<CuttingGroupCard group={group} readOnly={false} canRefreshEtk renderInstruction={() => null} />);
  expect(screen.queryByRole("button", { name: "Обновить из ЭТК" })).toBeNull();
  unmount();
  const second = render(<CuttingGroupCard group={stale} readOnly={false} renderInstruction={() => null} />);
  expect(screen.queryByRole("button", { name: "Обновить из ЭТК" })).toBeNull();
  second.unmount();
  render(<CuttingGroupCard group={stale} readOnly={false} canRefreshEtk renderInstruction={() => null} />);
  fireEvent.click(screen.getByRole("button", { name: "Обновить из ЭТК" }));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Обновить из ЭТК" })).toBeNull());
  expect(String(fetch.mock.calls[0][0])).toContain("/cutting-groups/group-1/refresh-etk");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ version: 1 });
});

const plain = (rows: CuttingGroup["rows"]) => rows.map((row) => ({ ...row, labels: [] }));

it("cut group: «Резка кабеля» with КБ, length and pieces only, no label block", () => {
  render(<CuttingGroupCard group={{ ...group, kind: "cut", kind_name: "Резка кабеля", rows: plain(group.rows) }} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("heading", { name: "Резка кабеля · 708К. · 4 КБ" })).toBeTruthy();
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent?.trim())).toEqual(["КБ", "Длина, мм", "Шт.", "Осталось, шт", "Готово, шт", ""]);
  expect(screen.queryByRole("button", { name: "Этикетки группы" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Печатать оставшиеся" })).toBeNull();
});

it("labels group: printed / remaining per КБ and «Печатать оставшиеся» follows label pieces left", async () => {
  const answer = (remaining: number, labels = remaining * 3) => ({ rows: group.rows.map((row, index) => ({ task_id: row.task_id,
    task_number: row.task_number, item: row.item, quantity: 15, printed: 15 - remaining, remaining: index === 0 ? remaining : 0,
    remaining_labels: index === 0 ? labels : 0 })), printed: 0, remaining, remaining_labels: labels });
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(answer(5)));
  const { unmount } = render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("heading", { name: "Печать этикеток · 708К. · 4 КБ" })).toBeTruthy();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать оставшиеся" })).toBeEnabled());
  expect(String(fetch.mock.calls[0][0])).toBe("/api/production/marking/progress?group_id=group-1");
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent?.trim())).toEqual(["КБ", "Напечатано", "Осталось"]);
  expect(screen.queryByText("Длина, мм")).toBeNull();
  unmount();
  fetch.mockResolvedValue(json(answer(0)));
  const second = render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly={false} renderInstruction={() => null} />);
  await waitFor(() => expect(screen.getAllByText("15").length).toBeGreaterThan(0));
  expect(screen.getByRole("button", { name: "Печатать оставшиеся" })).toBeDisabled();
  second.unmount();
  // Every item's numbered labels printed, fixed ones left: no item remains, the button stays enabled.
  fetch.mockResolvedValue(json(answer(0, 30)));
  render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly={false} renderInstruction={() => null} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать оставшиеся" })).toBeEnabled());
});

it("tubes group: material, length and pieces, one value per column", () => {
  const rows = plain(group.rows).map((row, index) => ({ ...row, tubes: [
    { material: "ТУТ 2:1 Ø3", length_mm: 35, quantity: 30 + index }, { material: "ТУТ 2:1 Ø3", length_mm: 55, quantity: 15 }] }));
  render(<CuttingGroupCard group={{ ...group, kind: "tubes", rows }} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("heading", { name: "Резка трубок · 708К. · 4 КБ" })).toBeTruthy();
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent?.trim())).toEqual(["КБ", "Материал", "Длина, мм", "Шт."]);
  expect(screen.getAllByRole("row")).toHaveLength(9);
});

it("old mixed group (no kind) keeps the wide table and the label block", () => {
  render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByRole("heading", { name: "Резка · 708К. · ПФ-1 Заготовки · 4 КБ" })).toBeTruthy();
  expect(screen.getByText("Длина реза, мм")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Этикетки группы" })).toBeTruthy();
});

it("«Готово» on a KB row posts a one-row group submit; the input is locked until the group is started", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
    String(input).includes("/machines/runs/") ? json({ runs: [] }) : json({ group: working, outputs: [] }));
  const cut = { ...group, kind: "cut" as const, rows: plain(group.rows) };
  const { rerender } = render(<CuttingGroupCard group={cut} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByLabelText("Сколько готово КБ2")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Готово КБ2" })).toBeDisabled();
  rerender(<CuttingGroupCard group={{ ...cut, session: working.session }} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getByLabelText("Сколько готово КБ2")).toHaveValue(15);
  fireEvent.change(screen.getByLabelText("Сколько готово КБ2"), { target: { value: "6" } });
  fireEvent.click(screen.getByRole("button", { name: "Готово КБ2" }));
  await waitFor(() => expect(fetch.mock.calls.some(([input]) => String(input).includes("/submit"))).toBe(true));
  const call = fetch.mock.calls.find(([input]) => String(input).includes("/submit"))!;
  const body = JSON.parse(String((call[1] as RequestInit).body));
  expect(body.rows).toHaveLength(1);
  expect(body.rows[0]).toMatchObject({ member_id: "member-1", good_quantity: "6", defect_quantity: "0" });
});

it("shows the group norm to the master only", () => {
  const { rerender } = render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(screen.queryByText(/норма \d/)).toBeNull();
  rerender(<CuttingGroupCard group={group} readOnly={false} master renderInstruction={() => null} />);
  expect(screen.getByText(/норма 240 мин/)).toBeTruthy();
});

it("hides a «Резка кабеля» group that is wholly in the cutting queue, unless it was started", () => {
  const cut: CuttingGroup = { ...group, kind: "cut", rows: plain(group.rows) };
  const ops = new Set(cut.rows.map((row) => row.operation_id));
  expect(cutGroupInQueue(cut, ops)).toBe(true);
  expect(cutGroupInQueue(cut, new Set())).toBe(false);
  expect(cutGroupInQueue({ ...cut, session: working.session }, ops)).toBe(false);
  expect(cutGroupInQueue({ ...group, kind: "labels" }, ops)).toBe(false);
});

it("labels group: «Отметить напечатанные вне ERP» opens its window and a mark reloads «Напечатано / Осталось»", async () => {
  const progress = (printed: number) => ({ rows: group.rows.map((row) => ({ task_id: row.task_id, task_number: row.task_number,
    item: row.item, quantity: 15, printed, remaining: 15 - printed, remaining_labels: (15 - printed) * 3 })),
    printed: printed * 4, remaining: (15 - printed) * 4, remaining_labels: (15 - printed) * 12 });
  let marked = false;
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.startsWith("/api/production/marking/progress")) return json(progress(marked ? 1 : 0));
    if (url.startsWith("/api/production/marking/outside?")) return json({ rows: [] });
    const body = JSON.parse(String(init?.body));
    if (!body.dry_run) marked = true;
    return json({ dry_run: body.dry_run, added: 4, rows: group.rows.map((row) => ({ task_id: row.task_id, item: row.item,
      task_number: row.task_number, serial: 1, text: `FRX.${row.item}.1`, quantity: 1, state: "new" })) });
  });
  render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly={false} renderInstruction={() => null} />);
  await waitFor(() => expect(screen.getAllByText("0")).toHaveLength(4));
  fireEvent.click(screen.getByRole("button", { name: "Отметить напечатанные вне ERP" }));
  const dialog = screen.getByRole("dialog", { name: "Отметить напечатанные вне ERP" });
  fireEvent.click(await within(dialog).findByRole("button", { name: /^Отметить 4\sэтикетки$/ }));
  await waitFor(() => expect(screen.getAllByText("14").length).toBeGreaterThanOrEqual(4));
  expect(fetch.mock.calls.filter(([url]) => String(url).startsWith("/api/production/marking/progress"))).toHaveLength(2);
});

it("«Отметить напечатанные вне ERP» needs the print right; «Печатать оставшиеся» stays", async () => {
  const { setRightsPrincipal } = await import("./rights");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ rows: [], printed: 0, remaining: 0, remaining_labels: 0 }));
  setRightsPrincipal({ user: { id: "operator" }, roles: [], permissions: { myops: "edit", print: "view" } } as unknown as Principal);
  try {
    render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly={false} renderInstruction={() => null} />);
    expect(screen.getByRole("button", { name: "Печатать оставшиеся" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Отметить напечатанные вне ERP" })).toBeNull();
  } finally {
    setRightsPrincipal(null);
  }
});

it("read-only labels group has no «Отметить напечатанные вне ERP»", () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ rows: [], printed: 0, remaining: 0, remaining_labels: 0 }));
  render(<CuttingGroupCard group={{ ...group, kind: "labels" }} readOnly renderInstruction={() => null} />);
  expect(screen.queryByRole("button", { name: "Отметить напечатанные вне ERP" })).toBeNull();
});
