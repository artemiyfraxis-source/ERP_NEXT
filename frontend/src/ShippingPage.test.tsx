import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShippingPage } from "./ShippingPage";
import { capturePrintFrames } from "./testPrintFrames";

const principal = {
  user: { id: "u1", email: "admin@example.test", name: "Admin", department: "ERP" },
  roles: ["admin"], access_level: "admin", permissions: { shipping: "edit" as const },
};

const good = {
  id: "g1", version: 1, project: "591К.3", item_index: "КБ1", customer: "Заказчик",
  article: "A-1", name: "Жгут", ordered: "40", unit: "шт", accepted: "6", shipped: "0",
  remaining: "6", sent: "0", before_erp: "0", received_at: "2026-09-22", grn: "ГП.26.0000001", note: "",
};
const other = { ...good, id: "g2", project: "600К.1", item_index: "КБ7", customer: "Другой", grn: "ГП.26.0000002", name: "Кабель" };

const shipment = {
  id: "s1", number: "ОТГ-20260922-01", version: 3, created_at: "2026-09-22T09:00:00", status: "Собрана",
  customer: "Заказчик", receiver: "Получатель", shipping_point: "Москва", contact: "", phone: "",
  terms: "САМОВЫВОЗ", carrier: "", deadline: "2026-09-01", upd_type: "ЭДО", upd_number: "", upd_date: null, upd_url: "",
  pl_number: "PL591К.3-1", dn_number: "DN591К.3-1", track_number: "", packed_at: "2026-09-22", shipped_at: null,
  manager: "Менеджер", note: "", tk_note: "", places: 0, weight: "", boxes: [], inherited: ["receiver", "shipping_point"],
  lines: [{ id: "l1", good_id: "g1", project: "591К.3", item_index: "КБ1", article: "A-1", name: "Жгут", requested: "10", picked: "6", shortage: "4", unit: "шт", grn: "ГП.26.0000001" }],
};
const shippedOne = { ...shipment, id: "s2", number: "ОТГ-20260920-02", status: "Отгружена", shipped_at: "2026-09-25", upd_url: "/files/u", upd_number: "У-1" };
const card = {
  project: "591К.3", version: 2, receiver: "Получатель", shipping_point: "Склад заказчика", contact: "", phone: "", terms: "САМОВЫВОЗ",
  carrier: "", upd_type: "ЭДО", updated_by: "", updated_at: null, po: "PO-1", contract: "", spec: "", invoice: "", pays: "", customer_address: "", hours: "",
};

function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status }); }

function stub(shipments: unknown[] = [], goods: unknown[] = [good], addressees: unknown[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/goods" && method === "GET") return json({ goods });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/shipments" && method === "POST") return json({ shipment: { ...shipment, version: 1 } });
    if (url === "/api/shipping/upd-files") return json({ shipment: { ...shipment, version: 2 } });
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/shipping/project-addressee" && method === "PUT") return json({ addressee: { ...card, ...JSON.parse(String(init?.body)), version: 3 } });
    if (/^\/api\/shipping\/shipments\/s\d+\/print/.test(url)) return json({ forms: [{ kind: "pl", title: "УПАКОВОЧНАЯ ВЕДОМОСТЬ", head: [{ label: "Номер документа", value: "PL1", side: "" }], columns: ["GRN"], rows: [["КБ1"]], blank_rows: 0, sign_label: "Штамп / подпись кладовщика:", sign_name: "" }] });
    if (url === "/api/shipping/shipments/s1/labels") return json({ labels: [{ shipper: "ООО «ЗЭП ФРАКСИС»", consignee: "Получатель", address: "Москва", project: "591К.3", pl_number: "PL1", place: "Упаковка 1/1", items: [{ item_index: "КБ1", quantity: "6", unit: "шт" }], net: "11", dims: "32x25x21" }] });
    if (url === "/api/shipping/shipments/s1/ship") return json({ shipment, closed: true, why: "" });
    if (url === "/api/shipping/shipments/s2/unship") return json({ shipment: { ...shippedOne, status: "Упакована", shipped_at: null } });
    if (url === "/api/shipping/project-ship") return json({ shipment: { ...shipment, status: "Упакована" }, shipped: false, closed: false, why: "", replayed: false });
    return json({ shipment: {} });
  }));
}

function calls(url: string, method = "POST") {
  return vi.mocked(fetch).mock.calls.filter(([target, init]) => String(target) === url && init?.method === method).map(([, init]) => JSON.parse(String(init?.body)));
}
const sent = (url: string, method = "POST") => calls(url, method)[0];
const lastSent = (url: string, method = "POST") => calls(url, method).at(-1);
const tab = (name: RegExp) => fireEvent.click(screen.getByRole("tab", { name }));
/** Сколько запросов, меняющих данные, ушло. */
const writes = () => vi.mocked(fetch).mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET");
const keyOf = (url: string, index = 0) => {
  const init = vi.mocked(fetch).mock.calls.filter(([target]) => String(target) === url)[index]?.[1];
  return new Headers(init?.headers).get("Idempotency-Key");
};
const loaded = () => screen.findByText("591К.3", { selector: "strong" });

describe("ShippingPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?section=shipping");
    stub();
  });

  it("«На складе» packs a project into a shipment with one request and does not ship it", async () => {
    stub([], [good, { ...other, remaining: "0", accepted: "0" }], [card]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^На складе/);
    expect(screen.queryByText("600К.1", { selector: "strong" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByLabelText("Адрес доставки проекта 591К.3")).toHaveValue("Склад заказчика");
    expect(screen.queryByRole("button", { name: /^Отгрузить/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сколько упаковать КБ1"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Отгрузить до: проект 591К.3"), { target: { value: "2026-10-09" } });
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 4 шт" }));

    expect(await screen.findByText("Упаковано: ОТГ-20260922-01 · PL591К.3-1 — отгрузите во вкладке «Отправки»")).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(sent("/api/shipping/project-ship")).toEqual({
      project: "591К.3", addressee_version: 2, lines: [{ good_id: "g1", version: 1, quantity: "4" }], ship: false,
      documents: { upd_number: "", upd_date: null, deadline: "2026-10-09" },
    });
    expect(keyOf("/api/shipping/project-ship")).toBeTruthy();
  });

  it("refuses to pack more than is on stock or without an address — nothing is created", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^На складе/);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 6 шт" }));
    expect(screen.getByText("Укажите адрес доставки")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Адрес доставки проекта 591К.3"), { target: { value: "Тула" } });
    fireEvent.change(screen.getByLabelText("Сколько упаковать КБ1"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 9 шт" }));
    expect(screen.getByText("КБ1: на складе только 6")).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it("saves a dispatch-note requisite of the project on leaving the field", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^На складе/);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const contract = screen.getByLabelText("№ договора проекта 591К.3");
    fireEvent.change(contract, { target: { value: "Д-12" } });
    fireEvent.blur(contract);
    await waitFor(() => expect(sent("/api/shipping/project-addressee", "PUT")).toEqual({ project: "591К.3", version: 2, contract: "Д-12" }));
  });

  it("lets the storekeeper pack without touching the project card", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" } }} />);
    await loaded();
    tab(/^На складе/);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.queryByLabelText("№ УПД проекта 591К.3")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Добавить КБ проекта" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 6 шт" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(sent("/api/shipping/project-ship")).toEqual({ project: "591К.3", addressee_version: 2, lines: [{ good_id: "g1", version: 1, quantity: "6" }], ship: false });
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).not.toContain("/api/shipping/finished-goods/from-production");
  });

  it("the storekeeper fills the dispatch data too: the card is saved first, then the pack goes with its new version", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" } }} />);
    await loaded();
    tab(/^На складе/);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    fireEvent.change(screen.getByLabelText("Телефон проекта 591К.3"), { target: { value: "+7 900 000-00-00" } });
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 6 шт" }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(sent("/api/shipping/project-addressee", "PUT")).toEqual({ project: "591К.3", version: 2, phone: "+7 900 000-00-00" });
    expect(sent("/api/shipping/project-ship")).toEqual({ project: "591К.3", addressee_version: 3, lines: [{ good_id: "g1", version: 1, quantity: "6" }], ship: false });
  });

  it("shows the four dispatch fields of the project, the rest under «Ещё», and the paper УПД file", async () => {
    stub([], [good], [{ ...card, upd_type: "БУМАГА", upd_url: "/api/shipping/files/f1", upd_file_name: "УПД 591К.3.pdf", contact: "Иван" }]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^На складе/);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const dispatch = screen.getByRole("heading", { name: "Данные отправки" }).parentElement!;
    expect(within(dispatch).getAllByRole("textbox").map((input) => input.getAttribute("aria-label"))).toEqual(
      ["Получатель проекта 591К.3", "Адрес доставки проекта 591К.3", "Телефон проекта 591К.3"]);
    expect(within(dispatch).getByLabelText("УПД проекта 591К.3")).toHaveValue("БУМАГА");
    expect(within(dispatch).getByRole("link", { name: "УПД 591К.3.pdf" })).toHaveAttribute("href", "/api/shipping/files/f1");
    expect(within(dispatch).queryByLabelText("Контактное лицо проекта 591К.3")).not.toBeInTheDocument();
    const more = screen.getByText(/^Ещё: контакт, доставка, план/).closest("details")!;
    expect(more).not.toHaveAttribute("open");
    expect(within(more).getByLabelText("Контактное лицо проекта 591К.3")).toHaveValue("Иван");
    expect(within(more).getByLabelText("Условия доставки проекта 591К.3")).toHaveValue("САМОВЫВОЗ");
  });

  it("«Отправки» groups shipments by status and ships a packed one", async () => {
    stub([shipment, shippedOne]);
    const printed = capturePrintFrames();
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    expect(await screen.findByText("Собирается", { selector: ".group-row strong" })).toBeInTheDocument();
    expect(screen.getByText("просрочена")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отгружена · 1" }));
    expect(screen.getByText("Отгружена", { selector: ".group-row strong" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "В работе · 1" }));
    fireEvent.click(screen.getByText("ОТГ-20260922-01"));

    fireEvent.click(screen.getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalled());
    expect(printed.html[0]).toContain("УПАКОВОЧНАЯ ВЕДОМОСТЬ");
    fireEvent.click(screen.getByRole("button", { name: "Печать этикеток" }));
    await waitFor(() => expect(printed.html).toHaveLength(2));
    expect(printed.html[1]).toContain("Упаковка 1/1");
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить" }));
    expect(await screen.findByText("Отгружено и закрыто: ОТГ-20260922-01")).toBeInTheDocument();
    expect(sent("/api/shipping/shipments/s1/ship")).toEqual({ version: 3 });
  });

  it("edits requested quantity and lays out boxes of a shipment", async () => {
    stub([shipment]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    const requested = screen.getByLabelText("Заказано КБ1");
    fireEvent.change(requested, { target: { value: "8" } });
    fireEvent.blur(requested);
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/lines/l1", "PUT")).toEqual({ version: 3, quantity: "8" }));

    fireEvent.click(screen.getByRole("button", { name: "Перевыпустить" }));
    fireEvent.change(screen.getByLabelText("Вес брутто, кг BXN 1"), { target: { value: "2,5" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Перевыпустить" }).at(-1)!);
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/boxes", "PUT")).toEqual({
      version: 3, tk_note: "", boxes: [{ kind: "Короб", dims: "", net: "", gross: "2,5", seal: "", items: [{ item_index: "КБ1", quantity: "6" }] }],
    }));
  });

  it("lets the administrator roll a shipped shipment back with a reason", async () => {
    stub([shippedOne]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(screen.getByRole("button", { name: "Отгружена · 1" }));
    fireEvent.click(screen.getByText("ОТГ-20260920-02"));
    fireEvent.click(screen.getByRole("button", { name: "Откатить отгрузку" }));
    const confirm = screen.getByRole("button", { name: "Откатить в «Упакована»" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Причина отката ОТГ-20260920-02"), { target: { value: "нажали по ошибке" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(sent("/api/shipping/shipments/s2/unship")).toEqual({ version: 3, reason: "нажали по ошибке" }));
    expect(await screen.findByText("Отгрузка откатена: ОТГ-20260920-02 снова на складе, упакована")).toBeInTheDocument();
  });

  it("hides the rollback from everyone but the administrator", async () => {
    stub([shippedOne]);
    render(<ShippingPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { shipping: "edit", shipping_goods: "edit" } }} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(screen.getByRole("button", { name: "Отгружена · 1" }));
    fireEvent.click(screen.getByText("ОТГ-20260920-02"));
    expect(screen.getByRole("button", { name: "Закрыть" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Откатить отгрузку" })).not.toBeInTheDocument();
  });

  it("creates a shipment request in advance with requested quantity independent of stock and a deadline", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByRole("button", { name: "+ Заявка на отгрузку" }));
    fireEvent.change(await screen.findByLabelText("Заказать 591К.3 КБ1"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-01" } });
    fireEvent.click(screen.getByLabelText("Без отгрузочных документов (только набрать)"));
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));

    await waitFor(() => expect(sent("/api/shipping/shipments")).toBeDefined());
    const body = sent("/api/shipping/shipments");
    expect(body.lines).toEqual([{ good_id: "g1", quantity: "10" }]);
    expect(body.no_docs).toBe(true);
    expect(body.deadline).toBe("2026-10-01");
    expect(body.receiver).toBe("");
    expect(body.terms).toBeNull();
  });

  it("the shipment status is a shared chip; cancelling is a danger button that asks first", async () => {
    stub([shipment]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    expect(document.querySelector(".status-chip[data-tone=\"info\"]")).not.toBeNull();
    const cancel = screen.getByRole("button", { name: "Отменить заявку" });
    expect(cancel).toHaveClass("danger");
    fireEvent.click(cancel);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(writes()).toHaveLength(0);
  });

  it("edits the shipment head and uploads its УПД", async () => {
    stub([shipment]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    const point = screen.getByLabelText("Адрес отгрузки ОТГ-20260922-01");
    fireEvent.change(point, { target: { value: "Тула, ворота 3" } });
    fireEvent.blur(point);
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/head", "PUT")).toEqual({ version: 3, shipping_point: "Тула, ворота 3" }));
    const terms = screen.getByLabelText("Условия доставки ОТГ-20260922-01");
    fireEvent.change(terms, { target: { value: "" } });
    fireEvent.blur(terms);
    await waitFor(() => expect(lastSent("/api/shipping/shipments/s1/head", "PUT")).toEqual({ version: 3, terms: null }));

    expect(screen.getByText("файла УПД нет")).toBeInTheDocument();
    const file = new File(["%PDF"], "upd.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Загрузить УПД ОТГ-20260922-01"), { target: { files: [file] } });
    await waitFor(() => expect(sent("/api/shipping/upd-files")).toEqual({
      shipment_id: "s1", version: 3, file_name: "upd.pdf", content_type: "application/pdf", content_base64: btoa("%PDF"),
    }));
  });

  it("opens the tab named in the link", async () => {
    window.history.replaceState({}, "", "/?section=shipping&tab=shipments");
    stub([shipment]);
    render(<ShippingPage principal={principal} />);
    expect(await screen.findByText("ОТГ-20260922-01")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Отправки/ })).toHaveAttribute("aria-selected", "true");
  });

  it("gives the manager the manual КБ registration and requests", async () => {
    render(<ShippingPage principal={{ ...principal, roles: ["commercial"], access_level: "commercial", permissions: { shipping: "edit" } }} />);
    await screen.findByText("591К.3", { selector: "strong" });
    expect(await screen.findByRole("button", { name: "+ Добавить КБ проекта" })).toBeInTheDocument();
    tab(/^Отправки/);
    expect(await screen.findByRole("button", { name: "+ Заявка на отгрузку" })).toBeInTheDocument();
  });

  it("says when loading failed instead of showing an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Раздел закрыт" }), { status: 403 })));
    render(<ShippingPage principal={principal} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Раздел закрыт");
  });

  it("opens on «На складе» with three counted tabs and falls back there from the removed «Из производства» link", async () => {
    window.history.replaceState({}, "", "/?section=shipping&tab=production");
    stub([shipment], [{ ...good, location: "Стеллаж 3" }]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    expect(screen.getAllByRole("tab").map((item) => item.textContent?.replace(/\d+$/, ""))).toEqual(["На складе", "Отправки", "Журнал"]);
    expect(screen.getByRole("tab", { name: /^На складе/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: /Из производства/ })).not.toBeInTheDocument();
    expect(screen.getByText("Место хранения")).toBeInTheDocument();
    expect(screen.getByText("Стеллаж 3")).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).not.toContain("/api/shipping/finished-goods/from-production");
  });

  it("«Журнал» has one row per shipment line in the sheet column order and shows a partial shipment", async () => {
    const partial = {
      ...shipment, id: "s3", number: "ОТГ-20260924-03", status: "Отгружена", shipped_at: "2026-09-25", upd_type: "БЕЗ УПД", track_number: "TRK-77",
      terms: "ДОСТАВКА ТК", carrier: "СДЭК", contact: "Иван", phone: "+7 900", note: "хрупкое", dn_number: "DN591К.3-3", pl_number: "PL591К.3-3",
      boxes: [
        { place: 1, kind: "Короб", dims: "30х30х30", net: "5", gross: "6,5", seal: "", items: [] },
        { place: 2, kind: "Короб", dims: "20х20х20", net: "2", gross: "3", seal: "", items: [] },
      ],
      lines: [
        { ...shipment.lines[0], id: "l1", requested: "10", picked: "6", shortage: "4" },
        { ...shipment.lines[0], id: "l2", item_index: "КБ2", name: "Кабель", requested: "5", picked: "5", shortage: "0", grn: "ГП.26.0000002" },
      ],
    };
    stub([partial, { ...shipment, status: "Отменена" }]);
    const printed = capturePrintFrames();
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Журнал\s*2/);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼↑↓⇅]/gu, "").trim()).filter(Boolean);
    expect(headers).toEqual(["Дата", "ГРН", "Проект", "Заказчик", "КБ", "Наименование", "В заказе", "Отгружено", "Ед.", "№ PL", "Дата упаковки", "Условия доставки",
      "Способ отгрузки", "Трек-номер", "Дата отгрузки", "Статус", "Адрес доставки", "Контактное лицо", "Телефон", "УПД", "№ УПД", "Дата УПД", "№ DN", "Вес", "Габариты", "Комментарий"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2); // отменённая заявка в журнал не идёт
    const first = within(rows[0]);
    expect(first.getByText("недогруз 4")).toBeInTheDocument(); // заказано 10, уехало 6
    expect(first.getByText("TRK-77")).toBeInTheDocument();
    expect(first.getByText("9,5")).toBeInTheDocument(); // брутто по местам
    expect(first.getByText("30х30х30; 20х20х20")).toBeInTheDocument();
    expect(first.getByText("Без УПД")).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/недогруз/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Excel" })).toBeInTheDocument();
    fireEvent.click(first.getByRole("button", { name: "PL591К.3-3" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalled());
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).toContain("/api/shipping/shipments/s3/print?document=pl");
  });

  it("«Без УПД» ships without a missing-file warning and the ship button stays on", async () => {
    stub([{ ...shipment, upd_type: "БЕЗ УПД", inherited: [] }, { ...shippedOne, id: "s9", number: "ОТГ-20260921-09", upd_type: "БЕЗ УПД", upd_number: "", upd_url: "", inherited: [] }]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    expect(screen.queryByText("файла УПД нет")).not.toBeInTheDocument();
    expect(screen.getByText("Без УПД", { selector: ".ship-card-head span" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Загрузить УПД ОТГ-20260922-01")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отгрузить" })).toBeEnabled();
    // отгружено без УПД — не «без УПД» в отборе проблемных
    fireEvent.click(screen.getByRole("button", { name: "Отгружено без УПД · 0" }));
  });

  it("lets the storekeeper edit the shipment head and choose the УПД type", async () => {
    stub([{ ...shipment, inherited: [] }]);
    render(<ShippingPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { shipping_goods: "edit" } }} />);
    await loaded();
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    const point = screen.getByLabelText("Адрес отгрузки ОТГ-20260922-01");
    expect(point).toBeEnabled();
    fireEvent.change(point, { target: { value: "Казань" } });
    fireEvent.blur(point);
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/head", "PUT")).toEqual({ version: 3, shipping_point: "Казань" }));
    const select = screen.getByLabelText("Тип УПД ОТГ-20260922-01");
    expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual(["из карточки проекта", "ЭДО", "Бумага", "Без УПД"]);
    fireEvent.change(select, { target: { value: "БЕЗ УПД" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить документы" }));
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/documents", "PUT")).toMatchObject({ upd_type: "БЕЗ УПД" }));
  });

  it("saves the project planned date and comment from the project card", async () => {
    stub([], [good], [{ ...card, plan_date: null, note: "" }]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const date = screen.getByLabelText("Плановая дата проекта 591К.3");
    fireEvent.change(date, { target: { value: "2026-10-20" } });
    fireEvent.blur(date);
    await waitFor(() => expect(sent("/api/shipping/project-addressee", "PUT")).toEqual({ project: "591К.3", version: 2, plan_date: "2026-10-20" }));
  });
});
