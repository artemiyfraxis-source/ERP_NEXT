import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectAddressee, Shipment } from "./api";
import { dataGaps, grnText, IntakePositionsWindow, stageGaps } from "./IntakePositionsWindow";
import { forgetPersonalViews } from "./personalViews";
import type { IntakeRow } from "./productionIntakeApi";
import { setRightsPrincipal } from "./rights";

const take = (project: string, over: Partial<IntakeRow> = {}) => ({
  project, customer: `Заказчик ${project}`, deadline: "2026-12-02", item_index: "КБ1", name: "Жгут", unit: "шт", stage: "В работе", project_stage: "В работе",
  needed: "10", launched: "10", delivered: "0", accepted: "0", packed: "0", sent: "0", remaining: "10", good_id: null, good_version: null, grn: null, location: "", tasks: [], ...over,
}) as unknown as IntakeRow;
const shipment = (over: Record<string, unknown> = {}) => ({
  id: "s1", number: "ОТГ-20261124-01", version: 3, created_at: "2026-11-05T09:00:00", status: "Упакована", customer: "ООО «НТЦ Альбатрос»", receiver: "", shipping_point: "",
  contact: "", phone: "", terms: null, carrier: "", deadline: "2026-12-02", upd_type: null, upd_number: "", upd_date: null, upd_url: "", pl_number: "PL828К.-1", dn_number: "DN828К.-1",
  track_number: "", packed_at: "2026-11-05", shipped_at: null, manager: "", note: "", tk_note: "", places: 1, weight: "", boxes: [], inherited: [],
  missing_data: ["способ доставки", "адрес"], missing_ship: ["способ доставки", "адрес", "тип УПД"],
  lines: [{ id: "l1", good_id: "g1", project: "828К.", item_index: "КБ3", article: "", name: "Жгут для ТМЛР", requested: "45", picked: "45", shortage: "0", unit: "шт", grn: "ГП.26.0000126" }], ...over,
}) as unknown as Shipment;
const complete = shipment({ id: "s2", pl_number: "PL711К.3-1", dn_number: "DN711К.3-1", terms: "САМОВЫВОЗ", carrier: "Самовывоз", phone: "+7 900", receiver: "АО «Радиоком»", upd_type: "ЭДО", upd_number: "5",
  missing_data: [], missing_ship: [], lines: [{ id: "l2", good_id: "g2", project: "711К.3", item_index: "КБ1", article: "", name: "Кабель", requested: "5", picked: "5", shortage: "0", unit: "шт", grn: "" }] });
const cancelled = shipment({ id: "s3", status: "Отменена", pl_number: "PL999К.-1", lines: [{ id: "l3", good_id: "g3", project: "999К.", item_index: "КБ9", article: "", name: "Отменённая", requested: "1", picked: "0", shortage: "1", unit: "шт", grn: "" }] });

const props = (over: Record<string, unknown> = {}) => ({
  shipments: [shipment(), complete, cancelled], intake: [take("600К.1")],
  addressees: [{ project: "600К.1", terms: null, carrier: "", dispatch_gaps: ["условия доставки", "телефон получателя"] }] as unknown as ProjectAddressee[],
  itemAddressees: {}, forecasts: { "600К.1": { ready: "2026-12-30", status: "не успевает", slack_days: -3 } },
  canEdit: true, onOpen: vi.fn(), onEditDelivery: vi.fn(), close: vi.fn(), ...over,
});

beforeEach(() => { window.localStorage.clear(); forgetPersonalViews(); setRightsPrincipal(null); });
afterEach(() => { cleanup(); setRightsPrincipal(null); vi.restoreAllMocks(); });

describe("«Все позиции»", () => {
  it("одна таблица со строками КБ проектов и отправок, без «ОТГ» и без отменённых", () => {
    render(<IntakePositionsWindow {...props()} />);
    const dialog = within(screen.getByRole("dialog", { name: "Все позиции" }));
    expect(dialog.getAllByRole("table")).toHaveLength(1);
    expect(dialog.getByRole("button", { name: "Открыть 600К.1 КБ1" })).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Открыть 828К. КБ3" })).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Открыть 711К.3 КБ1" })).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: "Открыть 999К. КБ9" })).toBeNull();
    expect(dialog.getByText("PL711К.3-1")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/ОТГ-\d/);
    // Колонки бывших двух экранов: этап, срок, прогноз, доставка, УПД, документы; «Excel» есть.
    for (const title of ["Этап позиции", "Срок", "Прогноз готовности", "Способ доставки", "№ УПД", "PL", "DN"]) {
      expect(dialog.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(dialog.getByRole("button", { name: /Excel/ })).toBeInTheDocument();
  });

  it("прогноз готовности — красным, если цех не успевает", () => {
    render(<IntakePositionsWindow {...props()} />);
    expect(screen.getByText("30.12.2026")).toHaveClass("tone-danger");
  });

  it("«Только с пробелами» оставляет строки, которым чего-то не хватает", () => {
    render(<IntakePositionsWindow {...props()} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Только с пробелами" }));
    expect(screen.getByRole("button", { name: "Открыть 600К.1 КБ1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Открыть 828К. КБ3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Открыть 711К.3 КБ1" })).toBeNull();
  });

  it("«Изменить»: у отправки открывает её, у КБ проекта с доставкой — правку доставки; без права — «Открыть»", () => {
    const onOpen = vi.fn(), onEditDelivery = vi.fn();
    const item = { item_index: "КБ1", effective: {} };
    render(<IntakePositionsWindow {...props({ onOpen, onEditDelivery, itemAddressees: { "600К.1": [item] } })} />);
    fireEvent.click(screen.getByRole("button", { name: "Открыть 828К. КБ3" }));
    expect(onOpen.mock.calls[0][0].shipment.id).toBe("s1");
    fireEvent.click(screen.getByRole("button", { name: "Открыть 600К.1 КБ1" }));
    expect(onEditDelivery).toHaveBeenCalledWith("600К.1", [item]);
    cleanup();
    render(<IntakePositionsWindow {...props({ canEdit: false })} />);
    expect(screen.getAllByRole("button", { name: /^Открыть / })[0]).toHaveTextContent("Открыть");
  });
});

describe("помощники", () => {
  it("dataGaps: уехавшей отправке «не хватает» не считается; stageGaps прячет УПД на ранних этапах", () => {
    expect(dataGaps(shipment())).toEqual(["способ доставки", "адрес"]);
    expect(dataGaps(shipment({ status: "Отгружена" }))).toEqual([]);
    expect(stageGaps("production", ["адрес", "№ УПД"])).toEqual(["адрес"]);
    expect(stageGaps("accepted", ["адрес", "№ УПД"])).toEqual(["адрес", "№ УПД"]);
  });
  it("grnText: ГРН строки отправки; без партии — ГРН строки", () => {
    const row = { id: "s1:l1", shipment: shipment(), grn: "", project: "828К.", item_index: "КБ3" } as never;
    expect(grnText(row)).toBe("ГП.26.0000126");
  });
});
