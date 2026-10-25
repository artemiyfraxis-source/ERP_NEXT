import { afterEach, expect, it, vi } from "vitest";
import { openReadVisit, referencesApi, withConnectors, type CablesResult } from "./api";

/* «База данных» reads each list once per visit (26.10.2026: the same list was downloaded two or three times). */

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

it("shares a read inside a visit, reads again after a write and outside a visit", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    paths.push(`${init?.method ?? "GET"} ${String(input)}`);
    return json({ suppliers: [], countries: [], gost_awg: [] });
  });
  const close = openReadVisit();
  const [first, second] = await Promise.all([referencesApi.maps(), referencesApi.maps()]);
  expect(second).toBe(first); // one download, one object
  await referencesApi.maps();
  expect(paths).toEqual(["GET /api/references/maps"]);
  await referencesApi.saveMaps({ suppliers: [], countries: [], gost_awg: [] });
  await referencesApi.maps(); // the write forgot the shared reads: the list comes fresh
  expect(paths).toEqual(["GET /api/references/maps", "PUT /api/references/maps", "GET /api/references/maps"]);
  close();
  await referencesApi.maps();
  await referencesApi.maps();
  expect(paths.filter((path) => path === "GET /api/references/maps")).toHaveLength(4); // no visit — plain reads
});

it("asks a failed read again within the visit", async () => {
  let calls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
    calls += 1;
    return calls === 1 ? new Response("{}", { status: 500 }) : json({ bases: [] });
  });
  const close = openReadVisit();
  await expect(referencesApi.bases()).rejects.toThrow();
  await expect(referencesApi.bases()).resolves.toEqual({ bases: [] });
  close();
  expect(calls).toBe(2);
});

it("puts each coax row's connectors back from the list's shared cells", () => {
  const result = withConnectors({
    columns: [], counts: { coax: 2, wire: 0, multi: 0, lan: 0 }, gauges: [],
    connector_cells: { c1: { "Артикул": "SMA-J" }, c2: { "Артикул": "N-K" } },
    rows: [
      { id: "r1", kind: "coax", cable: "RG-58", connector_ids: ["c1", "c2"] },
      { id: "r2", kind: "coax", cable: "RG-58 C/U", connector_ids: ["c1"] },
      { id: "r3", kind: "wire", cable: "МГТФ 0,12" },
    ],
  } as unknown as CablesResult);
  expect(result.rows[0].connectors).toEqual([{ id: "c1", fields: { "Артикул": "SMA-J" } }, { id: "c2", fields: { "Артикул": "N-K" } }]);
  expect(result.rows[1].connectors?.[0]).toBe(result.rows[0].connectors?.[0]); // one object per connector
  expect(result.rows[2].connectors).toBeUndefined();
});
