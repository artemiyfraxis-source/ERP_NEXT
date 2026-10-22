import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { VswrChart, vswrChartSvg } from "./VswrChart";

const trace = [{ mhz: 1700, vswr: 1.2 }, { mhz: 1800, vswr: 1.9 }, { mhz: 1900, vswr: 1.4 }];

it("draws the trace, the norm line, the band edges and the maximum marker", () => {
  const svg = vswrChartSvg({ trace, normMax: 2.06, band: [1700, 1900], maxVswr: 1.9, maxMhz: 1800 });
  expect(svg).toContain("<polyline");
  expect(svg).toContain("Норма ≤ 2,06");
  expect(svg).toContain('stroke="#c00000"');
  expect(svg.match(/stroke-dasharray="5 3"/g)).toHaveLength(2);
  expect(svg).toContain("Максимум 1,9 на 1");
  expect(svg).toContain("<circle");
  expect(svg).toContain("Частота, МГц");
});

it("leaves the norm line out when there is no norm, and never uses style or var() (CSP-safe, black on white)", () => {
  const svg = vswrChartSvg({ trace, normMax: null });
  expect(svg).not.toContain("Норма ≤");
  expect(svg).not.toContain("style=");
  expect(svg).not.toContain("var(");
  expect(svg).not.toContain('stroke="#c00000"');
});

it("keeps the norm line inside the plot when the trace stays far below it", () => {
  const svg = vswrChartSvg({ trace: [{ mhz: 1, vswr: 1.1 }, { mhz: 2, vswr: 1.2 }], normMax: 2.5 });
  const y = Number(/<line x1="52" x2="\d+" y1="([\d.]+)" y2="[\d.]+" stroke="#c00000"/.exec(svg)?.[1]);
  expect(y).toBeGreaterThanOrEqual(16);
  expect(y).toBeLessThan(260);
});

it("says so when the file has no points, and exposes the chart to assistive tech", () => {
  const { rerender } = render(<VswrChart trace={[]} normMax={2} />);
  expect(screen.getByText("Нет точек замера")).toBeInTheDocument();
  rerender(<VswrChart trace={trace} normMax={2.06} />);
  expect(screen.getByRole("img", { name: "График КСВН по частоте, норма не более 2,06" })).toBeInTheDocument();
});
