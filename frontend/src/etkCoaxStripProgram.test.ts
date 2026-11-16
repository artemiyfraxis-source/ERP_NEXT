// @vitest-environment node
import { expect, it } from "vitest";
import programs from "./__fixtures__/coaxStripPrograms.json";
import { etkDrawCoax, etkParams } from "./etkDraw";

it.each(programs)("renders the backend $kind program in the sketch and ETK fields", ({ kind, operation }) => {
  const svg = etkDrawCoax(operation), params = JSON.stringify(etkParams(operation));
  const program = operation.fields.coax.stripProgram;
  expect(svg).toContain("от торца жилы");
  for (const b of program.boundaries) {
    expect(svg).toContain(`${b.label} = ${b.value} ${b.tolerance} мм`);
    expect(params).toContain(`${b.label} от торца`);
    expect(params).toContain(`${b.value} ${b.tolerance} мм`);
  }
  expect(params).toContain(program.source);
  expect(params).toContain(program.note);
  const bands = [...svg.matchAll(/data-band="([^"]+)"/g)].map((m) => m[1]);
  expect(bands).toEqual(["conductor", "dielectric", ...(["hfm", "bulkhead"].includes(kind) ? ["foil"] : []), "braid", "jacket"]);
  if (kind === "hfm") {
    expect(svg).toContain("Конечная геометрия");
    expect(svg).toContain("подтвердить программу станка");
    expect(svg).toContain("Отогнутая оплётка = 4,40 ±0,6");
    expect(svg).toContain("Фольга = 3,35 минимум номинально");
  }
  if (kind === "bulkhead") {
    expect(svg).toContain("Фольга = 3,7 ±0,2");
    expect(svg).toContain("Оплётка = 8,5 ±0,2");
    expect(svg).toContain("Оболочка = 13,9 ±0,2");
  }
});
