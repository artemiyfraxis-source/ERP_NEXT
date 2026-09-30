import { describe, expect, it } from "vitest";
import { mergeDayBars } from "./Gantt";

describe("mergeDayBars", () => {
  it("draws one plaque per day over the whole day, keeps multi-day bars and absences", () => {
    const merged = mergeDayBars([
      { start: "2026-09-30T09:00:00", finish: "2026-09-30T10:30:00", tone: "done", text: "П-1 · КБ1", title: "этап 1" },
      { start: "2026-09-30T10:30:00", finish: "2026-09-30T13:00:00", tone: "late", text: "П-1 · КБ1", title: "этап 2" },
      { start: "2026-09-30T13:30:00", finish: "2026-09-30T15:00:00", text: "П-2 · КБ3", title: "этап 1" },
      { start: "2026-10-01T09:00:00", finish: "2026-10-02T12:00:00", text: "П-3 · КБ1" },
      { start: "2026-10-05", finish: "2026-10-07", tone: "absent", text: "Отпуск" },
      { start: "2026-10-06T09:00:00", finish: "2026-10-06T12:00:00", tone: "done", text: "П-4 · КБ2", title: "этап 3" },
    ]);
    expect(merged).toEqual([
      { start: "2026-10-01T09:00:00", finish: "2026-10-02T12:00:00", text: "П-3 · КБ1" },
      { start: "2026-10-05", finish: "2026-10-07", tone: "absent", text: "Отпуск" },
      { start: "2026-09-30", finish: "2026-10-01", tone: "late", text: "3 оп. · 5,5 ч · П-1 · КБ1, П-2 · КБ3", title: "9:00–10:30 этап 1\n10:30–13:00 этап 2\n13:30–15:00 этап 1" },
      { start: "2026-10-06", finish: "2026-10-07", tone: "done", text: "П-4 · КБ2 · 3 ч", title: "9:00–12:00 этап 3" },
    ]);
  });
});
