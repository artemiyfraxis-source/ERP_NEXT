import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DataTable, type DataColumn } from "./DataTable";

type Row = { id: string; name: string };
const columns: DataColumn<Row>[] = [{ key: "name", title: "Название", value: (row) => row.name }];
let seen: ((entries: Array<{ isIntersecting: boolean }>) => void) | null = null;

afterEach(() => { vi.unstubAllGlobals(); seen = null; });

it("loads the next rows by itself when the end of the table scrolls into view, with no «Показать ещё» button", () => {
  vi.stubGlobal("IntersectionObserver", class {
    constructor(callback: (entries: Array<{ isIntersecting: boolean }>) => void) { seen = callback; }
    observe() { /* метка внизу таблицы */ }
    disconnect() { /* снята */ }
  });
  const rows = Array.from({ length: 450 }, (_, index) => ({ id: String(index), name: `Контакт ${index}` }));
  render(<DataTable rows={rows} columns={columns} rowKey={(row) => row.id} />);
  expect(screen.getByText("Показано 200 из 450")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
  act(() => seen!([{ isIntersecting: true }]));
  expect(screen.getByText("Показано 400 из 450")).toBeInTheDocument();
  act(() => seen!([{ isIntersecting: true }]));
  expect(screen.getByText("Показано 450 из 450")).toBeInTheDocument();
});
