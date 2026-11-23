import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useCollapsedGroups } from "./useCollapsedGroups";

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

it.each(["invalid JSON", "{}", "null", "42"])("defaults to expanded for invalid saved preferences: %s", value => {
  window.localStorage.setItem("groups:a", value);
  const { result } = renderHook(() => useCollapsedGroups("groups:a"));
  expect([...result.current.collapsed]).toEqual([]);
});

it("ignores invalid entries and immediately switches to the next viewer's preferences", () => {
  window.localStorage.setItem("groups:a", '["stock", null, false, 1]');
  window.localStorage.setItem("groups:b", '["supply"]');
  const { result, rerender } = renderHook(({ viewer }) => useCollapsedGroups(`groups:${viewer}`), { initialProps: { viewer: "a" } });
  expect([...result.current.collapsed]).toEqual(["stock"]);
  act(() => result.current.toggle("prod"));
  rerender({ viewer: "b" });
  expect([...result.current.collapsed]).toEqual(["supply"]);
  act(() => result.current.toggle("supply"));
  expect(JSON.parse(window.localStorage.getItem("groups:a")!)).toEqual(["stock", "prod"]);
  expect(JSON.parse(window.localStorage.getItem("groups:b")!)).toEqual([]);
});

it("keeps toggles usable when localStorage reads and writes throw", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  const { result } = renderHook(() => useCollapsedGroups("groups:a"));
  expect([...result.current.collapsed]).toEqual([]);
  act(() => result.current.toggle("supply"));
  expect([...result.current.collapsed]).toEqual(["supply"]);
  act(() => result.current.toggle("supply"));
  expect([...result.current.collapsed]).toEqual([]);
});
