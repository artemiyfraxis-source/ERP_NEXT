import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { shippingApi, type Shipment } from "./api";
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
});
