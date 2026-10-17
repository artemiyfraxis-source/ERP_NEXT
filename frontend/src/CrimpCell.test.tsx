import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CrimpCell, cleanCrimpValue, crimpText, parseCrimp } from "./CrimpCell";

const rows = () => Array.from(document.querySelectorAll(".crimp-row")).map((row) => row.textContent);
const cells = () => Array.from(document.querySelectorAll<HTMLElement>(".crimp-val"));

describe("CrimpCell", () => {
  it("writes the AWG once and lines the three values up per AWG", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56; 28 AWG: 0,52–0,58", insulation: "28 AWG: 1,30–1,45", pull: "30 AWG: 5,9; 28 AWG: 10" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,56—5,9 Н", "28 AWG0,52–0,581,30–1,4510 Н"]);
  });

  it("keeps three value columns and a blank AWG track for a plain value", () => {
    render(<CrimpCell values={{ conductor: "0,85–0,90", pull: "9,8–19,6" }} />);
    expect(rows()).toEqual(["0,85–0,90—9,8–19,6 Н"]);
    expect(document.querySelector(".crimp-awg")).toBeNull();
    expect(document.querySelectorAll(".crimp-row > *")).toHaveLength(4);
  });

  it("applies a value without an AWG to every AWG row instead of a separate row", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56; 28 AWG: 0,52–0,58", pull: "9,8–19,6" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,56—9,8–19,6 Н", "28 AWG0,52–0,58—9,8–19,6 Н"]);
  });

  it("shows a dash in its own column for a missing value, a dash in the sheet included", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56", pull: "—" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,56——"]);
    expect(cells().map((cell) => cell.className)).toEqual(["crimp-val", "crimp-val crimp-none", "crimp-val crimp-none"]);
    expect(cells()[1].title).toBe("Высота обжима по изоляции, мм");
  });

  it("shows a single dash when there is no crimp data at all", () => {
    const { container } = render(<CrimpCell values={{ conductor: "", insulation: "", pull: " " }} />);
    expect(container.textContent).toBe("—");
  });

  it("shows the clean number or range of legacy text and keeps the original in the tooltip", () => {
    render(<CrimpCell values={{
      conductor: "26 AWG: 1,55 (UL1061) / 1,65 (UL1007)",
      insulation: "26 AWG: 2,80–3,35 (справочно, зависит от профиля вставки A/B/C)",
      pull: "26 AWG: см. чертёж",
    }} />);
    expect(rows()).toEqual(["26 AWG1,55–1,652,80–3,35—"]);
    expect(cells()[0].title).toBe("Высота обжима по жиле, мм: 1,55 (UL1061) / 1,65 (UL1007)");
    expect(cells()[1].title).toContain("2,80–3,35 (справочно, зависит от профиля вставки A/B/C)");
    expect(cells()[2].title).toBe("Усилие отрыва, Н: см. чертёж");
  });

  it("names the column and the value in the tooltip of a clean value, only the column for a missing one", () => {
    render(<CrimpCell values={{ conductor: "0,85", pull: "54" }} />);
    expect(cells().map((cell) => cell.title)).toEqual(["Высота обжима по жиле, мм: 0,85", "Высота обжима по изоляции, мм", "Усилие отрыва, Н: 54"]);
  });

  it("does not split on a semicolon inside parentheses", () => {
    expect([...parseCrimp("28 AWG: 0,52 (справочно; профиль A); 26 AWG: 0,60").entries()]).toEqual([["28 AWG", "0,52 (справочно; профиль A)"], ["26 AWG", "0,60"]]);
  });

  it("parses AWG ranges and sections and joins the original text for search", () => {
    expect([...parseCrimp("28–24 AWG: 0,55–0,70; 0,5 мм2: 1,07–1,17").entries()]).toEqual([["28–24 AWG", "0,55–0,70"], ["0,5 мм2", "1,07–1,17"]]);
    expect(crimpText({ conductor: "0,85", insulation: "1,55 (UL1061)", pull: "54" })).toBe("0,85 | 1,55 (UL1061) | 54");
  });
});

describe("cleanCrimpValue", () => {
  it.each([
    ["0,65", "0,65"],
    ["0,65–0,70", "0,65–0,70"],
    ["0.65-0.70", "0,65–0,70"],
    ["2,80 / 2,85 / 3,35", "2,80–3,35"],
    ["2,80–3,35 (справочно, зависит от профиля вставки A/B/C)", "2,80–3,35"],
    ["1,55 (UL1061) / 1,65 (UL1007)", "1,55–1,65"],
    ["UL1007 1,65", "1,65"],
    ["3,35 / 2,80", "2,80–3,35"],
    ["≥ 30 Н", "30"],
    ["0,85 мм", "0,85"],
    ["1,10 / 1,10", "1,10"],
    ["см. чертёж", ""],
    ["(справочно 2,5)", ""],
  ])("%s → %s", (raw, clean) => {
    expect(cleanCrimpValue(raw)).toBe(clean);
  });
});
