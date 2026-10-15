import { describe, expect, it } from "vitest";
import { matchesSearch, searchKey, searchRows, searchWords } from "./searchText";

describe("поиск по базам", () => {
  it("не смотрит на регистр, пробелы, дефисы, точки и косые внутри марки", () => {
    for (const query of ["RG58", "RG-58", "rg 58", "rg-58", "Rg.58"]) expect(matchesSearch(["Кабель RG-58 C/U"], query)).toBe(true);
    expect(matchesSearch(["RG58"], "rg-58")).toBe(true);
    expect(matchesSearch(["0511910600"], "051191-0600")).toBe(true);
    expect(matchesSearch(["051191-0600"], "0511910600")).toBe(true);
    expect(matchesSearch(["SHR-02V-S-B"], "SHR02VSB")).toBe(true);
    expect(matchesSearch(["SHR02VSB"], "shr-02v-s-b")).toBe(true);
    expect(matchesSearch(["ТУТ 6.4/3.2"], "тут 6,4 3,2")).toBe(true);
  });

  it("кириллица и латиница-двойники — одна буква", () => {
    expect(matchesSearch(["МГТФ 0,35"], "MГТФ")).toBe(true); // латинская M
    expect(matchesSearch(["Наконечник НШВИ"], "HШBИ")).toBe(true); // латинские H и B
    expect(matchesSearch(["PK 50-3-11"], "РК 50")).toBe(true); // латиница в базе, кириллица в запросе
    expect(matchesSearch(["СЦНК"], "CЦHK")).toBe(true);
    expect(matchesSearch(["КВТ"], "KBT")).toBe(true);
    expect(matchesSearch(["Ёмкость"], "емкость")).toBe(true);
  });

  it("десятичная запятая равна точке", () => {
    expect(matchesSearch(["МГТФ 0.35"], "0,35")).toBe(true);
    expect(matchesSearch(["МГТФ 0,35"], "0.35")).toBe(true);
    expect(matchesSearch(["МГТФ 0,35"], "0,5")).toBe(false);
    expect(matchesSearch(["3,00 - 3,50"], "0,35")).toBe(false); // соседние числа не склеиваются в «300350»
    expect(matchesSearch(["Сечение 0,2–0,75"], "0.75")).toBe(true);
  });

  it("несколько слов в любом порядке — каждое должно найтись (И)", () => {
    expect(matchesSearch(["Molex", "Micro-Fit 3.0", "43030-0007"], "43030 molex")).toBe(true);
    expect(matchesSearch(["Molex", "Micro-Fit 3.0", "43030-0007"], "micro fit jst")).toBe(false);
    expect(matchesSearch(["МГТФ 0,35", "ГОСТ"], "0,35 мгтф")).toBe(true);
  });

  it("пробелы по краям и пустой запрос не ломают поиск", () => {
    expect(matchesSearch(["RG58"], "  rg58 ")).toBe(true);
    expect(matchesSearch(["RG58"], "   ")).toBe(true);
    expect(matchesSearch(["RG58"], " - ")).toBe(true);
    expect(matchesSearch([null, undefined, 5], "5")).toBe(true);
  });

  it("слово не склеивается из соседних ячеек", () => {
    expect(matchesSearch(["Molex", "5557"], "molex5557")).toBe(false);
  });

  it("запрос из нескольких слов — сперва как одна марка, иначе слова в любом порядке", () => {
    const rows = [
      { mark: "RG58", note: "" }, { mark: "RG-58 C/U", note: "" },
      { mark: "RG59 B/U", note: "Ø 3,58" }, // «rg» и «58» есть, но не одной маркой
      { mark: "МГТФ 0,35", note: "" }, { mark: "Molex", note: "5557" },
    ];
    const marks = (query: string) => searchRows(rows, (row) => [row.mark, row.note], query).map((row) => row.mark);
    expect(marks("rg 58")).toEqual(["RG58", "RG-58 C/U"]);
    expect(marks("0,35 мгтф")).toEqual(["МГТФ 0,35"]);
    expect(marks("5557 molex")).toEqual(["Molex"]);
    expect(marks(" ")).toHaveLength(5);
    expect(marks("rg 60")).toEqual([]);
  });

  it("ключ и слова запроса", () => {
    expect(searchKey("RG-58 C/U")).toBe("rg58cu");
    expect(searchKey("МГТФ 0,35")).toBe(searchKey("MГTФ 0.35"));
    expect(searchWords("  SHR-02V  0,35 ")).toEqual(["shr02v", "0.35"]);
  });
});
