import { render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import { shrinkRequirementLines } from "./etkOperation";

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

it.each(["", "0"])("shows unknown label-print time instead of false zero (%s) on screen and print", async (norm) => {
  stubFetch();
  const card = techcard({
    code: "PRINT_MARK_LABEL", type: "coaxCutTut", title: "Печать этикеток",
    times: { operation_seconds: "", machine_seconds: "0" },
    time: [{ name: "Этикетки", norm }],
  });
  const before = structuredClone(card);
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByLabelText("Расчётное время")).toHaveTextContent("не задано");
  expect(screen.getByLabelText("Расчётное время")).not.toHaveTextContent("0 мин");
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
  expect(html).toMatch(/Расчётное время[\s\S]*не задано/);
  expect(card).toEqual(before);
});

it("does not report a partial sum as the total when one time norm is missing", async () => {
  stubFetch();
  const card = techcard({ time: [{ name: "Печать", norm: "2" }, { name: "Нарезка", norm: "" }] });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByLabelText("Расчётное время")).toHaveTextContent("не задано");
});

it("preserves an explicitly supplied zero time norm", async () => {
  stubFetch();
  const card = techcard({ times: { operation_seconds: "0" }, time: [{ name: "Этикетки", norm: "0" }] });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByLabelText("Расчётное время")).toHaveTextContent("0 мин");
});

function stubFetch() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
}

it("distinguishes a legacy shrink operation without evidence from an explicitly non-shrinking PVC sleeve", () => {
  expect(shrinkRequirementLines({ type: "heatTut" })[0].text).toContain("не задано — проверить");
  expect(shrinkRequirementLines({ type: "heatTut", fields: { shrinkRequirements: [] } })).toEqual([]);
});

it("shows the stored material temperature under tooling identically on screen and in print", async () => {
  stubFetch();
  const card = techcard({ type: "heatTut", code: "HEAT_TYT_short", fields: { shrinkRequirements: [
    { material: { article: "20-4808", name: "ТУТ 4,8/1,6" }, status: "resolved", temperature: {
      lower_c: 85, upper_c: 125, kind: "оптимальная", document: "Паспорт Rexant", page: "2",
      source_url: "https://example.com/passport", quote: "Температура усадки 85–125 °C", characteristic_c: 120,
    } },
    { material: { article: "ТТК", name: "Трубка без паспорта" }, status: "review", temperature: null },
    { material: { article: "ТУТ 5/2,5мм, черная" }, status: "resolved",
      temperature: { lower_c: 90, upper_c: 120, kind: "диапазон" },
      display_text: "КВТ ТУТнг-LS 5/2,5: 90…120 °C — производитель по умолчанию" },
    { material: { article: "ТУТ 6/3мм, черная" }, status: "review", temperature: null,
      display_text: "КВТ ТУТ(HF) 6/3: 80…110 °C; КВТ ТУТнг-LS 6/3: 90…120 °C — производитель по умолчанию — проверить" },
  ] } });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  const tooling = await screen.findByRole("region", { name: "Оснастка" });
  const panel = within(tooling).getByLabelText("Температурный режим");
  const lines = shrinkRequirementLines((card.document as { operations: Record<string, unknown>[] }).operations[0]);
  const printed = buildPrintDocumentHtml("ЭТК", etkPrintPages(card, []));
  for (const line of lines) {
    expect(within(panel).getByText(line.text)).toBeVisible();
    expect(printed).toContain(line.text);
  }
  expect(within(panel).getByRole("link", { name: "Источник" })).toHaveAttribute("href", "https://example.com/passport");
  expect(printed).toContain("https://example.com/passport");
  expect(lines[0].text).toContain("85…125 °C (оптимальная, Паспорт Rexant, стр. 2)");
  expect(lines[0].text).toContain("по характеристикам: 120 °C");
  expect(lines[1].text).toContain("не задано — проверить");
  expect(lines[2].text).toContain("90…120 °C — производитель по умолчанию");
  expect(lines[2].missing).toBe(false);
  expect(lines[3].text).toContain("80…110 °C; КВТ ТУТнг-LS 6/3: 90…120 °C");
  expect(lines[3].missing).toBe(true);
});

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

it("shortens the contact line in «Вход» to end and wire, and puts the crimp parameters in the side columns of the header", async () => {
  stubFetch();
  render(<EtkSheet card={techcard(twoContacts())} principal={principal} onChange={() => undefined} />);
  const input = await screen.findByRole("region", { name: "Вход" });
  const specs = [...input.querySelectorAll(".etk-spec")].map((node) => node.textContent);
  expect(specs).toEqual([
    "Конец A · НВ4 0,2 (24 AWG)",
    "Конец B · НВ4 0,2 (24 AWG)",
  ]);
  // Параметры обжима — в шапке, по колонкам сторон; «не задано» — тоном опасности, «?» нигде нет.
  expect(input.querySelectorAll(".etk-spec .tone-danger")).toHaveLength(0);
  const block = screen.getByLabelText("Параметры операции");
  const sideA = within(block).getByRole("region", { name: "Сторона А" }), sideB = within(block).getByRole("region", { name: "Сторона В" });
  expect(sideA).toHaveTextContent("ТерминалT-DS1071-SC600");
  expect(sideA).toHaveTextContent("Высота обжимане задано");
  expect(sideA.querySelectorAll(".tone-danger").length).toBe(3);
  expect(sideB).toHaveTextContent("Высота обжима0,60–0,75 мм");
  expect(sideB).toHaveTextContent("АппликаторA2");
  expect(sideB).toHaveTextContent("Отрыв9,8–39,2 Н");
  expect(within(block).getByRole("region", { name: "Общие" })).toBeInTheDocument();
  expect(document.body.textContent).not.toMatch(/\?\s*\(/);
});

it("prints the contact's crimp spec under its name and the per-end crimp facts", () => {
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(techcard(twoContacts()), []));
  const operationPage = html.slice(html.indexOf("Операция 1."));
  expect(operationPage).toContain("<td>Контакт XH Female\nКонец B · НВ4 0,2 (24 AWG)");
  expect(operationPage).toContain("<th>СТОРОНА А</th>");
  expect(operationPage).toContain("<th>СТОРОНА В</th>");
  expect(operationPage).toContain("Высота обжима: 0,60–0,75 мм");
  expect(operationPage).toContain("Терминал: T-DS1071-SC600");
  expect(operationPage).toContain("<td>Провод НВ4 0,2 белый</td>");
});

it("shows an ETK issued before the per-end spec as it was, without «?»", async () => {
  stubFetch();
  const issued = twoContacts();
  issued.fields = { applicator: "A14/A2", crimpHeight: "? (А) / 0,60 - 0,75 (В)", pullForce: "? (А) / 9,8 - 39,2 (В)" };
  render(<EtkSheet card={techcard(issued)} principal={principal} onChange={() => undefined} />);
  const chips = await screen.findByLabelText("Параметры операции");
  expect(within(chips).getByRole("region", { name: "Общие" })).toHaveTextContent("Высота обжимане задано (А) / 0,60–0,75 (В) мм");
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

it("shows a pool machine row as «любой свободный» on screen and in print, a normal unit as before", async () => {
  stubFetch();
  const card = techcard({ card: {
    name: "Резка", steps: [{ number: "1", kind: "Операция", text: "Отрезать" }],
    tooling: [
      { section: "Оборудование", number: "1", name: "EW-50A(R) | Полуавтомат зачистки", inventory_number: "ОБ.027.X" },
      { section: "Оборудование", number: "2", name: "WL-2.0ВТ", inventory_number: "ОБ.027.4" },
    ],
  } });
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  const tooling = await screen.findByRole("region", { name: "Оснастка" });
  const rows = [...tooling.querySelectorAll(".tr-tool")].map((node) => node.textContent);
  expect(rows).toEqual(["1EW-50A — любой свободный ОБ.027.* · Полуавтомат зачистки", "2WL-2.0ВТОБ.027.4"]);
  const printed = buildPrintDocumentHtml("ЭТК", etkPrintPages(card, []));
  expect(printed).toContain("EW-50A — любой свободный ОБ.027.* · Полуавтомат зачистки");
  expect(printed).not.toContain("ОБ.027.X");
  expect(printed).toContain("ОБ.027.4");
});

it("shows the set-up unit in a pool tooling row of a task ETK, on screen and in print; card views keep the pool text", async () => {
  stubFetch();
  const card = techcard({ card: { name: "Резка", steps: [], tooling: [
    { section: "Оборудование", number: "1", name: "EW-50A(R) | Полуавтомат", inventory_number: "ОБ.027.X" },
  ] } });
  const units = [{ etk_position: 1, unit_id: "u1", inventory_number: "ОБ.027.2", model: "EW-50A", name: null,
    ready_at: new Date(2026, 10, 20, 14, 30).toISOString(), task_number: "ПЗ-1" }];
  const { unmount } = render(<EtkSheet card={card} principal={principal} onChange={() => undefined} task units={units} />);
  const tooling = await screen.findByRole("region", { name: "Оснастка" });
  expect(within(tooling).getByText(/EW-50A — ОБ\.027\.2 · налажен 20\.11 14:30 · Полуавтомат/)).toBeVisible();
  expect(buildPrintDocumentHtml("ЭТК", etkPrintPages(card, [], {}, null, null, units))).toContain("EW-50A — ОБ.027.2 · налажен 20.11 14:30 · Полуавтомат");
  unmount();
  render(<EtkSheet card={card} principal={principal} onChange={() => undefined} />);
  expect(within(await screen.findByRole("region", { name: "Оснастка" })).getByText(/EW-50A — любой свободный ОБ\.027\.\* · Полуавтомат/)).toBeVisible();
  expect(buildPrintDocumentHtml("ЭТК", etkPrintPages(card, []))).toContain("любой свободный ОБ.027.*");
});
