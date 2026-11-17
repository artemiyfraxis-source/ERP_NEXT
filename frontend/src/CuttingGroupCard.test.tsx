import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CuttingGroupCard } from "./CuttingGroupCard";
import { ProductionPage } from "./ProductionPage";
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

it("renders one card and one action set, with separate PF and blank quantities", () => {
  render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(screen.getAllByRole("button", { name: "Начать" })).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Начать" })).toBeDisabled();
  expect(screen.getAllByRole("button", { name: "Сдать" })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Простой" })).toHaveLength(1);
  expect(screen.getAllByRole("row")).toHaveLength(5);
  expect(screen.getAllByText("60")).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Сдать" }));
  const dialog = screen.getByRole("dialog", { name: "Сдать группу резки" });
  for (const row of group.rows) {
    expect(within(dialog).getByLabelText(`Годные ${row.item}`)).toHaveValue(15);
    expect(within(dialog).getByLabelText(`Брак ${row.item}`)).toHaveValue(0);
  }
  expect(within(dialog).getByRole("button", { name: "Сдать группу" })).toBeDisabled();
  expect(within(dialog).getByText(/Количества не сохранены/)).toBeTruthy();
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
  expect(buildPrintDocumentHtml("Этикетки группы", [page])).toContain("FRX.708К.120900/AA.1");
  const withPadding = { ...group, rows: [{ ...group.rows[0], labels: [{ text: "КБ-{№:3}", qty: 2 }] }] };
  expect(cuttingLabels(withPadding, ranges)[0]).toMatchObject({ text: "КБ-001", quantity: 2 });
});

it("refuses missing or inconsistent serials and excludes split members", () => {
  expect(() => cuttingLabels(group, {})).toThrow("Нет номеров изделий");
  expect(() => cuttingLabels(group, { ...ranges, "member-0": { first: 2, last: 4 } })).toThrow("Нет номеров изделий");
  expect(cuttingRows({ ...group, rows: [{ ...group.rows[0], membership_state: "split" }] })).toHaveLength(0);
});

it("loads the combined labels on demand and prints the complete table once", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ serial: { first: 1, last: 15 } }));
  render(<CuttingGroupCard group={group} readOnly={false} renderInstruction={() => null} />);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Этикетки группы" }));
  expect(await screen.findByText("Этикетки: 68 видов, 180 шт.")).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(4);
  fireEvent.click(screen.getByRole("button", { name: "Печатать все" }));
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
