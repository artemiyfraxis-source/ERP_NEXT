import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PackDecision } from "./shippingCells";
import { setRightsPrincipal } from "./rights";
afterEach(() => { setRightsPrincipal(null); vi.unstubAllGlobals(); });
const permission = { item_index: "КБ1", name: "Сборка", quantity: "10", consumed: "4", remaining: "6", physical_remaining: "8", version: 3 };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
async function open() { render(<PackDecision project="П" editable />); fireEvent.click(screen.getByRole("button", { name: "Разрешить упаковку по КБ" })); }
describe("PackDecision", () => {
  it("posts only explicitly selected KB and absolute cap with CAS version", async () => {
    const fetcher = vi.fn(async (_url: unknown, _init?: RequestInit) => json({ permissions: [permission, { ...permission, item_index: "КБ2" }] }));
    vi.stubGlobal("fetch", fetcher); await open();
    const input = await screen.findByLabelText("Общий лимит КБ1");
    expect(screen.getByRole("button", { name: /Сохранить разрешения/ })).toBeDisabled();
    expect(screen.getByLabelText("Изменить КБ2")).not.toBeChecked();
    fireEvent.change(input, { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ project: "П", lines: [{ item_index: "КБ1", quantity: "12", version: 3 }] });
  });
  it("rejects a cap below consumed without a write", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.change(await screen.findByLabelText("Общий лимит КБ1"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("не может быть меньше");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("CAS reload preserves input and uses refreshed version on retry", async () => {
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url, init) => init?.method === "POST"
      ? (++posts === 1 ? json({ detail: "Конфликт" }, 409) : json({ permissions: [{ ...permission, version: 5 }] }))
      : json({ permissions: [{ ...permission, version: posts ? 4 : 3 }] })));
    await open(); fireEvent.change(await screen.findByLabelText("Общий лимит КБ1"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(screen.getByLabelText("Общий лимит КБ1")).toHaveValue(12);
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[3][1]?.body)).lines[0].version).toBe(4);
  });
  it("failed loading invents no permission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "Нет связи" }, 503))); await open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
  it("warehouse cannot edit manager permission", async () => {
    setRightsPrincipal({ user: { id: "u", name: "Склад", email: "s@example.test", department: "Склад" }, roles: ["receiver"], permissions: { shipping: "view", shipping_goods: "edit" } });
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] })));
    render(<PackDecision project="П" editable />); fireEvent.click(screen.getByRole("button", { name: "Разрешения упаковки по КБ" }));
    expect(await screen.findByLabelText("Изменить КБ1")).toBeDisabled();
    expect(screen.queryByLabelText("Общий лимит КБ1")).not.toBeInTheDocument();
  });
});
