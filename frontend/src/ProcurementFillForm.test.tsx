import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementFillForm } from "./ProcurementFillForm";
import type { ProcurementLine } from "./api";

function line(id: number, fields: Partial<ProcurementLine> = {}): ProcurementLine {
  return {
    id: `50000000-0000-0000-0000-00000000000${id}`, legacy_id: `SM-${id}`, revision: "A", status: "В работе",
    version: 2, request_date: "2026-09-16", initiator: "Иванов", department: "КБ", designation: "D-1",
    project_reference: null, priority: "Срочно", delivery_point: "Москва", planned_date: "2026-09-30",
    link: null, item_type: "Комплектующие", design_article: null, category: "Общепром",
    calculated_name: `Позиция ${id}`, calculated_quantity: "2", calculated_unit: "шт", buyer: "Петров",
    supplier: null, site_name: null, actual_quantity: null, actual_unit: null, ...fields,
  } as ProcurementLine;
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("marks an edited row, refuses a marked row without supplier and saves marked rows in one call", async () => {
  const sent: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/in-work/mine") return json({ lines: [line(1), line(2)] });
    if (path === "/api/procurement/facts" && init?.method === "POST") {
      sent.push(JSON.parse(String(init.body)));
      return json({ lines: [line(1, { status: "Готов к запросу" })], saved: 1, ready: 1, skipped: [], failed: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const onSaved = vi.fn();

  render(<ProcurementFillForm units={["шт", "кг"]} onSaved={onSaved} onClose={() => undefined} />);
  fireEvent.change(await screen.findByLabelText("Ссылка SM-1"), { target: { value: "https://chipdip.ru/p/1" } });
  expect(screen.getByLabelText("В обработку SM-1")).toBeChecked();
  expect(screen.getByLabelText("В обработку SM-2")).not.toBeChecked();

  fireEvent.click(screen.getByRole("button", { name: "Сохранить отмеченные (1)" }));
  expect(await screen.findByText(/Заполни «Поставщик»: SM-1/)).toBeInTheDocument();
  expect(sent).toEqual([]);

  fireEvent.change(screen.getByLabelText("Поставщик SM-1"), { target: { value: "ЧипДип" } });
  fireEvent.change(screen.getByLabelText("Кол-во (факт) SM-1"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Ед.изм (факт) SM-1"), { target: { value: "кг" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить отмеченные (1)" }));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({
    rows: [expect.objectContaining({
      line_id: line(1).id, version: 2, supplier: "ЧипДип", link: "https://chipdip.ru/p/1",
      actual_quantity: "3", actual_unit: "кг",
    })],
  });
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith("Сохранено строк: 1 · готовы к запросу: 1. Остальные строки не тронуты."));
});
