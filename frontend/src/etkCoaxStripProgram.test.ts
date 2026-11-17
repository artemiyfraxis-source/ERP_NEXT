// @vitest-environment node
import { expect, it } from "vitest";
import programs from "./__fixtures__/coaxStripPrograms.json";
import { etkDrawCoax, etkParams } from "./etkDraw";
import { etkPrintPages } from "./etkPrint";
import { buildPrintDocumentHtml } from "./print";
import type { ProjectTechcard } from "./api";

it.each(programs)("renders the backend $kind program in the sketch and ETK fields", ({ kind, operation }) => {
  const svg = etkDrawCoax(operation), params = JSON.stringify(etkParams(operation));
  const program = operation.fields.coax.stripProgram;
  expect(svg.match(/<svg /g)).toHaveLength(1);
  expect(svg).toContain('data-part="break"');
  for (const b of program.boundaries) {
    expect(svg).toContain(`${b.value} ${b.tolerance}`);
    expect(params).toContain(`${b.label} от торца`);
    expect(params).toContain(`${b.value} ${b.tolerance} мм`);
  }
  expect(params).not.toContain(program.source);
  expect(params).not.toContain(program.note);
  expect(svg).not.toContain(program.source);
  expect(svg).not.toContain(program.note);
  const bands = [...svg.matchAll(/data-band="([^"]+)"/g)].map((m) => m[1]);
  expect(bands).toEqual(["conductor", "dielectric", ...(["hfm", "bulkhead"].includes(kind) ? ["foil"] : []), "braid", "jacket", "jacket"]);
  if (kind === "hfm") {
    expect(svg).toContain("4,40 ±0,6");
    expect(svg).toContain("3,35 минимум номинально");
  }
  if (kind === "bulkhead") {
    expect(svg).toContain("3,7 ±0,2");
    expect(svg).toContain("8,5 ±0,2");
    expect(svg).toContain("13,9 ±0,2");
  }
});

it("updates a stored asymmetric cut in screen and print without changing its source payload", () => {
  const a = programs.find((p) => p.kind === "bulkhead")!.operation.fields.coax;
  const b = programs.find((p) => p.kind === "straight")!.operation.fields.coax;
  const operation = { type: "coaxCut", code: "CUT_COAX+2strip", fields: {
    coax: { ...a, connectorType: "FAKRA-Male bulkhead (тип Z)" },
    coaxB: { ...b, connectorType: "FAKRA-Female (тип Z)" }, finishedLength: 10000, lengthKd: "10055мм",
  } };
  const before = JSON.stringify(operation);
  const sketch = etkDrawCoax(operation);
  const card = { id: "coax", project: "708К.", item_index: "КБ1", product_name: "Кабель", revision: 1,
    document: { operations: [operation] } } as unknown as ProjectTechcard;
  const printed = buildPrintDocumentHtml("ЭТК", etkPrintPages(card, []));
  expect(printed).toContain(sketch);
  expect(sketch.match(/<svg /g)).toHaveLength(1);
  expect(sketch).toContain("L = 10000 мм (готовое изделие)");
  expect(sketch).toContain("Длина реза = 10055 мм");
  expect(sketch).toContain("А — FAKRA-Male bulkhead (тип Z)");
  expect(sketch).toContain("В — FAKRA-Female (тип Z)");
  expect(printed).not.toMatch(/Источник:|Исходные размеры|Контроль разделки|rev.B|X =/);
  const conductors = [...sketch.matchAll(/data-band="conductor" x="([^"]+)"[^>]*width="([^"]+)"/g)];
  expect(conductors).toHaveLength(2);
  expect(Number(conductors[0][1])).toBe(32);
  expect(Number(conductors[1][1])).toBeGreaterThan(500);
  expect(Number(conductors[1][1]) + Number(conductors[1][2])).toBeCloseTo(608);
  expect(JSON.stringify(operation)).toBe(before);
});

it.each([
  [{ l1: "3" }, ["conductor", "jacket", "jacket"]],
  [{ l1: "3", l2: "6" }, ["conductor", "braid", "jacket", "jacket"]],
  [{ l1: "3", l3: "9" }, ["conductor", "dielectric", "jacket", "jacket"]],
  [{ l3: "9" }, ["braid", "jacket", "jacket"]],
])("preserves legacy missing layers and leaves the other cut end intact (%o)", (sizes, expected) => {
  const sketch = etkDrawCoax({ type: "coaxCut", fields: { coax: sizes } });
  expect([...sketch.matchAll(/data-band="([^"]+)"/g)].map((m) => m[1])).toEqual(expected);
  expect(sketch).not.toMatch(/NaN|undefined/);
  expect(sketch).not.toContain("В —");
});

it("shows a short actionable error for invalid stored programs without exposing provenance", () => {
  const { operation } = programs[0];
  const coax = operation.fields.coax;
  for (const boundaries of [[], [...coax.stripProgram.boundaries].reverse()]) {
    const sketch = etkDrawCoax({ ...operation, fields: { coax: { ...coax, stripProgram: { ...coax.stripProgram, boundaries } } } });
    expect(sketch).toContain("размеры от торца требуют проверки");
    expect(sketch).not.toContain("<svg");
    expect(sketch).not.toContain(coax.stripProgram.source);
    expect(sketch).not.toContain(coax.stripProgram.note);
  }
});
