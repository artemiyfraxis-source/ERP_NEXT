import { expect, it, vi } from "vitest";
import type { PrintSheet } from "./api";
import { buildPrintDocumentHtml, buildPrintSheetHtml, openPrintHtml, openPrintSheet, printStoredDocument } from "./print";

const sheet: PrintSheet = {
  title: "ЗАЯВКА НА ВЫДАЧУ REQ.26.001",
  subtitle: "Дата: 29.09.2026 09:00 · Заявитель: Технолог · Назначение: 316К.14 / КБ-1 · Дедлайн: —",
  columns: ["№", "Артикул КД", "Наименование", "ГРН", "Пакет", "Нужно", "Набрать", "Ед.", "Место хранения", "✓"],
  rows: [["1", "КД-9 <B>", "Разъём & колодка", "— ждём поставки —", "", "6", "0", "шт", "", ""]],
  note: "НЕДОБОР (на складе не хватило): Разъём & колодка",
  sign: "Набрал: ______________    Выдал: ______________    Получил: ______________    Дата: ________",
};

it("renders title, columns and rows into a standalone printable document", () => {
  const html = buildPrintSheetHtml(sheet);
  expect(html).toContain("<!doctype html>");
  expect(html).toContain("<title>ЗАЯВКА НА ВЫДАЧУ REQ.26.001</title>");
  expect(html).toContain("<th>Наименование</th>");
  expect(html).toContain("<td>— ждём поставки —</td>");
  expect(html).toContain(sheet.sign);
});

it("escapes cell content so an article or name cannot inject markup", () => {
  const html = buildPrintSheetHtml(sheet);
  expect(html).toContain("КД-9 &lt;B&gt;");
  expect(html).not.toContain("<B>");
  expect(html).toContain("Разъём &amp; колодка");
});

it("omits the note block entirely when there is no shortage", () => {
  const html = buildPrintSheetHtml({ ...sheet, note: "" });
  expect(html).not.toContain('class="note"');
});

it("renders a multi-page document: one section per page, empty tables left out, content escaped", () => {
  const html = buildPrintDocumentHtml("ТК P-1", [
    { title: "Титул", subtitle: "ревизия 1", blocks: [{ heading: "Маршрут" }, { table: { columns: ["№"], rows: [["1 <b>"]] } }], sign: "Подпись" },
    { title: "Операция 1", subtitle: "", blocks: [{ text: "ВАЖНО!" }, { table: { columns: ["Пусто"], rows: [] } }], sign: "Подпись" },
  ]);
  expect(html.match(/<section class="page">/g)).toHaveLength(2);
  expect(html).toContain("<h2>Маршрут</h2>");
  expect(html).toContain("1 &lt;b&gt;");
  expect(html).not.toContain("<th>Пусто</th>");
  expect(html).toContain("page-break-after: always");
});

it("prints a stored ERP document through a hidden same-origin frame", () => {
  printStoredDocument("/api/procurement/supplier-requests/PO-001/document");

  const frame = document.body.querySelector("iframe");
  expect(frame?.getAttribute("src")).toBe("/api/procurement/supplier-requests/PO-001/document");
  const print = vi.fn();
  Object.defineProperty(frame!, "contentWindow", { value: { print, focus: vi.fn(), addEventListener: vi.fn() } });
  frame!.onload?.(new Event("load"));
  expect(print).toHaveBeenCalledOnce();
  frame!.remove();
});

it("prints through a hidden iframe, never window.open (noopener made it return null and print nothing)", () => {
  const open = vi.spyOn(window, "open");
  openPrintSheet({ title: "Заявка REQ.1", subtitle: "", columns: ["A"], rows: [["x"]], note: "", sign: "" });

  const frame = document.body.querySelector("iframe");
  expect(frame?.srcdoc).toContain("Заявка REQ.1");
  expect(open).not.toHaveBeenCalled();
  const print = vi.fn();
  Object.defineProperty(frame!, "contentWindow", { value: { print, focus: vi.fn(), addEventListener: vi.fn() } });
  frame!.onload?.(new Event("load"));
  expect(print).toHaveBeenCalledOnce();
  frame!.remove();
  open.mockRestore();
});

it("prints synchronously inside the call: the frame exists as soon as openPrintHtml returns", () => {
  openPrintHtml("<p>x</p>");
  expect(document.body.querySelectorAll("iframe")).toHaveLength(1);
  document.body.querySelector("iframe")!.remove();
});
