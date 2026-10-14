import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferenceRoundtrip, type RoundtripReport } from "./ReferenceRoundtrip";
import type { Principal } from "./api";

const principal = (permissions: Record<string, "view" | "edit">): Principal => ({
  user: { id: "u1", email: "t@example.test", name: "Технолог", department: "ОГТ" }, roles: ["tech_lead"], permissions,
} as unknown as Principal);

const REPORT: RoundtripReport = {
  tab: "ter", label: "Терминалы", dry_run: true, applied: false, file_name: "Терминалы.xlsx",
  columns: [{ field: "terStep", title: "Шаг" }], ignored_columns: ["Цена"],
  updated: [{ row: 2, id: "r1", title: "JST | ZH", changes: [{ field: "terStep", title: "Шаг", old: "1,5", new: "1,6" }] }],
  created: [{ row: 5, title: "PHR-2", fields: [{ field: "terArticle", title: "Артикул контакта", value: "PHR-2" }] }],
  conflicts: [{ row: 3, id: "r2", title: "КВТ | НВИ", reason: "запись изменили после скачивания файла — скачайте таблицу заново" }],
  not_found: [{ row: 4, id: "00000000-0000-0000-0000-000000000007", reason: "записи с этим ID нет" }],
  errors: [{ row: 6, id: "", title: "=A1", reason: "Формулы вводить нельзя" }],
  unchanged: 520,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve() {
  const calls: Array<{ path: string; method: string; body: Record<string, unknown> | undefined }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.startsWith("/api/references/import/ter/file/fields")) return json({ fields: ["terStep", "Шаг разьема"], titles: {}, label: "Терминалы", rows: 2 });
    if (path === "/api/references/import/ter/file/export") return json({ file_name: "Терминалы — для правки 14.10.2026.xlsx", content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content_base64: btoa("PK"), rows: 2 });
    if (path === "/api/references/import/ter/file?dry_run=1") return json(REPORT);
    if (path === "/api/references/import/ter/file?dry_run=0") return json({ ...REPORT, dry_run: false, applied: true });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

it("shows «Скачать» to a viewer and «Загрузить исправленную» only to whom edits the tab", () => {
  const { rerender } = render(<ReferenceRoundtrip tab="ter" principal={principal({ erpref: "view" })} onApplied={() => undefined} />);
  expect(screen.getByRole("button", { name: "Скачать для правки" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Загрузить исправленную" })).toBeNull();

  rerender(<ReferenceRoundtrip tab="ter" principal={principal({ erpref: "edit" })} onApplied={() => undefined} />);
  expect(screen.getByRole("button", { name: "Загрузить исправленную" })).toBeInTheDocument();

  // the supplier / country maps are the buyer's
  rerender(<ReferenceRoundtrip tab="suppliers" principal={principal({ erpref: "edit" })} onApplied={() => undefined} />);
  expect(screen.queryByRole("button", { name: "Загрузить исправленную" })).toBeNull();
  rerender(<ReferenceRoundtrip tab="suppliers" principal={principal({ erpref: "edit", procurement: "edit" })} onApplied={() => undefined} />);
  expect(screen.getByRole("button", { name: "Загрузить исправленную" })).toBeInTheDocument();

  // a tab without a table of its own, or no access to the base: nothing
  rerender(<ReferenceRoundtrip tab="connectors" principal={principal({ erpref: "edit" })} onApplied={() => undefined} />);
  expect(screen.queryByRole("button", { name: "Скачать для правки" })).toBeNull();
  rerender(<ReferenceRoundtrip tab="templates" principal={principal({ erptctpl: "edit" })} onApplied={() => undefined} />);
  expect(screen.queryByRole("button", { name: "Скачать для правки" })).toBeNull();
});

it("downloads the tab with the screen's Russian titles", async () => {
  const calls = serve();
  const created = vi.fn(() => "blob:x");
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  render(<ReferenceRoundtrip tab="ter" principal={principal({ erpref: "view" })} onApplied={() => undefined} />);

  fireEvent.click(screen.getByRole("button", { name: "Скачать для правки" }));

  await waitFor(() => expect(click).toHaveBeenCalled());
  const exported = calls.find((call) => call.path.endsWith("/file/export"));
  expect(exported?.body).toEqual({ titles: { terStep: "Шаг", "Шаг разьема": "Шаг разъёма" }, kind: "" });
  expect(created).toHaveBeenCalled();
});

it("previews the file in a window, then applies it on «Применить»", async () => {
  const calls = serve();
  const applied = vi.fn();
  render(<ReferenceRoundtrip tab="ter" principal={principal({ erpref: "edit" })} onApplied={applied} />);

  const file = new File(["PK"], "Терминалы.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  fireEvent.change(screen.getByLabelText("Исправленная таблица .xlsx"), { target: { files: [file] } });

  const dialog = await screen.findByRole("dialog");
  const row = (await within(dialog).findByText("JST | ZH")).closest("tr") as HTMLElement;
  expect(within(row).getByText("Шаг")).toBeInTheDocument();
  expect(within(row).getByText("1,5")).toBeInTheDocument();
  expect(within(row).getByText("1,6")).toBeInTheDocument();
  expect(within(dialog).getByText(/удалить запись файлом нельзя/)).toBeInTheDocument();
  expect(within(dialog).getByText(/Колонки не узнаны и не загружаются: Цена/)).toBeInTheDocument();
  expect(within(dialog).getByText("изменены после скачивания", { exact: false })).toBeInTheDocument();
  const upload = calls.find((call) => call.path.endsWith("dry_run=1"));
  expect(upload?.body).toEqual({ file_name: "Терминалы.xlsx", content_base64: btoa("PK") });
  expect(applied).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole("tab", { name: /Пропущены/ }));
  expect(within(dialog).getByText("Формулы вводить нельзя")).toBeInTheDocument();
  expect(within(dialog).getByText("Изменена после скачивания")).toHaveClass("tone-warn");

  fireEvent.click(within(dialog).getByRole("button", { name: "Применить: 1 изменение, 1 новая" }));

  await within(dialog).findByText(/Записано: изменено 1 запись, добавлено 1/);
  expect(calls.some((call) => call.path.endsWith("dry_run=0"))).toBe(true);
  expect(applied).toHaveBeenCalledTimes(1);
  expect(within(dialog).queryByRole("button", { name: /Применить/ })).toBeNull();
});
