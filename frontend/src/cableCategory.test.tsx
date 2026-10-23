import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { CableTable } from "./CableTables";
import { cableCategory, cableFamily, cableGroup, cableSeries, compareByFamily, compareCables, screenSplit } from "./cableCategory";
import type { CablesResult } from "./api";

it.each([
  ["Cat5e F/UTP 4PR 24AWG", "Витая пара / Ethernet"], ["Паритет ParLan U/UTP Cat5e", "Витая пара / Ethernet"], ["SFTP Cat6a", "Витая пара / Ethernet"],
  ["Шлейф плоский 10 жил шаг 1,27", "Шлейфы плоские"], ["DS1057-10 Connfly", "Шлейфы плоские"],
  ["UL2464 24AWG 4C", "Коммуникационные многожильные"], ["CCCU 8C", "Коммуникационные многожильные"],
  ["LiYCY 4x0,5", "Контрольные / управления"], ["UNITRONIC LiYCY", "Контрольные / управления"], ["КДВВГ 7х1,5", "Контрольные / управления"],
  ["PAAR-TRONIC-CY", "Контрольные / управления"], ["FLEXICORE 5G1", "Контрольные / управления"],
  ["ВВГнг(A)-LS 3х2,5", "Силовые"], ["H05RN-F 3G1,5", "Силовые"],
  ["USB 2.0 AM-BM", "USB / интерфейсные"], ["RS-485 2x2x0,22", "USB / интерфейсные"],
  ["SDI RG-59 combo", "Коаксиальные / комбинированные"], ["RGB 5xRG-179", "Коаксиальные / комбинированные"],
  ["Неведомый кабель", "Прочие"],
])("classifies %s as %s", (mark, category) => {
  expect(cableCategory({ "Марка": mark, "Тип провода": "многожильный" }).category).toBe(category);
});

it("lets the manual cell «Категория» win, and ignores a value that is not a category", () => {
  expect(cableCategory({ "Марка": "LiYCY 4x0,5", "Категория": "Силовые" })).toEqual({ category: "Силовые", manual: true });
  expect(cableCategory({ "Марка": "LiYCY 4x0,5", "Категория": "что-то" })).toEqual({ category: "Контрольные / управления", manual: false });
});

it.each([
  ["", "без экрана"], ["нет", "без экрана"], ["Без экрана", "без экрана"], ["—", "экран не указан"], ["н/д", "экран не указан"],
  ["фольга + оплётка", "экранированный"], ["да", "экранированный"], ["F/UTP", "экранированный"],
])("screen «%s» is %s", (screen, split) => {
  expect(screenSplit(screen)).toBe(split);
});

it("sorts naturally: category, screen, series, cores as a number, section as a number", () => {
  const cable = (mark: string, cores: string, section: string, screen = "") => ({ "Марка": mark, "Тип провода": "", "Число жил": cores, "Сечение, мм2": section, "Экран": screen });
  const rows = [
    cable("LiYCY 12x0,5", "12", "0,5", "оплётка"), cable("LiYCY 4x1,5", "4", "1,5", "оплётка"), cable("LiYCY 4x0,5", "4", "0,5", "оплётка"),
    cable("LiYCY 4x10", "4", "10", "оплётка"), cable("КДВВГ 4x1", "4", "1"), cable("Cat5e U/UTP", "8", "0,2"),
  ];
  const sorted = [...rows].sort(compareCables).map((row) => `${cableGroup(row).label}|${row["Марка"]}`);
  expect(sorted).toEqual([
    "Витая пара / Ethernet · без экрана|Cat5e U/UTP",
    "Контрольные / управления · экранированный|LiYCY 4x0,5", "Контрольные / управления · экранированный|LiYCY 4x1,5",
    "Контрольные / управления · экранированный|LiYCY 4x10", "Контрольные / управления · экранированный|LiYCY 12x0,5",
    "Контрольные / управления · без экрана|КДВВГ 4x1",
  ]);
  expect(cableSeries("LiYCY 4x0,5")).toBe("LiYCY");
  expect(cableSeries("UL2464 24AWG 4C")).toBe("UL2464");
});

// Owner 15.10.2026: «нормальная сортировка по маркам — ПВВС, КДЭВГ, CAT5e, CAT6 и т.д.» — the marks of production.
it.each([
  ["ПВВС 4×0,35", {}, "ПВВС"], ["ПВС 3x1,5", {}, "ПВС"], ["ШВВП 2x0,75", {}, "ШВВП"], ["H05RN-F 3x0,75", {}, "H05RN-F"],
  ["КДЭВГ 9х0,14", {}, "КДЭВГ"], ["КДВВГ 6x0,14", {}, "КДВВГ"], ["КДВЭВГ 4х0,50", {}, "КДВЭВГ"], ["КСПВГ 4x0,20", {}, "КСПВГ"],
  ["КСПЭВ 4х0,50", {}, "КСПЭВ"], ["КСПЭВГ 4x0,35", {}, "КСПЭВГ"], ["RVVP 16x0,3", {}, "РВВП"],
  ["ШТЛЭн-4", {}, "ШТЛЭн"], ["ШТСПЭн 2х2-А", {}, "ШТСПЭн"], ["МКШ 2x0,75", {}, "МКШ"],
  ["FLEXICORE LiYCY 4x0,5", {}, "LiYCY"], ["FLEXICORE® 100 нг(А)-LS 3G2,5", {}, "FLEXICORE"],
  ["PAAR-TRONIC-CY 4x2x0,5", {}, "PAAR-TRONIC-CY"], ["CCC-9GG", {}, "UL2464 CCC"], ["CCCU-4G-26", {}, "UL2464 CCCU"],
  ["U/UTP 4х2х0,52мм Cat 5e", {}, "U/UTP Cat5e"], ["ParLan Patch U/UTP Cat5e 2x2x0,48", {}, "U/UTP Cat5e"],
  ["Cat5e", { "Экран": "" }, "U/UTP Cat5e"], ["U/UTP Cat 5 25 пар 24 AWG", {}, "U/UTP Cat5"],
  ["CAT5e FTP 4PR 24AWG", {}, "F/UTP Cat5e"], ["FTP-4P-Cat.5e-SOLID-GY", {}, "F/UTP Cat5e"], ["F/UTP Cat 5e PE 4х2х0,52", {}, "F/UTP Cat5e"],
  ["F/UTP 24AWG черный", { "Тип провода": "витая пара (LAN)", "Производители": "генерик Cat5e F/UTP" }, "F/UTP Cat5e"],
  ["Cat5 C5-F/U-26/7FRPVC-BK Teldor", {}, "F/UTP Cat5"], ["Cat5 RC5-LB1", { "Экран": "оплётка луж. медь" }, "S/UTP Cat5"],
  ["SFTP-4P-Cat.5e", {}, "SF/UTP Cat5e"], ["SSTP-4P-Cat.7-PATCH-IN-LSZH", {}, "S/FTP Cat7"], ["U/UTP Cat6 4x2x0,57", {}, "U/UTP Cat6"],
  ["DS1057-16-30", {}, "Шлейфы плоские"], ["6XV1830-0EH10", {}, "PROFIBUS"], ["TSK1160", { "Тип провода": "USB-кабель" }, "USB"],
  ["SDI мультикор RGB5s-15 First Cable", {}, "Мультикор SDI / RGB"], ["UL2725 28AWG", {}, "UL2725"],
])("puts %s into the family of its mark", (mark, fields, family) => {
  expect(cableFamily({ "Марка": mark, "Тип провода": "многожильный", ...fields }).family).toBe(family);
});

it("lets the cell «Семейство» win and orders families, then cores and section as numbers", () => {
  expect(cableFamily({ "Марка": "КСПВГ 4x0,20", "Семейство": "Сигнальные" })).toMatchObject({ family: "Сигнальные", manual: true });
  const cable = (mark: string, cores: string, section: string) => ({ "Марка": mark, "Тип провода": "", "Число жил": cores, "Сечение, мм2": section });
  const rows = [
    cable("U/UTP Cat6 4x2x0,57", "8", "0,25"), cable("F/UTP Cat5e 4x2x0,52", "8", "0,2"), cable("U/UTP Cat5e 4x2x0,52", "8", "0,2"),
    cable("КСПВГ 10x0,20", "10", "0,2"), cable("КСПВГ 4x0,50", "4", "0,5"), cable("КСПВГ 4x0,20", "4", "0,2"),
    cable("ПВС 3x1,5", "3", "1,5"), cable("ПВС 2x0,75", "2", "0,75"), cable("Неведомый 2x1", "2", "1"),
  ];
  expect([...rows].sort(compareByFamily).map((row) => row["Марка"])).toEqual([
    "ПВС 2x0,75", "ПВС 3x1,5", "КСПВГ 4x0,20", "КСПВГ 4x0,50", "КСПВГ 10x0,20",
    "U/UTP Cat5e 4x2x0,52", "F/UTP Cat5e 4x2x0,52", "U/UTP Cat6 4x2x0,57", "Неведомый 2x1",
  ]);
});

it("renders «Многожильные кабели» in groups of the mark family with the screen material as a column", () => {
  const row = (mark: string, fields: Record<string, string>) => ({
    id: mark, kind: "multi" as const, auto_kind: "multi" as const, reason: "", manual: false, cable: mark, dia: null, dia_hint: null,
    record: { id: mark, tab: "prov" as const, display_text: mark, source_sheet: "БД.ПРОВ", fields: { "Марка": mark, "Тип провода": "многожильный", ...fields } },
  });
  const data = {
    columns: ["Марка", "Тип провода", "Число жил", "Экран", "Материал экрана"], custom_columns: ["Материал экрана"],
    counts: { coax: 0, wire: 0, multi: 3 }, gauges: [],
    rows: [row("LiYCY 4x0,5", { "Материал экрана": "медь лужёная" }), row("U/UTP Cat5e 4x2x0,52", { "Экран": "нет" }), row("КДЭВГ 9х0,14", { "Материал экрана": "алюминий" })],
  } as unknown as CablesResult;
  render(<CableTable kind="multi" data={data} canEdit={false} onSaved={() => undefined} onError={() => undefined} />);
  const groups = screen.getAllByRole("row").filter((item) => item.classList.contains("group-row")).map((item) => item.textContent);
  expect(groups).toEqual(["КДЭВГ · 1", "LiYCY · 1", "U/UTP Cat5e · 1"]);
  expect(screen.getByRole("columnheader", { name: "Материал экрана" })).toBeInTheDocument();
  // owner 23.10: «Экран» says the screen type (нет / общий — фольга / попарный) and stands next to «Материал экрана»
  const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads.indexOf("Материал экрана")).toBe(heads.indexOf("Экран") + 1);
  expect(screen.queryByText("Разобрать позже")).toBeNull();
});
