import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FulfillmentPage } from "./FulfillmentPage";
import type { FulfillmentRequest, Principal } from "./api";

/* 10.10.2026, прод 719К.2 и 180К.11: «Вернуть в комплектацию», отмена с перечнем резерва, отменённая КБ в карточке
   проекта, «Выдача со списанием» с выбором стадий по умолчанию, «Довыдача». */

const principal: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "receiver@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"],
  access_level: "foreman",
  permissions: { fulfillment: "edit", handout: "edit" },
};

const line = {
  id: "51000000-0000-0000-0000-000000000001",
  requirement_id: "52000000-0000-0000-0000-000000000001",
  article: "CON-001",
  name: "Разъём",
  unit: "шт",
  requirement: "4",
  reserved: "4",
  issued: "0",
  returned_good: "0",
  defective_returned: "0",
  defective_missing: "0",
  consumed: "0",
  lot: { id: "40000000-0000-0000-0000-000000000001", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", storage_location: "1F4" },
};
const issuedLine = { ...line, issued: "4", reserved: "0" };

function kit(id: string, number: string, purpose: string, status: string, extra: Partial<FulfillmentRequest> = {}): FulfillmentRequest {
  return {
    id, number, version: 3, created_at: "2026-10-05T10:00:00+03:00", requester: "Технолог", purpose,
    project: purpose.includes(" · ") ? purpose.split(" · ")[0] : "", deadline: "2026-10-20", status,
    handout_location: null, return_location: null, fully_reserved: true, lines: [line], ...extra,
  };
}

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function serve(requests: FulfillmentRequest[], onPost: (path: string, init: RequestInit) => unknown = () => ({ request: requests[0] })) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") return json(onPost(path, init));
    if (path === "/api/issues") return json({ requests, next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

/* A kit of a project opens from the project's tile, then its КБ tab. */
async function openKb(project: string, kb: string) {
  fireEvent.click(await screen.findByRole("button", { name: (name) => name.startsWith(project) }));
  const tabs = screen.getByRole("tablist", { name: "Заявки проекта" });
  fireEvent.click(within(tabs).getByRole("tab", { name: (name) => name === kb || name.startsWith(`${kb} `) }));
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.removeItem("erp.fulfillment.give.stages");
});

it("takes a kit off the handout point with «Вернуть в комплектацию» and offers no cancel there", async () => {
  const posts: Array<{ path: string; body: unknown }> = [];
  const ready = kit("k1", "REQ.26.0000001", "719К.2 · КБ1 · Жгут", "Готов к выдаче", { handout_location: "5F1A" });
  serve([ready], (path, init) => { posts.push({ path, body: JSON.parse(String(init.body)) }); return { request: ready }; });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={principal} />);
  await openKb("719К.2", "КБ1");

  expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Вернуть в комплектацию" }));
  expect(confirm.mock.calls[0][0]).toMatch(/→ Укомплектован/);
  expect(confirm.mock.calls[0][0]).toMatch(/не возврат материала/);
  await waitFor(() => expect(posts).toEqual([{ path: "/api/issues/k1/return-to-kitting", body: { version: 3 } }]));
});

it("names every reserve a cancel gives back", async () => {
  serve([kit("k1", "REQ.26.0000001", "719К.2 · КБ1 · Жгут", "Укомплектован")]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

  render(<FulfillmentPage principal={principal} />);
  await openKb("719К.2", "КБ1");
  fireEvent.click(await screen.findByRole("button", { name: "Отменить заявку" }));

  expect(confirm.mock.calls[0][0]).toContain("Освободится резерв 1 поз.:\n· Разъём — 4 шт (FRX.26.0000001)");
  expect(confirm.mock.calls[0][0]).toContain("Заявка КБ1 проекта 719К.2 уйдёт в архив «Отменена»");
});

it("keeps a cancelled КБ on the project card as a muted chip with its request number", async () => {
  serve([
    kit("k1", "REQ.26.0000001", "719К.2 · КБ1 · Жгут", "Отменена"),
    kit("k2", "REQ.26.0000002", "719К.2 · КБ2 · Жгут", "Новая"),
    kit("k3", "REQ.26.0000003", "719К.2 · КБ3 · Жгут", "Отменена"),
    kit("k4", "REQ.26.0000004", "719К.2 · КБ3 · Жгут", "Новая"),
  ]);

  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^719К\.2/ }));

  const tabs = screen.getByRole("tablist", { name: "Заявки проекта" });
  expect(within(tabs).getByText("КБ1 · отменена · REQ.26.0000001")).toHaveClass("chip-cancelled");
  expect(within(tabs).queryByText(/КБ3 · отменена/)).not.toBeInTheDocument();  // КБ3 went again: its live one shows
  expect(screen.getByText(/КБ: 2 · отменено: 1/)).toBeInTheDocument();
});

it("opens «Выдача со списанием» on «Укомплектован» + «КБ Возвращена» and remembers the chips", async () => {
  serve([
    kit("k1", "REQ.26.0000001", "Ручная 1", "Укомплектован"),
    kit("k2", "REQ.26.0000002", "Ручная 2", "КБ Выдана", { lines: [issuedLine] }),
    kit("k3", "REQ.26.0000003", "Ручная 3", "КБ Возвращена", { lines: [issuedLine] }),
  ]);

  const { unmount } = render(<FulfillmentPage principal={principal} mode="give" />);
  expect(await screen.findByRole("button", { name: "Укомплектован · 1" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "КБ Возвращена · 1" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Все · 3" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getAllByText("REQ.26.0000001").length).toBeGreaterThan(0);
  expect(screen.queryByText("REQ.26.0000002")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "КБ Выдана · 1" }));
  expect(screen.getAllByText("REQ.26.0000002").length).toBeGreaterThan(0);
  expect(JSON.parse(window.localStorage.getItem("erp.fulfillment.give.stages")!)).toEqual(["Укомплектован", "КБ Возвращена", "КБ Выдана"]);
  unmount();

  render(<FulfillmentPage principal={principal} mode="give" />);
  expect(await screen.findByRole("button", { name: "КБ Выдана · 1" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Все · 3" }));
  expect(screen.getByRole("button", { name: "Укомплектован · 1" })).toHaveAttribute("aria-pressed", "false");
});

it("hands out a shortage of an issued kit as «Довыдача» with the reason", async () => {
  const posts: Array<{ path: string; body: unknown; key: string | null }> = [];
  const issued = kit("k1", "REQ.26.0000001", "180К.11 · КБ1 · Жгут", "КБ Выдана", { kind: "main", lines: [issuedLine] });
  serve([issued], (path, init) => {
    posts.push({ path, body: JSON.parse(String(init.body)), key: new Headers(init.headers).get("Idempotency-Key") });
    return { request: { ...issued, id: "k9", number: "REQ.26.0000009", kind: "extra" } };
  });

  render(<FulfillmentPage principal={principal} />);
  await openKb("180К.11", "КБ1");
  fireEvent.click(await screen.findByRole("button", { name: "Довыдать" }));
  expect(screen.getByRole("button", { name: "Создать довыдачу" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Довыдать Разъём"), { target: { value: "2" } });
  expect(screen.getByRole("button", { name: "Создать довыдачу (1 поз.)" })).toBeDisabled();  // no reason yet
  fireEvent.change(screen.getByLabelText("Причина довыдачи"), { target: { value: "брак при опрессовке" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать довыдачу (1 поз.)" }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/issues/k1/extra");
  expect(posts[0].body).toEqual({ reason: "брак при опрессовке", lines: [{ article: "CON-001", name: "Разъём", unit: "шт", quantity: "2" }] });
  expect(posts[0].key).toBeTruthy();
  expect(await screen.findByText(/Довыдача REQ\.26\.0000009 создана/)).toBeInTheDocument();
});
