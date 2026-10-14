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
  expect(within(input).getByText(/^184,8\sм$/).className).toBe("num");
  expect(within(input).getByText("со склада по комплектации")).toBeInTheDocument();
  const result = screen.getByRole("region", { name: "Результат" });
  expect(within(result).getByText("ПВАМ 1,5 264мм")).toBeInTheDocument();
  expect(within(result).getByText(/^700\sшт$/)).toBeInTheDocument();
  expect(screen.getByLabelText("Расчётное время")).not.toHaveTextContent(/на партию/);
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

/** Владелец 12.10: резка с обжимом обеих сторон, на концах разные контакты — у DS1071 норм в БД.ТЕР нет. */
function twoContacts(): Record<string, unknown> {
  return {
    code: "CUT_WIRE+2crimp", connector: "обжим А и В",
    components: [
      { art: "НВ4 0,2 белый", name: "Провод НВ4 0,2 белый", norm: "16", unit: "М." },
      { art: "T-DS1071-SC600", name: "Контакт BLS, BLD Female", norm: "100", unit: "Шт." },
      { art: "SXH-001T-P0.6N", name: "Контакт XH Female", norm: "100", unit: "Шт." },
    ],
    fields: {
      applicator: "M21/A2", crimpHeight: "не задано (А) / 0,60 - 0,75 (В)", pullForce: "9,8 - 39,2",
      crimpEnds: [
        { ends: ["A"], art: "T-DS1071-SC600", wire: "НВ4 0,2", section: "24 AWG", applicator: "", crimpHeight: "", pullForce: "", strip: "3,5" },
        { ends: ["B"], art: "SXH-001T-P0.6N", wire: "НВ4 0,2", section: "24 AWG", applicator: "A2", crimpHeight: "0,60 - 0,75", pullForce: "9.8 - 39.2", strip: "2,5" },
      ],
    },
  };
}

it("puts each contact's crimp spec on its own line in «Комплектующие» and labels the header chips per end", async () => {
  stubFetch();
  render(<EtkSheet card={techcard(twoContacts())} principal={principal} onChange={() => undefined} />);
  const input = await screen.findByRole("region", { name: "Вход" });
  const specs = [...input.querySelectorAll(".etk-spec")].map((node) => node.textContent);
  expect(specs).toEqual([
    "Конец A · НВ4 0,2 (24 AWG) · аппликатор не задано · обжим проводника не задано · отрыв не задано · зачистка 3,5 мм",
    "Конец B · НВ4 0,2 (24 AWG) · аппликатор A2 · обжим проводника 0,60–0,75 мм · отрыв 9,8–39,2 Н · зачистка 2,5 мм",
  ]);
  // Провод — без строки спецификации; «не задано» — тоном опасности, «?» нигде нет.
  expect(input.querySelectorAll(".etk-spec .tone-danger")).toHaveLength(3);
  const chips = screen.getByLabelText("Параметры операции");
  expect(chips).toHaveTextContent("Высота обжимаA (DS1071) не задано · B (SXH) 0,60–0,75 мм");
  expect(chips).toHaveTextContent("АппликаторA (DS1071) не задано · B (SXH) A2");
  expect(chips).toHaveTextContent("ОтрывA (DS1071) не задано · B (SXH) 9,8–39,2 Н");
  expect(document.body.textContent).not.toMatch(/\?\s*\(/);
});

it("prints the contact's crimp spec under its name and the per-end crimp facts", () => {
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard(twoContacts()), []));
  const operationPage = html.slice(html.indexOf("Операция 1."));
  expect(operationPage).toContain("<td>Контакт XH Female\nКонец B · НВ4 0,2 (24 AWG) · аппликатор A2 · обжим проводника 0,60–0,75 мм");
  expect(operationPage).toContain("Высота обжима: A (DS1071) не задано · B (SXH) 0,60–0,75 мм");
  expect(operationPage).toContain("<td>Провод НВ4 0,2 белый</td>");
});

it("shows an ETK issued before the per-end spec as it was, without «?»", async () => {
  stubFetch();
  const issued = twoContacts();
  issued.fields = { applicator: "A14/A2", crimpHeight: "? (А) / 0,60 - 0,75 (В)", pullForce: "? (А) / 9,8 - 39,2 (В)" };
  render(<EtkSheet card={techcard(issued)} principal={principal} onChange={() => undefined} />);
  const chips = await screen.findByLabelText("Параметры операции");
  expect(chips).toHaveTextContent("Высота обжимане задано (А) / 0,60–0,75 (В) мм");
  expect(document.querySelectorAll(".etk-spec")).toHaveLength(0);
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
