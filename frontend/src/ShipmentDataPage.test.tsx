import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShipmentDataPage, dataGaps, dataRows, sentNote } from "./ShipmentDataPage";

vi.mock("./ScanAgent", () => ({ useScanAgent: (start: (id?: string) => Promise<unknown>) => ({ begin: async () => start(), dialogs: () => null }), ScanDeviceLine: () => null }));

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
const working = { ...base, id: "s6", number: "ОТГ-20261027-06", status: "Новая" };
const cancelled = { ...complete, id: "s7", number: "ОТГ-20261001-07", status: "Отменена" };
const closed = { ...complete, id: "s5", number: "ОТГ-20260901-05", status: "Закрыта" };

// 713К.4 уже в отправках; 800К.1 — готовая продукция без отправки («Не в работе»); 900К.2 уехала целиком ещё до ERP.
const good = (project: string, remaining: string) => ({ id: `g-${project}`, version: 1, project, item_index: "КБ1", customer: `Заказчик ${project}`, article: "", name: "Жгут", ordered: "10", unit: "шт", accepted: "0", shipped: "0", remaining, sent: remaining === "0" ? "10" : "0", before_erp: "0", received_at: null, grn: null, note: "" });
const GOODS = [good("713К.4", "5"), good("800К.1", "10"), good("900К.2", "0")];
const UPD_JOB = { id: "j1", status: "done", error: "", recognising: false, scan_file: "/api/stock/receipts/files/f1", result: {
  scan_file: "/api/stock/receipts/files/f1", lines: [], note: "",
  recognised: { supplier: "", inn: "", upd_number: "РЭ-0915", upd_date: "2026-09-15", invoice_number: "РЭ-777", invoice_date: "2026-09-08" } } };
const APPLIED = { ...base, version: 6, upd_type: "ЭДО", upd_number: "РЭ-0915", upd_date: "2026-09-15", upd_url: "/api/shipping/files/f9" };

function json(body: unknown) { return new Response(JSON.stringify(body)); }
function stub(shipments: unknown[]) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/goods" && method === "GET") return json({ goods: GOODS });
    if (url.endsWith("/upd-scan/file")) return json(UPD_JOB);
    if (url.endsWith("/upd-scan/apply")) return json({ status: "applied", shipment: { ...APPLIED, version: JSON.parse(String(init?.body)).version + 1 }, invoice: "РЭ-777" });
    if (/\/head$/.test(url) || /\/documents$/.test(url)) return json({ shipment: { ...base, version: JSON.parse(String(init?.body)).version + 1 } });
    return json({});
  }));
}
const bodies = (suffix: string) => vi.mocked(fetch).mock.calls.filter(([target]) => String(target).endsWith(suffix)).map(([, init]) => JSON.parse(String(init?.body)));

describe("ShipmentDataPage («Данные отправок»)", () => {
  beforeEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

  it("splits the list into stages with counts: «Упакованы» is open by default, «Все» holds the cancelled one too", async () => {
    stub([base, complete, working, shippedNoFile, shippedOld, closed, cancelled]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼↑↓⇅]/gu, "").trim()).filter(Boolean);
    expect(headers).toEqual(["№ отправки", "Проект", "Заказчик", "Статус", "Данные", "Срок"]);
    const chips = within(screen.getByRole("group", { name: "Быстрый отбор" }));
    for (const name of ["Не в работе · 1", "В работе · 1", "Упакованы · 2", "Отправлены · 3", "Все · 8"]) expect(chips.getByRole("button", { name })).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Упакованы · 2" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(screen.getByText("Ждёт данных менеджера", { selector: ".status-chip" })).toHaveAttribute("title", "Не хватает: условия доставки, телефон получателя, тип УПД");
    expect(screen.queryByText("ОТГ-20261027-06")).not.toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "В работе · 1" }));
    expect(await screen.findByText("ОТГ-20261027-06")).toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Отправлены · 3" }));
    expect(await screen.findByText("ОТГ-20261020-03")).toBeInTheDocument();
    expect(screen.getByText("ОТГ-20260901-05")).toBeInTheDocument();
    expect(screen.queryByText("ОТГ-20261001-07")).not.toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Все · 8" }));
    expect(await screen.findByText("ОТГ-20261001-07")).toBeInTheDocument();
  });

  it("shipped shipments are neutral: no «Ждёт данных», not in «Только с недостающими данными»; only a quiet «Внести: файл УПД»", async () => {
    stub([base, complete, shippedNoFile, shippedOld]);
    render(<ShipmentDataPage principal={principal} />);
    const chips = within(await screen.findByRole("group", { name: "Быстрый отбор" }));
    expect(await screen.findByText("ОТГ-20261028-02")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    await waitFor(() => expect(screen.queryByText("ОТГ-20261028-02")).not.toBeInTheDocument());
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Отправлены · 0" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    fireEvent.click(await chips.findByRole("button", { name: "Отправлены · 2" }));
    expect(await screen.findByText("Внести: файл УПД")).toBeInTheDocument();
    expect(screen.queryByText("Ждёт данных")).not.toBeInTheDocument();
  });

  it("«Не в работе» lists projects with goods but no shipment and opens the project's «Данные отправки» card", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Не в работе · 1" }));
    expect(await screen.findByText("800К.1")).toBeInTheDocument();
    expect(screen.queryByText("900К.2")).not.toBeInTheDocument();
    expect(screen.getByText("внести заранее")).toBeInTheDocument();
    fireEvent.click(screen.getByText("800К.1"));
    expect(await screen.findByRole("dialog", { name: "Данные отправки 800К.1" })).toBeInTheDocument();
  });

  it("counts gaps only for live shipments; projects without a shipment come after the shipments", () => {
    expect(dataGaps(base)).toEqual(base.missing_data);
    for (const sent of [shippedNoFile, shippedOld, closed]) expect(dataGaps(sent as never)).toEqual([]);
    expect(sentNote(shippedNoFile as never)).toBe("Внести: файл УПД");
    expect(sentNote(shippedOld as never)).toBe("");
    const rows = dataRows([closed, complete, base, cancelled] as never, GOODS as never);
    expect(rows.map((row) => row.shipment?.number ?? row.project)).toEqual(["ОТГ-20260901-05", "ОТГ-20261001-07", "ОТГ-20261028-01", "ОТГ-20261028-02", "800К.1"]);
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

  it("has «Сканировать» and «Файл…», and no «+ Страница» before the first scan", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByRole("button", { name: "Сканировать" })).toBeEnabled();
    expect(form.getByRole("button", { name: "Файл…" })).toBeEnabled();
    expect(form.queryByRole("button", { name: "+ Страница" })).not.toBeInTheDocument();
  });

  it("an ЭДО file fills the type, № and date at once and shows the attached file; the form stays editable", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["<?xml"], "upd.xml", { type: "application/xml" })] } });
    await waitFor(() => expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915"));
    expect(form.getByLabelText("Тип УПД")).toHaveValue("ЭДО");
    expect(form.getByLabelText("Дата УПД")).toHaveValue("2026-09-15");
    expect(form.getByRole("link", { name: "Файл ЭДО · открыть" })).toHaveAttribute("href", "/api/shipping/files/f9");
    expect(form.getByRole("status")).toHaveTextContent("УПД № РЭ-0915 внесён, № счёта РЭ-777 — в проект");
    expect(bodies("/upd-scan/apply")).toEqual([{ version: 4, scan_file: "/api/stock/receipts/files/f1", upd_number: "РЭ-0915", upd_date: "2026-09-15", invoice_number: "РЭ-777", number: "replace" }]);
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "РЭ-0915/1" } });
    expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915/1");
  });

  it("never replaces a typed № silently: asks first, «Оставить мой №» sends keep, «Заменить» sends replace", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БУМАГА" } });
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "474" } });
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["x"], "upd.xml", { type: "application/xml" })] } });
    expect(await form.findByText(/Заменить № УПД «474» на «РЭ-0915» из документа\?/)).toBeInTheDocument();
    expect(bodies("/upd-scan/apply")).toHaveLength(0);
    fireEvent.click(form.getByRole("button", { name: "Оставить мой №" }));
    await waitFor(() => expect(bodies("/upd-scan/apply")).toHaveLength(1));
    expect(bodies("/upd-scan/apply")[0].number).toBe("keep");
    expect(form.getByLabelText("№ УПД")).toHaveValue("474");

    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["x"], "upd.xml", { type: "application/xml" })] } });
    fireEvent.click(await form.findByRole("button", { name: "Заменить" }));
    await waitFor(() => expect(bodies("/upd-scan/apply")).toHaveLength(2));
    expect(bodies("/upd-scan/apply")[1].number).toBe("replace");
    await waitFor(() => expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915"));
  });

  it("keeps the window open with the server's words when saving fails", async () => {
    stub([shippedNoFile]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Отправлены/ }));
    fireEvent.click(await screen.findByText("ОТГ-20261020-03"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261020-03" }));
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "8" } });
    vi.mocked(fetch).mockImplementation(async (input) => String(input).endsWith("/documents")
      ? new Response(JSON.stringify({ detail: "Закрытую или отменённую отгрузку нельзя менять" }), { status: 422 }) : json({ shipments: [shippedNoFile], goods: [] }));
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Закрытую или отменённую отгрузку нельзя менять");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
