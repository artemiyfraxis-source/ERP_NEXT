import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CrimpCell, crimpText, parseCrimp } from "./CrimpCell";

const rows = () => Array.from(document.querySelectorAll(".crimp-row")).map((row) => row.textContent);

describe("CrimpCell", () => {
  it("writes the AWG once and lines the three values up per AWG", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56; 28 AWG: 0,52–0,58", insulation: "28 AWG: 1,30–1,45", pull: "30 AWG: 5,9; 28 AWG: 10" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,56—5,9 Н", "28 AWG0,52–0,581,30–1,4510 Н"]);
  });

  it("keeps a plain value on one line without an AWG column", () => {
    render(<CrimpCell values={{ conductor: "0,85–0,90", pull: "9,8–19,6" }} />);
    expect(rows()).toEqual(["0,85–0,909,8–19,6 Н"]);
    expect(document.querySelector(".crimp-awg")).toBeNull();
  });

  it("applies a value without an AWG to every AWG row instead of a separate row", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56; 28 AWG: 0,52–0,58", pull: "9,8–19,6" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,569,8–19,6 Н", "28 AWG0,52–0,589,8–19,6 Н"]);
  });

  it("ignores a dash written in the sheet instead of a value", () => {
    render(<CrimpCell values={{ conductor: "30 AWG: 0,52–0,56", pull: "—" }} />);
    expect(rows()).toEqual(["30 AWG0,52–0,56"]);
  });

  it("shows a single dash when there is no crimp data at all", () => {
    const { container } = render(<CrimpCell values={{ conductor: "", insulation: "", pull: " " }} />);
    expect(container.textContent).toBe("—");
  });

  it("parses AWG ranges and sections and joins text for search", () => {
    expect([...parseCrimp("28–24 AWG: 0,55–0,70; 0,5 мм2: 1,07–1,17").entries()]).toEqual([["28–24 AWG", "0,55–0,70"], ["0,5 мм2", "1,07–1,17"]]);
    expect(crimpText({ conductor: "0,85", pull: "54" })).toBe("0,85 | 54");
  });
});

