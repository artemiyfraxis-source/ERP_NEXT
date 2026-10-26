import { expect, it } from "vitest";
import type { CableAssemblyPassport, PassportCheck } from "./api";
import { buildCoaxPassportHtml } from "./coaxPassportPrint";

const check: PassportCheck = {
  kind: "vswr", title: "КСВН по ВАЦ", verdict: "pass", verdict_name: "Годно", attempts: 2,
  inspector: { id: "u", name: "Иванов <И.>" }, inspected_at: "2026-10-22T10:00:00+03:00",
  instrument: { name: "Анализатор Arinst", instrument_type: "ВАЦ", inventory_number: "СИ-021", verification_number: "С-ВУ/01-2026", verified_at: "2026-03-01T00:00:00", verification_due: "2027-03-01T00:00:00" },
  measurement: {
    id: "m", serial_number: "ПЗ-1-01", attempt: 2, band_mhz: [1700, 1900], max_vswr: 1.62, max_vswr_mhz: 1812.5, norm: {}, norm_vswr_max: 2.06,
    verdict: "pass", verdict_name: "Годно", verdict_manual: false, verdict_reason: "", inspector: { id: "u", name: "Иванов" },
    measured_at: "2026-10-22T10:00:00+03:00", file_name: "cable.s1p", points: 3, file_meta: {},
    trace: [{ mhz: 1700, vswr: 1.2 }, { mhz: 1800, vswr: 1.6 }, { mhz: 1900, vswr: 1.4 }],
  },
};

const passport = (checks: PassportCheck[] = [check], over: Partial<CableAssemblyPassport> = {}): CableAssemblyPassport => ({
  product: {
    task_number: "ПЗ-0001", project: "716К.1", item: "716К.1.КБ2", name: "Кабель SMA-SMA", serial_number: "ПЗ-1-01", position: 1,
    cable: { name: "RG-316", loss_db_m: 0.9 }, length_m: 1.5,
    ends: [{ side: "А", article: "SMA-J 1,25", vswr_text: "≤ 1,25", vswr: 1.25, max_ghz: 6 }, { side: "Б", article: "SMA-J 1,3", vswr_text: "", vswr: 1.3, max_ghz: 6 }],
  },
  etk: { techcard_id: "t", item_index: "716К.1.КБ2", revision: 3, product_name: "Кабель" },
  norm: { status: "ok", reason: "", vswr_max: 2.06, band_mhz: [1700, 1900], basis: "ЭТК 716К.1.КБ2, ревизия 3" },
  checks, verdict: checks[0]?.verdict ?? null, inspector: checks[0]?.inspector ?? null, inspected_at: checks[0]?.inspected_at ?? null,
  ...over,
});

// Числа форматируются узким пробелом разрядов — в проверках приводим любые пробелы к обычному.
const flat = (html: string) => html.replace(/[   ]/g, " ");

it("prints every part of the passport", () => {
  const html = flat(buildCoaxPassportHtml(passport()));
  for (const part of ["Паспорт кабельной сборки", "ПЗ-1-01", "ПЗ-0001", "716К.1 / 716К.1.КБ2", "ревизия 3", "RG-316", "затухание 0,9 дБ/м",
    "SMA-J 1,25", "до 6 ГГц", "КСВН не более 2,06", "ЭТК 716К.1.КБ2, ревизия 3", "1,62", "1 812,5 МГц", "<svg", "СИ-021", "С-ВУ/01-2026",
    "01.03.2026", "действительно до 01.03.2027", "Контролёр ОТК", "Дата: ____________"]) {
    expect(html).toContain(part);
  }
});

it("escapes names and uses black on white with no inline style", () => {
  const html = buildCoaxPassportHtml(passport());
  expect(html).toContain("Иванов &lt;И.&gt;");
  expect(html).not.toContain("<И.>");
  expect(html).not.toContain("style=");
  expect(html).toContain("background: #fff");
});

it("iterates the checks: another kind of check is appended as its own section", () => {
  const other: PassportCheck = { kind: "isolation", title: "Сопротивление изоляции", verdict: "fail", verdict_name: "Не годно", attempts: 1, inspector: { id: "u", name: "Петров" }, inspected_at: "2026-10-22T11:00:00+03:00", instrument: null };
  const html = buildCoaxPassportHtml(passport([check, other]));
  expect(html.match(/<section class="check">/g)).toHaveLength(2);
  expect(html).toContain("<h2>КСВН по ВАЦ</h2>");
  expect(html).toContain("<h2>Сопротивление изоляции</h2>");
  expect(html).toContain("Петров");
});

it("states an undefined norm with its reason and a hand-set verdict", () => {
  const manual: PassportCheck = { ...check, verdict: "fail", verdict_name: "Не годно", measurement: { ...check.measurement!, verdict: "fail", verdict_name: "Не годно", verdict_manual: true, verdict_reason: "Нет КСВ у разъёма", norm_vswr_max: null } };
  const html = buildCoaxPassportHtml(passport([manual], { norm: { status: "undefined", reason: "у разъёма нет КСВ", vswr_max: null, band_mhz: null, basis: "ЭТК 716К.1.КБ2, ревизия 3" } }));
  expect(html).toContain("не определена — у разъёма нет КСВ");
  expect(html).toContain("Вердикт назначен вручную");
  expect(html).toContain("Нет КСВ у разъёма");
  expect(html).not.toContain("Норма ≤");
});

it("prints a passport with no checks yet", () => {
  const html = buildCoaxPassportHtml(passport([]));
  expect(html).toContain("Проверки ОТК по изделию ещё не проводились.");
  expect(html).not.toContain("<svg");
});

it("a unit outside the 1-of-N sample refers to the batch sample and its verdicts", () => {
  const sample = { every: 50, units: [
    { position: 1, serial_number: "ПЗ-1-01", verdict: "pass", verdict_name: "Годно", max_vswr: 1.42 },
    { position: 51, serial_number: null, verdict: null, verdict_name: null, max_vswr: null },
  ] };
  const html = flat(buildCoaxPassportHtml(passport([], { control: { mode: "sample", every: 50 }, vswr_sample: sample })));
  expect(html).toContain("КСВН: контроль выборочный 1 из 50 — изделие в выборку не входило.");
  expect(html).toContain("<td>1</td><td>ПЗ-1-01</td><td>Годно</td><td>1,42</td>");
  expect(html).toContain("<td>51</td><td>—</td><td>не измерено</td><td>—</td>");
  expect(html).toContain("выборочный, 1 из 50");
  expect(html).not.toContain("Проверки ОТК по изделию ещё не проводились");
  // Замеренное изделие печатается как раньше — своим замером, без раздела выборки.
  const measured = buildCoaxPassportHtml(passport([check], { control: { mode: "sample", every: 50 }, vswr_sample: null }));
  expect(measured).not.toContain("в выборку не входило");
  expect(measured).toContain("cable.s1p");
});

it("a customer norm prints as the acceptance norm with its basis and the components norm for reference", () => {
  const html = flat(buildCoaxPassportHtml(passport([check], { norm: {
    status: "ok", reason: "", vswr_max: 1.5, band_mhz: [700, 2700], source: "customer",
    basis: "ЭТК 716К.1.КБ2, ревизия 3; ТУ 16-505.123, п. 3.4",
    components: { status: "ok", reason: "", vswr_max: 2.06, band_mhz: [1, 6000] },
  } })));
  expect(html).toContain("КСВН не более 1,5 (норма заказчика)");
  expect(html).toContain("ЭТК 716К.1.КБ2, ревизия 3; ТУ 16-505.123, п. 3.4");
  expect(html).toContain("700 … 2 700");
  expect(html).toContain("<th>Норма по комплектующим (справочно)</th><td>КСВН не более 2,06</td>");
});

it("prints the cable with its standard, «—» when the cable list has none — never a guess (владелец 25.10)", () => {
  const withStandard = passport();
  withStandard.product.cable = { name: "RG-316", loss_db_m: 0.9, standard: "MIL-DTL-17/113" };
  expect(flat(buildCoaxPassportHtml(withStandard))).toContain("RG-316 (MIL-DTL-17/113), затухание 0,9 дБ/м");
  expect(flat(buildCoaxPassportHtml(passport()))).toContain("RG-316 (—), затухание 0,9 дБ/м");
});
