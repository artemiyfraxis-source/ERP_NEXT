import { expect, it } from "vitest";
import { shipmentTitle } from "./shipmentTitle";

const line = (project: string, item_index: string, picked = "1") => ({ project, item_index, picked }) as never;

it("называет отправку проектом, КБ и PL без номера ОТГ", () => {
  expect(shipmentTitle({ lines: [line("828К.", "КБ3")], pl_number: "PL828К.-1" })).toBe("828К. · КБ3 · PL828К.-1");
  expect(shipmentTitle({ lines: [line("828К.", "КБ1"), line("533К.11", "КБ2"), line("828К.", "КБ1")], pl_number: "" })).toBe("828К. + 533К.11 · КБ1, КБ2");
  expect(shipmentTitle({ lines: ["КБ1", "КБ2", "КБ3", "КБ4", "КБ5"].map((kb) => line("711К.3", kb)), pl_number: "PL711К.3-1" })).toBe("711К.3 · КБ1, КБ2, КБ3 и ещё 2 · PL711К.3-1");
});
