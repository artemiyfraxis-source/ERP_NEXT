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
const card = {
  project: "591К.3", version: 2, receiver: "Получатель", shipping_point: "Склад заказчика", contact: "", phone: "", terms: "САМОВЫВОЗ",
  carrier: "", upd_type: "ЭДО", updated_by: "", updated_at: null, po: "PO-1", contract: "", spec: "", invoice: "", pays: "", customer_address: "", hours: "",
};

function json(body: unknown) { return new Response(JSON.stringify(body), { status: 200 }); }

function stub(shipments: unknown[] = [], goods: unknown[] = [good], addressees: unknown[] = []) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/goods" && method === "GET") return json({ goods });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/shipments" && method === "POST") return json({ shipment: { ...shipment, version: 1 } });
    if (url === "/api/shipping/project-pack") return json({ shipment: { ...shipment, version: 1, pl_number: "", dn_number: "" } });
    if (url === "/api/shipping/upd-files") return json({ shipment: { ...shipment, version: 2 } });
    if (url === "/api/shipping/project-addressees") return json({ addressees });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (url.startsWith("/api/shipping/shipments/s1/print")) return json({ sheets: [{ title: "PL", subtitle: "", columns: ["#"], rows: [["КБ1"]], note: "", sign: "" }] });
    if (url === "/api/shipping/shipments/s1/ship") return json({ shipment, closed: true, why: "" });
    if (url === "/api/shipping/project-ship") {
      const shipNow = JSON.parse(String(init?.body)).ship;
      return json({ shipment: { ...shipment, status: shipNow ? "Закрыта" : "Упакована" }, shipped: shipNow, closed: shipNow, why: "", replayed: false });
    }
    return json({ shipment: {} });
  }));
}

function calls(url: string, method = "POST") {
  return vi.mocked(fetch).mock.calls.filter(([target, init]) => String(target) === url && init?.method === method).map(([, init]) => JSON.parse(String(init?.body)));
}
const sent = (url: string, method = "POST") => calls(url, method)[0];
const lastSent = (url: string, method = "POST") => calls(url, method).at(-1);
const tab = (name: RegExp) => fireEvent.click(screen.getByRole("tab", { name }));
/** Сколько запросов, меняющих данные, ушло — кнопки «К отгрузке» шлют ровно один. */
const writes = () => vi.mocked(fetch).mock.calls.filter(([, init]) => (init?.method ?? "GET") !== "GET");
const shipKeyOf = (index = 0) => {
  const init = vi.mocked(fetch).mock.calls.filter(([target]) => String(target) === "/api/shipping/project-ship")[index]?.[1];
  return new Headers(init?.headers).get("Idempotency-Key");
};

describe("ShippingPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    stub();
  });

  it("opens on «К отгрузке» with what is ready, grouped by customer", async () => {
    stub([], [good, { ...other, remaining: "0", accepted: "0" }]);
    render(<ShippingPage principal={principal} />);
    expect(await screen.findByText("591К.3", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Отгрузка", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Заказчик", { selector: ".group-row strong" })).toBeInTheDocument();
    expect(screen.getByText("адрес не указан")).toBeInTheDocument();
    // Проект без готового остатка — не в отборе «К отгрузке», а в «Все».
    expect(screen.queryByText("600К.1", { selector: "strong" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Все · 2" }));
    expect(screen.getByText("600К.1", { selector: "strong" })).toBeInTheDocument();
  });

  it("ships a project with one request: card change, documents and the ship step together", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    fireEvent.change(screen.getByLabelText("Адрес отгрузки проекта 591К.3"), { target: { value: "Склад заказчика" } });
    fireEvent.change(screen.getByLabelText("Тип УПД проекта 591К.3"), { target: { value: "ЭДО" } });
    fireEvent.change(screen.getByLabelText("Сколько отгрузить КБ1"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("№ УПД проекта 591К.3"), { target: { value: "УПД-7" } });
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 4 шт" }));

    expect(await screen.findByText("Отгружено и закрыто: ОТГ-20260922-01")).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(sent("/api/shipping/project-ship")).toEqual({
      project: "591К.3", addressee_version: 0, lines: [{ good_id: "g1", version: 1, quantity: "4" }], ship: true,
      addressee: { shipping_point: "Склад заказчика", upd_type: "ЭДО" },
      documents: { upd_number: "УПД-7", upd_date: expect.any(String), deadline: null },
    });
    expect(shipKeyOf()).toBeTruthy();
  });

  it("packs without shipping and does not touch an unchanged project card", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByLabelText("Адрес отгрузки проекта 591К.3")).toHaveValue("Склад заказчика");
    fireEvent.change(screen.getByLabelText("Отгрузить до: проект 591К.3"), { target: { value: "2026-10-09" } });
    fireEvent.click(screen.getByRole("button", { name: "Упаковать, отгрузить позже" }));

    expect(await screen.findByText("Упаковано: ОТГ-20260922-01 · PL591К.3-1")).toBeInTheDocument();
    expect(writes()).toHaveLength(1);
    expect(sent("/api/shipping/project-ship")).toEqual({
      project: "591К.3", addressee_version: 2, lines: [{ good_id: "g1", version: 1, quantity: "6" }], ship: false,
      documents: { upd_number: "", upd_date: null, deadline: "2026-10-09" },
    });
  });

  it("refuses to ship more than is on stock or by paper УПД without the file — nothing is created", async () => {
    stub([], [good], [{ ...card, upd_type: "БУМАГА" }]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 6 шт" }));
    expect(screen.getByText(/По бумажной УПД отгрузить можно только с файлом УПД/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Сколько отгрузить КБ1"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 9 шт" }));
    expect(screen.getByText("КБ1: на складе только 6")).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });

  it("sends the chosen УПД file inside the same single request", async () => {
    stub([], [good], [{ ...card, upd_type: "БУМАГА" }]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const file = new File(["%PDF"], "upd.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Файл УПД проекта 591К.3"), { target: { files: [file] } });
    expect(screen.getByText("upd.pdf")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 6 шт" }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(sent("/api/shipping/project-ship")).toMatchObject({
      ship: true, upd_file: { file_name: "upd.pdf", content_type: "application/pdf", content_base64: btoa("%PDF") },
    });
  });

  it("on a refused ship says why, nothing is half-done, and a retry reuses the same key", async () => {
    stub([], [good], [card]);
    const base = vi.mocked(fetch).getMockImplementation()!;
    let refuse = true;
    vi.mocked(fetch).mockImplementation(async (input, init) => String(input) === "/api/shipping/project-ship" && refuse
      ? new Response(JSON.stringify({ detail: "Нельзя отгрузить: не хватает — файл УПД (без файла — только по ЭДО)" }), { status: 422 }) : base(input, init));
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 6 шт" }));
    expect(await screen.findByText("Нельзя отгрузить: не хватает — файл УПД (без файла — только по ЭДО)")).toBeInTheDocument();
    expect(writes()).toHaveLength(1);

    refuse = false;
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 6 шт" }));
    expect(await screen.findByText("Отгружено и закрыто: ОТГ-20260922-01")).toBeInTheDocument();
    expect(writes()).toHaveLength(2);
    expect(shipKeyOf(1)).toBe(shipKeyOf(0));
  });

  it("saves a dispatch-note requisite of the project on leaving the field", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const contract = screen.getByLabelText("№ договора проекта 591К.3");
    fireEvent.change(contract, { target: { value: "Д-12" } });
    fireEvent.blur(contract);
    await waitFor(() => expect(sent("/api/shipping/project-addressee", "PUT")).toEqual({ project: "591К.3", version: 2, contract: "Д-12" }));
  });

  it("creates a shipment request in advance with requested quantity independent of stock and a deadline", async () => {
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    fireEvent.click(await screen.findByRole("button", { name: "+ Заявка на отгрузку" }));
    fireEvent.change(await screen.findByLabelText("Заказать 591К.3 КБ1"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));

    await waitFor(() => expect(sent("/api/shipping/shipments")).toBeDefined());
    const body = sent("/api/shipping/shipments");
    expect(body.lines).toEqual([{ good_id: "g1", quantity: "10" }]);
    expect(body.no_docs).toBe(false);
    expect(body.deadline).toBe("2026-10-01");
    // Получатель и точка не обязательны: пустые наследуются из карточки проекта.
    expect(body.receiver).toBe("");
    expect(body.terms).toBeNull();
  });

  it("sends no_docs when the manager wants the request only assembled", async () => {
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    fireEvent.click(await screen.findByRole("button", { name: "+ Заявка на отгрузку" }));
    fireEvent.change(await screen.findByLabelText("Заказать 591К.3 КБ1"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("Без отгрузочных документов (только набрать)"));
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));

    await waitFor(() => expect(sent("/api/shipping/shipments")?.no_docs).toBe(true));
  });

  it("shows ready-goods balance and finds a row by ГРН", async () => {
    stub([], [good, other]);
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Готовая продукция/);
    expect(await screen.findByText("ГП.26.0000001")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Поиск: Склад готовой продукции"), { target: { value: "0000002" } });
    expect(screen.queryByText("ГП.26.0000001")).not.toBeInTheDocument();
    expect(screen.getByText("ГП.26.0000002")).toBeInTheDocument();
  });

  it("accepts from the shop in one bar: row click picks the КБ, today is the default date", async () => {
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Готовая продукция/);
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    expect(await screen.findByLabelText("Дата поступления")).toHaveValue(`${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`);
    fireEvent.click(screen.getByText("ГП.26.0000001"));
    expect(screen.getByLabelText("Позиция для приёмки")).toHaveValue("g1");
    fireEvent.change(screen.getByLabelText("Принять, шт"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Дата поступления"), { target: { value: "2026-09-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять" }));

    await waitFor(() => expect(sent("/api/shipping/goods/g1/accept")).toEqual({ version: 1, quantity: "3", received_at: "2026-09-20" }));
  });

  it("edits requested quantity, lays out boxes, prints and ships from the registry", async () => {
    stub([shipment]);
    const printed = capturePrintFrames();
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    expect(await screen.findByText("просрочена")).toBeInTheDocument();
    fireEvent.click(screen.getByText("ОТГ-20260922-01"));

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

    fireEvent.click(screen.getByRole("button", { name: "Печать PL" }));
    await waitFor(() => expect(printed.print).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Отгрузить" }));
    expect(await screen.findByText("Отгружено и закрыто: ОТГ-20260922-01")).toBeInTheDocument();
  });

  it("О-2: the shipment status is a shared status chip; cancelling is a danger button that asks first", async () => {
    stub([shipment]);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    expect(document.querySelector(".status-chip[data-tone=\"info\"]")).not.toBeNull();
    const cancel = screen.getByRole("button", { name: "Отменить заявку" });
    expect(cancel).toHaveClass("danger");
    fireEvent.click(cancel);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(writes()).toHaveLength(0);
  });

  it("shows an open shipment inside its project and ships it from there", async () => {
    stub([shipment], [{ ...good, remaining: "0", shipped: "6" }]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByText("Всё готовое уже упаковано — отгрузите отправку ниже.")).toBeInTheDocument();
    const card = screen.getByText("ОТГ-20260922-01", { selector: ".ship-card-head strong" }).closest("article")!;
    fireEvent.click(within(card as HTMLElement).getByRole("button", { name: "Отгрузить" }));
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/ship")).toEqual({ version: 3 }));
  });

  it("edits the shipment head: own value is sent, emptied address field goes back to the project card", async () => {
    stub([shipment]);
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    expect(screen.getAllByText("из карточки проекта").length).toBeGreaterThan(0);

    const point = screen.getByLabelText("Адрес отгрузки ОТГ-20260922-01");
    fireEvent.change(point, { target: { value: "Тула, ворота 3" } });
    fireEvent.blur(point);
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/head", "PUT")).toEqual({ version: 3, shipping_point: "Тула, ворота 3" }));

    const terms = screen.getByLabelText("Условия доставки ОТГ-20260922-01");
    fireEvent.change(terms, { target: { value: "" } });
    fireEvent.blur(terms);
    await waitFor(() => expect(lastSent("/api/shipping/shipments/s1/head", "PUT")).toEqual({ version: 3, terms: null }));

    fireEvent.change(screen.getByLabelText("Дата УПД"), { target: { value: "2026-09-30" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить документы" }));
    await waitFor(() => expect(sent("/api/shipping/shipments/s1/documents", "PUT")).toMatchObject({ version: 3, upd_date: "2026-09-30", upd_type: "ЭДО", upd_url: "" }));
  });

  it("uploads the УПД file of a shipment and links it", async () => {
    stub([shipment]);
    render(<ShippingPage principal={principal} />);
    await screen.findByText("591К.3", { selector: "strong" });
    tab(/^Отправки/);
    fireEvent.click(await screen.findByText("ОТГ-20260922-01"));
    expect(screen.getByText("файла УПД нет")).toBeInTheDocument();
    const file = new File(["%PDF"], "upd.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Загрузить УПД ОТГ-20260922-01"), { target: { files: [file] } });

    await waitFor(() => expect(sent("/api/shipping/upd-files")).toEqual({
      shipment_id: "s1", version: 3, file_name: "upd.pdf", content_type: "application/pdf", content_base64: btoa("%PDF"),
    }));
  });

  it("accepts confirmed final boxes with lineage by marking them and acting once", async () => {
    let payload: unknown;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/shipping/goods") return json({ goods: [good] });
      if (url === "/api/shipping/shipments") return json({ shipments: [] });
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
      return json({});
    });

    render(<ShippingPage principal={principal} />);
    expect(await screen.findByRole("tab", { name: /^Готовая продукция\s*1$/ })).toBeInTheDocument();
    tab(/^Готовая продукция/);
    fireEvent.click(await screen.findByLabelText("Выбрать: коробка 104233-7"));
    fireEvent.click(screen.getByRole("button", { name: "Принять на склад ГП" }));

    await waitFor(() => expect(payload).toEqual({ version: 3, finished_good_id: "g1" }));
  });

  // Access levels (owner 04.10.2026): «Отгрузка» (`shipping`) is the manager's side, «Отгрузка — склад» (`shipping_goods`)
  // the storekeeper's; the screen reads both, whoever holds them.
  const holder = (level: string, permissions: Record<string, "view" | "edit">) => ({ ...principal, roles: [level], access_level: level, permissions });

  it("lets the storekeeper (edit on shipping_goods only) ship by the project card, accept from the shop, but not edit the card", async () => {
    stub([], [good], [card]);
    render(<ShippingPage principal={holder("foreman", { shipping_goods: "edit" })} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByLabelText("Адрес отгрузки проекта 591К.3")).toBeDisabled();
    expect(screen.queryByLabelText("№ УПД проекта 591К.3")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отгрузить 6 шт" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    // Кладовщик карточку и документы не шлёт: сервер отказал бы всей команде.
    expect(sent("/api/shipping/project-ship")).toEqual({ project: "591К.3", addressee_version: 2, lines: [{ good_id: "g1", version: 1, quantity: "6" }], ship: true });

    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).toContain("/api/production/finished-goods-ready");
    tab(/^Готовая продукция/);
    expect(await screen.findByLabelText("Позиция для приёмки")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Добавить КБ проекта" })).not.toBeInTheDocument();
    tab(/^Отправки/);
    expect(screen.queryByRole("button", { name: "+ Заявка на отгрузку" })).not.toBeInTheDocument();
  });

  it("gives the manager (edit on shipping only) packing and documents but not the ship and accept actions", async () => {
    render(<ShippingPage principal={holder("commercial", { shipping: "edit" })} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByRole("button", { name: "Упаковать, отгрузить позже" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Отгрузить/ })).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.map(([target]) => String(target))).not.toContain("/api/production/finished-goods-ready");
    tab(/^Готовая продукция/);
    expect(await screen.findByRole("button", { name: "+ Добавить КБ проекта" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Позиция для приёмки")).not.toBeInTheDocument();
    tab(/^Отправки/);
    expect(await screen.findByRole("button", { name: "+ Заявка на отгрузку" })).toBeInTheDocument();
  });

  it("shows a full-access level every action even when the permissions map is empty", async () => {
    render(<ShippingPage principal={holder("superadmin", {})} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByRole("button", { name: "Отгрузить 6 шт" })).toBeInTheDocument();
    tab(/^Готовая продукция/);
    expect(await screen.findByLabelText("Позиция для приёмки")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Добавить КБ проекта" })).toBeInTheDocument();
  });

  it("reads shipping for a view grant but offers no action", async () => {
    render(<ShippingPage principal={holder("commercial", { shipping: "view" })} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.queryByRole("button", { name: /Отгрузить|Упаковать/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Сколько отгрузить КБ1")).not.toBeInTheDocument();
    tab(/^Готовая продукция/);
    await screen.findByText("ГП.26.0000001");
    expect(screen.queryByLabelText("Позиция для приёмки")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Добавить КБ проекта" })).not.toBeInTheDocument();
  });

  it("says when loading failed instead of showing an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Раздел закрыт" }), { status: 403 })));
    render(<ShippingPage principal={principal} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Раздел закрыт");
  });
});
