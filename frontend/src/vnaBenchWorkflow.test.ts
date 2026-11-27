import { describe, expect, it } from "vitest";
import { BENCH_STEPS, benchMainAction } from "./vnaBenchWorkflow";
import type { VnaBenchItem, VnaBenchPlan, VnaBenchState, VnaBenchTarget } from "./vnaBenchApi";

const target: VnaBenchTarget = { kind: "instrument", instrument_id: "si35", number: "СИ.035", name: "Переход", serial_number: null };
const plan: VnaBenchPlan = { screen_name: "Переход", band_mhz: [10, 18000], points: 1601, ifbw_hz: 1000, power_dbm: 0,
  traces: [], norm_vswr: 1.06, norm_source: "Паспорт", uncertainty: 0.012, connection_hint: null, refusal: null };
function state(): VnaBenchState {
  return { device: { pc_name: "ОТК", agent_online: true, agent_version: "1", instrument_number: "СИ.002", instrument_id: "si2",
    model: "Planar", serial: "1", s2vna_ready: true, warmup_min: 50, temperatures: null, acm: null, refusal: null, read_at: null },
    calibration: { valid: true, calibrated_at: null, valid_until: null, reason: null, temperatures: null }, due: [],
    session: { id: "s1", version: 1, status: "open", operator: "ОТК", started_at: "", command: null, checks: [], items: [],
      steps: BENCH_STEPS.map((key) => ({ key, status: "passed", result_text: null, refusal: null, finished_at: null })) } };
}
function item(extra: Partial<VnaBenchItem> = {}): VnaBenchItem {
  return { id: "i1", version: 1, target, plan, connections_total: 3, connections: [], trace: [], verdict: null,
    refusal: null, recorded_at: null, recorded_to: null, ...extra };
}

describe("the single workplace action", () => {
  it("opens a session before calibration, then advances through both load ports", () => {
    const value = state(); value.session = null;
    expect(benchMainAction(value, target, null).kind).toBe("open");
    value.session = state().session!;
    value.session.steps = [];
    expect(benchMainAction(value, target, null)).toMatchObject({ kind: "step", step: "calibration" });
    value.session.steps = state().session!.steps.slice(0, 2);
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "load_port1" });
    value.session.steps = state().session!.steps.slice(0, 3);
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "load_port2" });
  });

  it("waits for automatic confidence testing and running measurements", () => {
    const value = state(); value.session!.steps = value.session!.steps.slice(0, 1);
    expect(benchMainAction(value, target, plan).kind).toBe("wait");
    value.session = state().session!;
    value.session.items = [item({ connections: [{ no: 1, status: "running", max_vswr: null, max_mhz: null, measured_at: null }] })];
    expect(benchMainAction(value, target, plan).kind).toBe("wait");
  });

  it("recalibrates after a failed or expired calibration and retries other refusals", () => {
    const value = state(); value.session!.steps[2].status = "failed";
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "calibration" });
    value.session!.steps[2] = { ...value.session!.steps[2], status: "refused", refusal: { code: "busy", message: "Занят" } };
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "load_port1", label: "Повторить" });
    value.session!.steps[1] = { ...value.session!.steps[1], status: "refused", refusal: { code: "busy", message: "Занят" } };
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "confidence", label: "Повторить" });
    value.session = state().session!; value.calibration.valid = false;
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "calibration" });
  });

  it("measures only after target selection and a usable plan", () => {
    const value = state();
    expect(benchMainAction(value, null, null).reason).toBeTruthy();
    expect(benchMainAction(value, target, null).reason).toBeTruthy();
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "measure", label: "Замерить" });
    expect(benchMainAction(value, target, { ...plan, refusal: { code: "no_norm", message: "Нет нормы" } }).reason).toBeTruthy();
  });

  it("opens a different band before attempting a measurement", () => {
    expect(benchMainAction(state(), target, { ...plan, refusal: { code: "other_calibration", message: "Другая полоса" } }).kind).toBe("open");
  });

  it("repeats a border connection, then saves a final instrument result once", () => {
    const value = state();
    const measured = item({ connections: [{ no: 2, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }] });
    value.session!.items = [measured];
    expect(benchMainAction(value, target, plan)).toMatchObject({ kind: "connection", label: "Измерить: 2 из 3" });
    measured.verdict = { code: "pass", final: true, worst_vswr: 1.04, worst_mhz: 18000, norm_vswr: 1.06, uncertainty: 0.012, reason: null };
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "record", label: "Сохранить" });
    expect(benchMainAction(value, target, null)).toEqual({ kind: "record", label: "Сохранить" });
    expect(benchMainAction(value, target, { ...plan, refusal: { code: "no_norm", message: "Нет нормы" } })).toEqual({ kind: "record", label: "Сохранить" });
    measured.recorded_at = "2026-11-23T10:00:00Z";
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "measure", label: "Измерить заново" });
  });

  it("keeps assembly results automatically recorded and never offers manual saving", () => {
    const value = state();
    const assembly: VnaBenchTarget = { kind: "assembly", task_id: "t1", task_number: "ПЗ-1", position: 1, name: "Кабель", item: "КБ1", serial: null };
    value.session!.items = [item({ target: assembly, verdict: { code: "pass", final: true, worst_vswr: 1.04, worst_mhz: 6000, norm_vswr: 1.2, uncertainty: null, reason: null } })];
    expect(benchMainAction(value, assembly, plan).label).toBe("Измерить заново");
    value.session = null;
    expect(benchMainAction(value, assembly, null).label).toBe("Открыть сеанс для КБ");
  });
});
