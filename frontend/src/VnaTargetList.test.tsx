import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { VnaTargetList } from "./VnaTargetList";
import { vnaBenchApi, type VnaBenchInstrumentTarget } from "./vnaBenchApi";

afterEach(() => vi.restoreAllMocks());
const row = (number: string, last: string | null = null): VnaBenchInstrumentTarget => ({
  kind: "instrument", instrument_id: number, number, name: `Переход ${number}`, serial_number: "N8B141",
  interfaces_text: "SMA розетка – SMA вилка", last_measured_at: last,
});
const props = { sessionId: "session-1", query: "", visible: true, onPick: vi.fn() };

it("sorts never measured first, then actual measurement time, then natural inventory number", async () => {
  vi.spyOn(vnaBenchApi, "targets").mockResolvedValue({ targets: [row("СИ.011", "2026-11-01"), row("СИ.010"),
    row("СИ.012", "2026-10-01"), row("СИ.002"), row("СИ.003", "2026-10-01")] });
  render(<VnaTargetList {...props} />);
  await screen.findByText("СИ.011");
  const numbers = screen.getAllByRole("row").slice(1).map((entry) => within(entry).getAllByRole("cell")[0].textContent);
  expect(numbers).toEqual(["СИ.002", "СИ.010", "СИ.003", "СИ.012", "СИ.011"]);
  expect(screen.getAllByText("не мерили")).toHaveLength(2);
  expect(screen.getAllByText("01.10.2026")).toHaveLength(2);
});

it("filters loaded rows without another request, even after selection", async () => {
  const load = vi.spyOn(vnaBenchApi, "targets").mockResolvedValue({ targets: [row("СИ.035"), row("СИ.036")] });
  const { rerender } = render(<VnaTargetList {...props} />);
  await screen.findByText("СИ.035");
  rerender(<VnaTargetList {...props} query="036 SMA" />);
  expect(screen.queryByText("СИ.035")).not.toBeInTheDocument();
  expect(screen.getByText("СИ.036")).toBeInTheDocument();
  rerender(<VnaTargetList {...props} visible={false} />);
  rerender(<VnaTargetList {...props} query="нет такого" />);
  expect(screen.getByText("Нет СИ по запросу — измените поиск")).toBeInTheDocument();
  expect(screen.queryByText(/заведите СИ/)).not.toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(1);
});

it("picks the lookup target on a whole-row click and keyboard Enter", async () => {
  const entry = row("СИ.035");
  vi.spyOn(vnaBenchApi, "targets").mockResolvedValue({ targets: [entry] });
  const pick = vi.fn();
  render(<VnaTargetList {...props} onPick={pick} />);
  const line = (await screen.findByText("СИ.035")).closest("tr")!;
  fireEvent.click(within(line).getByText(entry.interfaces_text));
  fireEvent.keyDown(line, { key: "Enter" });
  expect(pick.mock.calls).toEqual([[{ kind: "instrument", instrument_id: entry.instrument_id, number: entry.number,
    name: entry.name, serial_number: entry.serial_number }], [{ kind: "instrument", instrument_id: entry.instrument_id,
    number: entry.number, name: entry.name, serial_number: entry.serial_number }]]);
  expect(line).toHaveAttribute("tabindex", "0");
});

it("shows loading then the actionable registry empty state", async () => {
  let finish!: (value: { targets: VnaBenchInstrumentTarget[] }) => void;
  vi.spyOn(vnaBenchApi, "targets").mockReturnValue(new Promise((done) => { finish = done; }));
  render(<VnaTargetList {...props} />);
  expect(screen.getByRole("status")).toHaveTextContent("Загрузка…");
  finish({ targets: [] });
  expect(await screen.findByText("Нет СИ с ВЧ-разъёмами — заведите СИ в реестре")).toBeInTheDocument();
});

it("reports failureText errors and retries without claiming the registry is empty", async () => {
  const load = vi.spyOn(vnaBenchApi, "targets").mockRejectedValueOnce(new Error("Нет ответа"))
    .mockResolvedValue({ targets: [row("СИ.035")] });
  render(<VnaTargetList {...props} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет ответа");
  expect(screen.queryByText(/заведите СИ/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await screen.findByText("СИ.035");
  expect(load).toHaveBeenCalledTimes(2);
});

it("reloads on session change and ignores a response from the previous session", async () => {
  let old!: (value: { targets: VnaBenchInstrumentTarget[] }) => void;
  const load = vi.spyOn(vnaBenchApi, "targets").mockReturnValueOnce(new Promise((done) => { old = done; }))
    .mockResolvedValue({ targets: [row("СИ.036")] });
  const { rerender } = render(<VnaTargetList {...props} />);
  rerender(<VnaTargetList {...props} sessionId="session-2" />);
  await screen.findByText("СИ.036");
  old({ targets: [row("СИ.035")] });
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("СИ.035")).not.toBeInTheDocument();
});
