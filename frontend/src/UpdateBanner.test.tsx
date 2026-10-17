import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpdateBanner, UpdateFailure } from "./UpdateBanner";

function serve(versions: Array<object | null>) {
  const queue = [...versions];
  const fetchMock = vi.fn(async () => {
    const body = queue.length > 1 ? queue.shift() : queue[0];
    return body ? new Response(JSON.stringify(body), { status: 200 }) : new Response("", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("UpdateBanner", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("lists what changed since the version this browser saw last, until dismissed", async () => {
    window.localStorage.setItem("erp.seenVersion", "old");
    serve([{ version: "new", date: "29.09.2026 22:00", news: ["#104 Что нового", "#103 Отгруженные"] }]);
    render(<UpdateBanner />);
    expect(await screen.findByText("Обновлено: 29.09.2026 22:00")).toBeInTheDocument();
    expect(screen.getByText("#104 Что нового")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Понятно"));
    expect(screen.queryByRole("status")).toBeNull();
    expect(window.localStorage.getItem("erp.seenVersion")).toBe("new");
  });

  it("shows nothing without version.json (dev server, server deploy)", async () => {
    const fetchMock = serve([null]);
    render(<UpdateBanner />);
    await act(async () => { await Promise.resolve(); });
    expect(fetchMock).toHaveBeenCalled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("reloads an open tab on a new version once the user is idle", async () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    window.localStorage.setItem("erp.seenVersion", "v1");
    serve([{ version: "v1", news: [] }, { version: "v2", news: ["#105 Новое"] }]);
    render(<UpdateBanner pollMs={1000} idleMs={3000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(reload).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(reload).toHaveBeenCalled();
  });
});

describe("UpdateFailure", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows why the auto-update failed and the tail of its log", async () => {
    const fetchMock = serve([{
      ok: false, kind: "update", at: "30.09.2026 14:05", retry: "повтор в 14:15",
      reason: "миграции базы: sqlite3.OperationalError: database is locked — база возвращена из копии",
      log: ["== Останавливаю API на порту 8022", "sqlite3.OperationalError: database is locked"],
    }]);
    render(<UpdateFailure />);
    expect(await screen.findByText("Обновление не установилось 30.09.2026 14:05:")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("database is locked — база возвращена из копии");
    expect(screen.getByRole("alert")).toHaveTextContent("повтор в 14:15");
    expect(screen.getByText(/== Останавливаю API на порту 8022/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/update-status.json", { cache: "no-store" });
  });

  it("says so when updates do not download at all", async () => {
    serve([{ ok: false, kind: "fetch", reason: "обновления не скачиваются с GitHub больше 30 мин", log: "fatal: unable to access" }]);
    render(<UpdateFailure />);
    expect(await screen.findByText("Обновления не скачиваются:")).toBeInTheDocument();
    expect(screen.getByText("fatal: unable to access")).toBeInTheDocument();
  });

  it("shows nothing when there is no failure file or the last update went fine", async () => {
    const fetchMock = serve([null]);
    const { unmount } = render(<UpdateFailure />);
    await act(async () => { await Promise.resolve(); });
    expect(fetchMock).toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
    unmount();
    serve([{ ok: true }]);
    render(<UpdateFailure />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("appears on the next poll when an update fails while the page is open", async () => {
    vi.useFakeTimers();
    serve([null, { ok: false, reason: "обновление не собралось (npm ci: EPERM)" }]);
    render(<UpdateFailure pollMs={1000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.queryByRole("alert")).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("alert")).toHaveTextContent("npm ci: EPERM");
    vi.useRealTimers();
  });
});
