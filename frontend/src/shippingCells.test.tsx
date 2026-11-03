import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PackDecision } from "./shippingCells";
import { setRightsPrincipal } from "./rights";

afterEach(() => { setRightsPrincipal(null); vi.unstubAllGlobals(); });

describe("PackDecision", () => {
  it("failed standalone loading never invents an unchecked decision", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Нет связи" }), { status: 503 })));
    render(<PackDecision project="П" editable />);
    expect(await screen.findByText("Упаковывать — решение не загружено")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/склад ждёт решения/)).not.toBeInTheDocument();
  });

  it("a failed write retains the confirmed state and reports the error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Решение не принято" }), { status: 422 })));
    render(<PackDecision project="П" editable card={{ project: "П", version: 0, pack_decided: false }} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "Упаковывать П" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Решение не принято");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({ project: "П", version: 0, decided: true });
  });

  it("standalone conflict reloads the actual latest decision", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input) => String(input).includes("project-pack-decision")
      ? new Response(JSON.stringify({ detail: "Версия" }), { status: 409 })
      : new Response(JSON.stringify({ addressee: { project: "П", version: 5, pack_decided: true } }))));
    render(<PackDecision project="П" editable card={{ project: "П", version: 3, pack_decided: false }} />);
    fireEvent.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());
    expect(screen.getByRole("alert")).toHaveTextContent("Данные проекта уже изменил другой сотрудник");
  });

  it("warehouse edit rights cannot enable the manager decision even inside an editable dialog", () => {
    setRightsPrincipal({ user: { id: "u", name: "Склад", email: "s@example.test", department: "Склад" }, roles: ["receiver"],
      permissions: { shipping: "view", shipping_goods: "edit" } });
    render(<PackDecision project="П" editable card={{ project: "П", version: 3, pack_decided: true }} />);
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });
});
