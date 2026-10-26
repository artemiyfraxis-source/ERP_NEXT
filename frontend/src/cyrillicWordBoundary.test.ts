// @vitest-environment node
import { expect, it } from "vitest";
import { etkToleranceIsReference } from "./etkOperation";
import { cableCategory } from "./cableCategory";
import { markTubeShrinks } from "./tcGenMarks";

// `\b` в JS не видит кириллицу — «\bтут», «\bпвс», «\bКД\b» не срабатывали никогда (26.10.2026, 708К.5: трубка
// маркировки проводов «ТУТ 2/1 белая» выпадала из выбора).
it("трубка маркировки: ТУТ / ТТК без «мм» в наименовании — термоусадка", () => {
  expect(markTubeShrinks("ТУТ 2/1 белая")).toBe(true);
  expect(markTubeShrinks("ТТК 3/1 черная")).toBe(true);
  expect(markTubeShrinks("Бирка флажковая")).toBe(false);
});

it("кабель ПВС — силовой", () => {
  expect(cableCategory({ "Марка": "ПВС 2х0,75" }).category).toBe("Силовые");
});

it("ссылка на КД / ТУ / ОСТ — не размер; «ТУТ 4/2» — размер", () => {
  expect(etkToleranceIsReference("Согласно КД")).toBe(true);
  expect(etkToleranceIsReference("ТУ 16-505.656")).toBe(true);
  expect(etkToleranceIsReference("ОСТ 92-0286-2000")).toBe(true);
  expect(etkToleranceIsReference("ТУТ 4/2, 20 мм")).toBe(false);
});
