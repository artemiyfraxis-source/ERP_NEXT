import { describe, expect, it } from "vitest";
import type { ConnectorSeries } from "./api";
import type { TerminalRow } from "./TerminalCard";
import { NO_SERIES, makerOrder, seriesCaption, sortBySeries, terminalGroups } from "./terminalSeries";

const row = (id: string, maker: string, series: string, article: string): TerminalRow => ({
  id, tab: "ter", display_text: `${maker} ${series} ${article}`, source_sheet: "БД.ТЕР", sort_key: id,
  fields: { terManufacturer: maker, terSeries: series, terArticle: article },
} as unknown as TerminalRow);

const series = (title: string, maker: string, articles: string[]): ConnectorSeries => ({
  id: title, title, maker, terminals: articles.map((article) => ({ article })),
} as unknown as ConnectorSeries);

const CATALOG = [
  series("JST SH", "JST", ["SSH-003T-P0.2"]),
  series("JST LBT", "JST", ["SSH-003T-P0.2"]),
  series("JST PA", "JST", ["SPHD-001T-P0.5"]),
  series("JST PHD", "JST", ["SPHD-001T-P0.5"]),
  series("Molex Micro-Fit 3.0", "Molex", ["43030-0007"]),
  series("Molex Mini-Fit Jr.", "Molex", ["39000038"]),
  series("Наконечники и гильзы", "", ["НШВИ 1,5-8", "ПМ 10-6"]),
];

const ROWS = [
  row("1", "JST", "SHDP, SHR, APSH, LBT", "SSH-003T-P0.2"),
  row("2", "JST", "PHDR", "SPHD-001T-P0.5"),
  row("3", "Molex", "43025, 43645", "430300007"), // без дефиса в базе — тот же артикул каталога
  row("4", "Molex", "5557", "39-00-0038"),
  row("5", "КВТ", "НШВИ 1.5", "НШВИ 1,5-8"),
  row("6", "КВТ", "НШВИ 0.5", "НШВИ 0,5-8"), // нет в каталоге — к соседям по классу НШВИ
  row("7", "КВТ", "ПМ 120", "ПМ 120-12"), // нет в каталоге, соседа ПМ тоже нет в выборке — своя серия
  row("8", "JST", "PAP", "SPH-001T-X"), // нет в каталоге — серия JST PA по имени корпуса
  row("9", "JST", "JWPF", "SWPT-001T-P025"), // своя серия по «Серии»
  row("10", "Molex", "90123, 90142", "90119-2110"), // только номера корпусов — без серии
  row("11", "н/д", "", "X-1"),
];

describe("«Терминалы» по сериям", () => {
  const groups = terminalGroups(ROWS, CATALOG);
  const name = (id: string) => groups.get(id)?.name;

  it("серия — из каталога разъёмов по артикулу контакта", () => {
    expect(name("1")).toBe("JST SH"); // «SHDP…» — JST SH, а не JST LBT
    expect(name("2")).toBe("JST PHD"); // самое длинное совпадение имени
    expect(name("3")).toBe("Molex Micro-Fit 3.0");
    expect(name("4")).toBe("Molex Mini-Fit Jr.");
    expect(name("5")).toBe("Наконечники и гильзы");
  });

  it("чего каталог не знает — по соседям, по имени серии, по своей «Серии», иначе «Без серии»", () => {
    expect(name("6")).toBe("Наконечники и гильзы");
    expect(name("7")).toBe("КВТ ПМ");
    expect(name("8")).toBe("JST PA");
    expect(name("9")).toBe("JST JWPF");
    expect(name("10")).toBe(NO_SERIES);
    expect(name("11")).toBe(NO_SERIES);
  });

  it("группы по имени (латиница раньше кириллицы), «Без серии» в конце, внутри — по артикулу", () => {
    const order = sortBySeries(ROWS, groups).map((item) => groups.get(item.id)?.name);
    expect(order).toEqual([
      "JST JWPF", "JST PA", "JST PHD", "JST SH", "Molex Micro-Fit 3.0", "Molex Mini-Fit Jr.",
      "КВТ ПМ", "Наконечники и гильзы", "Наконечники и гильзы", NO_SERIES, NO_SERIES,
    ]);
    const lugs = sortBySeries(ROWS, groups).filter((item) => groups.get(item.id)?.name === "Наконечники и гильзы");
    expect(lugs.map((item) => item.fields.terArticle)).toEqual(["НШВИ 0,5-8", "НШВИ 1,5-8"]);
  });

  it("производители — в порядке каталога «Разъёмы» (Molex раньше JST, если так в каталоге), сборные группы после них", () => {
    const order = sortBySeries(ROWS, groups, ["Molex", "JST"]).map((item) => groups.get(item.id)?.name);
    expect(order.slice(0, 6)).toEqual(["Molex Micro-Fit 3.0", "Molex Mini-Fit Jr.", "JST JWPF", "JST PA", "JST PHD", "JST SH"]);
    expect(order.slice(-2)).toEqual([NO_SERIES, NO_SERIES]);
    expect(makerOrder([{ maker: "JST" }, { maker: "Molex" }, { maker: "JST" }, { maker: "" }] as never)).toEqual(["JST", "Molex"]);
  });

  it("подпись группы называет производителей, которых нет в имени серии", () => {
    expect(seriesCaption("JST SH", [ROWS[0]])).toBe("JST SH");
    expect(seriesCaption("Наконечники и гильзы", [ROWS[4], ROWS[5]])).toBe("Наконечники и гильзы — КВТ");
    expect(seriesCaption(NO_SERIES, [ROWS[10]])).toBe(NO_SERIES);
  });

  it("без каталога (нет доступа к разъёмам) — серия по колонке «Серия»", () => {
    const own = terminalGroups(ROWS, []);
    expect(own.get("1")?.name).toBe("JST SHDP");
    expect(own.get("5")?.name).toBe("КВТ НШВИ");
    expect(own.get("10")?.name).toBe(NO_SERIES);
  });
});
