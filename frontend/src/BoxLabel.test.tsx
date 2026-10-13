import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BoxLabelPanel, BoxName, boxCodeKey, boxMatches, boxTitle, buildBoxLabelHtml } from "./BoxLabel";
import type { ProductionBox } from "./api";

afterEach(() => vi.restoreAllMocks());

const box: ProductionBox = {
  id: "74000000-0000-0000-0000-000000000009", number: "000007-5", code: "180К.11·КБ1·13.10·Б07·3", version: 1,
  task_id: "t", operation_id: "o", item: "КБ1", operation_position: 2, operator_id: "u",
  quantity: "12", consumed: "0", reserved: "0", rejected: "0", available: "12", status: "Закрыта оператором",
  created_at: "2026-10-13T10:00:00+03:00", closed_at: "2026-10-13T10:00:00+03:00", closed_by: "Белов",
  confirmed_at: null, confirmed_by: null, return_reason: "",
};

it("search key matches the server: case, spaces, separators and latin lookalikes do not matter", () => {
  expect(boxCodeKey("180к.11 кб1 13.10 б07 3")).toBe(boxCodeKey("180К.11·КБ1·13.10·Б07·3"));
  expect(boxCodeKey("180K11-KB1-1310-Б07-3")).toBe("180К11КБ11310Б073");
  expect(boxMatches(box, "кб1 13.10 б07")).toBe(true);
  expect(boxMatches(box, "000007")).toBe(true);
  expect(boxMatches(box, "Б08")).toBe(false);
  expect(boxMatches({ code: null, number: "000001-8" }, "")).toBe(true);
});

it("names a box by its code and an old box by its number", () => {
  expect(boxTitle(box)).toBe("180К.11·КБ1·13.10·Б07·3");
  expect(boxTitle({ code: null, number: "000001-8" })).toBe("000001-8");
  const { container, rerender } = render(<BoxName box={box} />);
  expect(container).toHaveTextContent("180К.11·КБ1·13.10·Б07·3№ 000007-5");
  rerender(<BoxName box={{ code: null, number: "000001-8" }} />);
  expect(container).toHaveTextContent(/^000001-8$/);
});

it("label holds the code, quantity, stage and the accounting number, escaped", () => {
  const html = buildBoxLabelHtml({ code: box.code!, number: "000007-5", quantity: "12", item: "КБ1", stage: "Сборка <А>", closedAt: null });
  expect(html).toContain("180К.11·<wbr>КБ1·<wbr>13.10·<wbr>Б07·<wbr>3");
  expect(html).toContain("12 шт");
  expect(html).toContain("КБ1 · Сборка &lt;А&gt;");
  expect(html).toContain("№ 000007-5");
  expect(html).toContain("size: 58mm 40mm");
});

it("after «Сдать» shows the code big, copies it and closes on «Подписал»", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const close = vi.fn();
  render(<BoxLabelPanel number="000007-5" box={box} stage="Сборка" close={close} />);
  const dialog = screen.getByRole("dialog", { name: "Подпишите коробку" });
  expect(screen.getByLabelText("Код коробки")).toHaveTextContent("180К.11·КБ1·13.10·Б07·3");
  expect(dialog).toHaveTextContent("В коробке 12 шт");
  expect(dialog).toHaveTextContent("КБ1 · Сборка");
  expect(screen.getByRole("button", { name: "Подписал" })).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "Копировать код" }));
  expect(writeText).toHaveBeenCalledWith("180К.11·КБ1·13.10·Б07·3");
  expect(await screen.findByText("Скопировано")).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Подписал" }));
  expect(close).toHaveBeenCalledTimes(2);
});

it("a box without a code (before 13.10) shows its number", () => {
  render(<BoxLabelPanel number="000001-8" box={{ ...box, code: null, number: "000001-8" }} close={() => undefined} />);
  expect(screen.getByLabelText("Код коробки")).toHaveTextContent("000001-8");
});
