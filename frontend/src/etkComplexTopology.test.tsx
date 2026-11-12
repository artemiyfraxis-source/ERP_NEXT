import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { etkConnectionRows, etkDocumentConnectionRows, etkPhysicalEndLabel, etkTubePlacementRows } from "./etkDraw";
import { etkLinkScheme, etkLinkSchemeMarkup, EtkLinkSchemeView } from "./etkLinkScheme";
import { etkPrintPages } from "./etkPrint";
import { EtkSheet } from "./ProjectEtk";
import { buildPrintDocumentHtml } from "./print";
import { makeGen } from "./tcGenLogic";
import { D663, harness663 } from "./tcGenComplex663.fixture";

const end = (idx: number, side: "A" | "B", unit = 0) => ({ idx, unit, side });
const pin = (idx: number, side: "A" | "B", contact: number, epId = "connector-uuid") => ({
  end: end(idx, side), kind: "pin", epId, pin: contact, conn: "XHP-4", label: "Х1",
});
const splice = (idx: number, side: "A" | "B", id = "splice-uuid", label = "С1") => ({
  end: end(idx, side), kind: "splice", spliceId: id, label,
});
const lug = (idx: number, side: "A" | "B", art: string) => ({
  end: end(idx, side), kind: "lug", material: { art, name: `Наконечник ${art}` }, crimp: "lug",
});
const segment = (idx: number, from: object, to: object, unit = 0) => ({ idx, unit, wire: "НВ4 красный", from, to });
const legacy = () => ({
  connections: [{ wire: "ПВ3", from: { end: "А1", conn: "XHP-2", label: "Вход", pin: 1 }, to: { end: "В1", conn: "PHR-2", label: "", pin: 2 } }],
  operations: [],
});
const complex = () => ({
  ...legacy(),
  build_config: {
    tab: "complex", topologyVersion: 1,
    wires: [{ idx: 7, art: "НВ4 красный", length: 200 }],
    // A stale legacy projection must not influence the authoritative topology.
    pinout: { ends: [{ end: "А9", conn: "СТАРЫЙ", wires: [{ pos: 0, pins: "8" }] }] },
    physicalConnections: [
      segment(7, pin(7, "A", 1), splice(7, "B")),
      segment(11, splice(11, "A"), splice(11, "B", "splice-2", "С2")),
      segment(12, splice(12, "A", "splice-2", "С2"), pin(12, "B", 2)),
      segment(14, pin(14, "A", 3), pin(14, "B", 4)),
      segment(21, splice(21, "A"), lug(21, "B", "НК-1")),
      segment(22, lug(22, "A", "НК-2"), lug(22, "B", "НК-3")),
      { ...segment(23, { end: end(23, "A"), kind: "free" }, lug(23, "B", "НК-4")), shield: true },
    ],
    tubePlacements: [
      { id: "t1", material: { art: "ТТК 3/1", name: "Трубка" }, lengthMm: 15, count: 2,
        target: { kind: "branch", end: end(7, "B") }, phase: "preload" },
      { id: "t2", material: { art: "ТТК 6/2", name: "Трубка" }, lengthMm: 25, count: 1,
        target: { kind: "splice", spliceId: "splice-uuid" }, phase: "preload", preloadEnd: end(21, "A") },
      { id: "t3", material: { art: "ТТК 2/1", name: "Трубка" }, lengthMm: 8.5, count: 1,
        target: { kind: "cup", end: end(12, "B") }, phase: "afterJoin" },
    ],
  },
});
const card = (document: object) => ({
  id: "c4", project: "Проверка", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана", document,
}) as ProjectTechcard;
const principal: Principal = { user: { id: "c4", email: "test@example.org", name: "Технолог", department: "" }, roles: [], permissions: {} };

afterEach(() => vi.restoreAllMocks());

describe("versioned ETK physical topology", () => {
  it("lists every physical segment through chained splices, same-connector jumpers and per-end lugs", () => {
    const rows = etkDocumentConnectionRows(complex());
    expect(rows).toHaveLength(7);
    expect(rows.map((row) => row[0])).toEqual(["8.1", "12.1", "13.1", "15.1", "22.1", "23.1", "24.1"]);
    expect(rows[0]).toEqual(["8.1", "НВ4 красный", "Контакт разъёма · Х1 · XHP-4", "1", "Сросток · С1", ""]);
    expect(rows[1].slice(2)).toEqual(["Сросток · С1", "", "Сросток · С2", ""]);
    expect(rows[3].slice(2)).toEqual(["Контакт разъёма · Х1 · XHP-4", "3", "Контакт разъёма · Х1 · XHP-4", "4"]);
    expect(rows[5][2]).toContain("НК-2");
    expect(rows[5][4]).toContain("НК-3");
    expect(rows[6][1]).toBe("Экран · НВ4 красный");
    expect(rows[6][2]).toBe("Свободный конец");
    expect(JSON.stringify(rows)).not.toMatch(/СТАРЫЙ|\[object Object\]|uuid/);
  });

  it("describes branch/splice/cup tubes, quantities, timing and the physical preload end", () => {
    const rows = etkTubePlacementRows(complex());
    expect(rows[0]).toEqual(["ТТК 3/1 · Трубка", "15", "2", "На ветвь · Участок 8.1, конец В · Сросток · С1", "До соединения", ""]);
    expect(rows[1]).toEqual(["ТТК 6/2 · Трубка", "25", "1", "На сросток · С1", "До соединения", "Участок 22.1, конец А · Сросток · С1"]);
    expect(rows[2]).toEqual(["ТТК 2/1 · Трубка", "8,5", "1", "На чашку · Участок 13.1, конец В · Контакт разъёма · Х1 · XHP-4, конт. 2", "После соединения", ""]);
  });

  it("uses physical topology even with stale pinout data, retaining every segment in the diagram", () => {
    const doc = complex(), scheme = etkLinkScheme(doc)!;
    expect(scheme.physical?.connections).toEqual(doc.build_config.physicalConnections);
    expect(scheme.wires).toHaveLength(7);
    const { container } = render(<EtkLinkSchemeView scheme={scheme} />);
    expect(container.querySelector("svg")).not.toBeNull();
    for (const text of ["С1", "С2", "НК-1", "НК-2", "НК-3", "НК-4", "Свободный конец"]) {
      expect(container.textContent).toContain(text);
    }
    expect(container.textContent).not.toMatch(/СТАРЫЙ|uuid|\[object Object\]/);
  });

  it("keeps parallel and reversed segments individually visible", () => {
    const doc = complex();
    doc.build_config.physicalConnections = [
      segment(0, pin(0, "A", 1), splice(0, "B")),
      segment(1, pin(1, "A", 1), splice(1, "B")),
      segment(2, splice(2, "A"), pin(2, "B", 1)),
    ];
    const { container } = render(<EtkLinkSchemeView scheme={etkLinkScheme(doc)!} />);
    const groups = [...container.querySelectorAll("[data-segment]")];
    const paths = groups.map((group) => group.querySelector("path")!.getAttribute("d"));
    expect(groups).toHaveLength(3);
    expect(new Set(paths).size).toBe(3);
    const labels = groups.map((group) => {
      const text = group.querySelector("text")!;
      return `${text.getAttribute("x")},${text.getAttribute("y")}`;
    });
    expect(new Set(labels).size).toBe(3);
  });

  it("distinguishes unlabeled connectors in the real C1 export without direct legacy links", () => {
    const state = harness663();
    const config = makeGen(D663, { ...state, topology: { version: 1 }, endpoints: state.endpoints.map((ep) => ({ ...ep, label: "" })) }).buildConfig();
    expect(config.connections).toBeUndefined();
    const links = config.physicalConnections as Array<{ from: Record<string, unknown>; to: Record<string, unknown> }>;
    const pins = links.flatMap((link) => [link.from, link.to]).filter((at) => at.kind === "pin");
    expect(pins.length).toBeGreaterThan(1);
    const byId = new Map(pins.map((at) => [at.epId, etkPhysicalEndLabel(at, config)]));
    expect(new Set(byId.values()).size).toBe(byId.size);
    expect([...byId.values()].every((label) => /XS\d+/.test(label))).toBe(true);
    const html = etkLinkSchemeMarkup(etkLinkScheme({ build_config: config })!);
    for (const label of byId.values()) expect(html).toContain(label);
  });

  it("renders connectorless lug/free branches and never falls back from an empty physical array", () => {
    const doc = complex();
    doc.build_config.physicalConnections = doc.build_config.physicalConnections.slice(-2);
    const scheme = etkLinkScheme(doc)!;
    expect(scheme).not.toBeNull();
    expect(etkLinkSchemeMarkup(scheme)).toContain("НК-4");
    doc.build_config.physicalConnections = [];
    expect(etkLinkScheme(doc)).toBeNull();
    expect(etkDocumentConnectionRows(doc)).toEqual([]);
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(card(doc), []))).not.toContain("ПВ3");
  });

  it.each([false, true])("keeps the complete connection and tube tables on the ETK screen (task=%s)", async (task) => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ items: [] }), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    render(<EtkSheet card={card(complex())} principal={principal} task={task} onChange={() => undefined} />);
    const table = screen.getByRole("table", { name: "Таблица соединений", hidden: true });
    expect(within(table).getAllByRole("row", { hidden: true })).toHaveLength(8);
    expect(screen.getByRole("table", { name: "Размещение трубок", hidden: true })).toHaveTextContent("После соединения");
    expect(table).toHaveTextContent("НК-3");
    // Settle the revisions/sketch requests before unmounting.
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
  });

  it("prints the same complete tables alongside the diagram, escaping material and endpoint text", () => {
    const doc = complex();
    doc.build_config.physicalConnections[0].wire = '<script>alert("x")</script>';
    const pages = etkPrintPages(card(doc), []);
    const tables = pages[0].blocks.flatMap((block) => "table" in block ? [block.table] : []);
    expect(tables).toContainEqual({ columns: ["№", "Провод", "Откуда", "Конт.", "Куда", "Конт."], rows: etkDocumentConnectionRows(doc) });
    expect(tables.some((table) => JSON.stringify(table.rows) === JSON.stringify(etkTubePlacementRows(doc)))).toBe(true);
    const html = buildPrintDocumentHtml("ТК", pages);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain('<script>alert("x")</script>');
    expect(html).toContain("Размещение трубок");
    expect(html).not.toContain("Схема жгута");
  });
});

describe("legacy ETK output compatibility", () => {
  it("retains the existing table rows and old link-scheme model", () => {
    const doc = legacy();
    expect(etkDocumentConnectionRows(doc)).toEqual(etkConnectionRows(doc.connections));
    expect(etkTubePlacementRows(doc)).toEqual([]);
    expect({ rows: etkDocumentConnectionRows(doc), scheme: etkLinkScheme(doc) }).toMatchInlineSnapshot(`
      {
        "rows": [
          [
            "1",
            "ПВ3",
            "А1 Вход · XHP-2",
            "1",
            "В1 · PHR-2",
            "2",
          ],
        ],
        "scheme": {
          "connectors": [
            {
              "conn": "XHP-2",
              "designation": "XS1",
              "id": "А1",
              "key": "А1",
              "pins": 2,
              "series": "",
              "side": "A",
              "terminal": "",
            },
            {
              "conn": "PHR-2",
              "designation": "XS2",
              "id": "В1",
              "key": "В1",
              "pins": 2,
              "series": "",
              "side": "B",
              "terminal": "",
            },
          ],
          "splices": [],
          "twists": [],
          "wires": [
            {
              "a": [
                {
                  "epId": "А1",
                  "kind": "pin",
                  "pin": 1,
                },
              ],
              "at": {
                "А1": 1,
                "В1": 2,
              },
              "b": [
                {
                  "epId": "В1",
                  "kind": "pin",
                  "pin": 2,
                },
              ],
              "color": null,
              "key": "0",
              "length": 0,
              "name": "ПВ3",
              "num": "1",
              "shield": false,
              "stripe": null,
            },
          ],
        },
      }
    `);
  });

  it("ignores unversioned physical fields and keeps the existing print output exactly", () => {
    const doc = legacy();
    const withDraft = { ...doc, build_config: { physicalConnections: complex().build_config.physicalConnections, tubePlacements: complex().build_config.tubePlacements } };
    expect(etkLinkScheme(withDraft)).toEqual(etkLinkScheme(doc));
    const baseline = buildPrintDocumentHtml("ТК", etkPrintPages(card(doc), []));
    expect(buildPrintDocumentHtml("ТК", etkPrintPages(card(withDraft), []))).toBe(baseline);
    expect(baseline).toContain("Схема связей");
    expect(baseline).not.toContain("Таблица соединений");
    expect(baseline).not.toContain("Размещение трубок");
  });
});
