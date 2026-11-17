import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, DELIVERY_FIELDS, shippingApi, type ItemAddressee } from "./api";
import { ItemDeliveryWindow } from "./ProjectData";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const item = (item_index: string, version = 0): ItemAddressee => ({
  project: "591К.3", item_index, version, name: "Жгут", overrides: {},
  effective: { receiver: "Получатель", shipping_point: "Москва", phone: "123", contact: "Иванов", terms: "ДОСТАВКА ТК", carrier: "СДЭК", upd_type: "ЭДО" },
  provenance: Object.fromEntries(DELIVERY_FIELDS.map((key) => [key, "project"])) as ItemAddressee["provenance"], updated_by: "", updated_at: null,
});

it("deduplicates receipt rows and explicitly clears only the edited field", async () => {
  const save = vi.spyOn(shippingApi, "saveItemAddressee").mockResolvedValue({ project: "591К.3", items: [] });
  const close = vi.fn();
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1", 2), item("КБ1", 2)]} onSaved={vi.fn()} close={close} />);
  fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(save).toHaveBeenCalledWith("591К.3", { item_index: "КБ1", version: 2, fields: { phone: "" } });
});

it("shows effective values with no inheritance controls and saves only the changed recipient", async () => {
  const save = vi.spyOn(shippingApi, "saveItemAddressee").mockResolvedValue({ project: "591К.3", items: [] });
  const project = vi.spyOn(shippingApi, "saveAddressee");
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1", 3)]} onSaved={vi.fn()} close={vi.fn()} />);
  expect(screen.queryByText(/Как у проекта|Задано для КБ|Не менять/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Применить:/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Организация")).toHaveValue("Получатель");
  expect(screen.getByLabelText("Получатель")).toHaveValue("Иванов");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "Петров" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("591К.3", { item_index: "КБ1", version: 3, fields: { contact: "Петров" } }));
  expect(project).not.toHaveBeenCalled();
});

it("bulk saves explicit overrides and keeps drafts after an atomic version conflict", async () => {
  const save = vi.spyOn(shippingApi, "saveItemAddressees")
    .mockRejectedValueOnce(new ApiError("Доставка КБ2 изменилась", 409))
    .mockResolvedValue({ project: "591К.3", items: [] });
  vi.spyOn(shippingApi, "itemAddressees").mockResolvedValue({ project: "591К.3", items: [item("КБ1", 4), { ...item("КБ2", 8), effective: { ...item("КБ2").effective, phone: "999" } }] });
  const close = vi.fn();
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1", 4), item("КБ2", 7)]} onSaved={vi.fn()} close={close} />);
  fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "456" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Доставка КБ2 изменилась");
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Телефон")).toHaveValue("456");
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  await screen.findByText("Сейчас: 123; 999");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(save).toHaveBeenLastCalledWith("591К.3", [
    { item_index: "КБ1", version: 4, fields: { phone: "456" } },
    { item_index: "КБ2", version: 8, fields: { phone: "456" } },
  ]);
});

it("shows mixed addresses and saves only an edited phone to every selected KB", async () => {
  const save = vi.spyOn(shippingApi, "saveItemAddressees").mockResolvedValue({ project: "591К.3", items: [] });
  const second = { ...item("КБ2", 5), effective: { ...item("КБ2").effective, shipping_point: "Тула" } };
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1", 2), second]} onSaved={vi.fn()} close={vi.fn()} />);
  expect(screen.getByLabelText("Адрес доставки")).toHaveValue("");
  expect(screen.getByLabelText("Адрес доставки")).toHaveAttribute("placeholder", "разные");
  expect(screen.getByLabelText("Телефон")).toHaveValue("123");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "456" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith("591К.3", [
    { item_index: "КБ1", version: 2, fields: { phone: "456" } },
    { item_index: "КБ2", version: 5, fields: { phone: "456" } },
  ]));
});

it("disables saving again when an edit is reverted to the effective value", () => {
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1")]} onSaved={vi.fn()} close={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "Петров" } });
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "Иванов" } });
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
});

it("refreshes a reverted edit after conflict without overwriting another employee's value", async () => {
  const save = vi.spyOn(shippingApi, "saveItemAddressee")
    .mockRejectedValueOnce(new ApiError("Доставка изменилась", 409))
    .mockResolvedValue({ project: "591К.3", items: [] });
  vi.spyOn(shippingApi, "itemAddressees").mockResolvedValue({ project: "591К.3", items: [
    { ...item("КБ1", 3), effective: { ...item("КБ1").effective, phone: "999" } },
  ] });
  const close = vi.fn();
  render(<ItemDeliveryWindow project="591К.3" items={[item("КБ1", 2)]} onSaved={vi.fn()} close={close} />);
  fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "456" } });
  fireEvent.change(screen.getByLabelText("Телефон"), { target: { value: "123" } });
  fireEvent.change(screen.getByLabelText("Получатель"), { target: { value: "Петров" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Доставка изменилась");
  expect(save).toHaveBeenLastCalledWith("591К.3", { item_index: "КБ1", version: 2, fields: { contact: "Петров" } });
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
  await waitFor(() => expect(screen.getByLabelText("Телефон")).toHaveValue("999"));
  expect(screen.getByLabelText("Получатель")).toHaveValue("Петров");
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(save).toHaveBeenLastCalledWith("591К.3", { item_index: "КБ1", version: 3, fields: { contact: "Петров" } });
});
