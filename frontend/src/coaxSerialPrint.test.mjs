import { expect, it } from "vitest";
import snapshot from "./__fixtures__/coaxSerialEtk.json";
import { etkMarkLabelsPage, etkPrintPages, etkSerialMarks } from "./etkPrint";
import { buildPrintDocumentHtml } from "./print";

// The ETK in the fixture is what backend `build_etk` makes of a generated КБ1 coax config with the FRX centre label, and the
// serial range is `mark_serial_range` for a 15-piece task (backend/tests/techcards/test_coax_serial_fixture.py keeps it current;
// the frontend CI job has no Python).
it("prints 15 distinct FRX labels from a generated КБ1 config through the backend ETK and task serial range", () => {
  const { config, document, numbered, serial } = structuredClone(snapshot);

  expect(numbered).toBe(true);
  expect(serial).toEqual({ first: 1, last: 15, task: "ПЗ-0001" });
  expect(etkSerialMarks(document)).toHaveLength(1); // Printing and installation must not double the labels.
  const card = { project: "708К.", item_index: "КБ1", product_name: config.assemblyName, revision: 1, document };
  const labels = etkMarkLabelsPage(card, serial);
  const expected = Array.from({ length: 15 }, (_, index) => [String(index + 1), `FRX.708К.120907/AA.${index + 1}`]);
  expect(labels.blocks[0]).toEqual({ table: { columns: ["№ изделия", "Кабель · Лента Vell VL-S641, 18 мм"], rows: expected } });
  const pages = etkPrintPages(card, [], {}, serial);
  expect(pages.at(-1)).toEqual(labels);
  const labelHtml = buildPrintDocumentHtml("Надписи", [labels]);
  for (const [, text] of expected) expect(labelHtml).toContain(`<td>${text}</td>`);
  expect(labelHtml).not.toContain("{№}");
  for (const operation of document.operations) {
    for (const step of operation.card?.steps ?? []) expect(step.text).not.toContain("{№}");
  }
  expect(document.warnings.join(" ")).toContain("лента из состава не расходуется");
});
