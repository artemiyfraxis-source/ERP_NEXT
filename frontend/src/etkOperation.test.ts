import { describe, expect, it } from "vitest";
import { etkLabelResult, etkMarkPhase, etkOperationName, etkToleranceLines, etkToleranceParts, parseDefects, solderMode, toolingSections } from "./etkOperation";

it("counts printed label kinds and pieces for a task subrange, while installation keeps its own output", () => {
  const operation = {
    code: "PRINT_MARK_LABEL", fields: { marks: [
      { text: "КБ1-{№:6}", qty: "2", batch: "30" },
      { text: "Camera 14", qty: "1", batch: "15" },
      { text: "Splitter B C6", qty: "1", batch: "15" },
    ] },
    result: [{ name: "Напечатанные этикетки: 4 шт.", norm: "60", unit: "Шт." }],
  };
  expect(etkLabelResult(operation, { first: 11, last: 15 })).toEqual({ name: "Напечатанные этикетки", norm: "20", unit: "Шт.", summary: "7\u00a0видов, 20 шт." });
  expect(etkLabelResult(operation)).toEqual({ name: "Напечатанные этикетки", norm: "60", unit: "Шт.", summary: "3\u00a0вида, 60 шт." });
  // A printer code on an installation operation is not a print job: no label output, the table is a placement spec.
  const stale = { ...operation, card: { name: "Наклейка этикеток маркировки" }, result: [{ name: "Кабель маркирован" }] };
  expect(etkMarkPhase(stale)).toBe("install");
  expect(etkLabelResult(stale)).toBeNull();
  expect(etkMarkPhase(operation)).toBe("print");
  expect(etkMarkPhase({ code: "INS_MARK_HARNESS", fields: operation.fields })).toBeNull();
  expect(etkLabelResult({ ...operation, code: "INS_MARK_LABEL", result: [{ name: "Кабель маркирован" }] }, { first: 1, last: 15 })).toBeNull();
  expect(etkLabelResult({ code: "PRINT_MARK_LABEL" }, { first: 1, last: 15 })).toBeNull();
});

describe("etkOperationName", () => {
  it("names a coax operation by its kind where the card says only the first word (796К.2 КБ1, owner 15.10)", () => {
    expect(etkOperationName("Резка", "coaxCut")).toBe("Резка кабеля");
    expect(etkOperationName("Резка", "coaxCutTut")).toBe("Резка ТУТ");
    expect(etkOperationName("Монтаж", "coaxInsTut")).toBe("Монтаж ТУТ");
    expect(etkOperationName("Монтаж", "coaxInsSleeve")).toBe("Монтаж втулки экрана");
    expect(etkOperationName("Пайка", "coaxPin")).toBe("Пайка центрального контакта");
    expect(etkOperationName("Опрессовка", "coaxShield")).toBe("Опрессовка втулки экрана");
    expect(etkOperationName("Усадка", "coaxHeat")).toBe("Усадка ТУТ");
    // Своё имя карты — как есть; без имени — пусто (дальше заголовок шаблона).
    expect(etkOperationName("Монтаж корпуса", "coaxHousing")).toBe("Монтаж корпуса");
    expect(etkOperationName("Автомат резка + опрессовка", "cutWireCrimp")).toBe("Автомат резка + опрессовка");
    expect(etkOperationName("Резка", "")).toBe("Резка");
    expect(etkOperationName("", "coaxCut")).toBe("");
  });
});

const tooling = (equipment: string, parameters = "") => [
  { section: "Расходники", name: "Припой Sn63Pb37", parameters: "от 190 °С до 270 °С" },
  { section: "Оборудование", name: equipment, parameters },
  { section: "Инструмент", name: "Микроскоп" },
];

describe("solderMode", () => {
  it("takes the tip and temperature from the operation steps (SDR_XT60)", () => {
    const mode = solderMode({ code: "SDR_XT60", card: { name: "Пайка", tooling: tooling("T21(R) | Станция паяльная"), steps: [
      { text: "Паяльник ≥80 Вт / станция с массивным жалом; t° 350–380 °C", tolerance: "(+/-) 10 °C" },
      { text: "Прогреть чашку массой жала" },
    ] } });
    expect(mode).toMatchObject({ method: "iron", temperature: "350–380 °C", tolerance: "±10 °C", tipAssumed: false, temperatureAssumed: false });
    expect(mode?.tip).toMatch(/Массивное/);
  });

  it("falls back to the ERP default when the card has neither (SDR_COAX_pin)", () => {
    const mode = solderMode({ code: "SDR_COAX_pin", card: { tooling: tooling("T21(R) | Станция паяльная"), steps: [
      { text: "Выставить рабочую температуру жала (согласно технологической инструкции)" },
    ] } });
    expect(mode).toMatchObject({ temperature: "240 °C", tipAssumed: true, temperatureAssumed: true });
  });

  it("knows a tinning bath and induction need no tip", () => {
    expect(solderMode({ code: "SDR_tin", card: { tooling: tooling("CT-53D | Ванна паяльная"), steps: [] } }))
      .toMatchObject({ method: "bath", temperature: "240 °C", temperatureAssumed: true });
    expect(solderMode({ code: "SDR_tin", card: { tooling: [{ section: "Оборудование", name: "CT-53D | Ванна паяльная", program: "200 °C" }], steps: [] } }))
      .toMatchObject({ method: "bath", temperature: "200 °C", temperatureAssumed: false });
    // Резка с обжимом и лужением: станок BZW-5.0+Z лудит погружением, как ванна.
    expect(solderMode({ code: "CUT_WIRE+1crimp+tin", card: { name: "Автомат резка + опрессовка + лужение",
      tooling: [{ section: "Оборудование", name: "BZW-5.0+Z | Станок резки/ обжим+лужение", program: "A19" }], steps: [] } }))
      .toMatchObject({ method: "bath", temperature: "240 °C", temperatureAssumed: true });
    expect(solderMode({ code: "SDR_COAX_housing", card: { tooling: tooling("DW-OHF-6KW | Установка индукционной пайки"), steps: [] } }))
      .toMatchObject({ method: "induction", tipAssumed: false });
  });

  it("ignores non-soldering operations", () => {
    expect(solderMode({ code: "CUT_WIRE_auto", card: { name: "Резка", steps: [] } })).toBeNull();
  });
});

describe("parseDefects", () => {
  it("splits glued items into groups (SDR_DB)", () => {
    const parsed = parseDefects("ТИПОВЫЕ ДЕФЕКТЫ (Критерии отбраковки / NG) со ссылками на IPC/WHMA-A-620F:\n" +
      "1. Дефекты паяного соединения чашечки (Раздел 4.3):Наплывы на наружной поверхности: Избыток припоя вытек наружу.Недолив припоя: Чашечка заполнена менее чем на 75,0% объема.«Холодная» пайка: Рыхлая структура.\n" +
      "2. Механические дефекты жил:Распушение жил: Проволочки торчат.\n");
    expect(parsed.intro[0]).toMatch(/^ТИПОВЫЕ ДЕФЕКТЫ/);
    expect(parsed.groups.map((group) => group.title)).toEqual(["Дефекты паяного соединения чашечки (Раздел 4.3)", "Механические дефекты жил"]);
    expect(parsed.groups[0].items.map((item) => item.name)).toEqual(["Наплывы на наружной поверхности", "Недолив припоя", "«Холодная» пайка"]);
    expect(parsed.groups[1].items[0]).toMatchObject({ name: "Распушение жил", text: "Проволочки торчат." });
  });

  it("keeps items without numbered groups and joins wrapped lines", () => {
    const parsed = parseDefects("Типовые дефекты (NG):\nНеверная распиновка: Порядок проводов\nне по чертежу.");
    expect(parsed.groups).toEqual([{ title: "", items: [{ name: "Неверная распиновка", text: "Порядок проводов не по чертежу." }] }]);
  });
});

it("orders tooling sections tool → equipment → consumables → other", () => {
  expect(toolingSections(["Расходники", "Матрицы", "Инструмент", "Оборудование", "Инструмент", "Прочее"]))
    .toEqual(["Инструмент", "Оборудование", "Расходники", "Матрицы", "Прочее"]);
});

it("reads the one-line and sentence-per-line defect formats (HEAT_TYT, STRIP_WIRE)", () => {
  const list = parseDefects("Типовые дефекты (NG): «Недоусадка» (Трубка болтается или проворачивается); прожог ТУТ; смещение ТУТ по длине.");
  expect(list.intro).toEqual(["Типовые дефекты (NG)"]);
  expect(list.groups[0].items).toEqual([
    { name: "«Недоусадка»", text: "Трубка болтается или проворачивается" },
    { name: "Прожог ТУТ", text: "" },
    { name: "Смещение ТУТ по длине", text: "" },
  ]);
  const lines = parseDefects("ТИПОВЫЕ ДЕФЕКТЫ (NG), IPC/WHMA-A-620F (Раздел 3 «Провода»):\nНадрезы, царапины или обрыв проволочек жилы.\n" +
    "Несоответствие длины зачистки чертежу. Смятие изоляции от губок.\nОтсутствие юбки втулки. Приводит к подрезке проволочек.");
  expect(lines.intro).toHaveLength(1);
  expect(lines.groups[0].items.map((item) => [item.name, item.text])).toEqual([
    ["Надрезы, царапины или обрыв проволочек жилы", ""], ["Несоответствие длины зачистки чертежу", ""],
    ["Смятие изоляции от губок", ""], ["Отсутствие юбки втулки", "Приводит к подрезке проволочек."],
  ]);
});

describe("etkToleranceLines / etkToleranceParts", () => {
  it("splits a step tolerance into a line per value with its label, normalising ± and ranges", () => {
    expect(etkToleranceLines("Допуск: Сторона В: 2–3 мм")).toEqual([{ label: "Сторона В", value: "2–3 мм" }]);
    expect(etkToleranceLines("(+/-) 10 °C")).toEqual([{ label: "", value: "± 10 °C" }]);
    expect(etkToleranceLines("Время: ≤ 2,0 сек\nЗазор: 0,5 - 1,5 мм")).toEqual([
      { label: "Время", value: "≤ 2,0 сек" }, { label: "Зазор", value: "0,5–1,5 мм" },
    ]);
    expect(etkToleranceLines("ГОСТ 23592-96; КД")).toEqual([{ label: "", value: "ГОСТ 23592-96" }, { label: "", value: "КД" }]);
    expect(etkToleranceLines("-")).toEqual([]);
    expect(etkToleranceLines(undefined)).toEqual([]);
  });

  it("bolds the numbers with their unit and sign, never inside a reference to a standard", () => {
    expect(etkToleranceParts("± 10 °C")).toEqual([{ text: "± 10 °C", strong: true }]);
    expect(etkToleranceParts("не более 3 сек")).toEqual([{ text: "не более ", strong: false }, { text: "3 сек", strong: true }]);
    expect(etkToleranceParts("IPC/WHMA-A-620F (Раздел 3)")).toEqual([{ text: "IPC/WHMA-A-620F (Раздел 3)", strong: false }]);
  });
});

describe("etkMarkPhase matrix", () => {
  const fields = { marks: [{ text: "X1", batch: "1" }] };
  const phase = (name: string, extra: Record<string, unknown> = {}) => etkMarkPhase({ fields, card: { name, steps: [] }, ...extra });
  it("reads the name: install words beat a «печат» inside another word, print at a word start stays print", () => {
    expect(phase("Монтаж напечатанных этикеток")).toBe("install");
    expect(phase("Установка после печати")).toBe("install");
    expect(phase("Печать и наклейка")).toBe("print");
    expect(phase("Печать этикеток ×3")).toBe("print");
    expect(phase("Наклейка этикеток маркировки ×3")).toBe("install");
  });
  it("old and English codes, and a name-less operation, stay undecided or follow the code", () => {
    expect(phase("", { code: "INS_MARK_LABEL" })).toBeNull();
    expect(phase("", { code: "PRINT_MARK_LABEL" })).toBe("print");
    expect(phase("", { code: "print_mark_tube" })).toBe("print");
    expect(phase("Mark harness", { code: "INS_MARK_HARNESS" })).toBeNull();
    expect(etkMarkPhase({ fields })).toBeNull();
    expect(etkMarkPhase({ code: "PRINT_MARK_LABEL" })).toBeNull();
  });
  it("an explicit markPhase wins", () => {
    expect(etkMarkPhase({ fields: { ...fields, markPhase: "install" }, card: { name: "Печать этикеток" } })).toBe("install");
  });
  it("takes the phase from what the steps tell the fitter when the name and code say «печать»", () => {
    const install = { fields, code: "PRINT_MARK_LABEL", card: { name: "Печать этикеток", steps: [{ text: "Наклеить этикетки на кабель по таблице." }] } };
    expect(etkMarkPhase(install)).toBe("install");
    const print = { fields, code: "PRINT_MARK_LABEL", card: { name: "Печать этикеток", steps: [{ text: "Заправить ленту в маркиратор и напечатать партию." }] } };
    expect(etkMarkPhase(print)).toBe("print");
  });
  // KNOWN GAP (findings.md:75): an installation whose name, code, template AND steps all say «печать» cannot be told
  // from a print by any saved field; it needs slice 1's builder to write fields.markPhase. Until then it is classed as print.
  it("known gap: an install that says «печать» everywhere is still print until the builder saves markPhase", () => {
    const stale = { fields, code: "PRINT_MARK_LABEL", card: { name: "Печать этикеток", steps: [{ text: "Выполнить печать этикеток." }] } };
    expect(etkMarkPhase(stale)).toBe("print");
  });
});
