import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CuttingQueue, REFRESH_MS } from "./CuttingQueue";
import { rowsOf, type CuttingQueueMachine, type CuttingQueueRow } from "./cuttingQueueApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const BZW = { machine_id: "ОБ.095", machine_name: "BZW-50X" };

function row(overrides: Partial<CuttingQueueRow>): CuttingQueueRow {
  return {
    key: "k1", task_id: "t1", operation_id: "o1", task_number: "ПЗ-1", project: "708К.", item: "КБ3", group_number: null,
    wire: "RTK031", wire_name: "RTK 031", length_mm: "120", planned: "120", done: "0", left: "120",
    strip_a: "3/7,6", strip_a_mode: "full", strip_b: null, strip_b_mode: null, laser_mark: null, marking_mode: "ends",
    operator_names: [], warnings: [], blocked: null, setup_change: false, setup_text: "", setup_key: "s1",
    machine_id: BZW.machine_id, machine_name: BZW.machine_name, machines: [BZW], ...overrides,
  };
}

const MACHINES: CuttingQueueMachine[] = [
  { machine_id: "ОБ.095", machine_name: "BZW-50X", rows: 2, left: "170", projects: 2 },
  { machine_id: null, machine_name: "Станок не указан", rows: 1, left: "5", projects: 1 },
];
const ROWS = [
  row({ setup_change: true, setup_text: "Заправьте RTK 031 · без разделки B" }),
  row({ key: "k2", task_id: "t2", operation_id: "o2", project: "801К.1", item: "КБ1", left: "50", length_mm: "300",
    warnings: ["нет в ЭТК: зачистка Б"], blocked: "Группа РЗ-1 начата на карточке группы" }),
];
// «Все станки» order: the bucket row shares the first setup, so the server marks no setup change on it.
const NONE_ROW = row({ key: "n1", task_id: "t3", operation_id: "o3", project: "999К.", warnings: ["нет в ЭТК: станок"],
  machine_id: null, machine_name: "Станок не указан", machines: [], setup_text: "Заправьте RG 316", setup_key: "s2" });

function snapshot(rows: CuttingQueueRow[] = [...ROWS, NONE_ROW], machines = MACHINES, version = "v") {
  return { machine_id: "all", equipment_id: null, version, rows, machines };
}

const isSnapshot = (url: string) => url.includes("snapshot=true");
const snapshots = (mock: ReturnType<typeof vi.spyOn>) => mock.mock.calls.filter(([url]: unknown[]) => isSnapshot(String(url))).length;

function mockApi(onDone?: (init: RequestInit) => Response, body: () => unknown = () => snapshot()) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/cutting-queue/done")) return onDone ? onDone(init as RequestInit) : json({ notice: "Готово: 708К. · КБ3 · 120 шт", assigned: false, output_id: "x" });
    if (isSnapshot(url)) return json(body());
    return json({ detail: `unexpected ${url}` }, 500);
  });
}

it("renders rows in API order with the setup banner, warnings and the current row marked", async () => {
  mockApi();
  render(<CuttingQueue />);
  await screen.findByText("Заправьте RTK 031 · без разделки B");
  const lines = screen.getAllByRole("row").slice(1);
  expect(lines.map((line) => line.textContent)).toEqual([
    "Заправьте RTK 031 · без разделки B",
    expect.stringContaining("708К."),
    expect.stringContaining("801К.1"),
  ]);
  expect(lines[1]).toHaveAttribute("aria-current", "true");
  expect(lines[2]).not.toHaveAttribute("aria-current");
  // Причина блокировки — целиком отдельной строкой; пометки «нет в ЭТК» — одна строка с подсказкой.
  expect(within(lines[2]).getByText("Группа РЗ-1 начата на карточке группы")).toHaveClass("cq-blocked");
  const notes = within(lines[2]).getByTitle("нет в ЭТК: зачистка Б");
  expect(notes).toHaveTextContent("нет в ЭТК: зачистка Б");
  expect(notes).toHaveClass("cq-clip");
  expect(screen.getByText(/Сейчас/)).toBeInTheDocument();
});

it("reads one snapshot per refresh — never the machine list and a machine queue separately", async () => {
  const fetchMock = mockApi();
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(String(fetchMock.mock.calls[0][0])).toBe("/api/production/cutting-queue?machine_id=all&snapshot=true");
});

it("posts «Готово» with an Idempotency-Key, shows the notice and reloads", async () => {
  const fetchMock = mockApi();
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  expect(await screen.findByText("Готово: 708К. · КБ3 · 120 шт")).toBeInTheDocument();
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/cutting-queue/done"))!;
  const init = call[1] as RequestInit;
  expect(JSON.parse(String(init.body))).toEqual({ task_id: "t1", operation_id: "o1", quantity: 120 });
  expect(new Headers(init.headers).get("Idempotency-Key")).toBeTruthy();
  await waitFor(() => expect(snapshots(fetchMock)).toBeGreaterThan(1));
});

it("disables «Готово» of a blocked row with the reason as tooltip", async () => {
  mockApi();
  render(<CuttingQueue />);
  await screen.findByText("801К.1");
  const button = screen.getAllByRole("button", { name: "Готово" })[1];
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("title", "Группа РЗ-1 начата на карточке группы");
});

it("shows a server refusal as returned", async () => {
  mockApi(() => json({ detail: "Этап 1: уже сдано 10 из 10, осталось 0." }, 422));
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  expect(await screen.findByRole("alert")).toHaveTextContent("уже сдано 10 из 10");
});

it("selects the «Станок не указан» bucket from the same snapshot and marks its setup again", async () => {
  const fetchMock = mockApi();
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  fireEvent.change(screen.getByLabelText("Станок"), { target: { value: "none" } });
  await screen.findByText("999К.");
  expect(screen.getByText("нет в ЭТК: станок")).toBeInTheDocument();
  expect(screen.queryByText("708К.")).toBeNull();
  // First row of its own list: the setup banner is shown though the «Все станки» order had none before it.
  expect(screen.getByText("Заправьте RG 316")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("shows the blocked reason as text, skips blocked rows for the current mark and posts on behalf", async () => {
  const blocked = [row({ key: "b", blocked: "Группа РЗ-1 начата на карточке группы" }), row({ key: "c", task_id: "t3", operation_id: "o3", project: "801К.1" })];
  const fetchMock = mockApi((init) => { void init; return json({ notice: "Готово: 801К.1 · КБ3 · 120 шт", assigned: true, output_id: "x" }); },
    () => snapshot(blocked, MACHINES.slice(0, 1)));
  render(<CuttingQueue onBehalfOf="u-7" />);
  await screen.findByText("Группа РЗ-1 начата на карточке группы");
  const lines = screen.getAllByRole("row").slice(1).filter((line) => !line.classList.contains("cq-setup"));
  expect(lines[0]).not.toHaveAttribute("aria-current");
  expect(lines[1]).toHaveAttribute("aria-current", "true");
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[1]);
  await screen.findByText("Готово: 801К.1 · КБ3 · 120 шт");
  expect(String(fetchMock.mock.calls.find(([url]) => String(url).includes("/done"))![0])).toContain("on_behalf_of=u-7");
});

it("read-only mode has no «Готово» and says where it is submitted", async () => {
  mockApi();
  render(<CuttingQueue readOnly />);
  await screen.findByText("708К.");
  expect(screen.queryByRole("button", { name: "Готово" })).toBeNull();
  expect(screen.getByText("«Готово» сдаёт оператор в «Заданиях»")).toBeInTheDocument();
});

it("reloads the queue after a refused «Готово» and keeps the server text", async () => {
  const fetchMock = mockApi(() => json({ detail: "Этап 1: уже сдано 10 из 10, осталось 0." }, 422));
  render(<CuttingQueue />);
  await screen.findByText("708К.");
  const before = snapshots(fetchMock);
  fireEvent.click(screen.getAllByRole("button", { name: "Готово" })[0]);
  await waitFor(() => expect(snapshots(fetchMock)).toBeGreaterThan(before));
  expect(await screen.findByRole("alert")).toHaveTextContent("уже сдано 10 из 10");
});

it("hides itself when there is nothing to cut and reports it, but never hides a load error", async () => {
  const hasWork = vi.fn();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(snapshot([], [])));
  const view = render(<CuttingQueue hideWhenEmpty onHasWork={hasWork} />);
  await waitFor(() => expect(hasWork).toHaveBeenLastCalledWith(false));
  expect(view.container).toBeEmptyDOMElement();
  view.unmount();

  const failing = vi.fn();
  vi.restoreAllMocks();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Раздел закрыт" }, 403));
  render(<CuttingQueue hideWhenEmpty onHasWork={failing} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Раздел закрыт");
  expect(failing).not.toHaveBeenCalled();
});

it("reports the operations it can submit (not blocked ones, all machines) and clears them on unmount", async () => {
  mockApi();
  const listed = vi.fn();
  const view = render(<CuttingQueue onRows={listed} />);
  await screen.findByText("708К.");
  // o2 is blocked (group started on its card): its card stays in «Задания», where it is submitted.
  await waitFor(() => expect(listed).toHaveBeenLastCalledWith(new Set(["o1", "o3"])));
  view.unmount();
  expect(listed).toHaveBeenLastCalledWith(new Set());
});

it("keeps the last good rows with their age after a failed refresh, hides no cards, then recovers", async () => {
  let fail = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => fail ? json({ detail: "Нет связи" }, 503) : json(snapshot()));
  const listed = vi.fn();
  render(<CuttingQueue onRows={listed} />);
  await waitFor(() => expect(listed).toHaveBeenLastCalledWith(new Set(["o1", "o3"])));
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Нет связи — показано на \d\d:\d\d/);
  expect(screen.getByText("708К.")).toBeInTheDocument();
  expect(screen.getByText(/не обновилось, данные на/)).toBeInTheDocument();
  // onRows is called from a passive effect, which may flush just after the alert appears: wait for it.
  await waitFor(() => expect(listed).toHaveBeenLastCalledWith(new Set()));
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  await waitFor(() => expect(listed).toHaveBeenLastCalledWith(new Set(["o1", "o3"])));
});

it("lists «Все станки» first, shows every row with its machines", async () => {
  const all = { machine_id: "all", machine_name: "Все станки", rows: 3, left: "175", projects: 2 };
  mockApi(undefined, () => snapshot(undefined, [all, ...MACHINES]));
  render(<CuttingQueue />);
  await screen.findByText("999К.");
  expect(screen.getByLabelText("Станок")).toHaveValue("all");
  expect(screen.getAllByRole("option")[0]).toHaveTextContent("Все станки · 3 строк");
  expect(screen.getByRole("columnheader", { name: "Станок" })).toBeInTheDocument();
  expect(screen.getAllByText("BZW-50X", { selector: ".cq-machine .cq-clip" })).toHaveLength(2);
});

it("has a section title and keeps a long wire name on one line with a tooltip", async () => {
  const long = "Компания Крокс. Разработка, производство и продажа антенн";
  mockApi(undefined, () => snapshot([row({ wire_name: "RTK 031", wire_title: long, setup_change: true, setup_text: `Заправьте ${long}` })]));
  render(<CuttingQueue />);
  expect(await screen.findByRole("heading", { name: "Очередь резки" })).toBeInTheDocument();
  const clip = screen.getByText("RTK 031", { selector: ".cq-wire .cq-clip" });
  expect(clip).toHaveAttribute("title", long);
  expect(screen.getByTitle(`Заправьте ${long}`)).toBeInTheDocument();
});

it("with equipmentId reads the unit queue, has no machine picker and sends equipment_id on Done", async () => {
  const calls: { url: string; body: string }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, body: String(init?.body ?? "") });
    if (url.includes("/cutting-queue/done")) return json({ notice: "Готово: 708К. · КБ3 · 120 шт", assigned: false, output_id: "x" });
    return json({ machine_id: null, equipment_id: "u100", version: "v", rows: [row({})] });
  });
  render(<CuttingQueue equipmentId="u100" />);
  await screen.findByText("708К.");
  expect(screen.queryByLabelText("Станок")).toBeNull();
  expect(calls.some((call) => isSnapshot(call.url) || call.url.includes("/cutting-queue/machines"))).toBe(false);
  expect(calls[0].url).toContain("/api/production/cutting-queue?equipment_id=u100");
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(calls.some((call) => call.url.includes("/cutting-queue/done"))).toBe(true));
  const done = calls.find((call) => call.url.includes("/cutting-queue/done"))!;
  expect(JSON.parse(done.body)).toEqual({ task_id: "t1", operation_id: "o1", quantity: 120, equipment_id: "u100" });
});

it("with a supplied queue reads nothing itself and refreshes through the owner", async () => {
  const fetchMock = mockApi();
  const onRefresh = vi.fn(async () => undefined);
  render(<CuttingQueue equipmentId="u100" supplied={{ queue: { machine_id: null, version: "v", rows: [row({})] }, loadedAt: new Date() }}
    onRefresh={onRefresh} />);
  expect(screen.getByText("708К.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));
  await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(2));
  expect(fetchMock.mock.calls.filter(([url]) => !String(url).includes("/done"))).toEqual([]);
});

it("keeps the same rows and the typed quantity when the version is unchanged, updates on a new version", async () => {
  let version = "v";
  let left = "120";
  const fetchMock = mockApi(undefined, () => snapshot([row({ left, setup_change: true, setup_text: "Заправьте RTK 031" })], MACHINES.slice(0, 1), version));
  render(<CuttingQueue />);
  const input = await screen.findByLabelText("Сколько готово: 708К. КБ3");
  fireEvent.change(input, { target: { value: "7" } });
  const line = screen.getAllByRole("row")[2];
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  expect(screen.getAllByRole("row")[2]).toBe(line);
  expect(screen.getByLabelText("Сколько готово: 708К. КБ3")).toHaveValue(7);
  version = "w";
  left = "90";
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  expect(await screen.findByText("90")).toBeInTheDocument();
  expect(screen.getByLabelText("Сколько готово: 708К. КБ3")).toHaveValue(7);
});

describe("polling", () => {
  function hidden(value: boolean) {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
    document.dispatchEvent(new Event("visibilitychange"));
  }
  afterEach(() => { Object.defineProperty(document, "hidden", { configurable: true, get: () => false }); });

  it("never overlaps: a hung read times out, and the next poll starts only a minute after it ended", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>(() => undefined));
    render(<CuttingQueue />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await act(async () => { vi.advanceTimersByTime(REFRESH_MS - 1_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("alert")).toHaveTextContent("не ответила");
    fetchMock.mockImplementation(async () => json(snapshot()));
    await act(async () => { vi.advanceTimersByTime(REFRESH_MS); });
    await screen.findByText("708К.");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("pauses while the page is hidden and refreshes on return when the rows are older than a minute", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = mockApi();
    render(<CuttingQueue />);
    await screen.findByText("708К.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    hidden(true);
    await act(async () => { vi.advanceTimersByTime(REFRESH_MS * 5); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    hidden(false);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("aborts the read in flight on unmount and ignores its late answer", async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      signals.push((init as RequestInit).signal!);
      return new Promise<Response>(() => undefined);
    });
    const view = render(<CuttingQueue />);
    await waitFor(() => expect(signals).toHaveLength(1));
    view.unmount();
    expect(signals[0].aborted).toBe(true);
  });

  it("«Обновить» supersedes the read in flight: the late answer of the old one is not shown", async () => {
    const answers: ((response: Response) => void)[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise<Response>((resolve) => { answers.push(resolve); }));
    render(<CuttingQueue />);
    await waitFor(() => expect(answers).toHaveLength(1));
    // First answer arrives, then a manual refresh is pending while the first poll's replacement answers late.
    await act(async () => { answers[0](json(snapshot())); });
    await screen.findByText("708К.");
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(answers).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(answers).toHaveLength(3));
    await act(async () => { answers[2](json(snapshot([row({ project: "NEW" })], MACHINES.slice(0, 1), "w"))); });
    await screen.findByText("NEW");
    await act(async () => { answers[1](json(snapshot([row({ project: "OLD" })], MACHINES.slice(0, 1), "x"))); });
    expect(screen.queryByText("OLD")).toBeNull();
    expect(screen.getByText("NEW")).toBeInTheDocument();
  });
});

describe("rowsOf", () => {
  const pool = { machine_id: "model-uuid", machine_name: "ОБ.100 BZW-D2.0S · любой свободный (3)" };
  const rows = [
    row({ key: "a", setup_key: "s1", setup_change: true, machines: [BZW, pool] }),
    row({ key: "b", setup_key: "s2", setup_change: true, machines: [pool], machine_id: pool.machine_id, machine_name: pool.machine_name }),
    row({ key: "c", setup_key: "s2", setup_change: false, machines: [BZW] }),
    row({ key: "d", setup_key: "s3", setup_change: true, machines: [], machine_id: null, machine_name: "Станок не указан" }),
  ];

  it("keeps «Все станки» as given", () => { expect(rowsOf(rows, "all")).toBe(rows); });

  it("puts a row on every machine it names, with that machine as its own, and marks setups again", () => {
    expect(rowsOf(rows, "ОБ.095").map((item) => [item.key, item.machine_id, item.setup_change])).toEqual([
      ["a", "ОБ.095", true], ["c", "ОБ.095", true]]);
    expect(rowsOf(rows, pool.machine_id).map((item) => [item.key, item.machine_id, item.machine_name, item.setup_change])).toEqual([
      ["a", pool.machine_id, pool.machine_name, true], ["b", pool.machine_id, pool.machine_name, true]]);
    expect(rowsOf(rows, "none").map((item) => [item.key, item.machine_id, item.setup_change])).toEqual([["d", null, true]]);
    expect(rowsOf(rows, "ОБ.1")).toEqual([]);
  });

  it("returns the same row objects where nothing changes", () => {
    expect(rowsOf(rows, pool.machine_id)[1]).toBe(rows[1]);
  });
});
