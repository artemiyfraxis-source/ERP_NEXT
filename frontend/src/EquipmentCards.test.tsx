import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { referencesApi, type TechOperationRecord } from "./api";
import { EquipmentCards } from "./EquipmentCards";
import { TARGETS_KEY } from "./equipmentModel";
import type { EquipmentTab } from "./referenceTabs";

beforeEach(() => { vi.spyOn(referencesApi, "crimpTargets").mockResolvedValue({ by_code: {} }); });
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

function capabilities(tab: EquipmentTab, code = "A14", fail = false, fieldColumns: string[] = [TARGETS_KEY]) {
  vi.spyOn(referencesApi, "crimpCodes").mockResolvedValue({ owners: {}, missing: [] });
  vi.spyOn(referencesApi, "equipmentModels").mockResolvedValue({ models: [] });
  vi.spyOn(referencesApi, "equipmentLinksReport").mockRejectedValue(new Error("unavailable"));
  vi.mocked(referencesApi.crimpTargets).mockResolvedValue({ by_code: { A14: [
    { series: "SHR, SHD", maker: "JST", articles: ["SSH-003GA-P0.2"], doubtful: false },
    { series: "SHR, SHD", maker: "JST", articles: ["SSH-004"], doubtful: true },
  ] } });
  if (fail) vi.mocked(referencesApi.crimpTargets).mockRejectedValue(new Error("Нет связи с ERP"));
  const record = { id: "linked", tab, display_text: "Аппликатор", source_sheet: "", fields: {
    Тип: tab === "ob_applicators" ? "Аппликаторы" : tab === "ob_dies" ? "Матрицы" : "Обжимной инструмент",
    Наменование: "Обжим", "Инв. номер": "ОБ.001", "Код в базе терминалов": code, Артикул: "APLMK",
    [TARGETS_KEY]: JSON.stringify([
      { kind: "series", key: "s1", label: "JST SHR, SHD" },
      { kind: "series", key: "shr", label: "JST SHR" },
      { kind: "series", key: "shd", label: "SHD" },
      { kind: "series", key: "s2", label: "GHR" },
      { kind: "series", key: "s2", label: "GHR" },
      { kind: "series", key: "sh", label: "SH" },
    ]),
  } } as TechOperationRecord;
  render(<EquipmentCards records={[record]} operations={[]} tab={tab} fieldColumns={fieldColumns} canEdit={false}
    onEdit={vi.fn()} onCreate={vi.fn()} onChanged={vi.fn()} onNotice={vi.fn()} onError={vi.fn()} />);
}

it.each(["ob_applicators", "ob_dies", "ob_tools"] as const)("shows linked contacts and deduplicated manual series on %s", async (tab) => {
  capabilities(tab);
  const contact = await screen.findByText("SSH-003GA-P0.2");
  const cell = contact.closest("td")!;
  expect(cell).toHaveTextContent("SHR, SHD: SSH-003GA-P0.2, SSH-004?GHRSH");
  expect(within(cell).getByTitle("связь под вопросом в БД.ТЕР")).toHaveTextContent("SSH-004?");
  expect(within(cell).getAllByText("GHR")).toHaveLength(1);
  expect(within(cell).queryByText("JST SHR, SHD")).not.toBeInTheDocument();
  expect(within(cell).queryByText("JST SHR")).not.toBeInTheDocument();
  expect(within(cell).queryByText("SHD")).not.toBeInTheDocument();
  expect(within(cell).getByText("SH")).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader", { name: /Обжимает/ })).toHaveLength(1);
  expect(screen.getByRole("columnheader", { name: tab === "ob_applicators" ? /^Артикул аппликатора$/ : /^Артикул$/ })).toBeInTheDocument();
});

it("exports the displayed contacts, uncertainty markers and manual series", async () => {
  let blob: Blob | null = null;
  vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:x"; });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  capabilities("ob_applicators");
  await screen.findByText("SSH-003GA-P0.2");
  fireEvent.click(screen.getByRole("button", { name: "Excel" }));
  const text = new TextDecoder("utf-16le").decode(await blob!.arrayBuffer());
  expect(text).toContain("Обжимает (серии · артикулы контактов)");
  expect(text).toContain("SHR, SHD: SSH-003GA-P0.2, SSH-004? GHR");
  expect(text).not.toContain("JST SHR, SHD");
});

it("retains manual targets for a tool without a code", async () => {
  capabilities("ob_dies", "");
  await waitFor(() => expect(referencesApi.crimpTargets).toHaveBeenCalled());
  expect(screen.queryByText("SSH-003GA-P0.2")).not.toBeInTheDocument();
  expect(screen.getByText("JST SHR, SHD")).toBeInTheDocument();
  expect(screen.getAllByText("GHR")).toHaveLength(1);
});

it("shows the derived column even when the raw target field is absent from column metadata", async () => {
  capabilities("ob_applicators", "A14", false, []);
  expect(await screen.findByText("SSH-003GA-P0.2")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /Обжимает/ })).toBeInTheDocument();
});

it("shows a visible error when terminal links cannot be loaded", async () => {
  capabilities("ob_dies", "A14", true);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  expect(screen.getByText("GHR")).toBeInTheDocument();
});
