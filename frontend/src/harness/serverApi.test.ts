import { describe, expect, it, vi } from "vitest";
import { ApiError, type HarnessDrawing } from "../api";
import { HarnessDesignApiError } from "./editor/design-api";
import { createEmptyHarnessDesign } from "./editor/model";
import { CONFLICT_CODE, createServerHarnessDesignApi, type ServerHarnessClient, type ServerSaveState } from "./serverApi";

const document = { id: "h1", index: "КБ1", name: "Чертёж жгута", size: 1, stored: true, url: null, uploaded_at: "2026-10-06T10:00:00", who: "Технолог", kind: "harness" as const };
const design = () => JSON.parse(JSON.stringify(createEmptyHarnessDesign())) as Record<string, unknown>;
const drawing = (version: string | null): HarnessDrawing => version
  ? { document, design: design(), version, warnings: [] }
  : { document: null, design: null, version: null, warnings: [] };

function setup(stored: string | null) {
  const states: ServerSaveState[] = [];
  const client: ServerHarnessClient = {
    harness: vi.fn(async () => drawing(stored)),
    saveHarness: vi.fn(async () => ({ ...drawing("v2"), created: stored === null })),
  };
  return { api: createServerHarnessDesignApi({ client, onState: (state) => states.push(state) }), client, states };
}

describe("серверный адаптер чертежа жгута", () => {
  it("нет чертежа — пустой документ, первое сохранение без версии создаёт его", async () => {
    const { api, client } = setup(null);
    const loaded = await api.get("708К.4", "КБ1");
    expect(loaded.revision).toBe(0);
    expect(loaded.content.connectors).toHaveLength(0);
    const saved = await api.save("708К.4", "КБ1", 0, loaded.content);
    expect(client.saveHarness).toHaveBeenCalledWith("708К.4", "КБ1", expect.any(Object), null);
    expect(saved.revision).toBe(1);
  });

  it("сохраняет по версии, полученной при чтении, и берёт новую из ответа", async () => {
    const { api, client, states } = setup("v1");
    const loaded = await api.get("708К.4", "КБ1");
    expect(loaded.revision).toBe(1);
    const first = await api.save("708К.4", "КБ1", 1, loaded.content);
    await api.save("708К.4", "КБ1", first.revision, first.content);
    expect(vi.mocked(client.saveHarness).mock.calls.map((call) => call[3])).toEqual(["v1", "v2"]);
    expect(states).toEqual(["saved", "saving", "saved", "saving", "saved"]);
  });

  it("409 — конфликт: ошибка с кодом, дальше сохранения не уходят на сервер", async () => {
    const { api, client, states } = setup("v1");
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(client.saveHarness).mockRejectedValueOnce(new ApiError("Чертёж жгута только что сохранили", 409));
    const failure = await api.save("708К.4", "КБ1", 1, loaded.content).catch((reason) => reason);
    expect(failure).toBeInstanceOf(HarnessDesignApiError);
    expect(failure.code).toBe(CONFLICT_CODE);
    expect(api.conflicted).toBe(true);
    await expect(api.save("708К.4", "КБ1", 1, loaded.content)).rejects.toBeInstanceOf(HarnessDesignApiError);
    expect(client.saveHarness).toHaveBeenCalledTimes(1);
    expect(states.at(-1)).toBe("conflict");
    await api.get("708К.4", "КБ1");
    expect(api.conflicted).toBe(false);
  });

  it("другая ошибка сервера не называется конфликтом", async () => {
    const { api, client, states } = setup("v1");
    const loaded = await api.get("708К.4", "КБ1");
    vi.mocked(client.saveHarness).mockRejectedValueOnce(new ApiError("Чертёж жгута больше 20 МБ", 422));
    await expect(api.save("708К.4", "КБ1", 1, loaded.content)).rejects.toThrow("больше 20 МБ");
    expect(api.conflicted).toBe(false);
    expect(states.at(-1)).toBe("error");
  });
});
