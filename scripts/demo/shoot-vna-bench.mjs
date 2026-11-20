#!/usr/bin/env node
// Shots of «ОТК → ВАЦ — рабочее место» over a MOCKED /api/production/vna-bench/* in the server's shapes (PR #973,
// workflows/vna_bench_api.py) — for screens the local stand cannot reach (a КБ needs a task with ETK).
// Everything else comes from the running ERP (preview or demo). Without the mock the tab stays hidden — shot «off».
//   ERP_URL=http://127.0.0.1:8091 SHOTS_DIR=<dir> node scripts/demo/shoot-vna-bench.mjs
import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require("playwright"); } catch { /* global install */ }
  return require(path.join(execSync("npm root -g").toString().trim(), "playwright"));
}
const { chromium } = loadPlaywright();
const BASE = process.env.ERP_URL || "http://127.0.0.1:8021";
const OUT = process.env.SHOTS_DIR || "/tmp/erp-shots";
mkdirSync(OUT, { recursive: true });

const at = (time) => `2026-11-19T${time}:00+03:00`;
const step = (key, status, result_text = null, refusal = null) => ({ key, status, result_text, refusal, finished_at: status === "pending" ? null : at("14:20") });
const trace = Array.from({ length: 241 }, (_, index) => {
  const mhz = 10 + (index * (18000 - 10)) / 240;
  const vswr = 1.012 + 0.012 * (mhz / 18000) + 0.006 * Math.abs(Math.sin(mhz / 700)) + (Math.abs(mhz - 17200) < 200 ? 0.035 * (1 - Math.abs(mhz - 17200) / 200) : 0);
  return { mhz: Math.round(mhz * 10) / 10, vswr: Math.round(vswr * 10000) / 10000 };
});
trace.push({ mhz: 17200, vswr: 1.065 });
trace.sort((a, b) => a.mhz - b.mhz);
const kb = { kind: "assembly", task_id: "t1", task_number: "ПЗ-0001", position: 3, serial: null, name: "Кабель коаксиальный КБ1", item: "591К.3.КБ1", operation_id: "op1" };
const target = { kind: "instrument", instrument_id: "si-035", number: "СИ.035", name: "Переход ADP1A 3,5 мм (розетка) — 3,5 мм (вилка)", serial_number: "N8B141" };
const plan = {
  screen_name: "ОТК — переход", band_mhz: [10, 18000], points: 1601, ifbw_hz: 1000, power_dbm: 0,
  traces: [{ label: "КСВН S11", scale: "1,00–1,10" }, { label: "КСВН S22", scale: "1,00–1,10" }, { label: "Потери S21", scale: "0…−0,5 дБ" }, { label: "Потери S12", scale: "0…−0,5 дБ" }],
  norm_vswr: 1.06, norm_source: "ADP1A, данные изготовителя, до 20 ГГц", uncertainty: 0.012,
  connection_hint: "Сторона 1 перехода — к кабелю порта 1, сторона 2 — к кабелю порта 2. Затяните тарированным ключом.", refusal: null,
};
const device = {
  pc_name: "ОТК-121", agent_online: true, agent_version: "2.10.0", instrument_number: "СИ.002", instrument_id: "si-002", model: "Planar S50180",
  serial: "26030095", s2vna_ready: true, warmup_min: 95, temperatures: { vna_c: [31.2, 30.8], acm_c: 30.9 }, acm: { model: "АКМ", serial: "26140008" },
  refusal: null, read_at: at("15:02"),
};
const due = [
  { kind: "weekly", due_at: at("00:00").replace("11-19", "11-23"), last_at: at("10:00").replace("11-19", "11-16"), status: "ok" },
  { kind: "monthly", due_at: at("00:00").replace("11-19", "12-16"), last_at: at("10:00").replace("11-19", "11-16"), status: "ok" },
];
const validCal = { valid: true, calibrated_at: at("14:40"), valid_until: at("18:40"), reason: null, temperatures: { vna_c: [31.0, 30.7], acm_c: 30.8 } };
const allPassed = [
  step("calibration", "passed", "двухпортовая, АКМ зав. № 26140008, 1601 точка, 10 МГц – 18 ГГц"),
  step("confidence", "passed", "передача 0,031 дБ ≤ 0,1; отражение 0,12 дБ ≤ 0,4 (до 10 ГГц), 0,21 дБ ≤ 0,5 (выше)"),
  step("load_port1", "passed", "КСВН 1,032 ≤ 1,06 на 17 940 МГц"),
  step("load_port2", "passed", "КСВН 1,028 ≤ 1,06 на 16 120 МГц"),
];
const session = (extra) => ({ id: "s1", version: 5, status: "open", state: "ready", owned: true, error: null, started_at: at("14:30"), operator: "Артемий", steps: allPassed, command: null, items: [], checks: [], ...extra });

// «Экраны» (S5b): шесть заводских экранов, как их сеет 0207_vna_screens, и один свой.
const screens = [{"id": "sc-adapter", "name": "ОТК — переход", "purpose": "adapter", "split": 8, "traces": [{"param": "S11", "format": "SWR", "scale": "norm", "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S12", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "SWR", "scale": "norm", "limit": "norm", "fixed": null, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "замер переходника", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-assembly", "name": "ОТК — кабельная сборка", "purpose": "assembly", "split": 5, "traces": [{"param": "S11", "format": "SWR", "scale": {"pdiv": 0.05, "rlev": 1.0, "rpos": 0}, "limit": "etk", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.3, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "SWR", "scale": {"pdiv": 0.05, "rlev": 1.0, "rpos": 0}, "limit": "etk", "fixed": null, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "сборка с двух сторон, полоса и норма по ЭТК", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-one_port", "name": "ОТК — один порт", "purpose": "one_port", "split": 3, "traces": [{"param": "S11", "format": "SWR", "scale": {"pdiv": 0.05, "rlev": 1.0, "rpos": 0}, "limit": "etk", "fixed": null, "hidden": false, "memory": false}, {"param": "S11", "format": "MLOG", "scale": {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "сборка с одной стороны, на другом конце нагрузка", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-load", "name": "Проверка калибровки (нагрузка)", "purpose": "load", "split": 3, "traces": [{"param": "S11", "format": "SWR", "scale": {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}, "limit": "fixed", "fixed": {"kind": "upper", "value": 1.06}, "hidden": false, "memory": false}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": true, "memory": false}, {"param": "S12", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": true, "memory": false}, {"param": "S22", "format": "SWR", "scale": {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}, "limit": "fixed", "fixed": {"kind": "upper", "value": 1.06}, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "нагрузка 6650F27 на каждом порту", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-flex", "name": "Проверка кабелей (шевеление)", "purpose": "flex", "split": 8, "traces": [{"param": "S11", "format": "SWR", "scale": {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}, "limit": "none", "fixed": null, "hidden": false, "memory": true}, {"param": "S22", "format": "SWR", "scale": {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}, "limit": "none", "fixed": null, "hidden": false, "memory": true}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.03, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": true}, {"param": "S21", "format": "PHAS", "scale": {"pdiv": 36.0, "rlev": 0.0, "rpos": 5}, "limit": "none", "fixed": null, "hidden": false, "memory": true}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "запомнить графики, изогнуть кабель, сравнить", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-engineer", "name": "Подробно (инженер)", "purpose": "engineer", "split": 15, "traces": [{"param": "S11", "format": "SWR", "scale": "norm", "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S12", "format": "MLOG", "scale": {"pdiv": 0.05, "rlev": 0.0, "rpos": 10}, "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "SWR", "scale": "norm", "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S11", "format": "SMIT", "scale": {"pdiv": 1.0, "rlev": null, "rpos": null}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "PHAS", "scale": {"pdiv": 36.0, "rlev": 0.0, "rpos": 5}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "GDEL", "scale": "auto", "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "SMIT", "scale": {"pdiv": 1.0, "rlev": null, "rpos": null}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S11", "format": "MLOG", "scale": {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S21", "format": "MLOG", "scale": {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S12", "format": "MLOG", "scale": {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "MLOG", "scale": {"pdiv": 5.0, "rlev": 0.0, "rpos": 10}, "limit": "none", "fixed": null, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "все графики: КСВН, потери, Вольперт-Смит, фаза, ГВЗ, возвратные потери", "is_default": true, "active": true, "version": 1, "updated_by": null, "updated_at": "2026-11-20T00:00:00+00:00"}, {"id": "sc-wide", "name": "КСВН крупно (просмотр)", "purpose": "engineer", "split": 3, "traces": [{"param": "S11", "format": "SWR", "scale": "norm", "limit": "norm", "fixed": null, "hidden": false, "memory": false}, {"param": "S22", "format": "SWR", "scale": {"pdiv": 0.01, "rlev": 1.0, "rpos": 0}, "limit": "fixed", "fixed": {"kind": "upper", "value": 1.1}, "hidden": false, "memory": false}], "markers_hz": [1000000000.0, 3000000000.0, 6000000000.0, 12000000000.0, 18000000000.0], "note": "", "is_default": false, "active": true, "version": 2, "updated_by": "Артемий", "updated_at": "2026-11-20T09:12:00+03:00"}];
const scenarios = {
  idle: { device: { ...device, s2vna_ready: false, warmup_min: 28 }, calibration: { valid: false, calibrated_at: null, valid_until: null, reason: "нет — сеанс не открыт", temperatures: null }, due, session: null },
  steps: { device, calibration: { ...validCal, valid: false, reason: "калибровка выполнена, проверка не закончена" }, due, session: session({
    steps: [allPassed[0], allPassed[1], step("load_port1", "failed", "КСВН 1,094 > 1,06 на 17 980 МГц"), step("load_port2", "pending")],
  }) },
  border: { device, calibration: validCal, due, session: session({ items: [{
    id: "i1", version: 2, target, plan, connections_total: 3,
    connections: [{ no: 1, status: "passed", max_vswr: 1.065, max_mhz: 17200, measured_at: at("15:00") },
      { no: 2, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }, { no: 3, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }],
    trace, verdict: { code: "border", worst_vswr: 1.065, worst_mhz: 17200, norm_vswr: 1.06, uncertainty: 0.012, reason: "Норма < результат ≤ норма + U — повтор по СТО 12.4", final: false },
    refusal: null, recorded_at: null, recorded_to: null, label_instrument_id: null,
  }] }) },
  kb: { device, calibration: validCal, due, session: null },
  screens: { device, calibration: validCal, due, session: session({ view_screen_id: "sc-wide" }) },
  "screen-edit": { device, calibration: validCal, due, session: session({ view_screen_id: "sc-wide" }) },
  checks: { device, calibration: validCal, due: [due[0], { ...due[1], status: "overdue", due_at: at("00:00").replace("11-19", "11-18") }], session: session({ checks: [
    { id: "c0", version: 3, kind: "flex", status: "passed", result_text: "S21 0,021 дБ ≤ 0,05; КСВН 1,041", refusal: null, started_at: at("14:50"), finished_at: at("14:55"), phases: [] },
    { id: "c1", version: 2, kind: "monthly", status: "running", result_text: null, refusal: null, started_at: at("15:00"), finished_at: null, phases: [
      { key: "open_p1", label: "OPEN, порт 1", status: "passed", connection_no: 1, connections_total: 3, result_text: "0,31 допуска" },
      { key: "open_p1", label: "OPEN, порт 1", status: "passed", connection_no: 2, connections_total: 3, result_text: "0,28 допуска" },
      { key: "open_p1", label: "OPEN, порт 1", status: "passed", connection_no: 3, connections_total: 3, result_text: "0,35 допуска" },
      { key: "load_p1", label: "LOAD, порт 1", status: "passed", connection_no: 1, connections_total: 3, result_text: "0,42 допуска" },
      { key: "load_p1", label: "LOAD, порт 1", status: "pending", connection_no: 2, connections_total: 3, result_text: null },
      { key: "load_p1", label: "LOAD, порт 1", status: "pending", connection_no: 3, connections_total: 3, result_text: null },
    ] },
  ] }) },
};

const browser = await chromium.launch();
for (const vp of [{ tag: "16", width: 1536, height: 864 }, { tag: "27", width: 2560, height: 1440 }, { tag: "tablet", width: 1400, height: 900, touch: true }]) {
  const context = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, hasTouch: Boolean(vp.touch), locale: "ru-RU", timezoneId: "Europe/Moscow" });
  const login = await context.request.post(`${BASE}/api/auth/login`, { data: { login: "preview", password: "Preview-2026!" } });
  if (!login.ok()) throw new Error(`login ${login.status()}`);
  const page = await context.newPage();

  // «off»: без сервера рабочего места вкладки нет.
  await page.goto(`${BASE}/?section=quality`, { waitUntil: "networkidle" });
  await page.getByRole("tab", { name: "Средства измерения" }).waitFor();
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `vna-bench-off-${vp.tag}.png`) });

  let scenario = "idle";
  await page.route("**/api/production/vna-bench/**", (route) => {
    const url = new URL(route.request().url());
    const body = url.pathname.endsWith("/resolve") ? { candidates: [scenario === "kb" ? kb : target] } : url.pathname.endsWith("/plan") ? plan
      : url.pathname.endsWith("/screens") ? { screens } : scenarios[scenario];
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  const names = (process.env.VNA_SCENARIOS || "idle,steps,border,checks,kb,screens,screen-edit").split(",");
  for (const name of names) {
    scenario = name;
    await page.goto(`${BASE}/?section=quality&tab=vna`, { waitUntil: "networkidle" });
    await page.getByRole("tab", { name: "ВАЦ — рабочее место", selected: true }).waitFor();
    await page.locator(".vna-bench-head").waitFor();
    if (name === "border" || name === "kb") {
      await page.getByLabel("СИ или КБ — скан или номер").fill(name === "kb" ? "ПЗ-0001/3" : "СИ.035");
      await page.getByRole("button", { name: "Найти" }).click();
      await (name === "kb" ? page.getByRole("button", { name: "Открыть сеанс для КБ" }) : page.getByText("На границе допуска")).waitFor();
    }
    if (name === "checks") await page.getByRole("tab", { name: "Проверки" }).click();
    if (name === "screens" || name === "screen-edit") {
      await page.getByRole("tab", { name: "Экраны" }).click();
      await page.getByRole("table", { name: "Экраны ВАЦ" }).waitFor();
    }
    if (name === "screen-edit") {
      await page.getByText("ОТК — переход", { exact: true }).click();
      await page.getByRole("dialog").waitFor();
      await page.getByLabel("График 1: шкала").selectOption("manual");
      await page.getByLabel("График 1: деление").fill("0,02");
      await page.getByLabel("График 1: уровень").fill("1");
      await page.getByLabel("График 1: позиция").fill("0");
    }
    await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(OUT, `vna-bench-${name}-${vp.tag}.png`) });
    // Весь экран целиком: страница прокручивается внутри приложения, поэтому на время снимка окно выше.
    await page.setViewportSize({ width: vp.width, height: 2400 });
    await page.waitForTimeout(200);
    await page.locator(name === "screen-edit" ? ".page-window" : ".vna-bench").screenshot({ path: path.join(OUT, `vna-bench-${name}-${vp.tag}-full.png`) });
    await page.setViewportSize({ width: vp.width, height: vp.height });
  }
  await context.close();
}
await browser.close();
console.log("shots in", OUT);
