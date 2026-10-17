import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EtkRoute, Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { etkPrintPages } from "./etkPrint";
import { etkPfGroups, etkSemiSource } from "./etkRoute";

/* Владелец 14.10: «вход с прошлой» — «Вход» операции n и «Результат» операции n−1 это один и тот же текст и то же
   количество, на экране и в печати. Цепочка: резка → опрессовка А → монтаж А → опрессовка В → монтаж В; тексты —
   из сборщика ЭТК (backend/tests/techcards/test_etk_builder.py проверяет то же на его стороне). */

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

type Row = [string, string];
const CHAIN: Array<{ title: string; semis: Row[]; result: Row[] }> = [
  { title: "Резка", semis: [], result: [["ПВАМ 1,5 белый 250мм", "700"]] },
  { title: "Опрессовка А", semis: [["ПВАМ 1,5 белый 250мм", "700"]], result: [["ПВАМ 1,5 белый 250мм, обжатый терминал Molex 39000079", "700"]] },
  { title: "Монтаж А", semis: [["ПВАМ 1,5 белый 250мм, обжатый терминал Molex 39000079", "700"]],
    result: [["Сторона А собрана: провода вставлены в разъём Molex 39012140", "100"]] },
  { title: "Опрессовка В", semis: [["Сторона А собрана: провода вставлены в разъём Molex 39012140", "100"]],
    result: [["Сторона А собрана: провода вставлены в разъём Molex 39012140; сторона В: обжаты терминалы Molex 39000081", "100"]] },
  { title: "Монтаж В", semis: [["Сторона А собрана: провода вставлены в разъём Molex 39012140; сторона В: обжаты терминалы Molex 39000081", "100"]],
    result: [["Кабельная сборка 39012140 to 39012141", "100"]] },
];

function chainCard(): ProjectTechcard {
  const row = ([name, norm]: Row) => ({ name, norm, unit: "Шт." });
  return { id: "t", project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана", document: {
    kit: { part_quantity: "100" },
    operations: CHAIN.map((item, index) => ({
      position: index + 1, code: `OP_${index + 1}`, type: "cutWire", title: item.title, result_label: "Полуфабрикат",
      card: { name: item.title, steps: [], tooling: [] }, components: [], semis: item.semis.map(row), result: item.result.map(row),
      time: [{ name: item.title, norm: "5" }],
    })),
  } } as unknown as ProjectTechcard;
}

afterEach(() => vi.restoreAllMocks());

it("the input of every operation is the previous result, word for word, on the screen and in print", () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<EtkSheet card={chainCard()} principal={principal} onChange={() => undefined} />);
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(chainCard(), [], {}));
  for (let at = 0; at < CHAIN.length; at += 1) {
    expect(screen.getByRole("region", { name: "Результат" }).textContent).toContain(CHAIN[at].result[0][0]);
    if (at > 0) {
      const input = screen.getByRole("region", { name: "Вход" });
      const [name, norm] = CHAIN[at - 1].result[0];
      expect(input.textContent).toContain(name);
      expect(within(input).getByText(`${norm} шт`)).toBeInTheDocument();
      expect(within(input).getByText(`с операции 0${at}`)).toBeInTheDocument();
      expect(html.slice(html.indexOf(`Операция ${at + 1}.`))).toContain(`<td>${name}</td><td>${norm}</td>`);
    }
    if (at < CHAIN.length - 1) fireEvent.click(screen.getByRole("button", { name: /^Далее/ }));
  }
});

it("tells where to take a semi-finished product from: the previous operation of the PF, or the box of another PF", () => {
  const route: EtkRoute = {
    source: "derived", final: "b3",
    blocks: [
      { key: "b1", name: "Резка", ops: [1], final: false, components: [], outputs: 1, inputs: 0, minutes: 0, unit_minutes: 0, rolled_minutes: 0, rolled_ops: 1 },
      { key: "b2", name: "Опрессовка", ops: [2, 3], final: false, components: [], outputs: 1, inputs: 1, minutes: 0, unit_minutes: 0, rolled_minutes: 0, rolled_ops: 2 },
      { key: "b3", name: "Изделие", ops: [4], final: true, components: [], outputs: 1, inputs: 1, minutes: 0, unit_minutes: 0, rolled_minutes: 0, rolled_ops: 4 },
    ],
    edges: [{ from: "b1", to: "b2", per_unit: 1 }, { from: "b2", to: "b3", per_unit: 1 }],
  };
  expect(etkSemiSource(route, 1)).toBe("");
  expect(etkSemiSource(route, 2)).toBe("из ПФ 1, коробка с операции 01");
  expect(etkSemiSource(route, 3)).toBe("с операции 02");
  expect(etkSemiSource(route, 4)).toBe("из ПФ 2, коробка с операции 03");
  expect(etkSemiSource(null, 3)).toBe("с операции 02");
  // В списке ПФ вход назван результатом последней операции ПФ-источника, а не названием блока схемы.
  const operations = CHAIN.map((item) => ({ result: item.result.map(([name, norm]) => ({ name, norm })) }));
  const groups = etkPfGroups(route, operations);
  expect(groups[1].inputs[0].name).toBe(CHAIN[0].result[0][0]);
  expect(groups[2].inputs[0].name).toBe(CHAIN[2].result[0][0]);
});
