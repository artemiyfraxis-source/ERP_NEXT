import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { leftoverMatches, SemiLeftoversList, type SemiLeftover } from "./SemiLeftovers";

const row = (extra: Partial<SemiLeftover> = {}): SemiLeftover => ({
  task_id: "t1", task_number: "ПЗ-0001", task_status: "Отменено", project: "708К", item: "КБ1",
  operation_id: "o1", position: 1, code: "CUT", stage: "Резка",
  produced: "10", consumed: "4", in_boxes: "6", needed: "0", surplus: "6",
  boxes: [{ id: "b1", number: "104233", status: "Подтверждена", available: "6" }],
  ...extra,
});

it("matches leftovers of other tasks by project КБ, stage number, operation and stage name", () => {
  const rows = [
    row(),
    row({ operation_id: "o2", task_id: "t2", task_number: "ПЗ-0002" }),
    row({ operation_id: "o3", position: 2 }),
    row({ operation_id: "o4", code: "PRS" }),
    row({ operation_id: "o5", item: "КБ2" }),
    row({ operation_id: "o6", project: "614К.1" }),
  ];
  const planned = { task_id: "t2", project: "708к", item: "кб1", position: 1, code: "CUT", stage: "резка" };
  expect(leftoverMatches(rows, planned).map((item) => item.operation_id)).toEqual(["o1"]);
  expect(leftoverMatches(rows, { ...planned, item: "" })).toEqual([]);
});

it("lists leftovers with produced, taken, still needed and surplus", () => {
  render(<SemiLeftoversList rows={[row()]} error="" />);
  expect(screen.getByText(/Остатки полуфабрикатов · 1 позиция, 6 шт/)).toBeInTheDocument();
  const cells = screen.getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells).toEqual(["708К · КБ1", "1 · РезкаCUT", "ПЗ-0001Отменено", "10", "4", "0", "6", "104233"]);
});

it("has an honest empty and error state", () => {
  const { rerender } = render(<SemiLeftoversList rows={[]} error="" />);
  expect(screen.getByText("Остатков полуфабрикатов нет.")).toBeInTheDocument();
  rerender(<SemiLeftoversList rows={null} error="Нет связи с ERP" />);
  expect(screen.getByRole("alert")).toHaveTextContent("Нет связи с ERP");
});
