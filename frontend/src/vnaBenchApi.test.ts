import { afterEach, expect, it, vi } from "vitest";
import { arinstBenchApi, vnaBenchApi } from "./vnaBenchApi";

afterEach(() => vi.restoreAllMocks());
function requests() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ source: "arinst" }), {
    headers: { "Content-Type": "application/json" },
  }));
}

it("keeps the Planar default and explicitly encodes the Arinst registry UUID", async () => {
  const fetch = requests();
  await vnaBenchApi.state();
  await arinstBenchApi.state("si/1?");
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    "/api/production/vna-bench/state", "/api/production/vna-bench/state?instrument_id=si%2F1%3F",
  ]);
});

it("creates an Arinst session with identity only", async () => {
  const fetch = requests(); await arinstBenchApi.start("si1");
  expect(fetch.mock.calls[0][0]).toBe("/api/production/vna-bench/sessions");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ instrument_id: "si1" });
});

it.each(["connect", "confirm", "close", "abort"] as const)("sends %s with encoded session identity and the exact version", async (op) => {
  const fetch = requests(); await arinstBenchApi[op]("s/1", 12);
  expect(fetch.mock.calls[0][0]).toBe(`/api/production/vna-bench/sessions/s%2F1/${op === "close" || op === "abort" ? "" : "arinst/"}${op}`);
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ version: 12 });
  expect(fetch.mock.calls[0][1]?.method).toBe("POST");
});

it("sends only a diagnostic capture without a quality target and defines no device reset route", async () => {
  const fetch = requests(); await arinstBenchApi.capture("s1", 12, "reverse");
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ version: 12, orientation: "reverse" });
  expect(Object.keys(arinstBenchApi).join(" ")).not.toMatch(/reset|power|activate|renew/i);
});

it("sends profile and guided SOL/THRU input without client commissioning bypasses", async () => {
  const fetch = requests();
  const profile = { start_hz: 1e6, stop_hz: 8.8e9, points: 501, if_bandwidth_hz: 500 };
  await arinstBenchApi.profile("s1", 9, profile);
  await arinstBenchApi.calibration("s1", 10, "kit1", "Концы кабелей", true);
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({ version: 9, profile });
  expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({ version: 10, kit_id: "kit1", reference_plane: "Концы кабелей", transmission: true, orientation: "forward" });
});

it("builds immutable receipt downloads with escaped identifiers", () => {
  expect(arinstBenchApi.artifactUrl("s/1", "a?2")).toBe("/api/production/vna-sessions/s%2F1/artifacts/a%3F2");
});
