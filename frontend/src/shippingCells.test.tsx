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
    const input = await screen.findByLabelText("Разрешить ещё КБ1");
    expect(screen.getByRole("button", { name: /Сохранить разрешения/ })).toBeDisabled();
    expect(screen.getByLabelText("Изменить КБ2")).not.toBeChecked();
    fireEvent.change(input, { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ project: "П", lines: [{ item_index: "КБ1", quantity: "12", version: 3 }] });
  });
  it("rejects negative remaining permission without a write", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.change(await screen.findByLabelText("Разрешить ещё КБ1"), { target: { value: "-2" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("неотрицательное");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("CAS reload preserves input and uses refreshed version on retry", async () => {
    let posts = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url, init) => init?.method === "POST"
      ? (++posts === 1 ? json({ detail: "Конфликт" }, 409) : json({ permissions: [{ ...permission, version: 5 }] }))
      : json({ permissions: [{ ...permission, version: posts ? 4 : 3 }] })));
    await open(); fireEvent.change(await screen.findByLabelText("Разрешить ещё КБ1"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
    expect(screen.getByLabelText("Разрешить ещё КБ1")).toHaveValue(8);
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
    expect(screen.queryByLabelText("Разрешить ещё КБ1")).not.toBeInTheDocument();
  });
  it.each([
    { quantity: "0", consumed: "0", remaining: "0", physical_remaining: "330", expected: "330" },
    { quantity: "24", consumed: "24", remaining: "0", physical_remaining: "476", expected: "500" },
  ])("selecting a blocked KB proposes its physical remainder ($expected total)", async (values) => {
    const fetcher = vi.fn(async () => json({ permissions: [{ ...permission, ...values }] }));
    vi.stubGlobal("fetch", fetcher); await open();
    expect(await screen.findByLabelText("Разрешить ещё КБ1")).toHaveValue(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("Изменить КБ1"));
    expect(screen.getByLabelText("Разрешить ещё КБ1")).toHaveValue(Number(values.physical_remaining));
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body)).lines).toEqual([{ item_index: "КБ1", quantity: values.expected, version: 3 }]);
  });
  it("preserves unused permission above physical stock and prevents no-op success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [{ ...permission, quantity: "24", remaining: "20" }] })));
    await open(); expect(await screen.findByLabelText("Разрешить ещё КБ1")).toHaveValue(20);
    fireEvent.click(screen.getByLabelText("Изменить КБ1"));
    expect(screen.getByLabelText("Разрешить ещё КБ1")).toHaveValue(20);
    expect(screen.getByRole("button", { name: /Сохранить разрешения/ })).toBeDisabled();
    expect(screen.queryByText("Разрешения упаковки сохранены")).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("unchecking a proposed grant does not save it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.click(await screen.findByLabelText("Изменить КБ1"));
    fireEvent.click(screen.getByLabelText("Изменить КБ1"));
    expect(screen.getByRole("button", { name: /Сохранить разрешения/ })).toBeDisabled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("selecting a partially permitted KB does not expand its existing permission", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.click(await screen.findByLabelText("Изменить КБ1"));
    expect(screen.getByLabelText("Разрешить ещё КБ1")).toHaveValue(6);
    expect(screen.getByRole("button", { name: /Сохранить разрешения/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Разрешить ещё КБ1"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("Изменить КБ1"));
    fireEvent.click(screen.getByLabelText("Изменить КБ1"));
    expect(screen.getByLabelText("Разрешить ещё КБ1")).toHaveValue(2);
  });
  it("rejects excessive precision rather than rounding a grant", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.change(await screen.findByLabelText("Разрешить ещё КБ1"), { target: { value: "0.0000005" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("не более 6 знаков");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("adds valid six-decimal permission exactly without floating-point rounding", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [{ ...permission, consumed: "99999999999.999999", quantity: "99999999999.999999", remaining: "0" }] }))); await open();
    fireEvent.change(await screen.findByLabelText("Разрешить ещё КБ1"), { target: { value: "0.000001" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body)).lines[0].quantity).toBe("100000000000");
  });
  it("explicitly setting zero revokes unused permission but preserves consumed quantity", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] }))); await open();
    fireEvent.change(await screen.findByLabelText("Разрешить ещё КБ1"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: /Сохранить разрешения/ }));
    await screen.findByText("Разрешения упаковки сохранены");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body)).lines[0].quantity).toBe("4");
  });
  it("read-only context never exposes an editable grant even for an administrator", async () => {
    setRightsPrincipal({ user: { id: "u", name: "Администратор", email: "a@example.test", department: "" }, roles: ["superadmin"], permissions: { shipping: "edit" } });
    vi.stubGlobal("fetch", vi.fn(async () => json({ permissions: [permission] })));
    render(<PackDecision project="П" editable={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Разрешения упаковки по КБ" }));
    expect(await screen.findByLabelText("Изменить КБ1")).toBeDisabled();
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сохранить разрешения/ })).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
