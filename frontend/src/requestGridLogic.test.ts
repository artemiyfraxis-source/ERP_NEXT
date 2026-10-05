import { describe, expect, it } from "vitest";
import {
  GRID_LIMIT,
  applyPaste,
  emptyCommon,
  emptyDraft,
  hasHeaderRow,
  isBlankRow,
  payloadOf,
  positionsWord,
  submitLabel,
  validateRow,
} from "./requestGridLogic";

const seed = { ...emptyCommon(), delivery_point: "Москва", purpose: "RND" };

describe("requestGridLogic", () => {
  it("declines positions and the send button by number", () => {
    expect([1, 2, 5, 11, 21, 100].map(positionsWord)).toEqual(["1 позиция", "2 позиции", "5 позиций", "11 позиций", "21 позиция", "100 позиций"]);
    expect([1, 3, 12, 21, 100].map(submitLabel)).toEqual([
      "Оформить 1 позицию", "Оформить 3 позиции", "Оформить 12 позиций", "Оформить 21 позицию", "Оформить 100 позиций",
    ]);
  });

  it("tells a block with a header row from a plain block", () => {
    expect(hasHeaderRow("Наименование\tКол-во\nA\t1")).toBe(true);
    expect(hasHeaderRow("Разъём\tКомплектующие\nКабель\tКомплектующие")).toBe(false);
  });

  it("pastes a plain block from the focused cell, keeps what an empty pasted cell leaves, reads dates", () => {
    const start = [{ ...emptyDraft(seed), name: "Старое", unit: "м" }];
    const result = applyPaste(start, 0, "quantity", "5,5\t\tОП\tДа\t05.11.2026\n7", emptyDraft(seed));
    expect("error" in result).toBe(false);
    if ("error" in result) return;
    expect(result.positions).toHaveLength(2);
    expect(result.positions[0]).toMatchObject({ name: "Старое", quantity: "5.5", unit: "м", category: "ОП", quality_document: "Да", planned_date: "2026-11-05" });
    expect(result.positions[1]).toMatchObject({ quantity: "7", delivery_point: "Москва" });
  });

  it("a block with headers goes after the filled rows and reuses blank ones", () => {
    const filled = { ...emptyDraft(seed), name: "Есть" };
    const result = applyPaste([filled, emptyDraft(seed)], 1, "name", "Наименование\tКоличество\tЦвет\nНовая\t3\tкрасный", emptyDraft(seed));
    if ("error" in result) throw new Error(result.error);
    expect(result.positions.map((item) => item.name)).toEqual(["Есть", "Новая"]);
    expect(result.positions[1].quantity).toBe("3");
    expect(result.unknown).toEqual(["Цвет"]);
  });

  it("never grows past the 100-position limit", () => {
    const text = Array.from({ length: 130 }, (_, i) => `П${i}`).join("\n");
    const result = applyPaste([emptyDraft(seed)], 0, "name", text, emptyDraft(seed));
    if ("error" in result) throw new Error(result.error);
    expect(result.positions).toHaveLength(GRID_LIMIT);
    expect(result.dropped).toBe(30);
  });

  it("validates cell by cell and skips blank rows", () => {
    expect(isBlankRow(emptyDraft(seed))).toBe(true);
    const bad = { ...emptyDraft(seed), name: "X", quantity: "0", unit: "", category: "Левая", price: "abc", planned_date: "", quality_document: "", item_type: "", delivery_point: "", procurement_deadline: "" };
    // тип, док. качества, точка и даты не обязательны: пустые добирает сервер
    expect(Object.keys(validateRow(bad, ["Общепром", "ИП", "ОП"])).sort()).toEqual(["category", "price", "quantity", "unit"]);
    const ok = { ...emptyDraft(seed), name: "X", planned_date: "2026-11-05" };
    expect(validateRow(ok, ["Общепром"])).toEqual({});
  });

  it("payload drops empty fields and normalizes decimal commas", () => {
    expect(payloadOf({ ...emptyDraft(seed), name: "X", quantity: "1,5", price: "" })).toMatchObject({ name: "X", quantity: "1.5" });
    expect(payloadOf({ ...emptyDraft(seed), name: "X" })).not.toHaveProperty("price");
  });
});
