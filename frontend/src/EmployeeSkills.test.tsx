import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { EmployeeSkills } from "./EmployeeSkills";
import type { EmployeeCardSkill } from "./api";

const SKILLS: EmployeeCardSkill[] = [
  { operation: "PRS_TERM", allowed: false, stars: 0, coefficient: "1" },
  { operation: "CUT_WIRE", name: "Резка провода", allowed: true, stars: 3, coefficient: "1.25" },
  { operation: "SLD_JOINT", name: "Пайка", allowed: true, stars: 5, coefficient: "1.00" },
];

it("shows the competences as a dense table: allowed first, stars, coefficient, code as a small second line", () => {
  render(<EmployeeSkills skills={SKILLS} personnel={{ tab_number: "17", position: "Монтажник", category: "3 разряд", since: "2025-03-01", manager_name: null }} />);

  const qualification = screen.getByRole("region", { name: "Квалификация" });
  expect(qualification).toHaveTextContent("3 разряд");
  expect(qualification).toHaveTextContent("Монтажник");
  expect(qualification).toHaveTextContent("01.03.2025");
  expect(qualification).toHaveTextContent("2 из 3");

  const table = screen.getByRole("region", { name: "Допуски к операциям" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows[0]).toHaveTextContent("Пайка");
  expect(rows[0]).toHaveTextContent("Допущен");
  expect(rows[0]).toHaveTextContent("×1");
  expect(within(rows[0]).getByLabelText("5 из 5")).toBeInTheDocument();
  expect(rows[1]).toHaveTextContent("Резка провода");
  expect(rows[1]).toHaveTextContent("CUT_WIRE");
  expect(rows[1]).toHaveTextContent("×1,25");
  expect(rows[2]).toHaveTextContent("операция без названия");
  expect(rows[2]).toHaveTextContent("Нет допуска");
  expect(within(table).queryByText("PRS_TERM")).toBeNull(); // код без названия на экран не выходит
});

it("says there is no data instead of inventing a qualification", () => {
  render(<EmployeeSkills skills={[]} personnel={null} />);
  expect(screen.getAllByText("нет данных")).toHaveLength(3);
  expect(screen.getByText("Допусков в матрице компетенций нет.")).toBeInTheDocument();
});
