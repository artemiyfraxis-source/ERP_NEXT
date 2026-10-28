import { describe, expect, it } from "vitest";
import type { DeliveryNoteForm, PackingListForm, ShippingFormRow } from "./api";
import { buildBoxLabelsHtml, buildShippingFormsHtml, type BoxLabel } from "./shippingPrint";

const row: ShippingFormRow = { part: "КБ1 FRX.2600204", name: "Кабельная сборка <CCW 1.5>", quantity: "200", unit: "шт", batch: "ГП.26.0000001", pl: "PL533К.10-1", boxes: "BXN 1" };
const pl: PackingListForm = {
  kind: "pl", title: "PL533К.10-1", number: ["PL", "533К.10", "-", "1"], place: "1", places: "2", package: "Короб",
  dims: "40x30x20", net: "9,2", gross: "9,6", seal: "", rows: [row], packed_at: "18.09.26", shipped_at: "18.09.26", packer: "Гусев А.А.",
};
const dn: DeliveryNoteForm = {
  kind: "dn", title: "DN533К.10-1", number: ["DN", "533К.10", "-", "1"], date: "18.09.26", customer: "ООО «Мегатрон»",
  po: "PO-7", po_from_card: true, contract: "", spec: "", invoice: "533К.10", terms: "САМОВЫВОЗ", shipping_address: "Москва",
  hours: "9-18", contact: "", phone: "", carrier: "", track: "", tk_note: "", rows: [row], upd_edo: true, upd_paper: false,
  paper_box: "BXN 1", note: "", places: "1", gross: "9,6", upd: "385 от 18.09.26", shipped_at: "18.09.26", packer: "Гусев А.А.",
};

function parse(html: string): Document {
  return new DOMParser().parseFromString(html, "text/html");
}

const cells = (doc: Document, selector: string) => [...doc.querySelectorAll(selector)].map((cell) => cell.textContent);

describe("buildShippingFormsHtml", () => {
  it("lays out PL as form 04.2: logo, requisites, doc number in cells, the box, items with a blank row, signatures", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect(doc.title).toBe("PL533К.10-1");
    expect(doc.querySelector(".brand img")?.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
    expect(doc.querySelector(".brand h1")?.textContent).toBe("УПАКОВОЧНАЯ ВЕДОМОСТЬ");
    expect(doc.querySelector(".requisites")?.textContent).toContain("ИНН 5040191181 / КПП 504001001");
    expect(cells(doc, ".num td")).toEqual(["PL", "533К.10", "-", "1"]);
    expect(cells(doc, ".place tr:nth-child(2) td.val")).toEqual(["BXN", "1", "/", "2", "Короб"]);
    expect([doc.querySelector(".dims")?.textContent, doc.querySelector(".net")?.textContent]).toEqual(["40x30x20", "9,2"]);
    const items = [...doc.querySelectorAll(".items tr")].slice(2).map((line) => [...line.children].map((cell) => cell.textContent));
    expect(items).toEqual([["1.", "КБ1 FRX.2600204", "Кабельная сборка <CCW 1.5>", "200", "шт", "ГП.26.0000001"], ["2.", "", "", "", "", ""]]);
    expect([".packed", ".gross", ".packer", ".shipped"].map((css) => doc.querySelector(css)?.textContent)).toEqual(["18.09.26", "9,6", "Гусев А.А.", "18.09.26"]);
    expect(doc.querySelector(".foot")?.textContent).toContain("Форма/Form 04.2 / Rev.4");
  });

  it("lays out DN as form 04.1: order requisites, delivery data, items with PL and boxes, UPD ticks, totals", () => {
    const doc = parse(buildShippingFormsHtml([dn]));

    expect(doc.querySelector(".brand h1")?.textContent).toBe("ОТГРУЗОЧНАЯ ВЕДОМОСТЬ");
    expect([".customer", ".po", ".invoice", ".date", ".terms", ".shipping-address", ".hours"].map((css) => doc.querySelector(css)?.textContent))
      .toEqual(["ООО «Мегатрон»", "PO-7", "533К.10", "18.09.26", "САМОВЫВОЗ", "Москва", "9-18"]);
    const items = [...doc.querySelectorAll(".items tr")].slice(2, 3).map((line) => [...line.children].map((cell) => cell.textContent));
    expect(items).toEqual([["1.", "КБ1 FRX.2600204", "Кабельная сборка <CCW 1.5>", "200", "шт", "PL533К.10-1", "BXN 1"]]);
    expect(cells(doc, ".check")).toEqual(["☒", "☐"]);
    expect([".places", ".gross", ".upd", ".packer"].map((css) => doc.querySelector(css)?.textContent)).toEqual(["1", "9,6", "385 от 18.09.26", "Гусев А.А."]);
    expect(doc.querySelector(".foot")?.textContent).toContain("Форма/Form 04.1 Rev.4");
    // Владелец 28.10.2026: «Оплачивает» и «Адрес заказчика» в DN не печатаются; условия поставки — на всю строку.
    expect(doc.body.textContent).not.toContain("Оплачивает");
    expect(doc.body.textContent).not.toContain("Адрес заказчика");
    expect(doc.querySelector(".terms")?.getAttribute("colspan")).toBe("3");
  });

  it("prints «PO #» only for a customer order; otherwise the project number fills the whole cell", () => {
    const withPo = parse(buildShippingFormsHtml([dn]));
    const fallback = parse(buildShippingFormsHtml([{ ...dn, po: "533К.10", po_from_card: false }]));

    expect(cells(withPo, ".refs td").slice(1, 4)).toEqual(["PO", "#", "PO-7"]);
    expect(cells(fallback, ".refs td").slice(0, 3)).toEqual(["ООО «Мегатрон»", "533К.10", ""]);
    expect(fallback.querySelector(".po")?.getAttribute("colspan")).toBe("4");
  });

  it("labels the footer date of the PL as the shipping date, not a second packing date", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect(doc.querySelector(".shipped")?.previousElementSibling?.textContent).toContain("Дата отгрузки:");
  });

  it("puts every form on its own A4 sheet, prints the grey cells and escapes values", () => {
    const html = buildShippingFormsHtml([pl, { ...pl, place: "2" }]);

    expect(parse(html).querySelectorAll("section.form")).toHaveLength(2);
    expect(html).toContain("@page { size: A4 portrait");
    expect(html).toContain("print-color-adjust: exact");
    expect(html).toContain("Кабельная сборка &lt;CCW 1.5&gt;");
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
