import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkMarkLabelsPage, etkPrintPages } from "./etkPrint";

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

const marks = [
  { target: "Разъём X1 (DB-9F)", text: "X1", material: "Бирка маркировочная TMS", place: "у разъёма", qty: "1", batch: "10" },
  { target: "Провод W1", text: "708К.4КБ1", material: "Маркировка", place: "на обоих концах", qty: "2", batch: "20" },
];

function techcard(fields: Record<string, unknown>): ProjectTechcard {
  return { id: "t", project: "P-1", item_index: "КБ1", product_name: "Жгут", revision: 1, document: {
    kit: { part_quantity: "10" },
    operations: [{
      position: 1, code: "INS_MARK_HARNESS", type: "insMark", title: "Маркировка жгута", fields,
      card: { name: "Маркировка жгута", steps: [{ number: "1", kind: "Операция", text: "Установить маркеры на жгут в позициях по КД." }], tooling: [] },
      components: [{ art: "Бирка маркировочная TMS", name: "Бирка маркировочная TMS", norm: "10", unit: "Шт." }],
    }],
  } } as unknown as ProjectTechcard;
}

afterEach(() => vi.restoreAllMocks());

it("the marking operation lists every output mark in Result and keeps instructions in Steps, on screen and print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={techcard({ marks })} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  const rows = within(table).getAllByRole("row").map((row) => row.textContent);
  expect(rows[0]).toContain("На партию 10 шт");
  expect(rows.slice(1)).toEqual([
    "1Разъём X1 (DB-9F)X1Бирка маркировочная TMSу разъёма110",
    "2Провод W1708К.4КБ1Маркировкана обоих концах220",
  ]);
  const section = screen.getByRole("region", { name: "Ход операции" });
  expect(section).not.toContainElement(table);
  expect(screen.getByRole("region", { name: "Результат" })).toContainElement(table);
  expect(within(section).getByText("Установить маркеры на жгут в позициях по КД.")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks }), []));
  expect(html).toMatch(/Описание операции[\s\S]*Результат[\s\S]*Что маркируем[\s\S]*Надпись[\s\S]*708К\.4КБ1[\s\S]*на обоих концах/);
});

it("a tube cutting step without a length shows «длина не задана» in red on screen and with a warning sign in print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({ marks });
  const operation = (card.document as { operations: Array<{ card: { steps: unknown[] } }> }).operations[0];
  operation.card.steps = [
    { number: "1", kind: "Операция", text: "Нарезать трубки в размер — станок резки трубок.", tolerance: "длина не задана" },
    { number: "2", kind: "Контроль", text: "ВИЗУАЛЬНЫЙ КОНТРОЛЬ: надпись на трубке соответствует КД.", tolerance: "Согласно КД" },
  ];
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  await screen.findByText("Нарезать трубки в размер — станок резки трубок.");
  const tolerances = Array.from(document.querySelectorAll(".etk-step-tol dd"));
  expect(tolerances.map((item) => item.textContent)).toEqual(["длина не задана", "Согласно КД"]);
  expect(tolerances[0].closest("dl")).toHaveClass("tone-danger");
  expect(tolerances[1].closest("dl")).not.toHaveClass("tone-danger");
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(card, []))).toContain("⚠ длина не задана");
});

it("an old marking operation without a mark list renders and prints as before", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={techcard({})} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByText("Установить маркеры на жгут в позициях по КД.")).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Метки по КД" })).toBeNull();
  expect(etkPrintPages(techcard({}), [])).toEqual(etkPrintPages(techcard({ marks: [] }), []));
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard({}), []))).not.toContain("Что маркируем");
});

it("a label output list without instructions still gets a Result block, never an empty Steps block", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({ marks });
  const operation = (card.document as { operations: Array<{ code: string; type: string; card: { steps: unknown[] } }> }).operations[0];
  operation.code = "PRINT_MARK_LABEL";
  operation.type = "coaxCutTut";
  operation.card.steps = [];
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Что напечатано" });
  expect(screen.getByRole("region", { name: "Результат" })).toContainElement(table);
  expect(screen.queryByRole("region", { name: "Ход операции" })).toBeNull();
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
  expect(html).not.toContain("Ход операции");
  expect(html).toMatch(/Результат[\s\S]*Что маркируем/);
});

it("the marks table gets «Длина, мм» only when a tube row has it, on screen and in print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const withLength = [{ ...marks[0], material: "Термотрубка", length: "25" }, marks[1]];
  render(<EtkSheet card={techcard({ marks: withLength })} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  expect(within(table).getByRole("columnheader", { name: "Длина, мм" })).toBeInTheDocument();
  expect(within(table).getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([
    "1Разъём X1 (DB-9F)X1Термотрубкау разъёма25110", "2Провод W1708К.4КБ1Маркировкана обоих концах220",
  ]);
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks: withLength }), []))).toMatch(/Где[\s\S]*Длина, мм[\s\S]*На изделие/);
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks }), []))).not.toContain("Длина, мм");
});

it("marks without the «№ изделия» token retain their contents and quantities in screen and print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const old = [...marks, { target: "Изделие КБ1", text: "КБ1-№ 1 {N}", material: "Хлястик", place: "на изделии", qty: "1", batch: "10" }];
  const { unmount } = render(<EtkSheet card={techcard({ marks: old })} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  const html = table.outerHTML;
  expect(html).toMatchSnapshot("screen");
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  unmount();
  // В задании старые метки — те же байты, что в техкарте.
  render(<EtkSheet card={techcard({ marks: old })} principal={principal} onChange={() => undefined} task />);
  expect((await screen.findByRole("table", { name: "Метки по КД" })).outerHTML).toBe(html);
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks: old }), []))).toMatchSnapshot("print");
  // Without a task range, fixed labels retain their original project table.
  expect(screen.queryByRole("button", { name: /Печать надписей/ })).toBeNull();
  expect(etkMarkLabelsPage(techcard({ marks: old }), { first: 1, last: 10, task: "ПЗ-7" })?.blocks[0]).toEqual({ table: {
    columns: ["№", "Текст", "Где", "Материал", "Кол-во"], rows: [
      ["1", "X1", "у разъёма", "Бирка маркировочная TMS", "× 10"],
      ["2", "708К.4КБ1", "на обоих концах", "Маркировка", "× 20"],
      ["3", "КБ1-№ 1 {N}", "на изделии", "Хлястик", "× 10"],
    ],
  } });
});

const numbered = [
  { target: "Изделие КБ1", text: "КБ1-{№:3}", material: "Хлястик", place: "на изделии", qty: "1", batch: "10" },
  marks[0],
  { target: "Провод W1", text: "W1/{№}", material: "Термотрубка", place: "на обоих концах", qty: "2", batch: "20" },
];

it("outside a task the inscription retains its template, task-number hint and project quantity", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={techcard({ marks: numbered })} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  expect(within(table).getAllByRole("row").slice(1).map((row) => (row as HTMLTableRowElement).cells[2].textContent)).toEqual([
    "КБ1-{№} — номер по заданию, 10 шт.", "X1", "W1/{№} — номер по заданию, 20 шт.",
  ]);
  expect(screen.queryByRole("button", { name: /Печать надписей/ })).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks: numbered }), []));
  expect(html).toContain("КБ1-{№} — номер по заданию, 10 шт.");
  expect(html).not.toContain("Надписи меток по КД");
});

it("shows each task label explicitly on screen and print, numbered labels first and fixed labels once", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const serial = { first: 11, last: 15, task: "ПЗ-0042" };
  const card = techcard({ marks: numbered });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} task serial={serial} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  const expected = [
    ...Array.from({ length: 5 }, (_, index) => [String(index + 1), `КБ1-0${11 + index}`, "на изделии", "Хлястик", "× 1"]),
    ...Array.from({ length: 5 }, (_, index) => [String(index + 6), `W1/${11 + index}`, "на обоих концах", "Термотрубка", "× 2"]),
    ["11", "X1", "у разъёма", "Бирка маркировочная TMS", "× 5"],
  ];
  expect(within(table).getAllByRole("row").slice(1).map((row) => Array.from((row as HTMLTableRowElement).cells, (cell) => cell.textContent))).toEqual(expected);
  expect(screen.getByRole("button", { name: "Печать надписей № 11–15" })).toBeInTheDocument();
  expect(screen.getByText("5 изделий задания")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  const pages = etkPrintPages(card, [], {}, serial);
  const labels = pages[pages.length - 1];
  expect(labels.title).toBe("Надписи меток по КД");
  expect(labels.subtitle).toBe("Жгут · P-1 КБ1 · задание ПЗ-0042 · изделия № 11–15");
  expect(labels.blocks).toEqual([{ table: {
    columns: ["№", "Текст", "Где", "Материал", "Кол-во"], rows: expected,
  } }]);
  const printed = buildPrintDocumentHtml("ТК", pages);
  expect(printed).toContain("КБ1-011");
  expect(printed).not.toContain("{№}");
});

it("shows 17 label kinds and 45 pieces for a 15-piece 708К task, correcting an issued result only for display", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({ marks: [
    { text: "FRX.708К.120907/AA.{№:6}", qty: "1", batch: "15", place: "середина кабеля", material: "Лента Vell" },
    { text: "Camera 14", qty: "1", batch: "15", place: "у разъёма А", material: "Лента Vell" },
    { text: "Splitter B C6", qty: "1", batch: "15", place: "у разъёма В", material: "Лента Vell" },
  ] });
  const operation = ((card.document as Record<string, unknown>).operations as Record<string, unknown>[])[0];
  operation.code = "PRINT_MARK_LABEL";
  (operation.card as { steps: unknown[] }).steps = [{ number: "1", kind: "Операция", text: "Напечатать этикетки партии." }];
  operation.result = [{ name: "Напечатанные этикетки: 3 шт.", norm: "45", unit: "Шт." }];
  operation.time = [{ name: "Напечатанные этикетки: 3 шт.", norm: "0" }];
  const original = structuredClone(card);
  const serial = { first: 1, last: 15, task: "ПЗ-1" };
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} task serial={serial} />);
  const table = await screen.findByRole("table", { name: "Что напечатано" });
  fireEvent.click(within(screen.getByRole("region", { name: "Результат" })).getByRole("heading", { level: 4 }));
  expect(within(table).getAllByRole("row")).toHaveLength(19);
  expect(within(table).getByText("FRX.708К.120907/AA.000001")).toBeVisible();
  expect(within(table).getByText("FRX.708К.120907/AA.000015")).toBeVisible();
  expect(within(table).getAllByText("× 15")).toHaveLength(2);
  expect(within(screen.getByRole("region", { name: "Результат" })).getByText("Напечатанные этикетки")).toBeInTheDocument();
  expect(within(table).getByText("Итого: 17 видов, 45 шт.")).toBeInTheDocument();
  const pages = etkPrintPages(card, [], {}, serial);
  const printed = buildPrintDocumentHtml("ЭТК", pages);
  expect(printed.replaceAll("\u00a0", " ")).toContain("Итого: 17 видов, 45 шт.");
  expect(printed).not.toContain("Напечатанные этикетки: 3 шт.");
  const labelBlock = pages.at(-1)!.blocks[0];
  if (!("table" in labelBlock)) throw new Error("Expected the label list table");
  expect(labelBlock.table.rows).toHaveLength(17);
  const operationBlock = pages[1].blocks.find((block) => "table" in block && block.table.columns[1] === "Текст");
  expect(operationBlock).toEqual(labelBlock);
  expect(card).toEqual(original);
});

it("shows the tolerances of the steps in a column of their own, a line per value, numbers bold (owner 17.10: «почему допуски так мелко»)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({});
  const operation = (card.document as { operations: Array<{ card: { steps: unknown[] } }> }).operations[0];
  operation.card.steps = [
    { number: "1", kind: "Операция", text: "Зачистить провод.", tolerance: "Допуск: Сторона В: 2-3 мм\nЗазор: 0,5 - 1,5 мм" },
    { number: "2", kind: "Операция", text: "Пропаять.", tolerance: "(+/-) 10 °C; Время: ≤ 2,0 сек" },
    { number: "3", kind: "Контроль", text: "Осмотреть.", tolerance: "IPC/WHMA-A-620F (Раздел 3)" },
    { number: "4", kind: "Операция", text: "Уложить в тару.", tolerance: "-" },
  ];
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  await screen.findByText("Зачистить провод.");
  const steps = Array.from(document.querySelectorAll(".etk-steps > li"));
  const rows = (index: number) => Array.from(steps[index].querySelectorAll(".etk-step-tol > div")).map((row) =>
    [row.querySelector("dt")?.textContent, row.querySelector("dd")?.textContent, Array.from(row.querySelectorAll("b")).map((bold) => bold.textContent)]);
  expect(rows(0)).toEqual([["Сторона В", "2–3 мм", ["2–3 мм"]], ["Зазор", "0,5–1,5 мм", ["0,5–1,5 мм"]]]);
  expect(rows(1)).toEqual([["Допуск", "± 10 °C", ["± 10 °C"]], ["Время", "≤ 2,0 сек", ["≤ 2,0 сек"]]]);
  // Ссылка на стандарт — обычным текстом, без выделенных цифр; «-» — допуска нет.
  expect(rows(2)).toEqual([["Допуск", "IPC/WHMA-A-620F (Раздел 3)", []]]);
  expect(steps[3].querySelector(".etk-step-tol")).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
  expect(html).toContain("Сторона В: 2–3 мм\nЗазор: 0,5–1,5 мм");
  expect(html).toContain("± 10 °C\nВремя: ≤ 2,0 сек");
});

it("a label installation (even with a stale printer code) keeps its table as a placement spec in Input, not in Result, on screen and in print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({ marks });
  const operation = (card.document as { operations: Array<Record<string, unknown>> }).operations[0];
  operation.code = "PRINT_MARK_LABEL";
  operation.title = "Наклейка этикеток маркировки";
  (operation.card as { name: string }).name = "Наклейка этикеток маркировки";
  operation.result = [{ name: "Кабель с метками", norm: "10", unit: "Шт." }];
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Места установки меток" });
  expect(screen.getByRole("region", { name: "Вход" })).toContainElement(table);
  expect(screen.getByRole("region", { name: "Результат" })).not.toContainElement(table);
  expect(within(table).queryByText(/Итого/)).toBeNull();
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
  expect(html).toMatch(/1\. Вход[\s\S]*Места установки меток[\s\S]*Что маркируем[\s\S]*2\. Оснастка/);
  expect(html).not.toMatch(/Результат[\s\S]*Что маркируем/);
  expect(html).not.toContain("Напечатанные этикетки");
});

it("a label print shows the noun «Напечатанные этикетки», its rows and a total in the footer, on screen and in print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const card = techcard({ marks });
  const operation = (card.document as { operations: Array<Record<string, unknown>> }).operations[0];
  operation.code = "PRINT_MARK_LABEL";
  (operation.card as { steps: unknown[] }).steps = [{ number: "1", kind: "Операция", text: "Напечатать этикетки партии." }];
  operation.title = "Печать этикеток";
  (operation.card as { name: string }).name = "Печать этикеток";
  operation.result = [{ name: "Напечатанные этикетки: 3 шт.", norm: "30", unit: "Шт." }];
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Что напечатано" });
  const result = screen.getByRole("region", { name: "Результат" });
  expect(result).toContainElement(table);
  expect(within(result).getByText("Напечатанные этикетки")).toBeInTheDocument();
  expect(within(table).getByText("Итого: 2 вида, 30 шт.")).toBeInTheDocument();
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, [])).replaceAll(" ", " ");
  expect(html).toMatch(/Результат[\s\S]*Напечатанные этикетки[\s\S]*Что напечатано[\s\S]*Что маркируем[\s\S]*Итого: 2 вида, 30 шт\./);
});

it("an unknown operation norm prints «не задано» in the route, never an empty or zero cell", () => {
  const card = techcard({});
  const operation = (card.document as { operations: Array<Record<string, unknown>> }).operations[0];
  operation.times = { operation_seconds: "", setup_seconds: "" };
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(card, []))).toMatch(/<td>не задано<\/td>/);
});

it("the unserialised print footer spans every column, an install hides the empty-input line, and a missing time key prints «не задано»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const print = techcard({ marks });
  const printOp = (print.document as { operations: Array<Record<string, unknown>> }).operations[0];
  printOp.code = "PRINT_MARK_LABEL";
  printOp.card = { name: "Печать этикеток", steps: [{ number: "1", kind: "Операция", text: "Напечатать этикетки партии." }], tooling: [] };
  printOp.components = [];
  const { unmount } = render(<EtkSheet card={print} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Что напечатано" });
  const head = within(table).getAllByRole("columnheader").length;
  expect(table.querySelector("tfoot td")?.getAttribute("colspan")).toBe(String(head));
  unmount();
  const install = techcard({ marks });
  const op = (install.document as { operations: Array<Record<string, unknown>> }).operations[0];
  op.code = "INS_MARK_LABEL";
  op.card = { name: "Наклейка этикеток маркировки", steps: [], tooling: [] };
  op.components = [];
  op.times = {};
  render(<EtkSheet card={install} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByRole("table", { name: "Места установки меток" })).toBeInTheDocument();
  expect(screen.queryByText(/на входе нет/)).toBeNull();
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(install, []));
  expect(html).not.toContain("на входе нет");
  expect(html).toMatch(/<td>не задано<\/td>/);
});
