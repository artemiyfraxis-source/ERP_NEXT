import { afterEach, describe, expect, it, vi } from "vitest";
import { pfPending, proposePf } from "./pfPropose";

afterEach(() => vi.restoreAllMocks());

const block = (key: string, ops: number[], final = false) => ({ key, name: "", ops, final });

describe("pfPending", () => {
  it("is true only for a built ETK whose scheme is still one ПФ per operation", () => {
    const route = (source: string) => ({ document: { route: { source, blocks: [block("b1", [1], true)], edges: [] } } });
    expect(pfPending(route("derived"))).toBe(true);
    expect(pfPending(route("generated"))).toBe(false);
    expect(pfPending(route("manual"))).toBe(false);
    expect(pfPending({ document: null })).toBe(false);
    expect(pfPending({ document: { operations: [] } })).toBe(false);
  });
});

describe("proposePf", () => {
  it("asks the server and turns its answer into an editable scheme", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      route: { source: "generated", final: "B", blocks: [{ ...block("wires", [1]), minutes: 3 }, block("B", [2, 3], true)],
        edges: [{ from: "wires", to: "B", per_unit: 2 }] },
      same: false, rule: "generator",
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const proposal = await proposePf("c1");
    expect(fetch.mock.calls[0][0]).toBe("/api/techcards/projects/c1/route/propose");
    expect(proposal).toEqual({
      same: false,
      route: {
        blocks: [{ key: "wires", name: "", ops: [1], final: false, components: [] }, { key: "B", name: "", ops: [2, 3], final: true, components: [] }],
        edges: [{ from: "wires", to: "B", per_unit: 2 }],
      },
    });
  });
});
