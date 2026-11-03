import { describe, expect, it } from "vitest";
import { dispatchBoard, kbKey, shippedOnlyRows, stagePieces } from "./intakeStages";
import { dataRows } from "./ShipmentDataPage";
import type { DispatchShipment, IntakeRow } from "./productionIntakeApi";
import type { Shipment } from "./api";

const now = new Date("2026-11-03T12:00:00Z");
const row = (project: string, stage: string, accepted = 0, packed = 0) => ({
  project, item_index: "КБ1", name: "Кабель", unit: "шт", customer: "Заказчик", stage, project_stage: stage,
  needed: "100", accepted: String(accepted), packed: String(packed), sent: "0", remaining: stage === "В работе" ? "100" : "0", delivered: "0",
  good_id: "good", good_version: 1, tasks: [], acts: [],
} as unknown as IntakeRow);
const ship = (id: string, project: string, status = "Отгружена", date: string | null = "2026-10-25", picked = "10") => ({
  id, number: id, customer: "Заказчик", status, shipped_at: date, ready_to_ship: false,
  lines: [{ id: `${id}-line`, project, item_index: "КБ1", name: "Кабель", unit: "шт", picked, requested: "100", grn: null }],
} as DispatchShipment);

describe("общие счётчики КБ", () => {
  it("reconciles 125 intake KB versus 135 detail rows, preserving partial stages and the packed orphan", () => {
    const intake = [
      ...Array.from({ length: 77 }, (_, i) => row(`О-${i}`, "Новый")),
      ...Array.from({ length: 17 }, (_, i) => row(`П-${i}`, "В работе")),
      ...Array.from({ length: 8 }, (_, i) => row(`А-${i}`, "Принято складом", 100)),
      row("У-1", "Упаковано", 100, 100),
    ];
    const partial = ["П-0", "П-1", "П-2", "А-0", "А-1"];
    const shipments = [...partial, ...Array.from({ length: 22 }, (_, i) => `И-${i}`)].map((p, i) => ship(`s${i}`, p));
    shipments.push(ship("repeat1", "И-0"), ship("repeat2", "П-2"), ship("repeat3", "П-2"), ship("repeat4", "И-1"),
      ship("packed", "У-1", "Упакована", null, "100"), ship("orphan", "У-2", "Упакована", null, "38"));
    expect([...intake, ...shippedOnlyRows(intake, shipments, now)]).toHaveLength(125);
    expect(dataRows(shipments as unknown as Shipment[], intake)).toHaveLength(135);
    const board = dispatchBoard(intake, shipments, now);
    expect(board.counts).toEqual({ waiting: 77, production: 17, ready: 0, accepted: 8, packed: 2, to_ship: 0, shipped: 27, all: 126 });
    expect(board.rows.find((r) => r.project === "У-2")).toMatchObject({ needed: "0", accepted: "38", packed: "38", remaining: "0", good_id: null });
    expect(board.rows.find((r) => r.project === "А-0")).toBe(intake[94]);
    expect(intake[94].accepted).toBe("100");
  });

  it("keeps old/unknown/future shipments in All, but dates only recent quantities and includes boundary day", () => {
    const intake = [row("П", "Принято складом", 100, 90)];
    intake[0].sent = "90";
    const shipments = [ship("old", "П", "Отгружена", "2026-10-03", "50"), ship("edge", "П", "Закрыта", "2026-10-04", "20"),
      ship("recent", "П", "Отгружена", "2026-11-03", "20"), ship("unknown", "Другой", "Закрыта", null), ship("future", "Будущий", "Отгружена", "2026-11-04")];
    const board = dispatchBoard(intake, shipments, now);
    expect(board.counts).toMatchObject({ all: 3, accepted: 1, shipped: 1 });
    expect(board.pieces.get(kbKey(intake[0]))?.shipped).toBe(40);
    expect(stagePieces(intake[0], shipments).shipped).toBe(90);
  });

  it("normalizes KB keys and never manufactures free stock or production for incomplete/cancelled orphan requests", () => {
    const shipments = [ship("active", "п", "Собрана", null, "4"), ship("cancel", " П ", "Отменена", null, "100"), ship("empty", "Другой", "Новая", null, "0")];
    const board = dispatchBoard([], shipments, now);
    expect(board.counts).toEqual({ waiting: 0, production: 0, ready: 0, accepted: 1, packed: 0, to_ship: 0, shipped: 0, all: 2 });
    expect(board.rows[0]).toMatchObject({ needed: "0", accepted: "4", packed: "4", sent: "0", remaining: "0", good_id: null, pack_decided: false });
  });
});
