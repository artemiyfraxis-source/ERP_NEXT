import { afterEach, expect, it, vi } from "vitest";
import { machineControlApi } from "./machineControlApi";

afterEach(() => vi.restoreAllMocks());

it.each([null, "existing-run"])("run and transfer serialize exactly the confirmed source and active run %s", async (runId) => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200,
    headers: { "Content-Type": "application/json" } }));
  const confirmation = { expected_source: { group_id: "group", version: 7, length_mm: "325", plan_hash: "a".repeat(64) },
    expected_active_run_id: runId };
  await machineControlApi.startRun("ОБ.019", { kind: "cutting", group_id: "group", length_mm: "325", ...confirmation }, 102);
  await machineControlApi.transfer("ОБ.019", "group", "325", 102, confirmation);
  expect(fetch.mock.calls.map(([url, init]) => ({ url: decodeURIComponent(String(url)), body: JSON.parse(String(init?.body)) }))).toEqual([
    { url: "/api/production/machines/card/ОБ.019/run", body: { kind: "cutting", group_id: "group", length_mm: "325", confirm_state: 102, ...confirmation } },
    { url: "/api/production/machines/card/ОБ.019/transfer", body: { group_id: "group", length_mm: "325", confirm_state: 102, ...confirmation } },
  ]);
});
