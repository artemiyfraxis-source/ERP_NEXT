import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CableTable } from "./CableTables";
import { HookupWireTable } from "./CableStructure";
import { CoaxConnectors } from "./CoaxConnectors";
import { sortBySeries, terminalGroups, terminalMinSection } from "./terminalSeries";
import type { CableKind, CableRow, TechOperationRecord } from "./api";
import type { TerminalRow } from "./TerminalCard";

/* Порядок по умолчанию «размер, затем наименование» в списках «Базы данных» (владелец 25.10.2026). */
afterEach(cleanup);

const rec = (id: string, fields: Record<string, string>) => ({ id, tab: "prov" as const, display_text: fields["Марка"], source_sheet: "БД.ПРОВ", fields });
const cableRow = (kind: CableKind, mark: string, fields: Record<string, string>, extra: Partial<CableRow> = {}): CableRow =>
  ({ id: `${kind}-${mark}-${fields["Цвет"] ?? ""}`, kind, auto_kind: kind, reason: "", manual: false, cable: mark, record: rec(`${kind}-${mark}-${fields["Цвет"] ?? ""}`, { "Марка": mark, ...fields }), dia: null, dia_hint: null, ...extra }) as CableRow;
const dataOf = (rows: CableRow[], gauges: unknown[] = []) =>
  ({ columns: ["Марка", "Сечение, мм2", "Число жил", "Число пар", "Категория", "Наружный диаметр, мм", "Тип провода", "Цвет", "Семейство"], counts: { coax: 0, wire: 0, multi: 0, lan: 0 }, rows, gauges }) as unknown as Parameters<typeof CableTable>[0]["data"];
const marks = () => [...document.querySelectorAll("tbody tr:not(.group-row) td.key")].map((cell) => cell.textContent);
const noop = () => undefined;

describe("порядок списков", () => {
  it("многожильные: сечение (запятая, пустое в конце) → число жил → марка", () => {
    const rows = [
      cableRow("multi", "КДВВГ 10x1,5", { "Сечение, мм2": "1,5", "Число жил": "10", "Тип провода": "многожильный", "Семейство": "КДВВГ" }),
      cableRow("multi", "КДВВГ 2x1,5", { "Сечение, мм2": "1,5", "Число жил": "2", "Тип провода": "многожильный", "Семейство": "КДВВГ" }),
      cableRow("multi", "КДВВГ 4x0,5", { "Сечение, мм2": "0,5", "Число жил": "4", "Тип провода": "многожильный", "Семейство": "КДВВГ" }),
      cableRow("multi", "КДВВГ 4x0,35", { "Сечение, мм2": "0,35", "Число жил": "4", "Тип провода": "многожильный", "Семейство": "КДВВГ" }),
      cableRow("multi", "КДВВГ без размера", { "Тип провода": "многожильный", "Семейство": "КДВВГ" }),
    ];
    render(<CableTable kind="multi" data={dataOf(rows)} canEdit={false} onSaved={noop} onError={noop} />);
    expect(marks()).toEqual(["КДВВГ 4x0,35", "КДВВГ 4x0,5", "КДВВГ 2x1,5", "КДВВГ 10x1,5", "КДВВГ без размера"]);
    expect(screen.getByText("Сортировка: сечение, затем число жил и марка")).toBeInTheDocument();
  });

  it("Ethernet: категория Cat5 < Cat5e < Cat6 < Cat6A < Cat7 < Cat7A < Cat8 → число пар → марка внутри группы", () => {
    const lan = (mark: string, category: string, pairs: string) => cableRow("lan", mark, { "Категория": category, "Число пар": pairs, "Тип провода": "витая пара (LAN)", "Семейство": "Витая пара" });
    const rows = ["Cat8", "Cat7A", "Cat6A", "Cat6", "Cat5e", "Cat7", "Cat5"].map((cat) => lan(`X ${cat} 4`, cat, "4"))
      .concat([lan("X Cat5e 10", "Cat5e", "10"), lan("X Cat5e 2", "Cat5e", "2")]);
    render(<CableTable kind="lan" data={dataOf(rows)} canEdit={false} onSaved={noop} onError={noop} />);
    expect(marks()).toEqual(["X Cat5 4", "X Cat5e 2", "X Cat5e 4", "X Cat5e 10", "X Cat6 4", "X Cat6A 4", "X Cat7 4", "X Cat7A 4", "X Cat8 4"]);
    expect(screen.getByText("Сортировка: категория, затем число пар и марка")).toBeInTheDocument();
  });

  it("коаксиальные кабели: наружный диаметр → марка, пустой диаметр в конце", () => {
    const rows = [
      cableRow("coax", "RG-58 Z", { "Наружный диаметр, мм": "4,95" }),
      cableRow("coax", "RG-58 A", { "Наружный диаметр, мм": "4,95" }),
      cableRow("coax", "RG-58 B", { "Наружный диаметр, мм": "10,2" }),
      cableRow("coax", "RG-58 C", { "Наружный диаметр, мм": "2,5" }),
      cableRow("coax", "RG-58 D", {}),
    ];
    render(<CableTable kind="coax" data={dataOf(rows)} canEdit={false} onSaved={noop} onError={noop} />);
    expect(marks()).toEqual(["RG-58 C", "RG-58 A", "RG-58 Z", "RG-58 B", "RG-58 D"]);
    expect(screen.getByText("Сортировка: наружный диаметр, затем марка")).toBeInTheDocument();
  });

  it("монтажные провода: сечение → марка → цвет внутри серии, без сечения в конце; щелчок по заголовку сортирует иначе", () => {
    const gauges = [{ id: "g1", awg: "22", section_mm2: 0.35 }, { id: "g2", awg: "20", section_mm2: 0.5 }, { id: "g3", awg: "10", section_mm2: 6 }];
    const hook = (gauge: string) => ({ series: "UL", gauge_id: gauge, insulation: "", outer_d_mm: null, voltage_v: null, temp_min_c: null, temp_max_c: null });
    const wire = (mark: string, gauge: string, color: string) => cableRow("wire", mark, { "Цвет": color }, { hookup: hook(gauge) } as Partial<CableRow>);
    const rows = [wire("UL1 6", "g3", "красный"), wire("UL1 0,5", "g2", "синий"), wire("UL1 0,5", "g2", "белый"), wire("UL1 0,35", "g1", "чёрный"), wire("UL1 ?", "none", "белый")];
    render(<HookupWireTable data={dataOf(rows, gauges)} canEdit={false} onSaved={noop} onError={noop} kindSelect={() => null} />);
    const order = () => [...document.querySelectorAll("tbody tr:not(.group-row)")].map((tr) => `${tr.querySelector("td.key")?.textContent}|${tr.textContent?.match(/белый|синий|красный|чёрный/)?.[0] ?? ""}`);
    expect(order()).toEqual(["UL1 0,35|чёрный", "UL1 0,5|белый", "UL1 0,5|синий", "UL1 6|красный", "UL1 ?|белый"]);
    expect(screen.getByText("Сортировка: сечение, затем марка и цвет")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Марка провода/ }));
    expect(marks()[0]).toBe("UL1 ?"); // the header sort overrides the default order
  });
});

describe("разъёмы и контакты", () => {
  it("БД.КОАКС: внутри группы Ø кабеля (через кабель), без Ø — по имени кабеля, затем артикул", () => {
    const connector = (id: string, article: string, wire: string): TechOperationRecord => ({ id, tab: "coax", display_text: article, source_sheet: "БД.КОАКС", fields: { Артикул: article, "Тип/Серия": "SMA-male", Провод: wire } } as unknown as TechOperationRecord);
    const records = [connector("1", "A-3", "RG-58"), connector("2", "A-1", "RG-316"), connector("3", "A-2", "LMR-400"), connector("4", "A-0", "Zeta"), connector("5", "A-9", "Alpha")];
    const diameters: Record<string, number> = { "RG-316": 2.5, "RG-58": 4.95, "LMR-400": 10.3 };
    render(<CoaxConnectors records={records} canEdit={false} onEdit={noop} wireDiameter={(wire) => diameters[wire] ?? null} />);
    const articles = [...document.querySelectorAll("tbody tr:not(.group-row) td.key")].map((cell) => cell.textContent);
    expect(articles).toEqual(["A-1", "A-3", "A-2", "A-9", "A-0"]);
    expect(screen.getByText("Сортировка: серия, затем Ø кабеля и артикул")).toBeInTheDocument();
  });

  it("БД.ТЕР: внутри серии наименьшее сечение диапазона (мм², иначе AWG), затем артикул", () => {
    const ter = (id: string, article: string, card: Record<string, string>): TerminalRow => ({ id, tab: "ter", display_text: article, source_sheet: "БД.ТЕР", sort_key: id,
      fields: { terManufacturer: "JST", terSeries: "SH", terArticle: article }, card: { article, ...card } } as unknown as TerminalRow);
    const rows = [ter("1", "C", { mm2_from: "1,5", mm2_to: "2,5" }), ter("2", "B", { mm2_from: "0,5", mm2_to: "0,75" }), ter("3", "A", { mm2_from: "0,5", mm2_to: "1" }),
      ter("4", "Z", {}), ter("5", "D", { awg_from: "28", awg_to: "26" })];
    expect(terminalMinSection(rows[4])).toBeCloseTo(0.081, 2);
    const sorted = sortBySeries(rows, terminalGroups(rows, [])).map((row) => row.fields.terArticle);
    expect(sorted).toEqual(["D", "A", "B", "C", "Z"]);
  });
});
