// @vitest-environment node
import { expect, it } from "vitest";
import fixture from "./fixtures/ds1071OpenEndKb2.json";
import { etkWireColor } from "./etkDraw";
import { etkPfArt } from "./etkPfArt";
import { etkPfGroups } from "./etkRoute";

/*
 * 713К.4 КБ2 «Кабельная сборка I-DS1071-SCB01X3 to Open-End НВ4 26AWG 160mm» (прод, 19.10.2026): разъём с открытыми
 * концами. У изделия был пустой контур «Раз…» — у разъёма генератора нет обозначения и списка проводов, обзору жгута нечего
 * соединять. Теперь изделие — разъём видом сверху и провода от его контактов (владелец 19.10).
 */
const document = fixture.document as Record<string, unknown>;
const operations = document.operations as Array<Record<string, unknown>>;
const groups = etkPfGroups(document.route as never, operations as never);

it("draws the final ПФ of a connector with open ends as the connector's top view with its wires, not an empty box", () => {
  const group = groups.find((item) => item.final)!;
  const art = etkPfArt({ group, operations: group.ops.map((position) => operations[position - 1]), inputs: [], document, batch: 500 });
  const svg = art?.main && "svg" in art.main ? art.main.svg : "";
  expect(svg).toContain('data-view="top"');
  expect(svg).toContain("Connfly DS1071");
  expect(svg).not.toMatch(/Раз…|>Разъём</);
  // Каждый провод — своим цветом от своего контакта.
  for (const wire of ["НВ4 0,2 зеленый", "НВ4 0,2 желтый", "НВ4 0,2 черный"]) expect(svg).toContain(etkWireColor(wire));
});
