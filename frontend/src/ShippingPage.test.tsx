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

const incoming = [
  { project: "591К.3", item_index: "КБ1", name: "Жгут", unit: "шт", needed: "40", delivered: "26", accepted: "6", remaining: "20", in_production: "34",
    good_id: "g1", good_version: 1, tasks: [{ number: "ПЗ-0001", status: "Запущено", site: "ЖУК", quantity: 40, delivered: "26" }] },
  { project: "591К.3", item_index: "КБ2", name: "Кабель сигнальный", unit: "шт", needed: "10", delivered: "10", accepted: "0", remaining: "10", in_production: "10",
    good_id: null, good_version: null, tasks: [{ number: "ПЗ-0002", status: "Выполнено", site: "", quantity: 10, delivered: "10" }] },
  { project: "600К.1", item_index: "КБ7", name: "Кабель", unit: "шт", needed: "5", delivered: "0", accepted: "0", remaining: "0", in_production: "5",
    good_id: "g2", good_version: 1, tasks: [{ number: "ПЗ-0003", status: "Запущено", site: "", quantity: 5, delivered: "0" }] },
];

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

function stub(shipments: unknown[] = [], goods: unknown[] = [good], addressees: unknown[] = [], rows: unknown[] = incoming) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/goods" && method === "GET") return json({ goods });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/shipments" && method === "POST") return json({ shipment: { ...shipment, version: 1 } });
    if (url === "/api/shipping/finished-goods/from-production") return json({ rows });
    if (url === "/api/shipping/finished-goods/accept") return json({ goods: [good], replayed: false });
    if (url === "/api/shipping/upd-files") return json({ shipment: { ...shipment, version: 2 } });
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (url.startsWith("/api/shipping/shipments/s1/print")) return json({ forms: [{ kind: "pl", title: "УПАКОВОЧНАЯ ВЕДОМОСТЬ", head: [{ label: "Номер документа", value: "PL1", side: "" }], columns: ["GRN"], rows: [["КБ1"]], blank_rows: 0, sign_label: "Штамп / подпись кладовщика:", sign_name: "" }] });
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
const loaded = () => screen.findByText("КБ1", { selector: "strong" });

describe("ShippingPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.history.replaceState({}, "", "/?section=shipping");
    stub();
  });

  it("opens on «Из производства» with three counted tabs and what the shop delivered, by project", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    expect(screen.getByRole("heading", { name: "Отгрузка", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Из производства\s*2/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /На складе\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Отправки\s*0/ })).toBeInTheDocument();
    expect(screen.getByText("591К.3 · Заказчик", { selector: ".group-row strong" })).toBeInTheDocument();
    expect(screen.getByText("ПЗ-0001 · ЖУК")).toBeInTheDocument();
    // КБ, по которой цех ещё ничего не сдал, — не в «Можно принять», а в «Ещё в производстве».
    expect(screen.queryByText("КБ7", { selector: "strong" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ещё в производстве · 1" }));
    expect(screen.getByText("КБ7", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("ещё в производстве")).toBeInTheDocument();
  });

  it("accepts the ticked КБ in one request with a partial quantity, the date and an idempotency key", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    expect(screen.getByRole("button", { name: "Принять на склад · 0 шт" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "12" } });
    fireEvent.click(screen.getByLabelText("Принять 591К.3 КБ2"));
    fireEvent.change(screen.getByLabelText("Дата приёмки"), { target: { value: "2026-10-11" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 22 шт" }));

    expect(await screen.findByText("Принято на склад: 2 КБ · 22 шт")).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(sent("/api/shipping/finished-goods/accept")).toEqual({
      lines: [{ project: "591К.3", item_index: "КБ1", quantity: "12" }, { project: "591К.3", item_index: "КБ2", quantity: "10" }],
      received_at: "2026-10-11",
    });
    expect(keyOf("/api/shipping/finished-goods/accept")).toBeTruthy();
  });

  it("selects every delivered КБ at once and refuses more than delivered without sending", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    fireEvent.click(screen.getByLabelText("Отметить всё сданное · 2"));
    expect(screen.getByRole("button", { name: "Принять на склад · 30 шт" })).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 35 шт" }));
    expect(screen.getByText("больше сданного: можно 20")).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it("shows a refused batch (422) at its row, accepts nothing and retries with the same key", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    let refuse = true;
    vi.mocked(fetch).mockImplementation(async (input, init) => String(input) === "/api/shipping/finished-goods/accept" && refuse
      ? json({ detail: "591К.3 КБ1: сдано цехом 26 шт, уже принято 10 — принять можно ещё 16" }, 422) : base(input, init));
    render(<ShippingPage principal={principal} />);
    await loaded();
    fireEvent.click(screen.getByLabelText("Принять 591К.3 КБ1"));
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 20 шт" }));

    expect(await screen.findByText("сдано цехом 26 шт, уже принято 10 — принять можно ещё 16")).toHaveClass("ship-row-error");
    expect(screen.getByLabelText("Сколько принять 591К.3 КБ1")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/^Ничего не принято: 591К.3 КБ1/)).toBeInTheDocument();

    refuse = false;
    fireEvent.change(screen.getByLabelText("Сколько принять 591К.3 КБ1"), { target: { value: "16" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад · 16 шт" }));
    expect(await screen.findByText("Принято на склад: 1 КБ · 16 шт")).toBeInTheDocument();
    expect(keyOf("/api/shipping/finished-goods/accept", 1)).toBe(keyOf("/api/shipping/finished-goods/accept", 0));
  });

  it("gives a view grant the delivered list but no accept controls", async () => {
    render(<ShippingPage principal={{ ...principal, roles: ["commercial"], access_level: "commercial", permissions: { shipping: "view" } }} />);
    await loaded();
    expect(screen.queryByLabelText("Принять 591К.3 КБ1")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Принять на склад/ })).not.toBeInTheDocument();
  });

  it("«На складе» packs a project into a shipment with one request and does not ship it", async () => {
    stub([], [good, { ...other, remaining: "0", accepted: "0" }], [card]);
    render(<ShippingPage principal={principal} />);
    await loaded();
    tab(/^На складе/);
    expect(screen.queryByText("600К.1", { selector: "strong" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByLabelText("Адрес отгрузки проекта 591К.3")).toHaveValue("Склад заказчика");
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
    expect(screen.getByText("Укажите адрес отгрузки")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Адрес отгрузки проекта 591К.3"), { target: { value: "Тула" } });
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
    expect(screen.getByLabelText("Адрес отгрузки проекта 591К.3")).toBeDisabled();
    expect(screen.queryByLabelText("№ УПД проекта 591К.3")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Добавить КБ проекта" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 6 шт" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(sent("/api/shipping/project-ship")).toEqual({ project: "591К.3", addressee_version: 2, lines: [{ good_id: "g1", version: 1, quantity: "6" }], ship: false });
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).toContain("/api/production/finished-goods-ready");
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

  it("accepts confirmed final boxes with lineage under «Из производства»", async () => {
    let payload: unknown;
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/production/finished-goods-ready") return json({ boxes: [{
        id: "b1", number: "104233-7", version: 3, task_id: "t1", operation_id: "o1",
        item: "591К.3.КБ1", operation_position: 3, operator_id: "u1", quantity: "5",
        consumed: "0", reserved: "0", available: "5", status: "Подтверждена",
        created_at: "2026-09-23T09:00:00", closed_at: null, closed_by: null,
        confirmed_at: "2026-09-23T10:00:00", confirmed_by: "Мастер", return_reason: "",
        project: "591К.3", task_item: "591К.3.КБ1",
      }] });
      if (url === "/api/production/boxes/b1/handoff" && init?.method === "POST") {
        payload = JSON.parse(String(init.body));
        return json({ box: {}, finished_good: {}, receipt_id: "r1" });
      }
      return base(input, init);
    });
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByLabelText("Выбрать: коробка 104233-7"));
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад" }));
    await waitFor(() => expect(payload).toEqual({ version: 3, finished_good_id: "g1" }));
  });

  it("gives the manager the manual КБ registration and requests, but not the accept step", async () => {
    render(<ShippingPage principal={{ ...principal, roles: ["commercial"], access_level: "commercial", permissions: { shipping: "edit" } }} />);
    await loaded();
    expect(screen.queryByLabelText("Принять 591К.3 КБ1")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).not.toContain("/api/production/finished-goods-ready");
    tab(/^На складе/);
    expect(await screen.findByRole("button", { name: "+ Добавить КБ проекта" })).toBeInTheDocument();
    tab(/^Отправки/);
    expect(await screen.findByRole("button", { name: "+ Заявка на отгрузку" })).toBeInTheDocument();
  });

  it("says when loading failed instead of showing an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Раздел закрыт" }), { status: 403 })));
    render(<ShippingPage principal={principal} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Раздел закрыт");
  });

  it("keeps the selection inside the table rows", async () => {
    render(<ShippingPage principal={principal} />);
    await loaded();
    const row = screen.getByText("КБ2", { selector: "strong" }).closest("tr")!;
    fireEvent.click(within(row).getByLabelText("Принять 591К.3 КБ2"));
    expect(row).toHaveClass("ship-row-picked");
  });
});
