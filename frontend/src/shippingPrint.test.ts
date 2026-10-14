import { describe, expect, it } from "vitest";
import type { ShippingForm } from "./api";
import { buildBoxLabelsHtml, buildShippingFormsHtml, type BoxLabel } from "./shippingPrint";

const pl: ShippingForm = {
  kind: "pl", title: "УПАКОВОЧНАЯ ВЕДОМОСТЬ",
  head: [
    { label: "Номер документа", value: "PL228К.20.2-2", side: 'ООО "ЗЭП ФРАКСИС"' },
    { label: "Упаковка №", value: "BXN 1/1", side: "Сайт: https://fraxis.ru    Электронный адрес: order@fraxis.ru" },
    { label: "Габариты упаковки (cm)", value: "", side: "" },
  ],
  columns: ["GRN", "№ КБ", "ОПИСАНИЕ", "кол-во", "Ед. изм."],
  rows: [["FRX.2600290", "КБ1", "Кабельная сборка <SMA>", "1100", "шт"]],
  blank_rows: 15, sign_label: "Штамп / подпись кладовщика:", sign_name: "Гусев А.А.",
};

function parse(html: string): Document {
  return new DOMParser().parseFromString(html, "text/html");
}

describe("buildShippingFormsHtml", () => {
  it("lays out a form as the sample: head, item table with blank rows, storekeeper sign", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect(doc.title).toBe("PL228К.20.2-2");
    expect(doc.querySelector(".title")?.textContent).toBe("УПАКОВОЧНАЯ ВЕДОМОСТЬ");
    const head = [...doc.querySelectorAll("td.label")].map((cell) => [cell.textContent, cell.nextElementSibling?.textContent]);
    expect(head).toEqual([
      ["Номер документа", "PL228К.20.2-2"], ["Упаковка №", "BXN 1/1"], ["Габариты упаковки (cm)", ""],
    ]);
    expect(doc.querySelector("tr.sign")?.textContent).toBe("Штамп / подпись кладовщика:Гусев А.А.");
    expect(doc.querySelector("td.value[colspan='4']")?.textContent).toBe("");
    expect([...doc.querySelectorAll("tr.items th")].map((cell) => cell.textContent)).toEqual(["GRN", "№ КБ", "ОПИСАНИЕ", "кол-во", "Ед. изм."]);
    const items = [...doc.querySelectorAll("tr.items:not(.blank)")].slice(1);
    expect(items.map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([["FRX.2600290", "КБ1", "Кабельная сборка <SMA>", "1100", "шт"]]);
    expect(doc.querySelectorAll("tr.blank")).toHaveLength(15);
    expect(doc.querySelector(".side a")?.getAttribute("href")).toBe("https://fraxis.ru");
  });

  it("puts every form on its own A4 sheet and escapes values", () => {
    const html = buildShippingFormsHtml([pl, { ...pl, head: [{ ...pl.head[0] }, { ...pl.head[1], value: "BXN 2/2" }] }]);

    expect(parse(html).querySelectorAll("section.form")).toHaveLength(2);
    expect(html).toContain("@page { size: A4 portrait");
    expect(html).toContain("Кабельная сборка &lt;SMA&gt;");
  });
});

const label: BoxLabel = {
  shipper: 'ООО "ЗЭП ФРАКСИС"', consignee: "АО <Получатель>", address: "Москва, ул. Складская, 1", project: "228К.20.2",
  pl_number: "PL228К.20.2-2", place: "Упаковка 1/2", items: [{ item_index: "КБ1", quantity: "6", unit: "шт" }, { item_index: "КБ2", quantity: "2,5", unit: "м" }],
  net: "11", dims: "32x25x21",
};

describe("buildBoxLabelsHtml", () => {
  it("prints one A6 label per box: shipper, consignee, project and PL, place, KB contents, weight and dims", () => {
    const html = buildBoxLabelsHtml([label, { ...label, place: "Упаковка 2/2", net: "", dims: "" }]);
    const doc = parse(html);
    const [first, second] = [...doc.querySelectorAll("section.label")];

    expect(html).toContain("@page { size: A6 portrait");
    expect(doc.title).toBe("Этикетки PL228К.20.2-2");
    expect(first.querySelector(".shipper")?.textContent).toContain('ООО "ЗЭП ФРАКСИС"');
    expect(first.querySelector(".consignee")?.textContent).toBe("АО <Получатель>");
    expect(html).toContain("АО &lt;Получатель&gt;");
    expect([...first.querySelectorAll(".refs strong")].map((cell) => cell.textContent)).toEqual(["228К.20.2", "PL228К.20.2-2"]);
    expect(first.querySelector(".place")?.textContent).toBe("Упаковка 1/2");
    expect([...first.querySelectorAll("tbody tr")].map((row) => [...row.children].map((cell) => cell.textContent))).toEqual([["КБ1", "6", "шт"], ["КБ2", "2,5", "м"]]);
    expect([first.querySelector(".net")?.textContent, first.querySelector(".dims")?.textContent]).toEqual(["11", "32x25x21"]);
    // Не измеренный короб — пустая строка под вес и габариты.
    expect([second.querySelector(".net")?.textContent, second.querySelector(".dims")?.textContent]).toEqual(["", ""]);
  });
});
