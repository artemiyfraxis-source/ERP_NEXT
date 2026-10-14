import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { etkLinkScheme } from "./etkLinkScheme";
import { etkPrintPages } from "./etkPrint";
import { pinoutEnds, pinoutRecord, pinUnits } from "./pinoutCheck";
import { LinkSchemeView, pinoutScheme, schemeLayout } from "./PinoutLinks";
import { buildPrintDocumentHtml } from "./print";
import { EtkSheet } from "./ProjectEtk";
import { makeGen } from "./tcGenLogic";
import { D, harness } from "./tcGenRealHarness.fixture";

/* Схема связей в ЭТК (владелец 14.10: «не подтянулась новая версия»): та же схема Э4, что в генераторе, — из документа. */

const principal: Principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: {} };

/** ЭТК жгута из двух разъёмов, как на снимке владельца: 4 провода I-DS1071-SCB01X4 → PHR-4, перекрёст 1↔2. */
const simple = () => ({
  topology: {
    nodes: [{ id: "A", conn: "I-DS1071-SCB01X4", side: "A", depth: 0, housing: { title: "Molex Micro", n: 4 } }, { id: "B", conn: "PHR-4", side: "B", depth: 1 }],
    edges: [{ from: "A", to: "B", wires: ["НВ4 0,2 красный"], count: 4, length: 210 }],
  },
  connections: [
    { wire: "НВ4 0,2 красный", from: { end: "А1", conn: "I-DS1071-SCB01X4", label: "", pin: 1 }, to: { end: "В1", conn: "PHR-4", label: "", pin: 2 } },
  ],
  build_config: {
    tab: "simple",
    wires: [
      { name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 1, length: 210 },
      { name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 1, length: 210 },
      { name: "НВ4 0,2 белый", art: "НВ4 0,2 белый", qty: 2, length: 210, coreColors: ["", "бело-синий"] },
    ],
    twist: { enabled: true, mode: "pairs", pairs: [[0, 1]], wireIndices: [] },
    ops: [
      { type: "insTerm", side: "A", ep: { connArt: "I-DS1071-SCB01X4", termArt: "T-DS1071-SC600", label: "" } },
      { type: "solderConn", side: "B", ep: { connArt: "PHR-4", termArt: "", label: "" } },
    ],
    pinout: {
      nets: [{ pos: 0, unit: 0, net: "+5V" }],
      ends: [
        { end: "А1", conn: "I-DS1071-SCB01X4", checked: true, sig: "", wires: [{ pos: 0, art: "НВ4 0,2 красный", pins: "1" }, { pos: 1, art: "НВ4 0,2 черный", pins: "2" }, { pos: 2, art: "НВ4 0,2 белый", pins: "3, 4" }] },
        { end: "В1", conn: "PHR-4", checked: true, sig: "", wires: [{ pos: 0, art: "НВ4 0,2 красный", pins: "2" }, { pos: 1, art: "НВ4 0,2 черный", pins: "1" }, { pos: 2, art: "НВ4 0,2 белый", pins: "3, 4" }] },
      ],
    },
  },
});

const card = (document: Record<string, unknown>) => ({
  id: "t", project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана", document,
}) as unknown as ProjectTechcard;

const rowsOf = (svg: Element, end: string) => [...svg.querySelectorAll(`[data-end="${end}"] .pin-links-row`)]
  .map((row) => `${row.getAttribute("data-pin")}:${row.getAttribute("data-wires") ?? ""}`);
const linesOf = (svg: Element) => [...svg.querySelectorAll(".pin-links-line")]
  .map((line) => `${line.getAttribute("data-wire")} ${line.getAttribute("data-a")}→${line.getAttribute("data-b")}${line.getAttribute("data-free") ? ` ${line.getAttribute("data-free")}` : ""}`);

afterEach(() => vi.restoreAllMocks());

it("builds XS1/XS2 tables from the ETK pinout: rows per contact, terminal, net, colour and lines from contact to contact", () => {
  const scheme = etkLinkScheme(simple())!;
  expect(scheme.connectors.map((c) => `${c.key} ${c.designation} ${c.conn} ${c.pins} ${c.terminal}`))
    .toEqual(["А1 XS1 I-DS1071-SCB01X4 4 T-DS1071-SC600", "В1 XS2 PHR-4 4 пайка"]);
  render(<LinkSchemeView scheme={scheme} titled={false} />);
  const svg = screen.getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect(rowsOf(svg, "А1")).toEqual(["1:1", "2:2", "3:3.1", "4:3.2"]);
  expect(rowsOf(svg, "В1")).toEqual(["1:2", "2:1", "3:3.1", "4:3.2"]);
  expect(linesOf(svg)).toEqual(["1 А1·1→В1·2", "2 А1·2→В1·1", "3.1 А1·3→В1·3", "3.2 А1·4→В1·4"]);
  const row = svg.querySelector('[data-end="А1"] [data-pin="1"]')!;
  expect([...row.querySelectorAll(".pin-links-cell")].map((cell) => cell.textContent)).toEqual(["0,2 мм²", "НВ4 0,2 красный", "T-DS1071-SC600", "+5V"]);
  expect(svg.querySelector('[data-end="А1"] .pin-links-foot')).toHaveTextContent("Molex Micro I-DS1071-SCB01X4");
  // Жила «бело-синий» — с полосой; красный и чёрный свиты в пару.
  expect(svg.querySelector('[data-wire="3.2"] path[stroke-dasharray="4 4"]')).not.toBeNull();
  expect(svg.querySelectorAll('[data-twisted="true"]')).toHaveLength(2);
  expect(screen.queryByText("Схема связей")).toBeNull();
  expect(screen.getByText("4 пр. · свивок 1")).toBeInTheDocument();
});

it("«Сложная сборка»: the ETK scheme draws the same tables, splices and tails as the generator", () => {
  const S = harness(D), gen = makeGen(D, S);
  const config: Record<string, unknown> = { ...gen.buildConfig(), pinout: pinoutRecord(gen, S) };
  const etk = etkLinkScheme({ build_config: config, connections: config.connections })!;
  const ends = pinoutEnds(gen, S);
  const ordered = (["A", "B"] as const).flatMap((side) => gen.endpointsOnSide_(side));
  const designations = Object.fromEntries(ordered.map((ep, i) => [ep.id, (ep.label || "").trim() || `XS${i + 1}`]));
  const generator = pinoutScheme(ends, pinUnits(gen, S), {}, S, { designations });
  const shape = (scheme: typeof etk) => {
    const L = schemeLayout(scheme, 1600);
    const key = (id: string) => scheme.connectors.find((c) => c.id === id)?.key;
    return {
      tables: L.boxes.map((b) => `${b.end.key} ${b.end.designation} ${b.end.conn} ${b.rows} ${b.x},${b.y}`),
      rows: scheme.wires.map((w) => `${w.num} ${w.name} ${w.color} ${Object.entries(w.at).map(([id, pin]) => `${key(id)}·${pin}`).join(",")}`),
      lines: L.lines.map((l) => `${l.w.num} ${l.d} ${l.free ?? ""}`),
      splices: L.nodes.map((n) => `${n.id}:${n.y}`),
    };
  };
  expect(shape(etk)).toEqual(shape(generator));
  expect(etk.connectors.map((c) => c.designation)).toEqual(["5", "I3", "C", "A6", "XS5", "XS6"]);
  render(<LinkSchemeView scheme={etk} />);
  const svg = screen.getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect([...svg.querySelectorAll("[data-splice]")].map((n) => n.getAttribute("data-splice"))).toEqual(["С1", "С2", "С3", "С4"]);
  expect(svg.querySelectorAll('[data-free="tail"]')).toHaveLength(4);
  expect(svg.querySelectorAll(".pin-links-table")).toHaveLength(6);
  expect(screen.getByText(/17 пр\. · сростков 4 · хвостов 4/)).toBeInTheDocument();
});

it("an ETK without the generator's pinout draws the scheme from its connection table", () => {
  const { build_config: _config, ...doc } = simple();
  const scheme = etkLinkScheme({ ...doc, connections: [
    ...doc.connections,
    { wire: "НВ4 0,2 черный", from: { end: "А1", conn: "I-DS1071-SCB01X4", label: "", pin: 2 }, to: { end: "В1", conn: "PHR-4", label: "", pin: 1 } },
  ] })!;
  render(<LinkSchemeView scheme={scheme} />);
  const svg = screen.getByRole("img", { name: "Провода с контакта на контакт, сростки и хвосты" });
  expect(rowsOf(svg, "В1")).toEqual(["1:2", "2:1", "3:", "4:"]);
  expect(linesOf(svg)).toEqual(["1 А1·1→В1·2", "2 А1·2→В1·1"]);
});

it("an old ETK with neither pinout nor connections keeps the 3D harness scheme; a broken pinout does not crash", () => {
  expect(etkLinkScheme({ topology: simple().topology })).toBeNull();
  expect(etkLinkScheme(null)).toBeNull();
  expect(etkLinkScheme({ build_config: { wires: "мусор", pinout: { ends: [{ end: "Х", wires: 5 }] } } })).toBeNull();
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const topology = { ...simple().topology, nodes: simple().topology.nodes.map(({ id, conn, side, depth }) => ({ id, conn, side, depth })) };
  const { container } = render(<EtkSheet card={card({ topology, operations: [] })} principal={principal} onChange={() => undefined} />);
  fireEvent.click(screen.getByText("Схема жгута"));
  expect(container.querySelector(".etk-harness svg")).not.toBeNull();
  expect(container.querySelector(".pin-links")).toBeNull();
});

it("the issued ETK shows «Схема связей» instead of the 3D boxes and the flat connection table — on screen and in print", () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  const doc = { ...simple(), operations: [{ position: 1, code: "OP-1", type: "insTerm", title: "Опрессовка", card: { name: "Опрессовка", steps: [] } }] };
  const { container } = render(<EtkSheet card={card(doc)} principal={principal} onChange={() => undefined} />);
  fireEvent.click(screen.getByText("Схема связей"));
  expect(container.querySelector(".etk-schema .pin-links-svg")).not.toBeNull();
  expect(container.querySelector(".etk-harness")).toBeNull();
  expect(screen.queryByRole("table", { name: "Таблица соединений" })).toBeNull();

  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card(doc), []));
  expect(html).toContain("<h2>Схема связей</h2><div class=\"fig\"><div class=\"etk-print-scheme\"><style>");
  expect(html).toMatch(/<svg class="pin-links-svg"[\s\S]*data-end="А1"[\s\S]*data-wire="3\.2"/);
  expect(html).not.toContain("<h2>Схема жгута</h2>");
  expect(html).not.toContain("<h2>Таблица соединений</h2>");
});
