import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";

/* Порядок блоков операции ЭТК (erp-screens §6, аудит дизайна 10.10 ЭТК-1/ЭТК-2): Вход → Оснастка (Инструмент ·
   Оборудование · Расходники всегда) → Ход операции → Типовые дефекты → Эскиз → Результат — на экране и в печати. */

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

function techcard(operation: Record<string, unknown>): ProjectTechcard {
  return { id: "t", project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана", document: {
    kit: { part_quantity: "700" },
    operations: [{
      position: 1, code: "CUT_WIRE+1crimp", type: "cutWireCrimp", title: "Автомат резка + опрессовка", result_label: "Полуфабрикат",
      card: {
        name: "Автомат резка + опрессовка",
        steps: [{ number: "1", kind: "Операция", text: "Отрезать провод" }],
        defects: "Типовые дефекты (NG): подрез жил; обрыв проволочек.",
        tooling: [
          { section: "Оборудование", number: "1", name: "BZW-2.0 | Станок резки" },
          { section: "Инструмент", number: "1", name: "Линейка металлическая" },
        ],
      },
      components: [{ art: "ПВАМ 1,5 белый", name: "Провод ПВАМ 1,5", norm: "184.8", unit: "М." }],
      result: [{ name: "ПВАМ 1,5 264мм", norm: "700", unit: "Шт." }],
      time: [{ name: "Резка", norm: "76,67" }],
      ...operation,
    }],
  } } as unknown as ProjectTechcard;
}

afterEach(() => vi.restoreAllMocks());

function stubFetch() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
}

it("shows «Оснастка» with all three sections, an empty one as «нет», and input quantities as numbers with units", async () => {
  stubFetch();
  render(<EtkSheet card={techcard({})} principal={principal} onChange={() => undefined} />);
  const tooling = await screen.findByRole("region", { name: "Оснастка" });
  const sections = [...tooling.querySelectorAll(".tr-sec")].map((node) => [node.querySelector(".tr-sec-h")?.textContent, node.querySelector(".tr-none")?.textContent ?? ""]);
  expect(sections).toEqual([["Инструмент", ""], ["Оборудование", ""], ["Расходники", "нет"]]);

  const input = screen.getByRole("region", { name: "Вход" });
  expect(within(input).getByText(/^184,8\sм$/).className).toBe("etk-in-qty");
  expect(screen.getByRole("row", { name: /^ПВАМ 1,5 264мм 700\sшт$/ })).toBeInTheDocument();
  expect(screen.getByLabelText("Расчётное время")).toHaveTextContent(/на партию 700\sшт/);
  expect(screen.getByText(/^1 операция ·/)).toBeInTheDocument();
  // Кнопки без эмодзи (ПР-1).
  expect(screen.getByRole("button", { name: "Печать / PDF" })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/[🖨⚙✏⚠]/u);
});

it("prints the operation sections in the fixed ETK order, with the three tooling sections", () => {
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({}), [], { "CUT_WIRE+1crimp::::": "https://erp.test/sketch.png" }));
  const operationPage = html.slice(html.indexOf("Операция 1."));
  const headings = [...operationPage.matchAll(/<h2>([^<]+)<\/h2>/g)].map((match) => match[1]);
  expect(headings).toEqual([
    "1. Вход", "2. Оснастка: инструмент, оборудование, расходники", "3. Ход операции", "4. Типовые дефекты", "5. Эскиз", "6. Результат",
  ]);
  expect(operationPage).toMatch(/<td>Инструмент<\/td>[\s\S]*<td>Оборудование<\/td>[\s\S]*<td>Расходники<\/td><td><\/td><td>нет<\/td>/);
  expect(operationPage).toMatch(/<td>184\.8<\/td><td><\/td><td>м<\/td>/);
});

it("numbers only the printed sections: no defects, no sketch, no input", () => {
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard({
    code: "HEAT_TYT", type: "heat", components: [], card: { name: "Усадка", steps: [{ number: "1", kind: "Операция", text: "Усадить" }], tooling: [] },
  }), []));
  const operationPage = html.slice(html.indexOf("Операция 1."));
  const headings = [...operationPage.matchAll(/<h2>([^<]+)<\/h2>/g)].map((match) => match[1]);
  expect(headings).toEqual(["1. Вход", "2. Оснастка: инструмент, оборудование, расходники", "3. Ход операции", "4. Результат"]);
  expect(operationPage).toContain("Комплектующих и полуфабрикатов на входе нет.");
});
