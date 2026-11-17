import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Shipment } from "./api";
import { Banners, ShipmentHead, useMutate } from "./ShipmentCard";

afterEach(() => vi.unstubAllGlobals());

const shipment: Shipment = {
  id: "s1", number: "ОТГ-1", version: 3, receiver: "Организация", shipping_point: "Москва",
  contact: "Иван", phone: "123", carrier: "СДЭК", terms: "ДОСТАВКА ТК", deadline: null, note: "",
  inherited: [], delivery_snapshot_at: "2026-11-16T09:00:00",
  created_at: "2026-11-16T09:00:00", status: "Собрана", customer: "Заказчик",
  upd_type: "ЭДО", upd_number: "", upd_date: null, upd_url: "", pl_number: "", dn_number: "", track_number: "",
  packed_at: null, shipped_at: null, manager: "Менеджер", tk_note: "", places: 0, weight: "", boxes: [], lines: [],
  missing_data: [], missing_ship: [],
};

function Head({ value = shipment, reload = vi.fn() }: { value?: Shipment; reload?: () => void }) {
  const { busy, error, notice, mutate } = useMutate(reload);
  return <><Banners error={error} notice={notice} /><ShipmentHead shipment={value} busy={busy} mutate={mutate} /></>;
}

it.each(["Адрес отгрузки", "Получатель", "Телефон"])("explains and saves an empty snapshot %s", async (label) => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ shipment })));
  vi.stubGlobal("fetch", fetcher);
  render(<Head />);
  expect(screen.getByLabelText("Организация ОТГ-1")).toHaveAttribute("placeholder", "как в документах");
  const input = screen.getByLabelText(`${label} ОТГ-1`);
  expect(input).toHaveAttribute("placeholder", label === "Получатель" ? "ФИО, кому передать груз" : "пусто — оставить пустым");
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.blur(input);
  await screen.findByText("Отправка сохранена");
  const fields: Record<string, string> = { "Адрес отгрузки": "shipping_point", "Получатель": "contact", "Телефон": "phone" };
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ version: 3, [fields[label]]: "" });
});

it("refreshes snapshot delivery explicitly and reloads the saved shipment", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ shipment: { ...shipment, version: 4, shipping_point: "Тула" } })));
  vi.stubGlobal("fetch", fetcher);
  const reload = vi.fn();
  render(<Head reload={reload} />);
  fireEvent.click(screen.getByRole("button", { name: "Взять данные КБ" }));
  await screen.findByText("Доставка обновлена. Перепечатайте этикетки и документы.");
  expect(fetcher).toHaveBeenCalledWith("/api/shipping/shipments/s1/head", expect.objectContaining({ method: "PUT" }));
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ version: 3, refresh_delivery: true });
  expect(reload).toHaveBeenCalledOnce();
});

it("shows a failed refresh and keeps shipment fields available for correction", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "У КБ разные данные доставки" }), { status: 422 })));
  render(<Head />);
  fireEvent.click(screen.getByRole("button", { name: "Взять данные КБ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("разные данные доставки");
  expect(screen.getByLabelText("Адрес отгрузки ОТГ-1")).toHaveValue("Москва");
  await waitFor(() => expect(screen.getByRole("button", { name: "Взять данные КБ" })).toBeEnabled());
});

it("keeps the project inheritance hint for legacy shipments", () => {
  render(<Head value={{ ...shipment, delivery_snapshot_at: null }} />);
  expect(screen.getByLabelText("Адрес отгрузки ОТГ-1")).toHaveAttribute("placeholder", "пусто — из карточки проекта");
  expect(screen.queryByRole("button", { name: "Взять данные КБ" })).not.toBeInTheDocument();
});
