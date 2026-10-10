import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useLatestSearch } from "./useLatestSearch";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

/** One controllable answer per query: the test decides in which order they arrive. */
function setup() {
  const answers: Record<string, ReturnType<typeof deferred<string>>[]> = {};
  const load = (query: string) => {
    const answer = deferred<string>();
    (answers[query] ||= []).push(answer);
    return answer.promise;
  };
  const view = renderHook(({ query }) => useLatestSearch(() => load(query), [query], { delay: 250 }), { initialProps: { query: "A" } });
  const type = async (query: string) => {
    view.rerender({ query });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  };
  return { answers, view, type };
}

it("keeps the answer to the last query when the slow answer to an earlier one arrives later (A, B, A)", async () => {
  const { answers, view, type } = setup();
  await act(async () => { await vi.advanceTimersByTimeAsync(250); }); // A is sent
  await type("B");                                                     // B is sent while A is still in flight
  await type("A");                                                     // back to A: a second request for A

  const [slowA, lateA] = answers.A;
  await act(async () => { answers.B[0].resolve("результат B"); });
  expect(view.result.current.data).toBeNull();                         // B is no longer the current query
  await act(async () => { lateA.resolve("результат A (новый)"); });
  await act(async () => { slowA.resolve("результат A (старый)"); });
  expect(view.result.current).toEqual({ data: "результат A (новый)", loading: false, error: "" });
});

it("B wins over a late A: the slow answer to the old query is ignored", async () => {
  const { answers, view, type } = setup();
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  await type("B");

  await act(async () => { answers.B[0].resolve("результат B"); });
  expect(view.result.current).toEqual({ data: "результат B", loading: false, error: "" });
  await act(async () => { answers.A[0].resolve("результат A"); });
  expect(view.result.current.data).toBe("результат B");
});

it("is loading until the answer to the current query arrives, even if an older one came first", async () => {
  const { answers, view, type } = setup();
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  await type("B");
  expect(view.result.current.loading).toBe(true);
  await act(async () => { answers.A[0].resolve("результат A"); });
  expect(view.result.current).toEqual({ data: null, loading: true, error: "" });
});

it("ignores a late answer and a late failure after unmount or a newer query", async () => {
  const { answers, view, type } = setup();
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  await type("B");
  await act(async () => { answers.A[0].reject(new Error("устарело")); });
  expect(view.result.current.error).toBe("");
  view.unmount();
  await act(async () => { answers.B[0].resolve("после закрытия"); });
});

it("does not send a request for a query typed over before the delay ran out", async () => {
  const { answers, view } = setup();
  view.rerender({ query: "AB" });
  view.rerender({ query: "ABC" });
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(Object.keys(answers)).toEqual(["ABC"]);
});
