import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { identityApi } from "./api";
import { forgetPersonalViews, loadPersonalViews, resetPersonalView, SAVE_DELAY, savePersonalView, setPersonalViewsUser } from "./personalViews";

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views: {} });
  vi.spyOn(identityApi, "saveUiSetting").mockImplementation(async (view, value) => ({ view, value }));
  vi.spyOn(identityApi, "resetUiSetting").mockResolvedValue(undefined);
});
afterEach(() => { forgetPersonalViews(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("cancels a pending save before the auth cookie changes and rejects calls from an old screen", async () => {
  setPersonalViewsUser("a");
  savePersonalView("plan", { hidden: ["customer"] }, SAVE_DELAY, "a");
  setPersonalViewsUser(null);
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).not.toHaveBeenCalled();
  setPersonalViewsUser("b");
  savePersonalView("plan", { hidden: ["comment"] }, SAVE_DELAY, "a");
  expect(await loadPersonalViews("a")).toBeNull();
  expect(identityApi.uiSettings).not.toHaveBeenCalled();
  savePersonalView("plan", { hidden: [] }, SAVE_DELAY, "b");
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).toHaveBeenCalledExactlyOnceWith("plan", { hidden: [] });
});

it("does not reuse a previous account's read, or let its late failure evict the current read", async () => {
  let rejectA!: (reason: Error) => void;
  const pendingA = new Promise<Awaited<ReturnType<typeof identityApi.uiSettings>>>((_, reject) => { rejectA = reject; });
  vi.mocked(identityApi.uiSettings).mockReturnValueOnce(pendingA).mockResolvedValue({ views: { plan: { order: ["b"] } } });
  setPersonalViewsUser("a");
  const a = loadPersonalViews("a");
  setPersonalViewsUser("b");
  const b = loadPersonalViews("b");
  expect(await b).toEqual({ plan: { order: ["b"] } });
  rejectA(new Error("old offline"));
  expect(await a).toBeNull();
  expect(await loadPersonalViews("b")).toEqual({ plan: { order: ["b"] } });
  expect(identityApi.uiSettings).toHaveBeenCalledTimes(2);
});

it("ignores a late successful A load even after signing into A again", async () => {
  let resolveA!: (value: Awaited<ReturnType<typeof identityApi.uiSettings>>) => void;
  const pending = new Promise<Awaited<ReturnType<typeof identityApi.uiSettings>>>((resolve) => { resolveA = resolve; });
  vi.mocked(identityApi.uiSettings).mockReturnValueOnce(pending).mockResolvedValue({ views: { plan: { order: ["new"] } } });
  setPersonalViewsUser("a");
  const old = loadPersonalViews("a");
  setPersonalViewsUser(null);
  setPersonalViewsUser("a");
  expect(await loadPersonalViews("a")).toEqual({ plan: { order: ["new"] } });
  resolveA({ views: { plan: { order: ["old"] } } });
  expect(await old).toBeNull();
  expect(await loadPersonalViews("a")).toEqual({ plan: { order: ["new"] } });
});

it("keeps existing callers compatible, debounces, and retries a failed save on the next edit", async () => {
  vi.mocked(identityApi.saveUiSetting).mockRejectedValueOnce(new Error("offline"));
  await loadPersonalViews();
  savePersonalView("stock", { order: ["name"] });
  savePersonalView("stock", { order: ["name", "grn"] });
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).toHaveBeenCalledExactlyOnceWith("stock", { order: ["name", "grn"] });
  savePersonalView("stock", { order: ["name", "grn"] });
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).toHaveBeenCalledTimes(2);
  savePersonalView("stock", { order: ["name", "grn"] });
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).toHaveBeenCalledTimes(2);
  resetPersonalView("stock");
  expect(identityApi.resetUiSetting).toHaveBeenCalledWith("stock");
});

it("merges untouched server views but preserves edits and resets made while the initial GET was pending", async () => {
  let resolve!: (value: Awaited<ReturnType<typeof identityApi.uiSettings>>) => void;
  vi.mocked(identityApi.uiSettings).mockReturnValue(new Promise((done) => { resolve = done; }));
  const loading = loadPersonalViews();
  savePersonalView("plan", { order: ["new"] });
  resetPersonalView("stock");
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  resolve({ views: { plan: { order: ["old"] }, stock: { order: ["deleted"] }, untouched: { order: ["server"] } } });
  expect(await loading).toEqual({ plan: { order: ["new"] }, untouched: { order: ["server"] } });
  savePersonalView("plan", { order: ["new"] });
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.saveUiSetting).toHaveBeenCalledOnce();
});

it("does not send or read settings while the session is suspended", async () => {
  setPersonalViewsUser(null);
  expect(await loadPersonalViews()).toBeNull();
  savePersonalView("stock", { order: [] });
  resetPersonalView("stock");
  await vi.advanceTimersByTimeAsync(SAVE_DELAY);
  expect(identityApi.uiSettings).not.toHaveBeenCalled();
  expect(identityApi.saveUiSetting).not.toHaveBeenCalled();
  expect(identityApi.resetUiSetting).not.toHaveBeenCalled();
});

it("serializes an in-flight PUT before reset DELETE so a fresh read stays reset", async () => {
  setPersonalViewsUser("a");
  let finishPut!: () => void;
  const server: Record<string, unknown> = {};
  vi.mocked(identityApi.saveUiSetting).mockImplementationOnce(async (view, value) => {
    await new Promise<void>((resolve) => { finishPut = resolve; });
    server[view] = value;
    return { view, value };
  });
  vi.mocked(identityApi.resetUiSetting).mockImplementation(async (view) => { delete server[view]; });
  vi.mocked(identityApi.uiSettings).mockImplementation(async () => ({ views: { ...server } }));
  savePersonalView("scan.device", { agent_id: "pc-a", scanner_id: "s-a" }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  resetPersonalView("scan.device");
  expect(identityApi.resetUiSetting).not.toHaveBeenCalled();
  finishPut(); await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.resetUiSetting).toHaveBeenCalledExactlyOnceWith("scan.device");
  forgetPersonalViews(); setPersonalViewsUser("a");
  expect(await loadPersonalViews("a")).toEqual({});
});

it("persists two choices in order even when the first PUT is delayed", async () => {
  setPersonalViewsUser("a");
  let finishFirst!: () => void;
  const server: Record<string, unknown> = {};
  vi.mocked(identityApi.saveUiSetting).mockImplementation(async (view, value) => {
    if (!finishFirst) await new Promise<void>((resolve) => { finishFirst = resolve; });
    server[view] = value;
    return { view, value };
  });
  savePersonalView("scan.device", { scanner: "first" }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  savePersonalView("scan.device", { scanner: "second" }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.saveUiSetting).toHaveBeenCalledTimes(1);
  finishFirst(); await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.saveUiSetting).toHaveBeenNthCalledWith(2, "scan.device", { scanner: "second" });
  expect(server["scan.device"]).toEqual({ scanner: "second" });
});

it.each(["save", "reset"])("drops an old account's queued %s after switching cookies", async (operation) => {
  setPersonalViewsUser("a");
  let finishFirst!: () => void;
  vi.mocked(identityApi.saveUiSetting).mockImplementationOnce(async (view, value) => {
    await new Promise<void>((resolve) => { finishFirst = resolve; });
    return { view, value };
  });
  savePersonalView("scan.device", { scanner: "a-1" }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  if (operation === "reset") resetPersonalView("scan.device");
  else { savePersonalView("scan.device", { scanner: "a-2" }, 0, "a"); await vi.advanceTimersByTimeAsync(0); }
  setPersonalViewsUser(null); setPersonalViewsUser("b");
  savePersonalView("scan.device", { scanner: "b" }, 0, "b");
  await vi.advanceTimersByTimeAsync(0);
  finishFirst(); await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.resetUiSetting).not.toHaveBeenCalled();
  expect(identityApi.saveUiSetting).toHaveBeenCalledTimes(2);
  expect(identityApi.saveUiSetting).toHaveBeenLastCalledWith("scan.device", { scanner: "b" });
});

it("does not deduplicate a save against a PUT when a DELETE is queued behind it", async () => {
  setPersonalViewsUser("a");
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { plan: { order: ["old"] } } });
  await loadPersonalViews("a");
  let finishDelete!: () => void;
  vi.mocked(identityApi.resetUiSetting).mockImplementation(async () => { await new Promise<void>((resolve) => { finishDelete = resolve; }); });
  resetPersonalView("plan");
  savePersonalView("plan", { order: ["old"] }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.saveUiSetting).not.toHaveBeenCalled();
  finishDelete(); await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.saveUiSetting).toHaveBeenCalledExactlyOnceWith("plan", { order: ["old"] });
});

it("orders a new session's reset after the same account's already dispatched PUT", async () => {
  setPersonalViewsUser("a");
  let finishPut!: () => void;
  const server: Record<string, unknown> = {};
  vi.mocked(identityApi.saveUiSetting).mockImplementationOnce(async (view, value) => {
    await new Promise<void>((resolve) => { finishPut = resolve; });
    server[view] = value;
    return { view, value };
  });
  vi.mocked(identityApi.resetUiSetting).mockImplementation(async (view) => { delete server[view]; });
  savePersonalView("scan.device", { scanner: "old-session" }, 0, "a");
  await vi.advanceTimersByTimeAsync(0);
  setPersonalViewsUser(null); setPersonalViewsUser("a");
  resetPersonalView("scan.device", "a");
  expect(identityApi.resetUiSetting).not.toHaveBeenCalled();
  finishPut(); await vi.advanceTimersByTimeAsync(0);
  expect(identityApi.resetUiSetting).toHaveBeenCalledExactlyOnceWith("scan.device");
  expect(server).toEqual({});
});
