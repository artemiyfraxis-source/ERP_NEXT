import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { referencesApi } from "./api";
import { EquipmentForm } from "./EquipmentForm";

afterEach(() => vi.restoreAllMocks());

function Form({ initial = {}, isNew = true }: { initial?: Record<string, string>; isNew?: boolean }) {
  const [values, setValues] = useState(initial);
  return <><EquipmentForm values={values} onChange={setValues} isNew={isNew} records={[]} />
    <output aria-label="Сохранённый номер">{values["Инв. номер"]}</output></>;
}

it("uses the entered applicator code as a read-only number and never requests an automatic number", async () => {
  const proposal = vi.spyOn(referencesApi, "equipmentNumber");
  render(<Form initial={{ Тип: "Аппликаторы", "Инв. номер": "ОБ.106", "Код в базе терминалов": "m10" }} />);
  const number = screen.getByRole("textbox", { name: "Инв. номер" });
  expect(number).toHaveValue("M10");
  expect(number).toHaveAttribute("readonly");
  expect(screen.queryByPlaceholderText("назначится сам")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Код в базе терминалов" }), { target: { value: "S3" } });
  await waitFor(() => expect(screen.getByLabelText("Сохранённый номер")).toHaveTextContent("S3"));
  expect(proposal).not.toHaveBeenCalled();
  fireEvent.change(screen.getByRole("textbox", { name: "Код в базе терминалов" }), { target: { value: "" } });
  expect(number).toHaveValue("");
  expect(screen.getByRole("textbox", { name: "Код в базе терминалов" })).toBeRequired();
});

it("offers measuring instruments and follows its separate number when switching from applicators", async () => {
  const proposal = vi.spyOn(referencesApi, "equipmentNumber").mockResolvedValue({ inventory: "СИ.003", row: "4" });
  render(<Form initial={{ Тип: "Аппликаторы", "Код в базе терминалов": "A1" }} />);
  fireEvent.change(screen.getByRole("combobox", { name: "Тип" }), { target: { value: "Средства измерений" } });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Инв. номер" })).toHaveValue("СИ.003"));
  expect(proposal).toHaveBeenCalledWith("Средства измерений");
});

it("offers supporting equipment with the shared ОБ number sequence", async () => {
  const proposal = vi.spyOn(referencesApi, "equipmentNumber").mockResolvedValue({ inventory: "ОБ.204", row: "204" });
  render(<Form />);
  fireEvent.change(screen.getByRole("combobox", { name: "Тип" }), { target: { value: "Обеспечивающее оборудование" } });
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Инв. номер" })).toHaveValue("ОБ.204"));
  expect(proposal).toHaveBeenCalledWith("Обеспечивающее оборудование");
  expect(screen.queryByRole("textbox", { name: "Код в базе терминалов" })).not.toBeInTheDocument();
});

it("keeps the inventory number when editing reclassified supporting equipment", () => {
  const proposal = vi.spyOn(referencesApi, "equipmentNumber");
  render(<Form isNew={false} initial={{ Тип: "Обеспечивающее оборудование", "Инв. номер": "ОБ.200" }} />);
  expect(screen.getByRole("combobox", { name: "Тип" })).toHaveValue("Обеспечивающее оборудование");
  expect(screen.getByRole("textbox", { name: "Инв. номер" })).toHaveValue("ОБ.200");
  expect(proposal).not.toHaveBeenCalled();
});

it("ignores an old proposal after switching to applicators", async () => {
  let resolve!: (value: { inventory: string; row: string }) => void;
  vi.spyOn(referencesApi, "equipmentNumber").mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<Form initial={{ Тип: "Оборудование" }} />);
  fireEvent.change(screen.getByRole("combobox", { name: "Тип" }), { target: { value: "Аппликаторы" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Код в базе терминалов" }), { target: { value: "A8" } });
  await act(async () => resolve({ inventory: "ОБ.198", row: "200" }));
  expect(screen.getByLabelText("Сохранённый номер")).toHaveTextContent("A8");
});

it("keeps name edits made while an automatic proposal is loading", async () => {
  let resolve!: (value: { inventory: string; row: string }) => void;
  vi.spyOn(referencesApi, "equipmentNumber").mockReturnValue(new Promise((done) => { resolve = done; }));
  render(<Form initial={{ Тип: "Средства измерений" }} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Наименование" }), { target: { value: "Микрометр" } });
  await act(async () => resolve({ inventory: "СИ.001", row: "1" }));
  expect(screen.getByRole("textbox", { name: "Наименование" })).toHaveValue("Микрометр");
  expect(screen.getByRole("textbox", { name: "Инв. номер" })).toHaveValue("СИ.001");
});

it("shows the code while editing a legacy applicator", () => {
  const proposal = vi.spyOn(referencesApi, "equipmentNumber");
  render(<Form isNew={false} initial={{ Тип: "Аппликаторы", "Инв. номер": "ОБ.107", "Код в базе терминалов": "M2" }} />);
  expect(screen.getByLabelText("Сохранённый номер")).toHaveTextContent("M2");
  expect(proposal).not.toHaveBeenCalled();
});
