import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { MarkTable } from "./GenMarkTable";
import { newMarkRow } from "./tcGenMarks";

it("«Вид / материал» — выбор Термотрубка / Хлястик; старое сохранённое значение остаётся в списке строки как есть", () => {
  render(<MarkTable enabled onEnabled={() => undefined} hint="" onChange={() => undefined} targets={[]} comps={[]} mats={[]}
    rows={[newMarkRow({ text: "X1", material: "Термотрубка" }), newMarkRow({ text: "X2", material: "Маркировка" })]} />);

  const fresh = screen.getByLabelText("Материал 1") as HTMLSelectElement;
  expect(within(fresh).getAllByRole("option").map((option) => option.textContent)).toEqual(["Термотрубка", "Хлястик"]);
  const legacy = screen.getByLabelText("Материал 2") as HTMLSelectElement;
  expect(legacy).toHaveValue("Маркировка");
  expect(within(legacy).getAllByRole("option").map((option) => option.textContent)).toEqual(["Термотрубка", "Хлястик", "Маркировка"]);
});
