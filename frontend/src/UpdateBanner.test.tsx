import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UpdateBanner } from "./UpdateBanner";

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
