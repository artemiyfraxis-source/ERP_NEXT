import { expect, it } from "vitest";
import type { PrintSheet } from "./api";
import { buildPrintSheetHtml } from "./print";

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
