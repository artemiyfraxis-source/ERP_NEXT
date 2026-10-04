import { describe, expect, it } from "vitest";
import type { ProductionOperation, ProductionOperationInput, ProductionTask } from "./api";
import { feedsText, isFinal, isFirst, isNetwork, previousStage, requiredNote, requiredOf, upstreamIds } from "./productionGraph";

const edge = (operation_id: string, position: number, per_unit = "1"): ProductionOperationInput => ({ operation_id, position, per_unit, transfer_batch: null });
const op = (position: number, stage: string, extra: Partial<ProductionOperation> = {}): ProductionOperation => ({
  id: `op-${position}`, position, code: "", stage, description: "", seconds_per_unit: "60", setup_seconds: "0", norm_seconds: 600,
  operator_ids: [], planned_start: null, planned_finish: null, assigned_by: null, ...extra,
});
const task = (operations: ProductionOperation[]) => ({ id: "t", quantity: 10, operations } as unknown as ProductionTask);

// Нарезка А (1) и Нарезка Б (2, по 2 на изделие) идут в сборку (3); сборка — финал.
const net = task([
  op(1, "Нарезка А", { inputs: [], first: true, final: false, required_quantity: "10" }),
  op(2, "Нарезка Б", { inputs: [], first: true, final: false, required_quantity: "20" }),
  op(3, "Сборка", { inputs: [edge("op-1", 1), edge("op-2", 2, "2")], first: false, final: true, required_quantity: "10" }),
]);
// Цепочка из трёх этапов так, как её отдаёт сервер: вход — предыдущий номер.
const chain = task([
  op(1, "Резка", { inputs: [], first: true, final: false, required_quantity: "10" }),
  op(2, "Обжим", { inputs: [edge("op-1", 1)], first: false, final: false, required_quantity: "10" }),
  op(3, "Сборка", { inputs: [edge("op-2", 2)], first: false, final: true, required_quantity: "10" }),
]);

describe("сетевой график на экранах цеха", () => {
  it("цепочка читается как раньше: без «из этапов», штуки — тираж, ОТК только у последнего", () => {
    expect(chain.operations.map((item) => feedsText(chain, item))).toEqual(["", "", ""]);
    expect(chain.operations.map((item) => requiredNote(chain, item))).toEqual(["", "", ""]);
    expect(chain.operations.map((item) => isFirst(item))).toEqual([true, false, false]);
    expect(chain.operations.map((item) => isFinal(chain, item))).toEqual([false, false, true]);
    expect(isNetwork(chain)).toBe(false);
    expect(previousStage(chain.operations[2])).toBe(2);
  });

  it("ответ без полей сети — та же цепочка по номерам", () => {
    const old = task([op(1, "Резка"), op(2, "Обжим"), op(3, "Сборка")]);
    expect(old.operations.map((item) => feedsText(old, item))).toEqual(["", "", ""]);
    expect(old.operations.map((item) => isFirst(item))).toEqual([true, false, false]);
    expect(old.operations.map((item) => isFinal(old, item))).toEqual([false, false, true]);
    expect(requiredOf(old, old.operations[0])).toBe(10);
    expect(previousStage(old.operations[1])).toBe(1);
    expect(upstreamIds(old, old.operations[2])).toBeNull();
    expect(isNetwork(old)).toBe(false);
  });

  it("сборка с двумя ветками называет входы и штуки на изделие, начало ветки — ничего", () => {
    expect(feedsText(net, net.operations[2])).toBe("из этапов 1 «Нарезка А», 2 «Нарезка Б» ×2");
    expect(feedsText(net, net.operations[0])).toBe("");
    expect(feedsText(net, net.operations[1])).toBe("");
    expect(isNetwork(net)).toBe(true);
    expect(net.operations.map((item) => isFirst(item))).toEqual([true, true, false]);
    expect(net.operations.map((item) => isFinal(net, item))).toEqual([false, false, true]);
  });

  it("один вход не из предыдущего номера или не по одному на изделие — тоже называется", () => {
    const skip = task([
      op(1, "Нарезка А", { inputs: [] }), op(2, "Нарезка Б", { inputs: [] }),
      op(3, "Монтаж А", { inputs: [edge("op-1", 1, "0.5")] }),
    ]);
    expect(feedsText(skip, skip.operations[2])).toBe("из этапа 1 «Нарезка А» ×0,5");
    const far = task([op(1, "Резка", { inputs: [] }), op(2, "Другая", { inputs: [] }), op(3, "Сборка", { inputs: [edge("op-1", 1)] })]);
    expect(feedsText(far, far.operations[2])).toBe("из этапа 1 «Резка»");
  });

  it("название входа берёт из ответа сервера, если оно есть, иначе из операций задания, иначе только номер", () => {
    const named = task([op(3, "Сборка", { inputs: [{ ...edge("op-1", 1), stage: "Нарезка А" }, edge("op-9", 2)] })]);
    expect(feedsText(named, named.operations[0])).toBe("из этапов 1 «Нарезка А», 2");
  });

  it("сколько сдаёт операция: поле сервера, а в тираже — ничего лишнего", () => {
    expect(requiredOf(net, net.operations[1])).toBe(20);
    expect(requiredNote(net, net.operations[1])).toBe(" · сдать 20 шт");
    expect(requiredNote(net, net.operations[2])).toBe("");
  });

  it("все операции выше по схеме: входы и их входы", () => {
    expect([...upstreamIds(net, net.operations[2])!].sort()).toEqual(["op-1", "op-2"]);
    expect([...upstreamIds(net, net.operations[0])!]).toEqual([]);
    expect([...upstreamIds(chain, chain.operations[2])!].sort()).toEqual(["op-1", "op-2"]);
  });
});
