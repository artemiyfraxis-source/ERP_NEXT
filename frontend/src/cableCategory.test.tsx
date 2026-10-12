import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { CableTable } from "./CableTables";
import { cableCategory, cableGroup, cableSeries, compareCables, screenSplit } from "./cableCategory";
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

it("renders the «Кабели» table in groups of category and screen", () => {
  const row = (mark: string, fields: Record<string, string>) => ({
    id: mark, kind: "multi" as const, auto_kind: "multi" as const, reason: "", manual: false, cable: mark, dia: null, dia_hint: null,
    record: { id: mark, tab: "prov" as const, display_text: mark, source_sheet: "БД.ПРОВ", fields: { "Марка": mark, "Тип провода": "многожильный", ...fields } },
  });
  const data = {
    columns: ["Марка", "Тип провода", "Число жил", "Экран", "Категория"], counts: { coax: 0, wire: 0, multi: 3, other: 0 }, gauges: [],
    rows: [row("LiYCY 4x0,5", { "Экран": "оплётка" }), row("Cat5e U/UTP", { "Экран": "нет" }), row("Нестандартный", { "Экран": "", "Категория": "Силовые" })],
  } as unknown as CablesResult;
  render(<CableTable kind="multi" data={data} canEdit={false} onSaved={() => undefined} onError={() => undefined} />);
  expect(screen.getByText("Витая пара / Ethernet · без экрана")).toBeInTheDocument();
  expect(screen.getByText("Контрольные / управления · экранированный")).toBeInTheDocument();
  expect(screen.getByText("Силовые · без экрана")).toBeInTheDocument();
});
