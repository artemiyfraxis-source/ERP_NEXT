import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CRIMP_COLUMN_TITLE } from "./CrimpCell";
import { ConnectorGallery, countsText, wireText } from "./ConnectorGallery";
import type { ConnectorSeries } from "./api";
import { connectorModel, hasModel } from "./connectorModel";

/* «База данных → Разъёмы» по сериям (владелец 10.10.2026): список серий → серия со всеми корпусами, вилками на плату,
   терминалами; «Проверить артикул» открывает серию и подсвечивает корпус. */

const ghBody = { extra: 2.5, h: 2.85, len: 5.7, lock_inset: 2.5, lock_h: 1.3, win: [0.7, 1.2] };
const gh = (n: number) => ({
  art: `GHR-0${n}V-S`, family: "jst-gh", title: "JST GH", pitch: 1.25, rows: 1, cols: n, n, kind: "grid", color: "#efe6cf",
  latch: "ramp", cavity: "square", labels: null, grid: [Array.from({ length: n }, (_, i) => i + 1)], round: null,
  conf: "high", src: "JST eGH, Housing", note: "", body: ghBody, model: "plate",
});
const housing = (n: number) => ({ article: `GHR-0${n}V-S`, n, housing: gh(n), headers: [
  { title: "Вилка на плату SMD, вертикальная", article: `BM0${n}B-GHS-TBT` },
  { title: "Вилка на плату SMD, угловая", article: `SM0${n}B-GHS-TB` },
] });
const GH: ConnectorSeries = {
  id: "jst-gh", title: "JST GH", maker: "JST", range_checked: true, range_src: "JST eGH, Housing",
  datasheet: "https://www.jst-mfg.com/product/pdf/eng/eGH.pdf", note: "",
  variants: [{ title: "Корпус", family: "jst-gh", housings: [housing(2), housing(3), housing(4)] }],
  terminals: [{ article: "SSHL-002T-P0.2", maker: "JST", product: "GHD, GH", series: "GHDR, GHR", kind: "гнездо",
    awg_from: "30", awg_to: "26", mm2_from: "0,05", mm2_to: "0,12", strip: "1,7", applicator: "S4/M4/A4", crimp_height: "26 AWG: 0,62; 28 AWG: 0,70", pull_force: "26 AWG: 15; 28 AWG: 12" }],
  wire: { awg_thin: 30, awg_thick: 26, mm2_min: 0.05, mm2_max: 0.12 },
  housing_count: 3, counts: [2, 3, 4], pitch: 1.25, color: "#efe6cf", checked: true, sample: housing(2),
  category: "Провод — плата", catalog: [],
};
const MINIFIT: ConnectorSeries = {
  ...GH, id: "molex-minifit-jr", title: "Molex Mini-Fit Jr.", maker: "Molex", range_checked: false, range_src: "", datasheet: "",
  variants: [{ title: "Molex Mini-Fit Jr.", family: "molex-minifit-jr", housings: [{ article: "39012080", n: 8, headers: [],
    housing: { ...gh(4), art: "39012080", family: "molex-minifit-jr", title: "Molex Mini-Fit Jr.", rows: 2, cols: 4, n: 8, model: "",
      grid: [[8, 7, 6, 5], [4, 3, 2, 1]], pitch: 4.2 } }] }],
  terminals: [], wire: null, housing_count: 1, counts: [8], pitch: 4.2,
};
MINIFIT.sample = MINIFIT.variants[0].housings[0];
// A series known only from БД.ТЕР: no housings, the picture of the closest shape, one terminal.
const MQS: ConnectorSeries = {
  ...GH, id: "te-mqs", title: "TE MQS", maker: "TE", category: "Автомобильные", range_checked: false, range_src: "", datasheet: "",
  variants: [], housing_count: 0, counts: [], pitch: null, checked: false, wire: null,
  terminals: [{ ...GH.terminals[0], article: "929939-3", maker: "TE", product: "MQS", series: "MQS" }],
  sample: { ...housing(2), article: "" },
};

function mockApi() {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    const body = path === "/api/techcards/connector-series" ? { series: [GH, MINIFIT, MQS], match: null }
      : path === "/api/techcards/connector-series?article=GHR-03V-S" ? { series: [GH, MINIFIT, MQS], match: { series: "jst-gh", article: "GHR-03V-S", housing: gh(3) } }
        : path.startsWith("/api/techcards/connector-series?article=") ? { series: [GH, MINIFIT, MQS], match: null }
          : null;
    if (!body) throw new Error(`Unexpected request: ${path}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return paths;
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

describe("ConnectorGallery — разъёмы по сериям", () => {
  it("lists series, not housings, with their contact range and check state", async () => {
    mockApi();
    render(<ConnectorGallery />);
    const card = await screen.findByRole("button", { name: "Серия JST GH" });
    expect(card).toHaveTextContent("1,25 мм · 2–4 конт. · 3 корп.");
    expect(card).toHaveTextContent("по каталогу JST");
    expect(screen.getByRole("button", { name: "Серия Molex Mini-Fit Jr." })).toHaveTextContent("диапазон не сверен");
    expect(screen.getByText("3 серий · 4 корпусов")).toBeInTheDocument();
    expect(screen.queryByText("GHR-03V-S")).toBeNull();
  });

  it("groups the series into sections by type with a count, and a chip filters to one section", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await screen.findByRole("button", { name: "Серия JST GH" });
    expect(screen.getByRole("heading", { name: /^Провод — плата\s*2$/, level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /^Автомобильные\s*1$/, level: 3 })).toBeInTheDocument();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings[0]).toMatch(/^Провод — плата/);
    const chips = within(screen.getByRole("group", { name: "Тип разъёмов" }));
    expect(chips.getByRole("button", { name: /^Все\s*3$/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(chips.getByRole("button", { name: /^Автомобильные\s*1$/ }));
    expect(screen.queryByRole("button", { name: "Серия JST GH" })).toBeNull();
    expect(screen.getByRole("button", { name: "Серия TE MQS" })).toBeInTheDocument();
    // The search still looks through every type, also by a terminal article.
    fireEvent.click(chips.getByRole("button", { name: /^Все\s*3$/ }));
    fireEvent.change(screen.getByLabelText("Артикул корпуса"), { target: { value: "929939" } });
    expect(screen.getByRole("button", { name: "Серия TE MQS" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Серия JST GH" })).toBeNull();
    expect(chips.getByRole("button", { name: /^Провод — плата\s*0$/ })).toBeInTheDocument();
  });

  it("shows a series without housings as «корпуса не добавлены» with its terminals", async () => {
    mockApi();
    render(<ConnectorGallery />);
    const card = await screen.findByRole("button", { name: "Серия TE MQS" });
    expect(card).toHaveTextContent("1 терминалов");
    expect(card).toHaveTextContent("корпуса не добавлены");
    fireEvent.click(card);
    expect(screen.getByRole("heading", { name: "TE MQS", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("корпуса не добавлены")).toBeInTheDocument();
    expect(screen.getByText("929939-3")).toBeInTheDocument();
    expect(screen.queryByText("нумерация не сверена с чертежом")).toBeNull();
  });

  it("opens a series with every housing, its board headers, terminals, wire range and the maker drawing", async () => {
    mockApi();
    render(<ConnectorGallery />);
    fireEvent.click(await screen.findByRole("button", { name: "Серия JST GH" }));
    expect(screen.getByRole("heading", { name: "JST GH", level: 2 })).toBeInTheDocument();
    for (const article of ["GHR-02V-S", "GHR-03V-S", "GHR-04V-S"]) expect(screen.getByText(article)).toBeInTheDocument();
    expect(screen.getByText("BM04B-GHS-TBT")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Чертёж производителя (PDF)" })).toHaveAttribute("href", GH.datasheet);
    expect(screen.getByText("провод AWG 30–26 · 0,05–0,12 мм²")).toBeInTheDocument();
    expect(screen.getByText("SSHL-002T-P0.2")).toBeInTheDocument();
    expect(screen.getByText(CRIMP_COLUMN_TITLE)).toBeInTheDocument();
    const rows = Array.from(document.querySelectorAll(".crimp-row")).map((row) => row.textContent);
    expect(rows).toEqual(["26 AWG0,6215 Н", "28 AWG0,7012 Н"]);
    expect(screen.getAllByRole("img", { name: /^Вид сверху GHR/ })).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "← Все серии" }));
    expect(await screen.findByRole("button", { name: "Серия JST GH" })).toBeInTheDocument();
  });

  it("draws a housing without a drawing template the same way: top view and the numbered face", async () => {
    mockApi();
    render(<ConnectorGallery />);
    fireEvent.click(await screen.findByRole("button", { name: "Серия Molex Mini-Fit Jr." }));
    const card = screen.getByText("39012080").closest(".conn-housing") as HTMLElement;
    expect(within(card).getByRole("img", { name: "Вид сверху 39012080" })).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: "Вид со стороны проводов" })).toBeInTheDocument();
    expect(screen.getByText("В «Терминалы» нет контактов этой серии.")).toBeInTheDocument();
  });

  it("«Проверить артикул» opens the series and marks the housing", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await screen.findByRole("button", { name: "Серия JST GH" });
    fireEvent.change(screen.getByLabelText("Артикул корпуса"), { target: { value: "GHR-03V-S" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить артикул" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "JST GH", level: 2 })).toBeInTheDocument());
    expect(screen.getByText("GHR-03V-S").closest(".conn-housing")).toHaveClass("conn-hit");
    expect(screen.getByText("GHR-02V-S").closest(".conn-housing")).not.toHaveClass("conn-hit");

    fireEvent.click(screen.getByRole("button", { name: "← Все серии" }));
    fireEvent.change(screen.getByLabelText("Артикул корпуса"), { target: { value: "XYZ-1" } });
    expect(screen.getByText(/Серий по «XYZ-1» нет/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Проверить артикул" }));
    expect(await screen.findByText("«XYZ-1» — серия не распознана: на схеме будет только название.")).toBeInTheDocument();
  });

  it("filters the series list by the typed series or article", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await screen.findByRole("button", { name: "Серия JST GH" });
    fireEvent.change(screen.getByLabelText("Артикул корпуса"), { target: { value: "3901" } });
    expect(screen.queryByRole("button", { name: "Серия JST GH" })).toBeNull();
    expect(screen.getByRole("button", { name: "Серия Molex Mini-Fit Jr." })).toBeInTheDocument();
  });
});

describe("connector series helpers", () => {
  it("writes contact ranges shortly", () => {
    expect(countsText([2, 3, 4, 5])).toBe("2–5");
    expect(countsText([1, 2, 3, 16, 20])).toBe("1–3, 16, 20");
    expect(countsText([8, 10, 12, 14])).toBe("8–14, чётные");
    expect(countsText([4])).toBe("4");
    expect(countsText([])).toBe("");
  });

  it("writes the wire range as AWG and mm²", () => {
    expect(wireText({ awg_thin: 32, awg_thick: 28, mm2_min: 0.03, mm2_max: 0.08 })).toBe("AWG 32–28 · 0,03–0,08 мм²");
    expect(wireText({ awg_thin: 16, awg_thick: 16, mm2_min: 1.5, mm2_max: 1.5 })).toBe("AWG 16 · 1,5 мм²");
    expect(wireText(null)).toBe("");
  });

  it("draws a model only for a checked template with the drawing's sizes", () => {
    expect(hasModel(gh(4))).toBe(true);
    expect(hasModel({ ...gh(4), model: "" })).toBe(false);
    expect(hasModel({ ...gh(4), body: null })).toBe(false);
    expect(connectorModel({ ...gh(4), model: "" }, 100, 60)).toBeNull();
    const plate = connectorModel(gh(4), 130, 84)!;
    const frame = connectorModel({ ...gh(4), model: "frame", body: { ...ghBody, lock_inset: 1.8, lock_h: 1.6 } }, 130, 84)!;
    expect(plate.body).toContain('fill="#d7322d"'); // contact 1 marked on the model too
    expect(frame.body).not.toBe(plate.body);
    const slotsBelow = connectorModel({ ...gh(4), model: "slots", latch: "windows" }, 130, 84)!;
    expect(slotsBelow.h).toBeLessThanOrEqual(84 + 1);
  });
});

describe("ConnectorGallery — каталоги производителей (12.10.2026)", () => {
  const DF1B: ConnectorSeries = {
    ...GH, id: "hirose-df1b", title: "Hirose DF1B", maker: "Hirose", range_src: "Hirose DF1B, каталог 01.07.2020",
    checked: false, datasheet: "https://datasheet.lcsc.com/df1b.pdf", terminals: [],
    sources: [{ title: "Hirose DF1B, каталог 01.07.2020", url: "https://datasheet.lcsc.com/df1b.pdf" }],
    contacts: [{ article: "DF1B-2428SCFA", wire: "гнездо, AWG 28–24, золото", in_base: true },
      { article: "DF1-PD2428SCFB", wire: "штырь, AWG 28–24, золото", in_base: false }],
  };

  it("labels each maker inside a section, shows the documents and which catalogue contacts «Терминалы» lacks", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(
      JSON.stringify({ series: [GH, MINIFIT, DF1B], match: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    render(<ConnectorGallery />);
    await screen.findByRole("button", { name: "Серия Hirose DF1B" });
    const makers = [...document.querySelectorAll(".conn-maker")].map((node) => node.textContent);
    expect(makers).toEqual(["JST", "Molex", "Hirose"]);
    // GH and DF1B share the article list of the fixture: a housing in two series is counted once
    expect(screen.getByText("3 серий · 4 корпусов")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Серия Hirose DF1B" }));
    const doc = await screen.findByRole("link", { name: "Hirose DF1B, каталог 01.07.2020" });
    expect(doc).toHaveAttribute("href", "https://datasheet.lcsc.com/df1b.pdf");
    expect(screen.queryByRole("link", { name: "Чертёж производителя (PDF)" })).toBeNull();
    expect(screen.getByText(/нет во вкладке «Терминалы»: 1/)).toBeInTheDocument();
    // the long housing list pushes the table down: a chip at the top jumps to it
    expect(screen.getByRole("button", { name: "контактов нет в «Терминалы»: 1 ↓" })).toBeInTheDocument();
    const missingRow = screen.getByText("DF1-PD2428SCFB").closest("tr")!;
    expect(within(missingRow).getByText("нет — добавить")).toBeInTheDocument();
  });
});
