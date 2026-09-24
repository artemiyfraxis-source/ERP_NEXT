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

describe("ShippingPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/shipping/goods" && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ goods: [good] }), { status: 200 });
      if (url === "/api/shipping/shipments" && (!init?.method || init.method === "GET")) return new Response(JSON.stringify({ shipments: [] }), { status: 200 });
      if (url === "/api/production/finished-goods-ready") return new Response(JSON.stringify({ boxes: [] }), { status: 200 });
      if (url === "/api/shipping/shipments" && init?.method === "POST") return new Response(JSON.stringify({ shipment: {} }), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }));
  });

  it("creates a shipment request with requested quantity independent of stock", async () => {
    render(<ShippingPage principal={principal} />);
    await screen.findByText("Строк готовой продукции пока нет.").catch(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "+ Заявка на отгрузку" }));
    fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "Получатель" } });
    fireEvent.change(screen.getByLabelText("Точка отгрузки"), { target: { value: "Москва" } });
    fireEvent.change(screen.getByLabelText("Заказать 591К.3 КБ1"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));

    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/shipping/shipments", expect.objectContaining({ method: "POST" })));
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) => String(url) === "/api/shipping/shipments" && init?.method === "POST");
    expect(JSON.parse(String(call?.[1]?.body)).lines).toEqual([{ good_id: "g1", quantity: "10" }]);
  });

  it("shows ready-goods balance", async () => {
    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Склад ГП" }));
    expect(await screen.findByText("ГП.26.0000001")).toBeInTheDocument();
    expect(screen.getByText("6", { selector: "strong" })).toBeInTheDocument();
  });

  it("accepts a confirmed final box with lineage instead of a manual quantity", async () => {
    let payload: unknown;
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/shipping/goods") return new Response(JSON.stringify({ goods: [good] }), { status: 200 });
      if (url === "/api/shipping/shipments") return new Response(JSON.stringify({ shipments: [] }), { status: 200 });
      if (url === "/api/production/finished-goods-ready") return new Response(JSON.stringify({ boxes: [{
        id: "b1", number: "104233-7", version: 3, task_id: "t1", operation_id: "o1",
        item: "591К.3.КБ1", operation_position: 3, operator_id: "u1", quantity: "5",
        consumed: "0", reserved: "0", available: "5", status: "Подтверждена",
        created_at: "2026-09-23T09:00:00", closed_at: null, closed_by: null,
        confirmed_at: "2026-09-23T10:00:00", confirmed_by: "Мастер", return_reason: "",
        project: "591К.3", task_item: "591К.3.КБ1",
      }] }), { status: 200 });
      if (url === "/api/production/boxes/b1/handoff" && init?.method === "POST") {
        payload = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ box: {}, finished_good: {}, receipt_id: "r1" }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });

    render(<ShippingPage principal={principal} />);
    fireEvent.click(screen.getByRole("button", { name: "Склад ГП" }));
    fireEvent.click(await screen.findByRole("button", { name: "Принять коробку" }));

    await waitFor(() => expect(payload).toEqual({ version: 3, finished_good_id: "g1" }));
  });
});
