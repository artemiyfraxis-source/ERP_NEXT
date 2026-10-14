import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";

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

it("the marking operation of the ETK lists every mark by КД before the steps, and print carries the same table", async () => {
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
  expect(section).toContainElement(table);
  expect(within(section).getByText("Установить маркеры на жгут в позициях по КД.")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks }), []));
  expect(html).toMatch(/Что маркируем[\s\S]*Надпись[\s\S]*708К\.4КБ1[\s\S]*на обоих концах[\s\S]*Описание операции/);
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
  expect(await screen.findByText("Допуск: длина не задана")).toHaveClass("tone-danger");
  expect(screen.getByText("Допуск: Согласно КД")).not.toHaveClass("tone-danger");
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

it("marks without the «№ изделия» token render and print byte-identical to the ETK before numbering (snapshot taken on the old code)", async () => {
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
  // Задание без меток с «№ изделия»: ни кнопки печати надписей, ни лишнего листа.
  expect(screen.queryByRole("button", { name: /Печать надписей/ })).toBeNull();
  expect(etkPrintPages(techcard({ marks: old }), [], {}, { first: 1, last: 10, task: "ПЗ-7" }))
    .toEqual(etkPrintPages(techcard({ marks: old }), []));
});

const numbered = [
  { target: "Изделие КБ1", text: "КБ1-{№:3}", material: "Хлястик", place: "на изделии", qty: "1", batch: "10" },
  marks[0],
  { target: "Провод W1", text: "W1/{№}", material: "Термотрубка", place: "на обоих концах", qty: "2", batch: "20" },
];

it("outside a task the «№ изделия» inscription shows its template and «№ по партии», with no label print", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={techcard({ marks: numbered })} principal={principal} onChange={() => undefined} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  expect(within(table).getAllByRole("row").slice(1).map((row) => (row as HTMLTableRowElement).cells[2].textContent)).toEqual([
    "КБ1-{№} → № по партии", "X1", "W1/{№} → № по партии",
  ]);
  expect(screen.queryByRole("button", { name: /Печать надписей/ })).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({ marks: numbered }), []));
  expect(html).toContain("КБ1-{№} → № по партии");
  expect(html).not.toContain("Надписи меток по КД");
});

it("in a task the inscription shows the task's numbers and «Печать надписей» prints one row per item", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const serial = { first: 11, last: 15, task: "ПЗ-0042" };
  const card = techcard({ marks: numbered });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} task serial={serial} />);
  const table = await screen.findByRole("table", { name: "Метки по КД" });
  expect(within(table).getAllByRole("row").slice(1).map((row) => (row as HTMLTableRowElement).cells[2].textContent)).toEqual([
    "КБ1-{№} → КБ1-011 … КБ1-015", "X1", "W1/{№} → W1/11 … W1/15",
  ]);
  expect(screen.getByRole("button", { name: "Печать надписей № 11–15" })).toBeInTheDocument();
  expect(screen.getByText("5 изделий задания")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());

  const pages = etkPrintPages(card, [], {}, serial);
  const labels = pages[pages.length - 1];
  expect(labels.title).toBe("Надписи меток по КД");
  expect(labels.subtitle).toBe("Жгут · P-1 КБ1 · задание ПЗ-0042 · изделия № 11–15");
  expect(labels.blocks).toEqual([{ table: {
    columns: ["№ изделия", "Изделие КБ1 · Хлястик", "Провод W1 · Термотрубка · 2 шт на изделие"],
    rows: [["11", "КБ1-011", "W1/11"], ["12", "КБ1-012", "W1/12"], ["13", "КБ1-013", "W1/13"], ["14", "КБ1-014", "W1/14"], ["15", "КБ1-015", "W1/15"]],
  } }]);
  expect(buildPrintDocumentHtml("ТК", pages)).toContain("КБ1-{№} → КБ1-011 … КБ1-015");
});
