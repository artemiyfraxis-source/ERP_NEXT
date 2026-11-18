import { afterEach, expect, it, vi } from "vitest";
import { ApiError, productionApi, type ProductionOperation, type ProductionTask } from "./api";
import type { CuttingGroup } from "./cuttingGroups";
import { markingApi, markingTaskSource } from "./markingApi";

afterEach(() => vi.restoreAllMocks());
const operation = (id: string, position: number, positions?: number[]) => ({ id, position, code: id,
  ...(positions ? { etk_ops: positions.map((position) => ({ position, code: `ETK-${position}`, stage: "Маркировка" })) } : {}),
}) as ProductionOperation;
const task = (operations: ProductionOperation[]) => ({ id: "task", version: 9, operations }) as ProductionTask;

it("uses all per-operation queue rows and prioritizes ETK positions over PF positions", async () => {
  vi.spyOn(productionApi, "mine").mockResolvedValue({ tasks: [task([operation("first", 1, [1, 2])]), task([operation("second", 2, [4, 5])]), task([operation("fourth", 4, [9, 10])])] });
  vi.spyOn(productionApi, "list");
  await expect(markingTaskSource("task", { position: 4 })).resolves.toEqual({ task_id: "task", operation_id: "second", version: 9 });
  expect(productionApi.list).not.toHaveBeenCalled();
});

it("resolves an instruction whose task is represented only by a grouped cutting member", async () => {
  vi.spyOn(productionApi, "mine").mockResolvedValue({ tasks: [], cutting_groups: [{ rows: [
    { task_id: "task", operation_id: "cutting", task_version: 12, membership_state: "active" },
    { task_id: "task", operation_id: "split", task_version: 7, membership_state: "split" },
  ] } as CuttingGroup] });
  vi.spyOn(productionApi, "operationEtk").mockResolvedValue({ focus: { position: 3, code: "CUT" } } as Awaited<ReturnType<typeof productionApi.operationEtk>>);
  vi.spyOn(productionApi, "list");
  await expect(markingTaskSource("task", { position: 3, code: "CUT" })).resolves.toEqual({ task_id: "task", operation_id: "cutting", version: 12 });
  expect(productionApi.operationEtk).toHaveBeenCalledExactlyOnceWith("task", "cutting");
  expect(productionApi.list).not.toHaveBeenCalled();
});

it("uses the production overview for a master without my-queue permission", async () => {
  vi.spyOn(productionApi, "mine").mockRejectedValue(new ApiError("Нет доступа", 403));
  vi.spyOn(productionApi, "list").mockResolvedValue({ tasks: [task([operation("master-op", 2)])], next_cursor: null });
  await expect(markingTaskSource("task", { position: 2 })).resolves.toEqual({ task_id: "task", operation_id: "master-op", version: 9 });
});

it("does not hide a failed queue read behind a second request or guess an ambiguous operation", async () => {
  vi.spyOn(productionApi, "mine").mockRejectedValueOnce(new TypeError("network")).mockResolvedValue({ tasks: [task([operation("one", 1, [3]), operation("two", 2, [3])])] });
  vi.spyOn(productionApi, "list");
  await expect(markingTaskSource("task", { position: 3 })).rejects.toThrow("network");
  expect(productionApi.list).not.toHaveBeenCalled();
  await expect(markingTaskSource("task", { position: 3 })).rejects.toThrow("конкретной операции");
});

it("sends versioned source selections and idempotency as JSON through the shared authenticated client", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ id: "batch" }), { status: 200 }));
  const request = { profile_id: "profile", profile_version: 4, source: { group_id: "group", version: 8 }, selected: ["one"], manifest_hash: "hash", idempotency_key: "retry-key" };
  await markingApi.enqueue(request);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe("/api/production/marking/batches");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual(request);
  expect(fetch.mock.calls[0][1]?.credentials).toBe("include");
});
