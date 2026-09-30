import { describe, expect, it } from "vitest";
import { defectKind, defectPicto, parseDefects, solderMode, toolingSections } from "./etkOperation";

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
    expect(mode).toMatchObject({ temperature: "320–350 °C", tipAssumed: true, temperatureAssumed: true });
  });

  it("knows a tinning bath and induction need no tip", () => {
    expect(solderMode({ code: "SDR_tin", card: { tooling: tooling("CT-53D | Ванна паяльная"), steps: [] } }))
      .toMatchObject({ method: "bath", temperature: "250–270 °C", temperatureAssumed: true });
    expect(solderMode({ code: "SDR_tin", card: { tooling: [{ section: "Оборудование", name: "CT-53D | Ванна паяльная", program: "200 °C" }], steps: [] } }))
      .toMatchObject({ method: "bath", temperature: "200 °C", temperatureAssumed: false });
    expect(solderMode({ code: "SDR_COAX_housing", card: { tooling: tooling("DW-OHF-6KW | Установка индукционной пайки"), steps: [] } }))
      .toMatchObject({ method: "induction", tipAssumed: false });
  });

  it("ignores non-soldering operations", () => {
    expect(solderMode({ code: "CUT_WIRE_auto", card: { name: "Резка", steps: [] } })).toBeNull();
  });
});

describe("parseDefects", () => {
  it("splits glued items into groups with a pictogram kind each (SDR_DB)", () => {
    const parsed = parseDefects("ТИПОВЫЕ ДЕФЕКТЫ (Критерии отбраковки / NG) со ссылками на IPC/WHMA-A-620F:\n" +
      "1. Дефекты паяного соединения чашечки (Раздел 4.3):Наплывы на наружной поверхности: Избыток припоя вытек наружу.Недолив припоя: Чашечка заполнена менее чем на 75,0% объема.«Холодная» пайка: Рыхлая структура.\n" +
      "2. Механические дефекты жил:Распушение жил: Проволочки торчат.\n");
    expect(parsed.intro[0]).toMatch(/^ТИПОВЫЕ ДЕФЕКТЫ/);
    expect(parsed.groups.map((group) => group.title)).toEqual(["Дефекты паяного соединения чашечки (Раздел 4.3)", "Механические дефекты жил"]);
    expect(parsed.groups[0].items.map((item) => [item.name, item.kind])).toEqual([
      ["Наплывы на наружной поверхности", "excess"], ["Недолив припоя", "lack"], ["«Холодная» пайка", "cold"],
    ]);
    expect(parsed.groups[1].items[0]).toMatchObject({ name: "Распушение жил", kind: "strands", text: "Проволочки торчат." });
  });

  it("keeps items without numbered groups and joins wrapped lines", () => {
    const parsed = parseDefects("Типовые дефекты (NG):\nНеверная распиновка: Порядок проводов\nне по чертежу.");
    expect(parsed.groups).toEqual([{ title: "", items: [{ name: "Неверная распиновка", text: "Порядок проводов не по чертежу.", kind: "pinout" }] }]);
  });

  it("draws an SVG for every kind", () => {
    expect(defectKind("Затекание припоя")).toBe("wicking");
    expect(defectKind("Что-то новое")).toBe("generic");
    expect(defectPicto("bridge")).toMatch(/^<svg[^>]*viewBox="0 0 72 44"/);
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
    { name: "«Недоусадка»", text: "Трубка болтается или проворачивается", kind: "insulation" },
    { name: "Прожог ТУТ", text: "", kind: "insulation" },
    { name: "Смещение ТУТ по длине", text: "", kind: "seating" },
  ]);
  const lines = parseDefects("ТИПОВЫЕ ДЕФЕКТЫ (NG), IPC/WHMA-A-620F (Раздел 3 «Провода»):\nНадрезы, царапины или обрыв проволочек жилы.\n" +
    "Несоответствие длины зачистки чертежу. Смятие изоляции от губок.\nОтсутствие юбки втулки. Приводит к подрезке проволочек.");
  expect(lines.intro).toHaveLength(1);
  expect(lines.groups[0].items.map((item) => [item.name, item.text])).toEqual([
    ["Надрезы, царапины или обрыв проволочек жилы", ""], ["Несоответствие длины зачистки чертежу", ""],
    ["Смятие изоляции от губок", ""], ["Отсутствие юбки втулки", "Приводит к подрезке проволочек."],
  ]);
});
