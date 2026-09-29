import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShippingPage } from "./ShippingPage";

const principal = {
  user: { id: "u1", email: "admin@example.test", name: "Admin", department: "ERP" },
  roles: ["admin"], permissions: { shipping: "edit" as const },
};

const good = {
  id: "g1", version: 1, project: "591К.3", item_index: "КБ1", customer: "Заказчик",
  article: "A-1", name: "Жгут", ordered: "40", unit: "шт", accepted: "6", shipped: "0",
  remaining: "6", received_at: "2026-09-22", grn: "ГП.26.0000001", note: "",
};
const other = { ...good, id: "g2", project: "600К.1", item_index: "КБ7", customer: "Другой", grn: "ГП.26.0000002", name: "Кабель" };

const shipment = {
  id: "s1", number: "ОТГ-20260922-01", version: 3, created_at: "2026-09-22T09:00:00", status: "Собрана",
  customer: "Заказчик", receiver: "Получатель", shipping_point: "Москва", contact: "", phone: "",
  terms: "САМОВЫВОЗ", carrier: "", deadline: "2026-09-01", upd_type: "ЭДО", upd_number: "", upd_date: null, upd_url: "",
  pl_number: "PL591К.3-1", dn_number: "DN591К.3-1", track_number: "", packed_at: "2026-09-22", shipped_at: null,
  manager: "Менеджер", note: "", tk_note: "", places: 0, weight: "", boxes: [],
  lines: [{ id: "l1", good_id: "g1", project: "591К.3", item_index: "КБ1", article: "A-1", name: "Жгут", requested: "10", picked: "6", shortage: "4", unit: "шт", grn: "ГП.26.0000001" }],
};

function json(body: unknown) { return new Response(JSON.stringify(body), { status: 200 }); }

function stub(shipments: unknown[] = [], goods: unknown[] = [good]) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/goods" && method === "GET") return json({ goods });
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/project-addressees") return json({ addressees: [] });
    if (url === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (url.startsWith("/api/shipping/shipments/s1/print")) return json({ sheets: [{ title: "PL", subtitle: "", columns: ["#"], rows: [["КБ1"]], note: "", sign: "" }] });
    if (url === "/api/shipping/shipments/s1/ship") return json({ shipment, closed: true, why: "" });
    return json({ shipment: {} });
  }));
}

function sent(url: string, method = "POST") {
  const call = vi.mocked(fetch).mock.calls.find(([target, init]) => String(target) === url && init?.method === method);
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

describe("ShippingPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    stub();
  });

  it("creates a shipment request with requested quantity independent of stock and a deadline", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Отправки" }));
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
    fireEvent.click(screen.getByRole("button", { name: "Отправки" }));
    fireEvent.click(await screen.findByRole("button", { name: "+ Заявка на отгрузку" }));
    fireEvent.change(await screen.findByLabelText("Заказать 591К.3 КБ1"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("Без отгрузочных документов (только набрать)"));
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));

    await waitFor(() => expect(sent("/api/shipping/shipments")?.no_docs).toBe(true));
  });

  it("shows ready-goods balance and finds a row by ГРН", async () => {
    stub([], [good, other]);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Готовая продукция" }));
    expect(await screen.findByText("ГП.26.0000001")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Поиск: Склад готовой продукции"), { target: { value: "0000002" } });
    expect(screen.queryByText("ГП.26.0000001")).not.toBeInTheDocument();
    expect(screen.getByText("ГП.26.0000002")).toBeInTheDocument();
  });

  it("accepts with an explicit receipt date", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Готовая продукция" }));
    fireEvent.change(await screen.findByLabelText("Принять 591К.3 КБ1"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Дата поступления 591К.3 КБ1"), { target: { value: "2026-09-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Принять" }));

    await waitFor(() => expect(sent("/api/shipping/goods/g1/accept")).toEqual({ version: 1, quantity: "3", received_at: "2026-09-20" }));
  });

  it("packs the marked quantity from the project card", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    expect(screen.getByText("Отправок пока нет. Отметь в строках, что упаковать, и нажми «Упаковать».")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Упаковать КБ1"));
    fireEvent.change(screen.getByLabelText("Сколько упаковать КБ1"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Упаковать · 1" }));

    await waitFor(() => expect(sent("/api/shipping/project-pack")).toEqual({ project: "591К.3", lines: [{ good_id: "g1", quantity: "4" }] }));
  });

  it("saves a project addressee field from the card", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(await screen.findByText("591К.3", { selector: "strong" }));
    const point = screen.getByLabelText("Адрес отгрузки проекта 591К.3");
    fireEvent.change(point, { target: { value: "Склад заказчика" } });
    fireEvent.blur(point);

    await waitFor(() => expect(sent("/api/shipping/project-addressee", "PUT")).toEqual({ project: "591К.3", version: 0, shipping_point: "Склад заказчика" }));
  });

  it("edits requested quantity, lays out boxes, prints and ships from the registry", async () => {
    stub([shipment]);
    const open = vi.fn(() => ({ document: { open: vi.fn(), write: vi.fn(), close: vi.fn() }, focus: vi.fn(), print: vi.fn() }));
    vi.stubGlobal("open", open);
    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Отправки" }));
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
    await waitFor(() => expect(open).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: "Отгружено" }));
    expect(await screen.findByText("Отгружено и закрыто")).toBeInTheDocument();
  });

  it("accepts a confirmed final box with lineage instead of a manual quantity", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Готовая продукция" }));
    fireEvent.click(await screen.findByRole("button", { name: "Принять коробку" }));

    await waitFor(() => expect(payload).toEqual({ version: 3, finished_good_id: "g1" }));
  });
});
