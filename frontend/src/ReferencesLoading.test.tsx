import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import type { Principal } from "./api";

/* «База данных» on prod, 26.10.2026 («БД как-то глючит»): while the lists were loading «Операции» said «Ничего не
   найдено · Показано 0 из 0» and the maps «0»; every visit downloaded the same lists two or three times. */

const owner: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "owner@example.test", name: "Владелец", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", erptctpl: "edit" },
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

const TEMPLATES = { metadata: null, items: [
  { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
] };

/** Every list of the page; `hold` — paths answered only when `release()` is called. */
function serve(hold: string[] = []) {
  const asked: string[] = [];
  const waiting: Array<() => void> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    asked.push(`${init?.method ?? "GET"} ${path}`);
    const answer = () => {
      if (path === "/api/techcards/templates") return json(TEMPLATES);
      if (path === "/api/techcards/connector-families") return json({ items: [], match: null, series_count: 0 });
      if (path === "/api/references/maps") return json({ suppliers: [{ key: "example.ru", value: "Поставщик" }], countries: [], gost_awg: [] });
      if (path === "/api/references/bases") return json({ bases: [] });
      if (path === "/api/references/coverings") return json({ rows: [], titles: {}, kinds: {} });
      if (path === "/api/references/column-views") return json({ views: {}, can_edit: false });
      if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
      throw new Error(`Unexpected request: ${path}`);
    };
    if (hold.some((item) => path.startsWith(item))) return new Promise<Response>((resolve) => waiting.push(() => resolve(answer())));
    return answer();
  });
  return { asked, release: () => waiting.splice(0).forEach((resolve) => resolve()) };
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

it("says «Загрузка…» while the operations and the maps are loading, never «Ничего не найдено» or 0", async () => {
  const { release } = serve(["/api/techcards/templates", "/api/references/maps"]);
  render(<ReferencesPage principal={owner} />);
  expect(await screen.findByText("Загрузка…")).toBeInTheDocument();
  expect(screen.queryByText("Ничего не найдено.")).toBeNull();
  expect(screen.queryByText(/^Показано 0 из 0/)).toBeNull();
  // the maps' tabs have no number until the maps came
  expect(screen.getByRole("button", { name: "Домен → поставщик" })).toHaveTextContent(/^Домен → поставщик$/);
  fireEvent.click(screen.getByRole("button", { name: "Домен → поставщик" }));
  expect(screen.getByText("Загрузка…")).toBeInTheDocument();
  expect(screen.queryByText("Нет записей")).toBeNull();
  release();
  expect(await screen.findByText("Поставщик", { selector: "td" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Домен → поставщик/ })).toHaveTextContent("1");
  fireEvent.click(screen.getByRole("button", { name: /^Операции/ }));
  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  expect(screen.queryByText("Загрузка…")).toBeNull();
});

it("keeps the summary's count on the open tab while its rows load, not «0»", async () => {
  const { release } = serve(["/api/references/tech-operations?tab=ter"]);
  const fetchMock = vi.mocked(globalThis.fetch);
  const answer = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input, init) => String(input) === "/api/references/bases"
    ? json({ bases: [], tabs: { ter: 554 } }) : String(input) === "/api/techcards/connector-series" ? json({ series: [], match: null })
      : String(input) === "/api/references/tech-operations/crimp-codes" ? json({ owners: {} }) : answer(input, init));
  render(<ReferencesPage principal={owner} initialTab="ter" />);
  const tab = await screen.findByRole("button", { name: /^Терминалы/ });
  await waitFor(() => expect(tab).toHaveTextContent("554"));
  expect(screen.getByText("Загрузка…")).toBeInTheDocument();
  release();
  await waitFor(() => expect(screen.queryByText("Загрузка…")).toBeNull());
});

it("downloads each list once per visit: the tab counts, the tab and a switch back share it", async () => {
  const { asked } = serve();
  render(<ReferencesPage principal={owner} />);
  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: /^Операции/ })).toHaveTextContent("1"));
  fireEvent.click(screen.getByRole("button", { name: /^Оболочки/ }));
  await waitFor(() => expect(asked).toContain("GET /api/references/coverings"));
  fireEvent.click(screen.getByRole("button", { name: /^Операции/ }));
  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  const counts = new Map<string, number>();
  for (const path of asked) counts.set(path, (counts.get(path) ?? 0) + 1);
  expect([...counts].filter(([, count]) => count > 1)).toEqual([]);
  for (const path of ["GET /api/techcards/templates", "GET /api/references/tech-operations?tab=op&limit=5000",
    "GET /api/references/coverings", "GET /api/references/tech-operations?tab=mat&limit=5000"]) {
    expect(counts.get(path)).toBe(1);
  }
});
