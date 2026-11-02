import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, shippingApi, type Shipment } from "./api";
import { Readiness, ShipmentDataWindow } from "./ShipmentData";

vi.mock("./ScanAgent", () => ({
  useScanAgent: () => ({ begin: async () => null, dialogs: () => null }),
  ScanDeviceLine: () => null,
}));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const source = { receiver: "Получатель", phone: "+7 900", shipping_point: "Москва", carrier: "СДЭК", terms: "ДОСТАВКА ТК", upd_type: "ЭДО", upd_number: "123",
  missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true, ready_to_ship: false, track_number: "" };

describe("Readiness", () => {
  it("shows one readiness result and the actual missing list without green field pills", () => {
    render(<Readiness source={source} gaps={["телефон получателя", "№ УПД"]} />);
    expect(screen.getByText("телефон получателя, № УПД")).toBeInTheDocument();
    expect(screen.getAllByText("Ждёт данных менеджера")).toHaveLength(1);
    expect(screen.queryByText("Получатель")).not.toBeInTheDocument();
  });
  it("does not ask our courier for a track when server readiness is true", () => {
    render(<Readiness source={{ ...source, carrier: "Курьер (наш)", track_required: false, ready_to_ship: true, missing_ship: [] }} gaps={[]} withTrack />);
    expect(screen.getByText("Готово к отгрузке")).toBeInTheDocument();
    expect(screen.queryByText("Ждёт трек от склада")).not.toBeInTheDocument();
  });
  it("manager data ready still asks the warehouse for a track instead of promising dispatch readiness", () => {
    render(<Readiness source={source} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
  it("retains real manager blockers and accepts full readiness only after the server reply", () => {
    const view = render(<Readiness source={source} gaps={["телефон получателя"]} />);
    expect(screen.getByText("Ждёт данных менеджера")).toHaveAttribute("title", "Не хватает: телефон получателя");
    view.rerender(<Readiness source={{ ...source, ready_to_ship: true, missing_ship: [], track_number: "TR-1" }} gaps={[]} />);
    expect(screen.getByText("Готово к отгрузке")).toBeInTheDocument();
  });
  it("does not claim dispatch ready when the server gate is false and there is another warehouse blocker", () => {
    render(<Readiness source={{ ...source, missing_ship: ["разрешение на отгрузку без УПД"] }} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
  });
  it("track-only gaps stay a warehouse step when the shared strip includes the track chip", () => {
    render(<Readiness source={source} gaps={["трек-номер"]} withTrack />);
    expect(screen.queryByText("Ждёт данных менеджера")).not.toBeInTheDocument();
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
  it("complete project data without server dispatch readiness only confirms that the data is entered", () => {
    const { ready_to_ship: _ready, missing_ship: _missing, ...project } = source;
    render(<Readiness source={project} gaps={[]} />);
    expect(screen.queryByText("Готово к отгрузке")).not.toBeInTheDocument();
    expect(screen.getByText("Данные внесены")).toBeInTheDocument();
  });
  it("explicit track-only gaps work even without the optional shipment fields", () => {
    const { ready_to_ship: _ready, missing_ship: _missing, ...project } = source;
    render(<Readiness source={project} gaps={["трек-номер"]} />);
    expect(screen.getByText("Ждёт трек от склада")).toBeInTheDocument();
  });
});

describe("ShipmentDataWindow read-only mode", () => {
  const shipment: Shipment = {
    id: "s1", number: "ОТГ-20261102-01", version: 4, created_at: "2026-11-02T09:00:00", status: "Упакована",
    customer: "ООО «ТСР»", receiver: "Иван Петров", shipping_point: "Москва, ул. Заводская, 1", contact: "",
    phone: "+7 900 111-22-33", terms: "ДОСТАВКА ТК", carrier: "СДЭК", deadline: "2026-11-03",
    upd_type: "ЭДО", upd_number: "518", upd_date: "2026-11-01", upd_url: "/api/shipping/files/f1",
    pl_number: "PL713К.4-1", dn_number: "DN713К.4-1", track_number: "", packed_at: "2026-11-02", shipped_at: null,
    manager: "Менеджер", note: "Позвонить заранее", tk_note: "", places: 1, weight: "", boxes: [], inherited: [],
    missing_data: [], missing_ship: ["трек-номер"], ready_for_track: true, ready_to_ship: false,
    lines: [{ id: "l1", good_id: "g1", project: "713К.4", item_index: "КБ2", article: "", name: "Жгут",
      requested: "500", picked: "500", shortage: "0", unit: "шт", grn: "ГП.26.0000001" }],
  };

  it("blocks every shipment mutation and has no save action", () => {
    const head = vi.spyOn(shippingApi, "head");
    const documents = vi.spyOn(shippingApi, "documents");
    const applyUpdScan = vi.spyOn(shippingApi, "applyUpdScan");
    const onSaved = vi.fn();
    render(<ShipmentDataWindow shipment={shipment} stage="packed" canEdit={false} close={() => undefined} onSaved={onSaved} />);

    for (const label of ["Адрес доставки", "Получатель", "Телефон получателя", "Комментарий для склада", "№ УПД"]) {
      expect(screen.getByLabelText(label)).toHaveAttribute("readonly");
    }
    for (const label of ["Тип доставки", "Перевозчик", "Тип УПД", "Дата УПД"]) {
      expect(screen.getByLabelText(label)).toBeDisabled();
    }
    expect(screen.getByRole("link", { name: "Файл ЭДО · открыть" })).toHaveAttribute("href", shipment.upd_url);
    expect(screen.queryByLabelText("Файл УПД")).not.toBeInTheDocument();
    for (const action of ["Сканировать", "Файл…", "Убрать файл", "Сохранить"]) {
      expect(screen.queryByRole("button", { name: action })).not.toBeInTheDocument();
    }

    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    expect(head).not.toHaveBeenCalled();
    expect(documents).not.toHaveBeenCalled();
    expect(applyUpdScan).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  const historical = { ...shipment, status: "Закрыта", shipped_at: "2026-10-25", track_number: "TRK-42" };
  function openCorrection() {
    fireEvent.click(screen.getByRole("button", { name: "Исправить историю" }));
    fireEvent.click(screen.getByLabelText("Исправить Телефон получателя"));
    fireEvent.click(screen.getByRole("button", { name: "Очистить Телефон получателя" }));
    fireEvent.change(screen.getByLabelText("Причина исправления"), { target: { value: "Ошибка переноса" } });
    fireEvent.change(screen.getByLabelText("Источник правильных данных"), { target: { value: "Накладная 42" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить исправление" }));
  }

  it.each([undefined, false])("denies correction by default and keeps closed fields read-only: %s", (canCorrectHistory) => {
    render(<ShipmentDataWindow shipment={historical} canCorrectHistory={canCorrectHistory} close={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Исправить историю" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Тип доставки")).toBeDisabled();
    expect(screen.getByLabelText("Тип УПД")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("previews and submits only explicitly changed historical fields with evidence and version", async () => {
    const correction = vi.spyOn(shippingApi, "correctHistory").mockResolvedValue({ shipment: { ...historical, phone: "", version: 5 } });
    const head = vi.spyOn(shippingApi, "head");
    const documents = vi.spyOn(shippingApi, "documents");
    const close = vi.fn();
    render(<ShipmentDataWindow shipment={historical} canCorrectHistory close={close} onSaved={vi.fn()} />);
    openCorrection();
    expect(screen.getByText(/ОТГ-20261102-01/)).toBeInTheDocument();
    expect(screen.getByText(/25.10.2026/)).toBeInTheDocument();
    expect(screen.getByText("Будет очищено")).toBeInTheDocument();
    expect(screen.getByText(historical.phone)).toBeInTheDocument();
    expect(correction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Записать исправление" }));
    await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(correction).toHaveBeenCalledWith(historical.id, { version: 4, reason: "Ошибка переноса", source: "Накладная 42", phone: "" });
    expect(head).not.toHaveBeenCalled();
    expect(documents).not.toHaveBeenCalled();
  });

  it("requires reloading and reviewing after a conflict without retrying automatically", async () => {
    const correction = vi.spyOn(shippingApi, "correctHistory").mockRejectedValue(new ApiError("Отгрузка изменилась", 409));
    const reload = vi.spyOn(shippingApi, "shipments").mockResolvedValue({ shipments: [{ ...historical, phone: "Другой телефон", version: 9 }] });
    render(<ShipmentDataWindow shipment={historical} canCorrectHistory close={vi.fn()} onSaved={vi.fn()} />);
    openCorrection();
    fireEvent.click(screen.getByRole("button", { name: "Записать исправление" }));
    expect(await screen.findByRole("button", { name: "Загрузить актуальную отправку" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Записать исправление" })).toBeDisabled();
    expect(correction).toHaveBeenCalledTimes(1);
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Загрузить актуальную отправку" }));
    await waitFor(() => expect(screen.getByLabelText("Новое значение: Телефон получателя")).toHaveValue("Другой телефон"));
    expect(screen.getByLabelText("Исправить Телефон получателя")).not.toBeChecked();
    expect(screen.queryByRole("button", { name: "Записать исправление" })).not.toBeInTheDocument();
    expect(correction).toHaveBeenCalledTimes(1);
  });

  it("keeps shipped UPD completion available without ordinary header changes", async () => {
    const head = vi.spyOn(shippingApi, "head");
    const documents = vi.spyOn(shippingApi, "documents").mockResolvedValue({ shipment: { ...historical, status: "Отгружена", version: 5 } });
    render(<ShipmentDataWindow shipment={{ ...historical, status: "Отгружена" }} close={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("Тип доставки")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("№ УПД"), { target: { value: "УПД-42" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(documents).toHaveBeenCalled());
    expect(head).not.toHaveBeenCalled();
    expect(documents.mock.calls[0][1]).toMatchObject({ upd_number: "УПД-42", track_number: "TRK-42" });
  });

  it("does not offer correction for active or cancelled shipments even with capability", () => {
    const view = render(<ShipmentDataWindow shipment={shipment} canCorrectHistory close={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Исправить историю" })).not.toBeInTheDocument();
    view.rerender(<ShipmentDataWindow shipment={{ ...shipment, status: "Отменена" }} canCorrectHistory close={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Исправить историю" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  });

  it("requires actual selected changes and nonblank evidence before preview", () => {
    render(<ShipmentDataWindow shipment={historical} canCorrectHistory close={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Исправить историю" }));
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("Выберите поля и внесите исправления");
    fireEvent.click(screen.getByLabelText("Исправить Телефон получателя"));
    fireEvent.change(screen.getByLabelText("Новое значение: Телефон получателя"), { target: { value: "new" } });
    fireEvent.change(screen.getByLabelText("Причина исправления"), { target: { value: " " } });
    fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent("Укажите причину");
    expect(screen.getByLabelText("Причина исправления")).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Записать исправление" })).not.toBeInTheDocument();
  });

  it("does not repeat a committed correction when refreshing the list fails", async () => {
    const correction = vi.spyOn(shippingApi, "correctHistory").mockResolvedValue({ shipment: { ...historical, version: 5, phone: "" } });
    render(<ShipmentDataWindow shipment={historical} canCorrectHistory close={vi.fn()} onSaved={vi.fn().mockRejectedValue(new Error("Нет связи"))} />);
    openCorrection();
    fireEvent.click(screen.getByRole("button", { name: "Записать исправление" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Исправление истории сохранено");
    expect(screen.queryByRole("button", { name: "Записать исправление" })).not.toBeInTheDocument();
    expect(correction).toHaveBeenCalledTimes(1);
  });
});
