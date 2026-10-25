import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectorGallery, countsText, wireText } from "./ConnectorGallery";
import type { ConnectorSeries } from "./api";
import { connectorModel, hasModel } from "./connectorModel";
import { blockLines, listHeads } from "./test/blocks";

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
    awg_from: "30", awg_to: "26", mm2_from: "0,05", mm2_to: "0,12", strip: "1,7", applicator: "S4/M4/A4", crimp_height: "26 AWG: 0,62; 28 AWG: 0,70", pull_force: "26 AWG: 15; 28 AWG: 12",
    ins_crimp_height: "26 AWG: 1,35–1,50" }],
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

// The series list is a table in blocks by type (25.10.2026): a row per series, a click opens it.
const rowOf = (title: string) => screen.getByText(title, { selector: "td.key strong" }).closest("tr") as HTMLElement;
const seriesRow = async (title: string) => (await screen.findByText(title, { selector: "td.key strong" })).closest("tr") as HTMLElement;
const cellOf = (row: HTMLElement, header: string) => row.querySelectorAll("td")[listHeads().findIndex((head) => head.textContent === header)];

describe("ConnectorGallery — разъёмы по сериям", () => {
  it("lists series, not housings, with their contact range and check state", async () => {
    mockApi();
    render(<ConnectorGallery />);
    const row = await seriesRow("JST GH");
    expect(listHeads().map((head) => head.textContent)).toEqual(["Серия", "Производитель", "Шаг, мм", "Контактов", "Корпусов", "Терминалов", "Провод", "Состояние"]);
    expect(["Шаг, мм", "Контактов", "Корпусов", "Состояние"].map((header) => cellOf(row, header).textContent))
      .toEqual(["1,25", "2–4", "3", "по каталогу JST"]);
    expect(cellOf(rowOf("Molex Mini-Fit Jr."), "Состояние")).toHaveTextContent("диапазон не сверен");
    expect(screen.getByText("3 серий · 4 корпусов")).toBeInTheDocument();
    expect(screen.queryByText("GHR-03V-S")).toBeNull();
  });

  it("puts the series in a block per type with a count; the search goes through every block, also by a terminal article", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await seriesRow("JST GH");
    expect(blockLines()).toEqual(["Провод — плата · 2", "Автомобильные · 1"]);
    // the quick filters say what a series lacks
    fireEvent.click(screen.getByRole("button", { name: /^Корпуса не добавлены · 1/ }));
    expect(blockLines()).toEqual(["Автомобильные · 1"]);
    fireEvent.click(screen.getByRole("button", { name: /^Все · 3/ }));
    fireEvent.change(screen.getByLabelText("Серия или артикул корпуса"), { target: { value: "929939" } });
    expect(blockLines()).toEqual(["Автомобильные · 1"]); // an empty block is not shown
    expect(screen.queryByText("JST GH", { selector: "td.key strong" })).toBeNull();
  });

  it("shows a series without housings as «корпуса не добавлены» with its terminals", async () => {
    mockApi();
    render(<ConnectorGallery />);
    const card = await seriesRow("TE MQS");
    expect(cellOf(card, "Терминалов")).toHaveTextContent(/^1$/);
    expect(cellOf(card, "Состояние")).toHaveTextContent("корпуса не добавлены");
    fireEvent.click(card);
    expect(screen.getByRole("heading", { name: "TE MQS", level: 2 })).toBeInTheDocument();
    const view = document.querySelector(".conn-series-view") as HTMLElement;
    expect(within(view).getByText("корпуса не добавлены")).toBeInTheDocument();
    expect(within(view).getByText("929939-3")).toBeInTheDocument();
    expect(screen.queryByText("нумерация не сверена с чертежом")).toBeNull();
  });

  it("opens a series with every housing, its board headers, terminals, wire range and the maker drawing", async () => {
    mockApi();
    render(<ConnectorGallery />);
    fireEvent.click(await seriesRow("JST GH"));
    expect(screen.getByRole("heading", { name: "JST GH", level: 2 })).toBeInTheDocument();
    for (const article of ["GHR-02V-S", "GHR-03V-S", "GHR-04V-S"]) expect(screen.getByText(article)).toBeInTheDocument();
    expect(screen.getByText("BM04B-GHS-TBT")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Чертёж производителя (PDF)" })).toHaveAttribute("href", GH.datasheet);
    expect(screen.getByText("провод AWG 30–26 · 0,05–0,12 мм²")).toBeInTheDocument();
    expect(screen.getByText("SSHL-002T-P0.2")).toBeInTheDocument();
    for (const title of ["Обжим по жиле, мм", "Обжим по изоляции, мм", "Отрыв, Н"]) expect(screen.getByText(title)).toBeInTheDocument();
    const rows = Array.from(document.querySelectorAll(".crimp-row")).map((row) => row.textContent);
    expect(rows).toEqual(["26 AWG0,62", "28 AWG0,70", "26 AWG1,35–1,50", "26 AWG15 Н", "28 AWG12 Н"]);
    expect(screen.getAllByRole("img", { name: /^Вид сверху GHR/ })).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "← Все серии" }));
    expect(await seriesRow("JST GH")).toBeInTheDocument();
  });

  it("draws a housing without a drawing template the same way: top view and the numbered face", async () => {
    mockApi();
    render(<ConnectorGallery />);
    fireEvent.click(await seriesRow("Molex Mini-Fit Jr."));
    const card = screen.getByText("39012080").closest(".conn-housing") as HTMLElement;
    expect(within(card).getByRole("img", { name: "Вид сверху 39012080" })).toBeInTheDocument();
    expect(within(card).getByRole("img", { name: "Вид со стороны проводов" })).toBeInTheDocument();
    expect(screen.getByText("В «Терминалы» нет контактов этой серии.")).toBeInTheDocument();
  });

  it("«Проверить артикул» opens the series and marks the housing", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await seriesRow("JST GH");
    fireEvent.change(screen.getByLabelText("Серия или артикул корпуса"), { target: { value: "GHR-03V-S" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить артикул" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "JST GH", level: 2 })).toBeInTheDocument());
    expect(screen.getByText("GHR-03V-S").closest(".conn-housing")).toHaveClass("conn-hit");
    expect(screen.getByText("GHR-02V-S").closest(".conn-housing")).not.toHaveClass("conn-hit");

    fireEvent.click(screen.getByRole("button", { name: "← Все серии" }));
    fireEvent.change(screen.getByLabelText("Серия или артикул корпуса"), { target: { value: "XYZ-1" } });
    expect(screen.getByText(/Серий не найдено/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Проверить артикул" }));
    expect(await screen.findByText("«XYZ-1» — серия не распознана: на схеме будет только название.")).toBeInTheDocument();
  });

  it("filters the series list by the typed series or article", async () => {
    mockApi();
    render(<ConnectorGallery />);
    await seriesRow("JST GH");
    fireEvent.change(screen.getByLabelText("Серия или артикул корпуса"), { target: { value: "3901" } });
    expect(screen.queryByText("JST GH", { selector: "td.key strong" })).toBeNull();
    expect(rowOf("Molex Mini-Fit Jr.")).toBeInTheDocument();
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

  it("labels each maker inside a section and shows the documents; the catalogue contacts block is gone (owner 17.10)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(
      JSON.stringify({ series: [GH, MINIFIT, DF1B], match: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    render(<ConnectorGallery />);
    await seriesRow("Hirose DF1B");
    // the maker is a column of the table (25.10.2026), in the server's order inside the block
    expect(["JST GH", "Molex Mini-Fit Jr.", "Hirose DF1B"].map((title) => cellOf(rowOf(title), "Производитель").textContent)).toEqual(["JST", "Molex", "Hirose"]);
    // GH and DF1B share the article list of the fixture: a housing in two series is counted once
    expect(screen.getByText("3 серий · 4 корпусов")).toBeInTheDocument();

    fireEvent.click(rowOf("Hirose DF1B"));
    const doc = await screen.findByRole("link", { name: "Hirose DF1B, каталог 01.07.2020" });
    expect(doc).toHaveAttribute("href", "https://datasheet.lcsc.com/df1b.pdf");
    expect(screen.queryByRole("link", { name: "Чертёж производителя (PDF)" })).toBeNull();
    expect(screen.queryByText(/Контакты по каталогу/)).toBeNull();
    expect(screen.queryByText("DF1-PD2428SCFB")).toBeNull();
    expect(screen.queryByRole("button", { name: /контактов нет в «Терминалы»/ })).toBeNull();
  });
});

describe("ConnectorGallery — терминалы правятся в таблице карточки серии (owner 17.10)", () => {
  const EDITABLE: ConnectorSeries = {
    ...GH, terminals: [{ ...GH.terminals[0], id: "rec-1", raw: {
      terType: "Female", terGender: "Female", awgFrom: "30", awgTo: "26", mm2From: "0,05", mm2To: "0,12", terStrip: "1,7", terApplicator: "S4/M4/A4",
      terCrimpHeight: "26 AWG: 0,62; 28 AWG: 0,70", terInsCrimpHeight: "26 AWG: 1,35–1,50", terPullForceMin: "26 AWG: 15; 28 AWG: 12" } }],
  };
  function setup() {
    const calls: Array<{ path: string; method: string; body: unknown }> = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
      const body = method === "PATCH" ? { id: "rec-1", fields: {} } : { series: [EDITABLE], match: null };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    return calls;
  }
  const cellOf = (header: string) => {
    const row = screen.getByText("SSHL-002T-P0.2").closest("tr") as HTMLElement;
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === header);
    return row.querySelectorAll("td")[index] as HTMLElement;
  };
  const type = (cell: HTMLElement, value: string) => {
    fireEvent.click(cell);
    fireEvent.keyDown(cell, { key: "Enter" });
    const input = within(cell).getByRole("textbox");
    fireEvent.change(input, { target: { value } });
    fireEvent.keyDown(input, { key: "Enter" });
  };

  it("writes a crimp cell to the БД.ТЕР record with its old value, then reads the card again", async () => {
    const calls = setup();
    render(<ConnectorGallery canEdit />);
    fireEvent.click(await seriesRow("JST GH"));
    type(cellOf("Обжим по жиле, мм"), "26 AWG: 0,60; 28 AWG: 0,68");
    await waitFor(() => expect(calls.find((call) => call.method === "PATCH")).toMatchObject({
      path: "/api/references/tech-operations/rec-1",
      body: { edits: [{ field: "terCrimpHeight", old: "26 AWG: 0,62; 28 AWG: 0,70", value: "26 AWG: 0,60; 28 AWG: 0,68" }] },
    }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/api/techcards/connector-series")).toHaveLength(2));
  });

  it("splits the AWG range into «от AWG» and «до AWG»", async () => {
    const calls = setup();
    render(<ConnectorGallery canEdit />);
    fireEvent.click(await seriesRow("JST GH"));
    type(cellOf("AWG"), "28–24");
    await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({
      edits: [{ field: "awgFrom", old: "30", value: "28" }, { field: "awgTo", old: "26", value: "24" }],
    }));
  });

  it("keeps the article and the whole table read-only without the right to edit", async () => {
    setup();
    render(<ConnectorGallery />);
    fireEvent.click(await seriesRow("JST GH"));
    fireEvent.click(cellOf("AWG"));
    fireEvent.keyDown(cellOf("AWG"), { key: "Enter" });
    expect(within(cellOf("AWG")).queryByRole("textbox")).toBeNull();
  });

  it("does not open the article for editing", async () => {
    setup();
    render(<ConnectorGallery canEdit />);
    fireEvent.click(await seriesRow("JST GH"));
    fireEvent.click(cellOf("Артикул контакта"));
    fireEvent.keyDown(cellOf("Артикул контакта"), { key: "Enter" });
    expect(within(cellOf("Артикул контакта")).queryByRole("textbox")).toBeNull();
  });
});
