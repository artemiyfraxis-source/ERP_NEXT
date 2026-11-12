import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { referencesApi, type TechOperationRecord } from "./api";
import { EquipmentCards } from "./EquipmentCards";

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

function card(type: string, inventory: string, code?: string) {
  vi.spyOn(referencesApi, "crimpCodes").mockResolvedValue({ owners: {}, missing: [] });
  vi.spyOn(referencesApi, "equipmentModels").mockResolvedValue({ models: [] });
  vi.spyOn(referencesApi, "equipmentLinksReport").mockRejectedValue(new Error("unavailable"));
  const record = { id: "unit", tab: "ob", display_text: "Пресс", source_sheet: "", fields: {
    Тип: type, "Инв. номер": inventory, Наменование: "Пресс", ...(code === undefined ? {} : { "Код в базе терминалов": code }),
  } } as TechOperationRecord;
  render(<EquipmentCards records={[record]} operations={[]} canEdit focusId="unit" onEdit={vi.fn()} onCreate={vi.fn()}
    onChanged={vi.fn()} onNotice={vi.fn()} onError={vi.fn()} />);
}

it("shows an applicator's code, makes its number read-only and removes automatic duplication", async () => {
  card("Аппликаторы", "ОБ.106", "M10");
  await waitFor(() => expect(referencesApi.crimpCodes).toHaveBeenCalled());
  expect(screen.queryByText("ОБ.106")).not.toBeInTheDocument();
  expect(screen.getAllByText("M10").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Ещё такой же" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Инв\. №.*править/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Код терминалов.*править/ })).toBeInTheDocument();
});

it("lets the owner supply a missing applicator code without suggesting one", async () => {
  card("Аппликаторы", "ОБ.126");
  fireEvent.click(screen.getByRole("button", { name: /Код терминалов.*править/ }));
  expect(screen.getByRole("textbox", { name: /Код терминалов/ })).toHaveValue("");
  await waitFor(() => expect(referencesApi.crimpCodes).toHaveBeenCalled());
});

it("keeps automatic additional units for measuring instruments", async () => {
  card("Средства измерений", "СИ.001");
  expect(screen.getByRole("button", { name: "Ещё такой же" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Инв\. № СИ\.001.*править/ })).toBeInTheDocument();
  await waitFor(() => expect(referencesApi.crimpCodes).toHaveBeenCalled());
});
