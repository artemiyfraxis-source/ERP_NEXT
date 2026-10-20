import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingSettings } from "./PurchasingSettings";

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

const rows = [
  { key: "delivery_default", label: "Доставка для подбора КП, ₽", hint: "", kind: "decimal", value: "1500", default: "1500", minimum: "0", maximum: "1000000" },
  { key: "letter_requisites", label: "Реквизиты для писем", hint: "", kind: "text", value: "ООО «ЗЭП „ФРАКСИС“», ИНН 5040191181", default: "", minimum: null, maximum: null },
];

it("«Реквизиты для писем» is a text: edited in a box and saved with its commas", async () => {
  const puts: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PUT") {
      puts.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify(rows), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    if (path.includes("/me")) return new Response(JSON.stringify({ user: { id: "u", name: "Админ" }, roles: ["admin"], access_level: "admin", permissions: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(rows), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  render(<PurchasingSettings />);
  const box = await screen.findByRole("textbox", { name: "Реквизиты для писем" });
  expect(box.tagName).toBe("TEXTAREA");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: "ООО Тест, ИНН 7700000000\nМосква, ул. Ленина, 1" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(puts).toEqual([{ changes: { letter_requisites: "ООО Тест, ИНН 7700000000\nМосква, ул. Ленина, 1" } }]));
});
