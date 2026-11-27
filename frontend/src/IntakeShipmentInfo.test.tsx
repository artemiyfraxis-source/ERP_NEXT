import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IntakeShipmentInfo } from "./IntakeShipmentInfo";
import type { DispatchShipment } from "./productionIntakeApi";

const base = {
  id: "s1", number: "ОТГ-1", version: 3, created_at: "2026-11-20", status: "Упакована", customer: "Заказчик", receiver: "Организация",
  shipping_point: "Москва", contact: "", phone: "+7 900", terms: "ДОСТАВКА ТК", carrier: "СДЭК", deadline: null,
  upd_type: "БУМАГА", upd_number: "У-1", upd_date: null, upd_url: "", pl_number: "PL1", dn_number: "DN1", track_number: "TRK-1",
  packed_at: "2026-11-20", shipped_at: null, manager: "", note: "", tk_note: "", places: 1, weight: "", boxes: [], lines: [], inherited: [],
  missing_data: [], missing_ship: ["файл УПД"],
} as unknown as DispatchShipment;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
let calls: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? "{}"));
    calls.push({ url: String(input), method: init?.method ?? "GET", body });
    if (String(input).endsWith("/upd-files")) return json({ shipment: { ...base, version: 4, upd_url: "/api/shipping/files/f9" } });
    if (String(input).endsWith("/tk-note")) return json({ shipment: { ...base, version: 4, tk_note: body.tk_note } });
    if (String(input).endsWith("/head")) return json({ shipment: { ...base, ...body, version: 4 } });
    return json({ shipment: { ...base, ...body, version: Number(body.version) + 1 } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const show = (shipment: DispatchShipment, onShipment = vi.fn(), canHistory = false, onConflict = vi.fn()) => render(<IntakeShipmentInfo shipment={shipment} gaps={shipment.missing_ship}
  rights={{ canEdit: true, canHistory }} pl="PL1" dn="DN1" onShipment={onShipment} onHistory={vi.fn()} onConflict={onConflict} />);

describe("IntakeShipmentInfo", () => {
  it("documents: upload the УПД file with the version; the screen gets the shipment from the answer", async () => {
    const onShipment = vi.fn();
    show(base, onShipment);
    const file = screen.getByText("файл не приложен");
    expect(file).toHaveClass("muted");
    const input = screen.getByLabelText("Файл УПД ОТГ-1") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["%PDF"], "upd.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(onShipment).toHaveBeenCalledWith(expect.objectContaining({ upd_url: "/api/shipping/files/f9", version: 4 })));
    expect(calls[0]).toMatchObject({ url: "/api/shipping/upd-files", method: "POST", body: { shipment_id: "s1", version: 3, file_name: "upd.pdf" } });
  });

  it("removing the file is confirmed and goes through the documents save with every value", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    show({ ...base, upd_url: "/api/shipping/files/f1" });
    expect(screen.getByRole("link", { name: "Открыть файл УПД ОТГ-1" })).toHaveAttribute("href", "/api/shipping/files/f1");
    fireEvent.click(screen.getByRole("button", { name: "Убрать файл УПД ОТГ-1" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/shipping/shipments/s1/documents", method: "PUT",
      body: { version: 3, upd_type: "БУМАГА", upd_number: "У-1", upd_date: null, upd_url: "", track_number: "TRK-1" } });
  });

  it("section «Документы»: № УПД saved with the track resent; «Получатель» saved through the head", async () => {
    show(base);
    const documents = within(screen.getByRole("region", { name: "Документы" }));
    fireEvent.click(documents.getByRole("button", { name: "Редактировать: Документы" }));
    fireEvent.change(documents.getByRole("textbox", { name: "№ УПД" }), { target: { value: "У-2" } });
    expect(documents.getByText("● не сохранено")).toBeInTheDocument();
    fireEvent.click(documents.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ version: 3, upd_type: "БУМАГА", upd_number: "У-2", upd_date: null, upd_url: "", track_number: "TRK-1" });
    const recipient = within(screen.getByRole("region", { name: "Получатель" }));
    fireEvent.click(recipient.getByRole("button", { name: "Изменить: Получатель" }));
    fireEvent.change(recipient.getByRole("textbox", { name: "Получатель" }), { target: { value: "Петров" } });
    fireEvent.keyDown(recipient.getByRole("textbox", { name: "Получатель" }), { key: "Enter" });
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1]).toMatchObject({ url: "/api/shipping/shipments/s1/head", body: { version: 3, contact: "Петров" } });
  });

  it("carrier comment is saved by its own request; copy buttons for track, phone and address", async () => {
    show(base);
    expect(screen.getByRole("button", { name: "Скопировать трек-номер" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать телефон" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скопировать адрес" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Для перевозчика" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Для перевозчика" }), { target: { value: "Звонить за час" } });
    fireEvent.blur(screen.getByRole("textbox", { name: "Для перевозчика" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ url: "/api/shipping/shipments/s1/tk-note", method: "PUT", body: { version: 3, tk_note: "Звонить за час" } });
  });

  it("shipped: the head is read-only with «Исправить историю…» for the admin; documents stay editable, «Ждём» is not editable", () => {
    show({ ...base, status: "Отгружена", shipped_at: "2026-11-22" }, vi.fn(), true);
    expect(screen.queryByRole("button", { name: "Изменить: Получатель" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить: Трек-номер" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить: Для перевозчика" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Изменить: № УПД" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Исправить историю ОТГ-1" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Изменить: Ждём" })).not.toBeInTheDocument();
    expect(screen.getByText("22.11.2026")).toBeInTheDocument();
  });

  it("409: the screen reloads the shipment and the field says «Отгрузка изменилась — данные обновлены, повторите»", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Отгрузка изменилась после загрузки формы" }, 409)));
    const onConflict = vi.fn().mockResolvedValue(undefined);
    const onShipment = vi.fn();
    show(base, onShipment, false, onConflict);
    fireEvent.click(screen.getByRole("button", { name: "Изменить: Получатель" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Получатель" }), { target: { value: "Петров" } });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Получатель" }), { key: "Enter" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Отгрузка изменилась — данные обновлены, повторите");
    expect(onConflict).toHaveBeenCalledTimes(1);
    expect(onShipment).not.toHaveBeenCalled();
  });

  it("empty values are a calm «—», pickup hides the carrier and the address", () => {
    show({ ...base, terms: "САМОВЫВОЗ", carrier: "Самовывоз", phone: "", contact: "", shipping_point: "Склад", track_number: "", track_required: false, missing_ship: [] });
    const recipient = screen.getByRole("region", { name: "Получатель" });
    for (const label of ["Получатель", "Телефон", "Адрес"]) expect(recipient.querySelector(`[data-label="${label}"]`)).toHaveTextContent("—");
    expect(screen.getByRole("region", { name: "Доставка" }).querySelector('[data-label="ТК"]')).toHaveTextContent("—");
    expect(document.querySelector(".tone-danger, .tone-warn")).toBeNull();
  });
});
