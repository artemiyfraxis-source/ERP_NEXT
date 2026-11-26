import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { VnaBench, kitHint, kitPlane } from "./VnaBench";
import {
  vnaBenchApi,
  type KitPiece,
  type KitView,
  type VnaBenchSession,
  type VnaBenchState,
  type VnaBenchStep,
  type VnaBenchTarget,
} from "./vnaBenchApi";
import { kitMainAction } from "./vnaBenchWorkflow";

/* «Характеристика мер» SMA-MF06: формы — как `_kit_view` в backend/app/workflows/vna_bench_api.py. */

afterEach(() => { vi.restoreAllMocks(); });

const passed = (key: VnaBenchStep["key"]): VnaBenchStep => ({ key, status: "passed", result_text: null, refusal: null, finished_at: "2026-11-23T10:00:00+03:00" });
const calibrated = (["calibration", "confidence", "load_port1", "load_port2"] as const).map(passed);
const capture = (current = true) => ({ command_id: "c", captured_at: "2026-11-23T10:05:00+03:00", summary: "|Γ| 0,9900…0,9900", current, fixtures: [] });

function piece(kind: KitPiece["kind"], gender: KitPiece["gender"], extra: Partial<KitPiece> = {}): KitPiece {
  const low = gender === "female" && (kind === "OPEN" || kind === "SHORT");
  return {
    id: `${kind}-${gender}`, label: `${kind}-${gender === "female" ? "F" : "M"}`, kind, gender, gender2: kind === "THRU" ? gender : null,
    name: `${kind} ${gender}`, f_min_hz: 0, f_max_hz: low ? 3e9 : 6e9, version: 2, bands: low ? ["low"] : ["low", "high"],
    fixtures_needed: gender === "female" ? 0 : kind === "THRU" ? 2 : 1, in_band: true, state: "none", active: false,
    captures: {}, preview: null, plane: "Плоскости соединения вилок SMA тестовых кабелей (АКМ-калибровка)", can_save: false,
    latest_revision: null, ...extra,
  };
}

// The server's order: female pieces (direct) first, then male ones (through fixtures).
const ALL = (["female", "male"] as const).flatMap((gender) => (["OPEN", "SHORT", "LOAD", "THRU"] as const).map((kind) => piece(kind, gender)));

function view(extra: Partial<KitView> = {}): KitView {
  return {
    kit: { id: "kit", number: "СИ.004", name: "Набор SMA-MF06", version: 9, generation: 0, hash: null, manifest: null },
    band: "low",
    bands: [{ key: "low", label: "до 3 ГГц", start_mhz: 2, stop_mhz: 3000, points: 2999, step_mhz: 1 },
      { key: "high", label: "3–6 ГГц", start_mhz: 3000, stop_mhz: 6000, points: 3001, step_mhz: 1 }],
    torque: "0,56 Н·м", pieces: ALL, missing: [], fixtures: [],
    fixture_choices: [{ instrument_id: "f8", number: "СИ.008", name: "Переход SMA" }, { instrument_id: "f9", number: "СИ.009", name: "Переход SMA" }],
    can_activate: false, activation: null, ...extra,
  };
}

const session = (kit: KitView, extra: Partial<VnaBenchSession> = {}): VnaBenchSession => ({
  id: "s1", version: 5, status: "open", state: "ready", owned: true, error: null, started_at: "2026-11-23T09:00:00+03:00",
  operator: "Контролёр", steps: calibrated, command: null, items: [], checks: [], characterization: kit, ...extra,
});

const state = (kit: KitView | null, extra: Partial<VnaBenchState> = {}): VnaBenchState => ({
  device: {
    pc_name: "ОТК-121", agent_online: true, agent_version: "2.10.0", instrument_number: "СИ.002", instrument_id: "si-002",
    model: "Planar S50180", serial: "26030095", s2vna_ready: true, warmup_min: 95,
    temperatures: { vna_c: [31.2, 30.8], acm_c: 30.9 }, acm: { model: "ACM2520", serial: "26140008" }, refusal: null, read_at: null,
  },
  calibration: { valid: true, calibrated_at: "2026-11-23T09:30:00+03:00", valid_until: "2026-11-23T13:30:00+03:00", reason: null, temperatures: null },
  due: [], session: kit ? session(kit) : null, ...extra,
});

const done = (band: "low" | "high") => ALL.map((item) => item.bands.includes(band)
  ? { ...item, captures: { ...item.captures, [band]: capture() } } : item);

it("guides the pass: female pieces directly, then the fixture of the current calibration, then the male piece", () => {
  expect(kitMainAction(state(view()), null, [null, null])).toMatchObject({ kind: "kit-capture", label: "Снять OPEN-F",
    capture: { standard_id: "OPEN-female", fixtures: [] } });
  const females = view({ pieces: ALL.map((item) => item.gender === "female" ? { ...item, captures: { low: capture() } } : item) });
  expect(kitMainAction(state(females), null, [null, null]).reason).toBe("Выберите оснастку розетка–розетка для порта 1");
  expect(kitMainAction(state(females), null, ["f8", null])).toMatchObject({ label: "Снять оснастку СИ.008", capture: { fixture_id: "f8" } });
  const fixture = view({ ...females, fixtures: [{ instrument_id: "f8", number: "СИ.008", bands: { low: { captured_at: "", summary: "", current: true } } }] });
  expect(kitMainAction(state(fixture), null, ["f8", null])).toMatchObject({ label: "Снять OPEN-M",
    capture: { standard_id: "OPEN-male", fixtures: [{ instrument_id: "f8", reversed: false }] } });
  // «Оснастка развёрнута» goes to the server as `reversed`: the stored fixture curve is then used the other way round.
  expect(kitMainAction(state(fixture), null, ["f8", null], [true, false])).toMatchObject({
    capture: { standard_id: "OPEN-male", fixtures: [{ instrument_id: "f8", reversed: true }] } });
  // The same F–F fixture on both ports is refused before the server sees it.
  const thru = view({ ...fixture, pieces: fixture.pieces.map((item) => item.kind === "THRU" && item.gender === "male" ? item : { ...item, captures: { low: capture() } }) });
  expect(kitMainAction(state(thru), null, ["f8", "f8"]).reason).toBe("На порты 1 и 2 нужны разные оснастки");
});

it("moves to the second pass, saves, activates and stops when the kit is active", () => {
  expect(kitMainAction(state(view({ pieces: done("low") })), null, ["f8", "f9"])).toMatchObject({ kind: "kit-band", band: "high",
    label: "Перейти к проходу «3–6 ГГц»" });
  const high = view({ band: "high", pieces: done("low").map((item) => ({ ...item, in_band: item.bands.includes("high") })) });
  // A new pass needs its own calibration first.
  const uncalibrated = state(high, { calibration: { valid: false, calibrated_at: null, valid_until: null, reason: null, temperatures: null } });
  uncalibrated.session = session(high, { steps: [{ ...passed("calibration"), status: "pending", finished_at: null }] });
  expect(kitMainAction(uncalibrated, null, ["f8", "f9"])).toMatchObject({ kind: "step", step: "calibration" });
  const saved = ALL.map((item) => ({ ...item, captures: { low: capture(), high: capture() }, can_save: item.id === "LOAD-male" }));
  expect(kitMainAction(state(view({ band: "high", pieces: saved })), null, ["f8", "f9"])).toMatchObject({ kind: "kit-save", pieceId: "LOAD-male" });
  const activation = { expected_kit_version: 9, revisions: {}, expected_standard_versions: {} };
  const validated = saved.map((item) => ({ ...item, can_save: false, state: "validated" as const }));
  expect(kitMainAction(state(view({ band: "high", pieces: validated, can_activate: true, activation })), null, [null, null]).kind).toBe("kit-activate");
  const active = validated.map((item) => ({ ...item, active: true, state: "active" as const }));
  expect(kitMainAction(state(view({ band: "high", pieces: active, kit: { ...view().kit, generation: 1 } })), null, [null, null]))
    .toMatchObject({ kind: "wait", label: "Набор действует" });
  expect(kitMainAction(state(view({ missing: [{ kind: "OPEN", gender: "male", name: "ХХ (вилка)", f_max_hz: 6e9 }] })), null, [null, null]))
    .toMatchObject({ kind: "wait", label: "Внесите меры набора" });
});

it("explains the connection and the reference plane with the chosen fixtures and the SMA torque", () => {
  const male = piece("THRU", "male");
  expect(kitHint(male, ["СИ.008", "СИ.009"], "0,56 Н·м")).toBe("Оснастка СИ.008: сторона 1 (маркировка) — к кабелю порта 1; "
    + "оснастка СИ.009: сторона 2 — к кабелю порта 2; THRU-M между ними (сторона 1 меры — к порту 1). SMA затягивайте ключом 0,56 Н·м.");
  expect(kitHint(male, ["СИ.008", "СИ.009"], "0,56 Н·м", [true, true])).toContain(
    "СИ.008: сторона 2 — к кабелю порта 1; оснастка СИ.009: сторона 1 (маркировка) — к кабелю порта 2");
  expect(kitHint(piece("OPEN", "male"), ["СИ.008"], "0,56 Н·м")).toContain("СИ.008: сторона 1 (маркировка) — к кабелю порта 1, OPEN-M — на её сторону 2");
  expect(kitHint(piece("OPEN", "male"), ["СИ.008"], "0,56 Н·м", [true, false])).toContain("СИ.008: сторона 2 — к кабелю порта 1, OPEN-M — на её сторону 1 (маркировка)");
  expect(kitPlane(piece("OPEN", "male"), ["СИ.008"])).toBe("внешняя розетка оснастки СИ.008 на кабеле порта 1, оснастка исключена");
  expect(kitHint(piece("OPEN", "female"), [], "0,56 Н·м")).toContain("прямо на конец кабеля порта 1");
});

it("scanning the kit opens a characterization session; the list shows states and the main action captures", async () => {
  const kitTarget: VnaBenchTarget = { kind: "instrument", instrument_id: "kit", number: "СИ.004", name: "Набор SMA-MF06", serial_number: null, kit: "SMA-MF06" };
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state(null));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [kitTarget] });
  const plan = vi.spyOn(vnaBenchApi, "plan");
  const pieces = ALL.map((item) => item.id === "OPEN-female" ? { ...item, state: "validated" as const, captures: { low: capture() },
    latest_revision: { id: "r1", revision: 1, sha256: "a".repeat(64), valid: true, reason: null, max_residual: 1e-7, limit: 0.001,
      join_step: 0.0023, join_limit: 0.01, f_min_hz: 2e6, f_max_hz: 3e9, points: 2999, created_at: "2026-11-23T10:10:00+03:00", created_by: "Контролёр", download: "/file" },
    preview: { band: "low" as const, points: [[2, 0.99, -0.1], [1500, 0.99, -40], [3000, 0.99, -80]] as [number, number, number][] } } : item);
  const opened = state(view({ pieces }));
  const start = vi.spyOn(vnaBenchApi, "startSession").mockResolvedValue(opened);
  render(<VnaBench canEdit />);
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: "СИ.004" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ characterization: { kit_id: "kit" } }));
  expect(plan).not.toHaveBeenCalled();
  const panel = await screen.findByRole("region", { name: "Характеристика мер" });
  const rows = within(panel).getAllByRole("row");
  expect(rows).toHaveLength(9);
  expect(within(rows[1]).getByText("проверено")).toBeTruthy();
  expect(within(rows[2]).getByText("нет данных")).toBeTruthy();
  // The first piece without a capture in this pass is next: SHORT-F, directly on the port 1 cable end.
  const capture2 = vi.spyOn(vnaBenchApi, "kitCapture").mockResolvedValue(opened);
  const main = screen.getByRole("button", { name: "Снять SHORT-F" });
  fireEvent.click(main);
  await waitFor(() => expect(capture2).toHaveBeenCalledWith("s1", 5, { standard_id: "SHORT-female", fixtures: [] }));
  // Choosing a piece shows its revision evidence and makes it the main action.
  fireEvent.click(within(rows[1]).getByRole("button", { name: "OPEN female" }));
  expect(within(panel).getByText(/невязка середин/)).toBeTruthy();
  expect(within(panel).getByText(/скачок на стыке 3000 МГц 0,0023 ≤ 0,01/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Снять OPEN-F" })).toBeTruthy();
});

it("«Оснастка развёрнута» turns the hint around and goes with the capture", async () => {
  const fixtureCaptured = { captured_at: "2026-11-23T10:00:00+03:00", summary: "", current: true };
  const pieces = ALL.map((item) => item.gender === "female" ? { ...item, captures: { low: capture() } } : item);
  const opened = state(view({ pieces, fixtures: [{ instrument_id: "f8", number: "СИ.008", bands: { low: fixtureCaptured } }] }));
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(opened);
  render(<VnaBench canEdit />);
  const panel = await screen.findByRole("region", { name: "Характеристика мер" });
  fireEvent.change(within(panel).getByLabelText(/Оснастка розетка–розетка, порт 1/), { target: { value: "f8" } });
  expect(within(panel).getByText(/СИ\.008: сторона 1 \(маркировка\) — к кабелю порта 1, OPEN-M/)).toBeTruthy();
  fireEvent.click(within(panel).getByLabelText(/Оснастка развёрнута: к кабелю порта 1 — сторона 2/));
  expect(within(panel).getByText(/СИ\.008: сторона 2 — к кабелю порта 1, OPEN-M — на её сторону 1/)).toBeTruthy();
  const captured = vi.spyOn(vnaBenchApi, "kitCapture").mockResolvedValue(opened);
  fireEvent.click(screen.getByRole("button", { name: "Снять OPEN-M" }));
  await waitFor(() => expect(captured).toHaveBeenCalledWith("s1", 5, { standard_id: "OPEN-male", fixtures: [{ instrument_id: "f8", reversed: true }] }));
});
