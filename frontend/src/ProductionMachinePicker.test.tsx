import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionMachinePicker } from "./ProductionMachinePicker";

const resources = { enabled: true, task_version: 2, site: "ЦАР", blocked_reason: null,
  occupancy_rule: "Станки заняты до завершения всей группы", requirements: [{ etk_position: 3, family: "Резка",
    machines: [{ id: "ОБ.001:1", name: "Станок", model: "EW", site: "ЦАР" }, { id: "ОБ.001:2", name: "Станок", model: "EW", site: "ЦАР" }] }] };
afterEach(() => vi.restoreAllMocks());

it("requires an explicit physical choice and reports issued ETK position, inventory and site", async () => {
  const changed = vi.fn();
  render(<ProductionMachinePicker selectionKey="a" resources={resources} onChange={changed} />);
  expect(screen.getByLabelText("Станок · операция ЭТК 3")).toHaveValue("");
  expect(screen.getByText(/Станки заняты до завершения/)).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /ОБ\.001:2.*ЦАР/ })).toBeInTheDocument();
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ key: "a", ready: false, bindings: undefined }));
  fireEvent.change(screen.getByLabelText("Станок · операция ЭТК 3"), { target: { value: "ОБ.001:2" } });
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ key: "a", ready: true, bindings: [{ etk_position: 3, machine_id: "ОБ.001:2" }] }));
});

it("invalidates a choice when operator, task version or preview changes", async () => {
  const changed = vi.fn();
  const view = render(<ProductionMachinePicker selectionKey="old" resources={resources} onChange={changed} />);
  fireEvent.change(screen.getByLabelText("Станок · операция ЭТК 3"), { target: { value: "ОБ.001:1" } });
  view.rerender(<ProductionMachinePicker selectionKey="new" resources={resources} onChange={changed} />);
  expect(screen.getByLabelText("Станок · операция ЭТК 3")).toHaveValue("");
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ key: "new", ready: false, bindings: undefined }));
});

it("keeps blocked/missing equipment unavailable and does not turn failed loading into runtime-off", async () => {
  const changed = vi.fn();
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Нет связи"));
  render(<ProductionMachinePicker selectionKey="a" taskId="t" operationId="o" version={2} operatorIds={["u"]} onChange={changed} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
  expect(changed).toHaveBeenLastCalledWith({ key: "a", ready: false, bindings: undefined });
});

it.each([null, { enabled: false }])("preserves runtime-off without sending invented bindings", async (resources) => {
  const changed = vi.fn();
  render(<ProductionMachinePicker selectionKey="off" resources={resources} onChange={changed} />);
  await waitFor(() => expect(changed).toHaveBeenLastCalledWith({ key: "off", ready: true, bindings: undefined }));
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

it("blocks a stale task version and reports the backend reason instead of offering a guess", () => {
  const changed = vi.fn();
  const view = render(<ProductionMachinePicker selectionKey="stale" resources={resources} version={3} onChange={changed} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Задание изменилось");
  expect(changed).toHaveBeenLastCalledWith({ key: "stale", ready: false, bindings: undefined });
  view.rerender(<ProductionMachinePicker selectionKey="blocked" resources={{ ...resources, blocked_reason: "Нет выданной ЭТК" }} onChange={changed} />);
  expect(screen.getByRole("alert")).toHaveTextContent("Нет выданной ЭТК");
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
});

it("sends an explicit empty binding list for a known manual operation", () => {
  const changed = vi.fn();
  render(<ProductionMachinePicker selectionKey="manual" resources={{ ...resources, requirements: [] }} onChange={changed} />);
  expect(changed).toHaveBeenLastCalledWith({ key: "manual", ready: true, bindings: [] });
  expect(screen.getByText("Станок не требуется")).toBeInTheDocument();
});
