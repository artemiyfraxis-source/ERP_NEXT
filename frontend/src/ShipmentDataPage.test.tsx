import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShipmentDataPage, dataGaps, dataRows } from "./ShipmentDataPage";

const principal = {
  user: { id: "u2", email: "ladutko@example.test", name: "Ульяна Ладутько", department: "Продажи" },
  roles: ["pm"], access_level: "pm", permissions: { shipping: "edit" as const },
};

const base = {
  id: "s1", number: "ОТГ-20261028-01", version: 4, created_at: "2026-10-28T09:00:00", status: "Упакована",
  customer: "ООО «ТСР»", receiver: "ООО «ТСР»", shipping_point: "", contact: "", phone: "", terms: null, carrier: "",
  deadline: "2026-11-02", upd_type: null, upd_number: "", upd_date: null, upd_url: "", pl_number: "PL713К.4-1", dn_number: "DN713К.4-1",
  track_number: "", packed_at: "2026-10-28", shipped_at: null, manager: "Менеджер", note: "", tk_note: "", places: 1, weight: "", boxes: [],
  inherited: [] as string[],
  missing_data: ["условия доставки", "телефон получателя", "тип УПД"], missing_ship: ["условия доставки", "телефон получателя", "тип УПД"],
  lines: [{ id: "l1", good_id: "g1", project: "713К.4", item_index: "КБ2", article: "", name: "Жгут", requested: "500", picked: "500", shortage: "0", unit: "шт", grn: "ГП.26.0000001" }],
};
const complete = { ...base, id: "s2", number: "ОТГ-20261028-02", terms: "САМОВЫВОЗ", carrier: "Самовывоз", phone: "+7 900", upd_type: "ЭДО", upd_number: "5", missing_data: [], missing_ship: [] };
const shippedNoFile = { ...complete, id: "s3", number: "ОТГ-20261020-03", status: "Отгружена", shipped_at: "2026-10-25", upd_type: "БУМАГА", upd_number: "7", missing_data: ["файл УПД"], missing_ship: ["файл УПД"] };
// Уехала до новых правил: телефона нет, но УПД на месте — в списке менеджера её нет.
const shippedOld = { ...complete, id: "s4", number: "ОТГ-20261001-04", status: "Отгружена", shipped_at: "2026-10-05", missing_data: ["телефон получателя"], missing_ship: ["телефон получателя"] };
const closed = { ...complete, id: "s5", number: "ОТГ-20260901-05", status: "Закрыта" };

function json(body: unknown) { return new Response(JSON.stringify(body)); }
function stub(shipments: unknown[]) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (/\/head$/.test(url) || /\/documents$/.test(url)) return json({ shipment: { ...base, version: JSON.parse(String(init?.body)).version + 1 } });
    return json({});
  }));
}
const bodies = (suffix: string) => vi.mocked(fetch).mock.calls.filter(([target]) => String(target).endsWith(suffix)).map(([, init]) => JSON.parse(String(init?.body)));

describe("ShipmentDataPage («Данные отправок»)", () => {
  beforeEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

  it("lists active shipments and shipped ones missing the УПД file, with what is missing, filtered to «не хватает» by default", async () => {
    stub([base, complete, shippedNoFile, shippedOld, closed]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼↑↓⇅]/gu, "").trim()).filter(Boolean);
    expect(headers).toEqual(["№ отправки", "Проект", "Заказчик", "Статус", "Что не хватает", "Срок"]);
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(screen.getByText("условия доставки, телефон получателя, тип УПД")).toBeInTheDocument();
    expect(screen.getByText("ОТГ-20261020-03")).toBeInTheDocument();
    expect(screen.getByText("файл УПД")).toBeInTheDocument();
    // Всё внесено, уехавшая по старым правилам и закрытая — не в списке «не хватает».
    for (const number of ["ОТГ-20261028-02", "ОТГ-20261001-04", "ОТГ-20260901-05"]) expect(screen.queryByText(number)).not.toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Все · 3" })[0]);
    expect(await screen.findByText("ОТГ-20261028-02")).toBeInTheDocument();
    expect(screen.queryByText("ОТГ-20260901-05")).not.toBeInTheDocument();
  });

  it("takes only the УПД gaps of a shipped shipment and nothing from a closed one", () => {
    expect(dataGaps(base)).toEqual(base.missing_data);
    expect(dataGaps(shippedNoFile as never)).toEqual(["файл УПД"]);
    expect(dataGaps(shippedOld as never)).toEqual([]);
    expect(dataGaps(closed as never)).toEqual([]);
    expect(dataRows([closed, shippedOld, complete, base] as never).map((row) => row.number)).toEqual(["ОТГ-20261028-01", "ОТГ-20261028-02"]);
  });

  it("opens a window with only the manager's fields; a pickup hides the address and the carrier, a carrier delivery shows them", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByText(/не хватает: условия доставки, телефон получателя, тип УПД/)).toBeInTheDocument();
    for (const label of ["Тип доставки", "Получатель", "Телефон получателя", "Тип УПД"]) expect(form.getByLabelText(label)).toBeInTheDocument();
    expect(form.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Трек-номер")).not.toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    expect(form.getByLabelText("Адрес доставки")).toBeInTheDocument();
    expect(form.getByLabelText("Перевозчик")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "САМОВЫВОЗ" } });
    expect(form.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Перевозчик")).not.toBeInTheDocument();
    // «Без УПД» не просит ни номера, ни даты, ни файла.
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БУМАГА" } });
    expect(form.getByLabelText("№ УПД")).toBeInTheDocument();
    expect(form.getByLabelText("Файл УПД")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БЕЗ УПД" } });
    expect(form.queryByLabelText("№ УПД")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Файл УПД")).not.toBeInTheDocument();
  });

  it("saves through the shipment edit endpoints with the version: the head first, then the documents", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    fireEvent.change(form.getByLabelText("Перевозчик"), { target: { value: "СДЭК" } });
    fireEvent.change(form.getByLabelText("Адрес доставки"), { target: { value: "Тула, ул. Оружейная, 12" } });
    fireEvent.change(form.getByLabelText("Телефон получателя"), { target: { value: "+7 900 111-22-33" } });
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "ЭДО" } });
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "474" } });
    fireEvent.change(form.getByLabelText("Дата УПД"), { target: { value: "2026-10-27" } });
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(bodies("/documents")).toHaveLength(1));
    expect(bodies("/head")).toEqual([{ version: 4, phone: "+7 900 111-22-33", terms: "ДОСТАВКА ТК", carrier: "СДЭК", shipping_point: "Тула, ул. Оружейная, 12" }]);
    expect(bodies("/documents")).toEqual([{ version: 5, upd_type: "ЭДО", upd_number: "474", upd_date: "2026-10-27", upd_url: "", track_number: "" }]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("uploads the УПД file after the documents and keeps the window open with the server's words when saving fails", async () => {
    stub([shippedNoFile]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261020-03"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261020-03" }));
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf", { type: "application/pdf" })] } });
    vi.mocked(fetch).mockImplementation(async (input) => String(input) === "/api/shipping/upd-files"
      ? new Response(JSON.stringify({ detail: "Файл УПД больше 20 МБ." }), { status: 422 }) : json({ shipments: [shippedNoFile] }));
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Файл УПД больше 20 МБ.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
