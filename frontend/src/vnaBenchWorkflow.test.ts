import { describe, expect, it } from "vitest";
import { benchMainAction } from "./vnaBenchWorkflow";
import type { VnaBenchItem, VnaBenchPlan, VnaBenchState, VnaBenchStep, VnaBenchStepKey, VnaBenchTarget } from "./vnaBenchApi";

const target: VnaBenchTarget = { kind: "instrument", instrument_id: "si35", number: "СИ.035", name: "Переход", serial_number: null };
const plan: VnaBenchPlan = { screen_name: "Переход", band_mhz: [10, 18000], points: 1601, ifbw_hz: 1000, power_dbm: 0,
  traces: [], norm_vswr: 1.06, norm_source: "Паспорт", uncertainty: 0.012, connection_hint: null, refusal: null };
function state(): VnaBenchState {
  return { device: { pc_name: "ОТК", agent_online: true, agent_version: "1", instrument_number: "СИ.002", instrument_id: "si2",
    model: "Planar", serial: "1", s2vna_ready: true, warmup_min: 50, temperatures: null, acm: null, refusal: null, read_at: null },
    calibration: { valid: true, calibrated_at: null, valid_until: null, reason: null, temperatures: null }, due: [],
    session: { id: "s1", version: 1, status: "open", operator: "ОТК", started_at: "", command: null, checks: [], items: [],
      steps: [step("calibration")] } };
}
function step(key: VnaBenchStepKey, status: VnaBenchStep["status"] = "passed", refusal: VnaBenchStep["refusal"] = null): VnaBenchStep {
  return { key, status, result_text: null, refusal, finished_at: null };
}
const OLD_STEPS: VnaBenchStepKey[] = ["calibration", "confidence", "load_port1", "load_port2"];
function item(extra: Partial<VnaBenchItem> = {}): VnaBenchItem {
  return { id: "i1", version: 1, target, plan, connections_total: 3, connections: [], trace: [], verdict: null,
    refusal: null, recorded_at: null, recorded_to: null, ...extra };
}

describe("the single workplace action", () => {
  it("opens a session, calibrates the ACM and measures right after it — no confidence test, no LOAD (26.11.2026)", () => {
    const value = state(); value.session = null;
    expect(benchMainAction(value, target, null).kind).toBe("open");
    value.session = state().session!;
    value.session.steps = [step("calibration", "pending")];
    expect(benchMainAction(value, target, null)).toEqual({ kind: "step", step: "calibration", label: "Калибровать АКМ" });
    value.session.steps = [];
    expect(benchMainAction(value, target, null)).toMatchObject({ kind: "step", step: "calibration" });
    value.session.steps = [step("calibration")];
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "measure", label: "Замерить" });
  });

  it("an old session waiting for its LOAD or confidence test calibrates anew; a finished one measures", () => {
    const value = state();
    value.session!.steps = OLD_STEPS.map((key, index) => step(key, index < 2 ? "passed" : "pending"));
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "step", step: "calibration", label: "Откалибровать заново" });
    value.session!.steps = OLD_STEPS.map((key) => step(key));
    expect(benchMainAction(value, target, plan)).toEqual({ kind: "measure", label: "Замерить" });
  });

  it("waits for a running calibration and running measurements", () => {
    const value = state(); value.session!.steps = [step("calibration", "running")];
    expect(benchMainAction(value, target, plan).kind).toBe("wait");
    value.session = state().session!;
    value.session.items = [item({ connections: [{ no: 1, status: "running", max_vswr: null, max_mhz: null, measured_at: null }] })];
    expect(benchMainAction(value, target, plan).kind).toBe("wait");
  });

  it("recalibrates after a failed or expired calibration and retries other refusals", () => {
    const value = state(); value.session!.steps = [step("calibration", "failed")];
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "calibration", label: "Откалибровать заново" });
    value.session!.steps = [step("calibration", "refused", { code: "temperature_unknown", message: "Температура не прочитана" })];
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "calibration", label: "Откалибровать заново" });
    value.session!.steps = [step("calibration", "refused", { code: "busy", message: "Занят" })];
    expect(benchMainAction(value, target, plan)).toMatchObject({ step: "calibration", label: "Повторить" });
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
