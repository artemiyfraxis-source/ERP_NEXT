import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiTimeoutError,
  NO_CONNECTION,
  READ_DEADLINE_MS,
  READ_TIMEOUT_TEXT,
  UnknownResultError,
  WRITE_DEADLINE_MS,
  api,
  failureText,
  isAborted,
  latestRead,
} from "./api";

/** A server that never answers (and ignores the abort signal, as a stuck proxy would). */
const hang = () => new Promise<Response>(() => undefined);
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("api deadlines (U01)", () => {
  it("ends a hung read after 20 s with a clear Russian message and aborts the fetch", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(hang);
    const read = api("/api/requests");
    const outcome = read.then(() => "resolved", (reason) => reason);
    await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS - 1);
    expect(await Promise.race([outcome, Promise.resolve("pending")])).toBe("pending");
    await vi.advanceTimersByTimeAsync(1);
    const reason = await outcome;
    expect(reason).toBeInstanceOf(ApiTimeoutError);
    expect((reason as Error).message).toBe(READ_TIMEOUT_TEXT);
    expect((fetchMock.mock.calls[0][1]?.signal as AbortSignal).aborted).toBe(true);
  });

  it("does not call a hung write a failure: after its deadline the result is unknown", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(hang);
    const write = api("/api/requests", { method: "POST", body: "{}" });
    const outcome = write.then(() => "resolved", (reason) => reason);
    await vi.advanceTimersByTimeAsync(READ_DEADLINE_MS);
    expect(await Promise.race([outcome, Promise.resolve("pending")])).toBe("pending");
    await vi.advanceTimersByTimeAsync(WRITE_DEADLINE_MS - READ_DEADLINE_MS);
    expect(await outcome).toBeInstanceOf(UnknownResultError);
  });

  it("reports a broken connection on a write as an unknown result, on a read as no connection", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/api/requests", { method: "POST", body: "{}" })).rejects.toBeInstanceOf(UnknownResultError);
    const read = await api("/api/requests").catch((reason) => reason);
    expect(read).toBeInstanceOf(TypeError);
    expect(failureText(read, "")).toBe(NO_CONNECTION);
  });

  it("treats a safe POST (preview, sign-in) like a read", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/api/requests/import/parse", { method: "POST", body: "{}", safe: true })).rejects.toBeInstanceOf(TypeError);
  });

  it("gives a file upload no deadline", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(hang);
    const upload = api("/api/files", { method: "POST", body: new FormData() });
    const outcome = upload.then(() => "resolved", (reason) => reason);
    await vi.advanceTimersByTimeAsync(WRITE_DEADLINE_MS * 2);
    expect(await Promise.race([outcome, Promise.resolve("pending")])).toBe("pending");
  });

  it("cancels a superseded read: the older one ends as aborted, the newer one answers", async () => {
    const answers: Array<(response: Response) => void> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>((resolve) => answers.push(resolve)));
    const next = latestRead();
    const older = api<{ q: string }>("/api/requests?q=a", { signal: next() }).catch((reason) => reason);
    const newer = api<{ q: string }>("/api/requests?q=ab", { signal: next() });
    answers[1](json({ q: "ab" }));
    answers[0](json({ q: "a" }));
    expect(isAborted(await older)).toBe(true);
    expect(await newer).toEqual({ q: "ab" });
  });

  it("clears the deadline once the answer arrives", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ok: true }));
    await expect(api("/api/requests")).resolves.toEqual({ ok: true });
    expect(vi.getTimerCount()).toBe(0);
  });
});
