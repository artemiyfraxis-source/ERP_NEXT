import { afterEach, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { InstrumentVnaChecks } from "./InstrumentVnaChecks";
import type { VnaChecksSnapshot } from "./api";

const record = {
  id: "c1", kind: "session", kind_label: "Проверка калибровки", passed: false, performed_at: "2026-11-18T09:00:00Z",
  recorded_at: "2026-11-18T09:01:00Z", source: "vna-check-1", operator: "otk", actor: "Контролёр", calibration_id: "cal-1",
  acm_serial: "26140008", profile: "ОТК — переход", vna_temperature_c: 31.2, acm_temperature_c: 29, confidence_passed: true,
  confidence_tx_dev_db: 0.031, confidence_rx_dev_db: null, load_p1_vswr: 1.071, load_p2_vswr: 1.021, standard_inventory: null,
  reference: "", vswr_dev: null, vswr_limit: null, s21_dev_db: null, s21_limit_db: null, kit_ratio: null, note: "",
} as const;
const snapshot: VnaChecksSnapshot = {
  instrument_id: "vna", applicable: true, records: [record],
  due: [
    { kind: "control_standard", label: "Контрольная мера (еженедельно)", last_at: null, last_passed: null, due_at: null, status: "never", status_label: "Контроль просрочен — ещё не выполнялся" },
    { kind: "kit_standards", label: "Меры 6650F27 (ежемесячно)", last_at: "2026-11-10T09:00:00Z", last_passed: true, due_at: "2026-12-10T09:00:00Z", status: "ok", status_label: "В срок" },
  ],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => vi.restoreAllMocks());

it("shows overdue control, due date and one value per journal column", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(snapshot));
  render(<InstrumentVnaChecks instrumentId="vna" refreshKey={0} />);
  expect(await screen.findByText("Контроль просрочен — ещё не выполнялся")).toHaveClass("tone-warn");
  expect(screen.getByText(/^В срок, до /)).toHaveClass("tone-ok");
  const table = screen.getByRole("region", { name: "Записи журнала проверок ВАЦ" });
  expect(within(table).getAllByText("Не годен").some((node) => node.classList.contains("tone-danger"))).toBe(true);
  expect(within(table).getByText("1,071")).toBeInTheDocument();
  expect(within(table).getByRole("cell", { name: "Калибровка АКМ" })).toBeInTheDocument();
});

it("hides the journal on a card that is not a VNA and shows a Russian error on failure", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ ...snapshot, applicable: false, records: [] }));
  const { container, unmount } = render(<InstrumentVnaChecks instrumentId="adapter" refreshKey={0} />);
  await vi.waitFor(() => expect(container.querySelector(".instrument-vna-checks")).toBeNull());
  unmount();
  fetch.mockImplementation(async () => json({ detail: "СИ не найдено" }, 404));
  render(<InstrumentVnaChecks instrumentId="gone" refreshKey={0} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("СИ не найдено");
});
