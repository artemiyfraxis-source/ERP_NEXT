import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MarkingOutsideWindow } from "./MarkingOutsideWindow";
import type { CuttingGroup } from "./cuttingGroups";

afterEach(() => vi.restoreAllMocks());

const group = {
  id: "group-1", number: "РЗ-0023", version: 3, project: "708К.", site: "ЦАР", kind: "labels",
  rows: ["КБ3", "КБ4"].map((item, index) => ({ id: `member-${index}`, task_id: `task-${index}`, item, position: index, membership_state: "active" })),
} as unknown as CuttingGroup;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const label = (task: number, serial: number, state: string) => ({ task_id: `task-${task}`, item: `КБ${task + 3}`, task_number: `ПЗ-${task}`,
  serial, text: `FRX.708К.КБ${task + 3}.${serial}`, quantity: 1, state });

function server(marked: ReturnType<typeof label>[] = []) {
  const calls: { url: string; body: Record<string, unknown> | null }[] = [];
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, body });
    if (url.startsWith("/api/production/marking/outside?")) return json({ rows: marked.map((row) => ({ ...row, actor: "Мастер", at: "2026-11-20T10:00:00+03:00", comment: "" })) });
    const tasks: number[] = body.task_ids.length ? [Number(String(body.task_ids[0]).slice(5))] : [0, 1];
    const rows = tasks.flatMap((task) => body.serials.map((serial: number) => {
      const done = marked.some((row) => row.task_id === `task-${task}` && row.serial === serial);
      return label(task, serial, url.endsWith("/undo") ? "removed" : done ? "exists" : "new");
    })).filter((row) => !url.endsWith("/undo") || marked.some((item) => item.task_id === row.task_id && item.serial === row.serial));
    return json({ dry_run: body.dry_run, rows, added: rows.filter((row) => row.state === "new").length, removed: rows.length });
  });
  return { fetch, calls };
}

it("shows what will be marked for serial 1 of every КБ, marks it and refreshes the card", async () => {
  const { calls } = server();
  const onChange = vi.fn();
  render(<MarkingOutsideWindow group={group} close={() => undefined} onChange={onChange} />);
  const dialog = screen.getByRole("dialog", { name: "Отметить напечатанные вне ERP" });
  expect(within(dialog).getByLabelText("Номера изделий")).toHaveValue("1");
  expect(within(dialog).getByLabelText("КБ")).toHaveDisplayValue("Все КБ группы (2)");
  await within(dialog).findByText("FRX.708К.КБ4.1");
  expect(within(dialog).getAllByText("Будет отмечена")).toHaveLength(2);
  const preview = calls.find((call) => call.body?.dry_run);
  expect(preview?.body).toEqual({ group_id: "group-1", version: 3, task_ids: [], serials: [1], comment: "", dry_run: true });
  fireEvent.change(within(dialog).getByLabelText("Комментарий"), { target: { value: "Печать на старом принтере" } });
  fireEvent.click(within(dialog).getByRole("button", { name: /^Отметить 2\sэтикетки$/ }));
  await within(dialog).findByText(/^Отмечено как напечатанные вне ERP: 2\sэтикетки$/);
  const saved = calls.find((call) => call.body && call.body.dry_run === false);
  expect(saved?.url).toBe("/api/production/marking/outside");
  expect(saved?.body).toMatchObject({ serials: [1], task_ids: [], comment: "Печать на старом принтере" });
  expect(onChange).toHaveBeenCalledTimes(1);
});

it("one КБ, a serial range, a bad entry and «Снять отметку» of existing marks", async () => {
  const { calls } = server([label(1, 2, "exists")]);
  render(<MarkingOutsideWindow group={group} close={() => undefined} onChange={() => undefined} />);
  const dialog = screen.getByRole("dialog", { name: "Отметить напечатанные вне ERP" });
  await within(dialog).findByText("FRX.708К.КБ4.2");
  fireEvent.change(within(dialog).getByLabelText("Номера изделий"), { target: { value: "абв" } });
  expect(within(dialog).getByText("Укажите номера изделий: 1 или 1, 3–5")).toBeTruthy();
  expect(within(dialog).getByRole("button", { name: "Уже отмечены" })).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("КБ"), { target: { value: "task-1" } });
  fireEvent.change(within(dialog).getByLabelText("Номера изделий"), { target: { value: "2-3" } });
  await waitFor(() => expect(within(dialog).getByRole("button", { name: /^Отметить 1\sэтикетку$/ })).toBeEnabled());
  expect(within(dialog).getByText("Уже отмечена")).toBeTruthy();
  expect(calls.filter((call) => call.body?.dry_run).at(-1)?.body).toMatchObject({ task_ids: ["task-1"], serials: [2, 3] });
  fireEvent.click(within(dialog).getByRole("button", { name: /^Снять отметку: 1\sэтикетку$/ }));
  await within(dialog).findByText(/^Отметка снята: 1\sэтикетку$/);
  expect(calls.find((call) => call.url.endsWith("/undo"))?.body).toMatchObject({ task_ids: ["task-1"], serials: [2, 3], dry_run: false });
});
