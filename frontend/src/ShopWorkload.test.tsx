import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShopWorkloadView } from "./ShopWorkload";
import type { ShopWorkload, WorkloadProject } from "./api";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

function project(overrides: Partial<WorkloadProject>): WorkloadProject {
  return {
    project: "713К.4", customer: "ООО «ТСР»", deadline: "2026-11-10", hours: 40, by_site: { "ЦАР": 40 },
    by_kind: { "Обжим": 30, "Монтаж": 10 }, ready: "2026-11-05", status: "в срок", slack_days: 5, order: 1,
    kbs: [{ index: "КБ1", name: "Жгут НВ4", run: 500, done: 0, done_source: "", remaining: 500, hours: 40, unit_hours: 0.08,
      site: "ЦАР", site_note: "", by_kind: { "Обжим": 30, "Монтаж": 10 } }],
    ...overrides,
  };
}

const DATA: ShopWorkload = {
  today: "2026-10-29", horizon_to: "2027-01-28", shift_hours: 7, sites: ["ЦАР", "ЖУК"], unassigned: "Не распределено",
  shop: "Весь цех", kinds: ["Обжим", "Монтаж"], people: { "ЦАР": 4, "ЖУК": 2 },
  projects: [
    project({}),
    project({ project: "776К.1", customer: "ООО «ЯНДЕКС»", deadline: "2026-10-26", hours: 300, by_site: { "ЖУК": 300 },
      ready: "2026-11-20", status: "не успевает", slack_days: -25, order: 2 }),
  ],
  weeks: [{ start: "2026-10-26", end: "2026-11-01", sites: {
    "ЦАР": { capacity: 140, demand: 40, planned: 40, kinds: { "Обжим": 30, "Монтаж": 10 } },
    "ЖУК": { capacity: 70, demand: 300, planned: 70, kinds: { "Обжим": 300 } },
  } }],
  bottlenecks: [{ site: "ЖУК", start: "2026-10-26", demand: 300, capacity: 70, kinds: [["Обжим", 300]] }],
  no_run: [],
};

it("shows the projects with their remaining hours, forecast and status, the late count and the week strip", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(DATA));
  render(<ShopWorkloadView />);
  expect(await screen.findByText("713К.4")).toBeInTheDocument();
  expect(screen.getByText("776К.1")).toBeInTheDocument();
  expect(screen.getAllByText("не успевает").length).toBeGreaterThan(0);
  expect(screen.getByText(/не успевают: 1/)).toBeInTheDocument();
  // the week strip: ЖУК is overloaded (300 h needed against 70 h)
  expect(screen.getByText("429 %")).toBeInTheDocument();
  expect(screen.getByText(/Узкие места/)).toBeInTheDocument();
});

it("filters by site", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(DATA));
  render(<ShopWorkloadView />);
  await screen.findByText("713К.4");
  fireEvent.click(screen.getByRole("radio", { name: "ЦАР" }));
  expect(screen.getByText("713К.4")).toBeInTheDocument();
  expect(screen.queryByText("776К.1")).toBeNull();
});

it("says what went wrong when the server fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "нет доступа" }, 403));
  render(<ShopWorkloadView />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});
