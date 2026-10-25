import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import type { CableRow, Principal } from "./api";

/* «Кабели и провода» → «ETHERNET» (владелец 25.10.2026: «интернетные вынести вообще в другую вкладку — новая вкладка,
   новая БД», «не витая пара»): своя вкладка после «Многожильных», свои столбцы — все видны, группы «экран категория». */

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", erptctpl: "edit", procurement: "edit" },
};
const LAN = ["Марка", "Тип провода", "Категория", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число пар",
  "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал оболочки", "Прокладка",
  "Наружный диаметр, мм", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка на datasheet"];
const MULTI = ["Марка", "Тип провода", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число жил", "Жила: проволок × диаметр, мм",
  "Экран", "Наружный диаметр, мм", "Поставщик", "Ссылка на datasheet"];
const record = (id: string, fields: Record<string, string>) => ({ id, tab: "prov" as const, display_text: fields["Марка"], source_sheet: "БД.ПРОВ", fields });
const lanFields = (mark: string, category: string, screen: string) => ({
  ...Object.fromEntries(LAN.map((name) => [name, ""])), "Марка": mark, "Тип провода": `витая пара ${screen} ${category} (LAN, внутренняя прокладка)`,
  "Категория": category, "Экран": screen, "Число пар": "4", "Прокладка": "внутренняя", "Число жил": "8 (4 пары)", "AWG": "24",
});
const ROWS: CableRow[] = [
  { id: "l-1", kind: "lan", auto_kind: "lan", reason: "кабель Ethernet (Cat5e)", manual: false, cable: "ParLan U/UTP Cat5e PVC 4х2х0,52",
    record: record("l-1", lanFields("ParLan U/UTP Cat5e PVC 4х2х0,52", "Cat5e", "U/UTP")), dia: null, dia_hint: null },
  { id: "l-2", kind: "lan", auto_kind: "lan", reason: "кабель Ethernet (Cat6)", manual: false, cable: "ParLan F/UTP Cat6 ZH 4х2х0,57",
    record: record("l-2", lanFields("ParLan F/UTP Cat6 ZH 4х2х0,57", "Cat6", "F/UTP")), dia: null, dia_hint: null },
  { id: "m-1", kind: "multi", auto_kind: "multi", reason: "жил: 4", manual: false, cable: "КДВВГ 4х0,50",
    record: record("m-1", { "Марка": "КДВВГ 4х0,50", "Тип провода": "многожильный кабель управления (аналог LiYY)", "Число жил": "4" }),
    dia: null, dia_hint: null },
];

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [], can_edit: { suppliers: true, gost_awg: true } });
    if (path === "/api/references/bases") return json({ bases: [], tabs: {}, cables: { coax: 0, wire: 0, multi: 136, lan: 230 }, gauges: 0 });
    if (path === "/api/references/cables") return json({
      columns: [...new Set([...LAN, ...MULTI])], custom_columns: [], counts: { coax: 0, wire: 0, multi: 1, lan: 2 }, rows: ROWS, gauges: [],
      columns_by_kind: { coax: [], wire: [], multi: MULTI, lan: LAN }, custom_columns_by_kind: { coax: [], wire: [], multi: [], lan: [] },
      removed_columns_by_kind: { coax: [], wire: [], multi: [], lan: ["Число жил", "Источник данных"] },
    });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
    if (path === "/api/references/coverings") return json({ counts: {}, titles: {}, rows: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

const heads = () => screen.getAllByRole("columnheader").map((head) => head.textContent ?? "").filter(Boolean);

it("puts «Кабели Ethernet» right after «Многожильные кабели» with its own count", async () => {
  render(<ReferencesPage principal={admin} initialTab="cab_multi" />);
  const nav = screen.getByRole("navigation", { name: "Вкладки базы данных" });
  const group = [...nav.querySelectorAll(".db-tab-group")].find((item) => item.querySelector(".db-tab-group-title")?.textContent === "Кабели и провода")!;
  expect([...group.querySelectorAll("button")].map((button) => button.firstChild?.textContent))
    .toEqual(["Коаксиальные кабели", "Монтажные провода", "Многожильные кабели", "Кабели Ethernet"]);
  const chip = within(nav).getByRole("button", { name: /^Кабели Ethernet/ });
  expect(await within(chip).findByText(/^2$/)).toBeInTheDocument();
  // the многожильные keep their columns and lose the LAN rows
  expect(await screen.findByText("КДВВГ 4х0,50")).toBeInTheDocument();
  expect(screen.queryByText("ParLan U/UTP Cat5e PVC 4х2х0,52")).toBeNull();
  expect(heads()).toContain("Число жил");
  expect(heads()).not.toContain("Категория");
});

it("shows the ETHERNET list with all its columns in the group order, grouped by screen and category", async () => {
  render(<ReferencesPage principal={admin} initialTab="cab_multi" />);
  await screen.findByText("КДВВГ 4х0,50");
  fireEvent.click(screen.getByRole("button", { name: /^Кабели Ethernet/ }));
  expect(await screen.findByText("ParLan U/UTP Cat5e PVC 4х2х0,52")).toBeInTheDocument();
  expect(screen.queryByText("КДВВГ 4х0,50")).toBeNull();
  expect(heads()).toEqual([
    "Марка кабеля", "Тип провода", "Категория", "Стандарт", "Производители", "AWG", "Сечение, мм2", "Число пар",
    "Жила: проволок × диаметр, мм", "Материал жилы", "Материал изоляции жилы", "Экран", "Материал оболочки", "Прокладка",
    "Наружный диаметр, мм", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Ссылка на datasheet",
  ]);
  expect(screen.getByText("U/UTP Cat5e")).toBeInTheDocument();
  expect(screen.getByText("F/UTP Cat6")).toBeInTheDocument();
  // «Категория» is the cable's Cat, a plain cell — not the shop category choice of the многожильные
  expect(screen.queryByRole("combobox", { name: /Категория/ })).toBeNull();
  expect(screen.getAllByText("Cat5e").length).toBeGreaterThan(0);
});
