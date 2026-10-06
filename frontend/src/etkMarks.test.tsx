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
  expect(rows[0]).toContain("На партию 10 шт.");
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

it("an old marking operation without a mark list renders and prints as before", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={techcard({})} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByText("Установить маркеры на жгут в позициях по КД.")).toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Метки по КД" })).toBeNull();
  expect(etkPrintPages(techcard({}), [])).toEqual(etkPrintPages(techcard({ marks: [] }), []));
  expect(buildPrintDocumentHtml("ТК", etkPrintPages(techcard({}), []))).not.toContain("Что маркируем");
});
