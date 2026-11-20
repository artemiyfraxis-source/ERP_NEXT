import { describe, expect, it } from "vitest";
import type { DeliveryNoteForm, PackingListForm, ShippingFormRow } from "./api";
import { buildBoxLabelsHtml, buildShippingFormsHtml, type BoxLabel } from "./shippingPrint";

const row: ShippingFormRow = {
  part: "КБ1 FRX.2600204", item_index: "КБ1", article: "FRX.2600204", name: "Кабельная сборка <CCW 1.5>", quantity: "200", unit: "шт",
  batch: "ГП.26.0000001", pl: "PL533К.10-1", boxes: "BXN 1",
};
const pl: PackingListForm = {
  kind: "pl", title: "PL533К.10-1", number: ["PL", "533К.10", "-", "1"], place: "1", places: "2", package: "Короб",
  dims: "40x30x20", net: "9,2", gross: "9,6", seal: "", rows: [row], packed_at: "18.09.26", shipped_at: "18.09.26", packer: "Гусев А.А.",
  shipper: 'ООО "ЗЭП ФРАКСИС"', customer: "ООО «Мегатрон»", consignee: "АО «Склад»", shipping_address: "Москва, ул. Складская, 1",
  contact: "Соколов А.", phone: "+7 900", project: "533К.10", shipment: "ОТГ-20260918-01", dn: "DN533К.10-1", terms: "Доставка ТК",
  carrier: "СДЭК", track: "", positions: "1 позиция", quantity: "200 шт", total_gross: "",
  summary: [
    { place: "1", package: "Короб", dims: "40x30x20", net: "9,2", gross: "9,6", seal: "", contents: "КБ1 — 200 шт" },
    { place: "2", package: "Короб", dims: "", net: "", gross: "", seal: "", contents: "КБ2 — 5 шт" },
  ],
};
const dn: DeliveryNoteForm = {
  kind: "dn", title: "DN533К.10-1", number: ["DN", "533К.10", "-", "1"], date: "18.09.26", customer: "ООО «Мегатрон»",
  po: "PO-7", po_from_card: true, contract: "", spec: "", invoice: "533К.10", terms: "САМОВЫВОЗ", shipping_address: "Москва",
  hours: "9-18", contact: "", phone: "", carrier: "", track: "", tk_note: "", rows: [row], upd_edo: true, upd_paper: false,
  paper_box: "BXN 1", note: "", places: "1", gross: "9,6", upd: "385 от 18.09.26", shipped_at: "18.09.26", packer: "Гусев А.А.",
  positions: "1 позиция", quantity: "200 шт",
};

function parse(html: string): Document {
  return new DOMParser().parseFromString(html, "text/html");
}

const text = (doc: Document | Element, selector: string) => doc.querySelector(selector)?.textContent;
const caption = (doc: Document, selector: string) => doc.querySelector(selector)?.previousElementSibling?.textContent;
const itemRows = (doc: Document) => [...doc.querySelectorAll(".items tbody tr")].map((line) => [...line.children].map((cell) => cell.textContent));

describe("buildShippingFormsHtml — PL (владелец 20.11.2026: новый вид)", () => {
  it("heads the sheet with the logo, requisites, the PL number, the packing date and the place", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect(doc.title).toBe("PL533К.10-1");
    expect(doc.querySelector(".brand img")?.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
    expect(text(doc, ".requisites")).toContain("ИНН 5040191181 / КПП 504001001");
    expect(text(doc, ".doc .kind")).toContain("Упаковочная ведомость");
    expect(text(doc, ".doc .number")).toBe("PL533К.10-1");
    expect(text(doc, ".doc .meta")).toBe("Дата упаковки 18.09.26");
    expect(text(doc, ".place-badge")).toBe("Место 1 из 2");
  });

  it("prints the shipment data in label–value pairs, an empty value stays a blank cell", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect([".shipper", ".customer", ".consignee", ".shipping-address", ".contact", ".phone", ".project", ".shipment", ".dn", ".delivery", ".track"]
      .map((css) => text(doc, `.parties ${css}`))).toEqual([
      'ООО "ЗЭП ФРАКСИС"', "ООО «Мегатрон»", "АО «Склад»", "Москва, ул. Складская, 1", "Соколов А.", "+7 900", "533К.10",
      "ОТГ-20260918-01", "DN533К.10-1", "Доставка ТК · СДЭК", "",
    ]);
    expect(caption(doc, ".parties .consignee")).toBe("Грузополучатель");
    expect(caption(doc, ".parties .contact")).toBe("Получатель");
  });

  it("lists the box contents by columns with totals, then the shipment's places with this one marked", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect([".package", ".dims", ".net", ".gross", ".seal"].map((css) => text(doc, `.boxrow ${css}`))).toEqual(["Короб", "40x30x20", "9,2", "9,6", ""]);
    expect(itemRows(doc)).toEqual([["1", "КБ1", "FRX.2600204", "Кабельная сборка <CCW 1.5>", "ГП.26.0000001", "200", "шт"]]);
    expect([text(doc, ".items .positions"), text(doc, ".items .quantity")]).toEqual(["Итого в месте: 1 позиция", "200 шт"]);
    expect([...doc.querySelectorAll(".summary tbody tr")].map((line) => line.className)).toEqual(["current", ""]);
    expect(text(doc, ".summary tfoot")).toContain("Всего мест: 2");
  });

  it("leaves out the places summary for a single box", () => {
    expect(parse(buildShippingFormsHtml([{ ...pl, places: "1", summary: [] }])).querySelector(".summary")).toBeNull();
  });

  it("signs: storekeeper with the shipping date, checker, recipient; form code at the bottom", () => {
    const doc = parse(buildShippingFormsHtml([pl]));

    expect([...doc.querySelectorAll(".sign .who")].map((who) => who.textContent)).toEqual(["Сдал — кладовщик", "Проверил", "Принял — получатель"]);
    expect([text(doc, ".packer"), text(doc, ".shipped"), caption(doc, ".shipped")]).toEqual(["Гусев А.А.", "18.09.26", "Дата отгрузки"]);
    expect(text(doc, ".foot")).toContain("Форма/Form 04.2 / Rev.4");
  });

  it("reads an older answer without split cells: the whole part number goes to the КБ column", () => {
    const doc = parse(buildShippingFormsHtml([{ ...pl, rows: [{ ...row, item_index: undefined, article: undefined }] }]));
    expect(itemRows(doc)[0].slice(1, 3)).toEqual(["КБ1 FRX.2600204", ""]);
  });
});

describe("buildShippingFormsHtml — DN", () => {
  it("prints order requisites, delivery data, items with PL and places, totals, UPD ticks and signatures", () => {
    const doc = parse(buildShippingFormsHtml([dn]));

    expect(text(doc, ".doc .kind")).toContain("Отгрузочная ведомость");
    expect([text(doc, ".doc .number"), text(doc, ".date")]).toEqual(["DN533К.10-1", "18.09.26"]);
    // Человек в DN — «Получатель», как на экранах (владелец 17.11), не «Контактное лицо заказчика».
    expect(caption(doc, ".contact")).toBe("Получатель");
    expect(doc.body.textContent).not.toContain("Контактное лицо");
    expect([".customer", ".po", ".invoice", ".terms", ".shipping-address", ".hours"].map((css) => text(doc, css)))
      .toEqual(["ООО «Мегатрон»", "PO # PO-7", "533К.10", "САМОВЫВОЗ", "Москва", "9-18"]);
    expect(itemRows(doc)).toEqual([["1", "КБ1", "FRX.2600204", "Кабельная сборка <CCW 1.5>", "200", "шт", "PL533К.10-1", "BXN 1"]]);
    expect([text(doc, ".items .positions"), text(doc, ".items .quantity")]).toEqual(["Итого: 1 позиция", "200 шт"]);
    expect([...doc.querySelectorAll(".check")].map((cell) => cell.textContent)).toEqual(["☒", "☐"]);
    expect([".places", ".gross", ".upd", ".packer"].map((css) => text(doc, css))).toEqual(["1", "9,6", "385 от 18.09.26", "Гусев А.А."]);
    expect(text(doc, ".foot")).toContain("Форма/Form 04.1 Rev.4");
    // Владелец 28.10.2026: «Оплачивает» и «Адрес заказчика» в DN не печатаются; условия поставки — на всю строку.
    expect(doc.body.textContent).not.toContain("Оплачивает");
    expect(doc.body.textContent).not.toContain("Адрес заказчика");
    expect(doc.querySelector(".terms")?.getAttribute("colspan")).toBe("3");
  });

  it("prints «PO #» only for a customer order; otherwise the project number as is", () => {
    const fallback = parse(buildShippingFormsHtml([{ ...dn, po: "533К.10", po_from_card: false }]));
    expect(text(fallback, ".po")).toBe("533К.10");
  });

  it("prints «без УПД» with both UPD ticks empty for a shipment approved without UPD", () => {
    const doc = parse(buildShippingFormsHtml([{ ...dn, upd: "без УПД", upd_edo: false, upd_paper: false }]));
    expect(text(doc, ".upd")).toBe("без УПД");
    expect([...doc.querySelectorAll(".check")].map((cell) => cell.textContent)).toEqual(["☐", "☐"]);
  });

  it("prints the second note only when there is one", () => {
    expect(parse(buildShippingFormsHtml([dn])).querySelector(".note-text")).toBeNull();
    expect(text(parse(buildShippingFormsHtml([{ ...dn, note: "Звонить за час" }])), ".note-text")).toBe("2. Звонить за час");
  });
});

describe("buildShippingFormsHtml — лист", () => {
  it("puts every form on its own A4 sheet with page numbers, keeps backgrounds, lets long tables flow and escapes values", () => {
    const html = buildShippingFormsHtml([pl, { ...pl, place: "2" }]);

    expect(parse(html).querySelectorAll("section.form")).toHaveLength(2);
    expect(html).toContain("@page { size: A4 portrait");
    expect(html).toContain('counter(page) " из " counter(pages)');
    expect(html).toContain("print-color-adjust: exact");
    expect(html).toContain("display: table-header-group");
    expect(html).not.toContain("overflow: hidden");
    expect(html).toContain("Кабельная сборка &lt;CCW 1.5&gt;");
  });
});

const label: BoxLabel = {
  shipper: 'ООО "ЗЭП ФРАКСИС"', consignee: "АО <Получатель>", address: "Москва, ул. Складская, 1", project: "228К.20.2",
  pl_number: "PL228К.20.2-2", place: "Упаковка 1/2", items: [{ item_index: "КБ1", quantity: "6", unit: "шт" }, { item_index: "КБ2", quantity: "2,5", unit: "м" }],
  net: "11", dims: "32x25x21",
};

describe("buildBoxLabelsHtml — организация и получатель", () => {
  it("prints the organization under «Организация» and the person with phone under «Получатель»", () => {
    const doc = parse(buildBoxLabelsHtml([{ ...label, contact: "Соколов А.", phone: "+7 900" }]));
    const captions = [...doc.querySelectorAll(".caption")].map((caption) => caption.textContent);
    expect(captions).toContain("Организация");
    expect(doc.querySelector(".recipient")?.textContent).toBe("ПолучательСоколов А. · +7 900");
  });
  it("omits the «Получатель» line when nobody is named", () => {
    expect(parse(buildBoxLabelsHtml([label])).querySelector(".recipient")).toBeNull();
  });
});

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
