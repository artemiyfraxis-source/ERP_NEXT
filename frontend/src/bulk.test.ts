import { expect, it } from "vitest";
import { runInPool, selectRange } from "./bulk";

it("selects one row, or with Shift the whole range from the previous click in screen order", () => {
  const order = ["a", "b", "c", "d", "e"];
  expect([...selectRange(order, new Set(), null, "c", true, true)]).toEqual(["c"]);
  expect([...selectRange(order, new Set(["a"]), "a", "d", true, true)].sort()).toEqual(["a", "b", "c", "d"]);
  expect([...selectRange(order, new Set(order), "e", "c", false, true)].sort()).toEqual(["a", "b"]);
  expect([...selectRange(order, new Set(["a"]), "a", "d", true, false)].sort()).toEqual(["a", "d"]);
});

it("runs at most four requests at once, reports progress and returns every failure in row order", async () => {
  let running = 0;
  let peak = 0;
  const progress: number[] = [];
  const items = Array.from({ length: 10 }, (_, index) => index);
  const failures = await runInPool(items, async (item) => {
    running += 1;
    peak = Math.max(peak, running);
    await new Promise((resolve) => setTimeout(resolve, (10 - item) * 2));
    running -= 1;
    if (item % 3 === 0) throw new Error(`отказ ${item}`);
  }, (done) => progress.push(done));

  expect(peak).toBe(4);
  expect(progress).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  expect(failures.map(({ item, error }) => [item, (error as Error).message])).toEqual([
    [0, "отказ 0"], [3, "отказ 3"], [6, "отказ 6"], [9, "отказ 9"],
  ]);
});
