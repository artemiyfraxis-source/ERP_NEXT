// @ts-ignore — типы node в проекте не подключены, файл СТО читается напрямую из backend/data
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import type { ProjectTechcard } from "./api";
import { buildRouteSheetHtml } from "./routeSheetPrint";
import { buildStoHtml } from "./stoPrint";
import { buildTdKitHtml } from "./tdPrintSheets";
import type { Sto, TdPrintModel } from "./tdTypes";

declare const process: { cwd(): string };
const sto = JSON.parse(readFileSync(`${process.cwd()}/../backend/data/sto-frak-001-2026.json`, "utf8") as string) as Sto;

const cut = {
  position: 1, code: "CUT_WIRE_auto", type: "cutWireCrimp", title: "CUT_WIRE_auto | Автомат резка", connector: "", side: "",
  times: { operation_seconds: "12", setup_seconds: "90" },
  card: {
    name: "Автомат резка + опрессовка",
    steps: [
      { number: "1", kind: "Операция", text: "Отрезать провод по длине КД" },
      { number: "2", kind: "Операция", text: "Опрессовать контакт", tolerance: "Высота обжима: 1,2–1,4 мм" },
      { number: "3", kind: "Контроль", text: "Измерить длину штангенциркулем", tolerance: "± 1 мм" },
    ],
    tooling: [
      { section: "Оборудование", name: "BZW-2.0 | Станок резки", inventory_number: "ИН-17" },
      { section: "Инструмент", name: "Штангенциркуль ШЦ-1", inventory_number: "ИН-5" },
    ],
  },
  components: [
    { art: "SELLER-ART-777", name: "Провод ПВАМ 1,5", kd: "ПВАМ-1,5 ТУ", norm: "700", unit: "М." },
    { art: "SELLER-ART-888", name: "Припой ПОС-61", kind: "Материал", norm: "70", unit: "г" },
  ],
  fields: { crimpHeight: "1,3" },
};
const second = {
  position: 2, code: "SDR_TIN", type: "sdrTin", title: "Лужение", connector: "XS1", side: "A",
  times: { operation_seconds: "6", setup_seconds: "30" },
  card: {
    name: "Лужение",
    steps: [{ number: "1", kind: "Операция", text: "Облудить жилу" }, { number: "2", kind: "Контроль", text: "Осмотреть пайку", tolerance: "Без наплывов" }],
    tooling: [{ section: "Оборудование", name: "Паяльная станция", parameters: "320 °C" }],
  },
  components: [{ art: "SELLER-ART-999", name: "Провод ПВАМ 1,5", kd: "ПВАМ-1,5 ТУ", norm: "700", unit: "М." }],
};
const third = { position: 3, code: "INS_HSG", type: "insHousing", title: "Монтаж в корпус", card: { name: "Монтаж в корпус", steps: [{ kind: "Операция", text: "Вставить контакты" }] } };

const person = (name: string, position = "") => ({ name, position });

function model(over: Partial<TdPrintModel["issue"]> = {}): TdPrintModel {
  return {
    issue: {
      id: "i1", version: 1, project_card_id: "p", change_no: 0, status: "active", status_label: "действует", etk_revision: 4, current_etk_revision: 4,
      has_changes: false, sequence_no: 1, org_code: "ФРАК", method_code: "85", litera: "О1",
      designations: { vtd: "ФРАК.10185.00007", mk: "ФРАК.10185.00007 МК", ke: "ФРАК.10185.00007 КЭ", ok: "ФРАК.10185.00007 ОК", ok_tk: "ФРАК.60102.00007", vm: "ФРАК.10185.00007 ВМ", vo: "ФРАК.10185.00007 ВО" },
      signatories: { developer: person("Иванов И.И."), checker: person("Петров П.П."), normcontrol: person("Сидоров С.С."), approver: person("Козлов К.К.", "Главный инженер"), agreed_vp: person("", "") },
      notice_no: "", reason: "", created_at: "2026-10-20T10:00:00", created_by: "x", approved_at: null, annulled_at: null, ...over,
    },
    card: { id: "t", project: "713К.4", item_index: "КБ1", product_name: "Жгут 713К.4", revision: 4,
      document: { kit: { part_quantity: "700" }, operations: [cut, second, third] } } as unknown as ProjectTechcard,
    sto_sections: { "CUT_WIRE_auto::::": { section: "4.1", title: "Резка" }, "SDR_TIN::XS1::A": { section: "4.4", title: "Лужение и пайка" }, "INS_HSG::::": null },
    changes: [{ change_no: 1, notice_no: "ИЗВ-1", reason: "Смена провода", designation: "ФРАК.10185.00007", approved_at: "2026-10-21T00:00:00", developer: "Иванов И.И." }],
    sto: { designation: "СТО ФРАК-001-2026", revision: "1" },
  };
}

const html = buildTdKitHtml(model());
const sheets = html.split('<section class="sheet">').slice(1);
const byHeading = (title: string): string => sheets.find((sheet) => sheet.includes(`<div class="content"><h2>${title}`)) ?? "";
const textOf = (value: string) => value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

it("каждый лист несёт основную надпись: обозначение, литеру, Лист/Листов и четыре фамилии", () => {
  expect(sheets.length).toBeGreaterThan(8);
  for (const sheet of sheets) {
    expect(sheet).toContain('class="stamp"');
    expect(sheet).toMatch(/ФРАК\.(10185|60102)\.0000\d/);
    expect(sheet).toContain("Лит.");
    expect(sheet).toContain("О1");
    expect(sheet).toContain("Листов");
    for (const name of ["Иванов И.И.", "Петров П.П.", "Сидоров С.С.", "Козлов К.К."]) expect(sheet).toContain(name);
    for (const role of ["Разраб.", "Пров.", "Н. контр.", "Утв."]) expect(sheet).toContain(role);
  }
  expect(html).toContain("@page { size: A4 landscape; margin: 5mm; }");
});

it("титульный лист: утверждаю, согласовано с пустым ВП, название по методу", () => {
  const tl = textOf(sheets[0]);
  expect(tl).toContain("УТВЕРЖДАЮ");
  expect(tl).toContain("Главный инженер");
  expect(tl).toContain("СОГЛАСОВАНО");
  expect(tl).toContain("Представитель ВП МО");
  expect(tl).toContain("на технологический процесс электромонтажа");
  expect(buildTdKitHtml(model({ method_code: "88" }))).toContain("технологический процесс сборки");
  expect(buildTdKitHtml(model({ method_code: "80" }))).toContain("технологический процесс пайки");
});

it("ВТД перечисляет документы комплекта и справочные ГОСТ и СТО", () => {
  const vtd = textOf(byHeading("Ведомость технологических документов"));
  for (const part of ["Маршрутная карта", "Операционные карты", "Операционная карта технического контроля", "Ведомость материалов", "Ведомость оснастки",
    "Лист регистрации изменений", "СТО ФРАК-001-2026", "ГОСТ 23586-96", "ГОСТ 23587-96", "ГОСТ 23585-79", "ГОСТ Р 72182.2-2025"]) expect(vtd).toContain(part);
});

it("МК: строки А/Б/К/М, номера 005, 010…, время в минутах, ссылка на СТО", () => {
  const mk = byHeading("Маршрутная карта");
  expect(mk).toContain("<td>А</td>");
  expect(mk).toContain("<td>Б</td>");
  expect(mk).toContain("<td>К</td>");
  expect(mk).toContain("<td>М</td>");
  for (const number of ["005", "010", "015"]) expect(mk).toContain(`<td>${number}</td>`);
  expect(mk).toContain("<td>1,50</td>"); // 90 с подготовки
  expect(mk).toContain("<td>0,20</td>"); // 12 с на штуку
  expect(mk).toContain("монтажник РЭА и приборов");
  expect(mk).toContain("Тпз, мин");
  expect(mk).toContain("СТО ФРАК-001-2026, разд. 4.1");
  expect(mk).toContain("Провод ПВАМ 1,5 (по КД: ПВАМ-1,5 ТУ)");
});

it("ОК: переходы нумеруются 1, 2, 3; ссылка на ОК ТК и на СТО; нет текста типовых дефектов", () => {
  const ok = sheets.filter((sheet) => sheet.includes("Операционная карта. Операция"));
  expect(ok).toHaveLength(3);
  const first = ok[0];
  expect(first).toMatch(/<td>О<\/td><td>1<\/td><td>Отрезать провод/);
  expect(first).toMatch(/<td>О<\/td><td>2<\/td><td>Опрессовать/);
  expect(first).toMatch(/<td>О<\/td><td>3<\/td><td>Контролировать по ОК технического контроля ФРАК\.60102\.00007, операция 005/);
  expect(first).not.toContain("Измерить длину штангенциркулем</td><td></td>");
  expect(first).toContain("<td>Т</td>");
  expect(first).toContain("ИН-5");
  expect(first).toContain("<td>Р</td>");
  expect(first).toContain("Высота обжима");
  expect(first).toContain("по СТО ФРАК-001-2026, разд. 4.1 «Резка»");
  expect(ok[1]).toContain("разд. 4.4");
  expect(ok[2]).toContain("по СТО ФРАК-001-2026, разд. 4");
  expect(html).not.toContain("ТИПОВЫЕ ДЕФЕКТЫ");
  expect(html.toLowerCase()).not.toContain("типовые дефекты (ng)");
});

it("ОК технического контроля — только шаги «Контроль», 100 %, ОТК; последняя операция — ОТК, ВП", () => {
  const tk = byHeading("Операционная карта технического контроля");
  expect(tk).toContain("Измерить длину штангенциркулем");
  expect(tk).toContain("Осмотреть пайку");
  expect(tk).not.toContain("Отрезать провод");
  expect(tk).not.toContain("Облудить жилу");
  expect(tk).toContain("100 %");
  expect(tk).toContain("<td>ОТК</td>");
  expect(tk).toContain("<td>ОТК, ВП</td>");
  expect(tk).toContain("Штангенциркуль ШЦ-1");
  expect(tk).toContain("по ОК, графа Т");
});

it("ВМ без артикула продавца, с обозначением по КД и нормой на изделие", () => {
  const vm = byHeading("Ведомость материалов");
  expect(vm).toContain("Провод ПВАМ 1,5");
  expect(vm).toContain("ПВАМ-1,5 ТУ");
  expect(vm).toContain("<td>2</td>"); // 700 + 700 м на партию 700 — 2 м на изделие
  expect(html).not.toMatch(/SELLER-ART/);
});

it("ВО агрегирует оснастку, ЛР содержит изменения и пустые строки", () => {
  const vo = byHeading("Ведомость оснастки");
  expect(vo).toContain("ИН-17");
  expect(vo).toContain("Паяльная станция");
  const lr = byHeading("Лист регистрации изменений");
  expect(lr).toContain("ИЗВ-1");
  expect(lr).toContain("21.10.2026");
  expect(lr.match(/class="empty"/g)?.length ?? 0).toBeGreaterThanOrEqual(10);
});

it("при изменении в основной надписи — номер и извещение; листы нумеруются внутри документа", () => {
  const changed = buildTdKitHtml(model({ change_no: 2, notice_no: "ИЗВ-2", approved_at: "2026-11-02T00:00:00" }));
  expect(changed).toContain("ИЗВ-2");
  expect(changed).toContain("02.11.2026");
  const marks = [...html.matchAll(/<td>О1<\/td><td>(\d+)<\/td><td>(\d+)<\/td>/g)];
  expect(marks.length).toBeGreaterThan(8);
  expect(marks.some(([, n, total]) => n === "1" && total === "3")).toBe(true); // ОК: три операции
  expect(marks.some(([, n, total]) => n === "3" && total === "3")).toBe(true);
});

it("в комплекте нет латинских кодов видов операций", () => {
  expect(html).not.toMatch(/[A-Za-z]+_[A-Za-z0-9_]+/);
  expect(html).not.toContain("CUT_WIRE");
});

it("СТО печатается целиком: все 236 кодов дефектов, повтор шапки таблиц, действия и лист изменений", () => {
  const out = buildStoHtml(sto);
  const codes: string[] = [];
  const walk = (section: Sto["sections"][number]): void => { section.rows.forEach((row) => codes.push(row.code)); (section.subsections ?? []).forEach(walk); };
  sto.sections.forEach(walk);
  expect(codes).toHaveLength(236);
  for (const code of codes) expect(out).toContain(`<td class="code">${code}</td>`);
  expect(out).toContain("thead { display: table-header-group; }");
  expect(out).toContain("A4 portrait");
  expect(out).toContain("Признак / критерий отбраковки");
  expect(out).toContain("<ol>");
  expect(out).toContain("Лист регистрации изменений");
  expect(out).toContain("Первая редакция");
});

it("сопроводительный лист: шапка, операции, места для отметок и принятие изделия", () => {
  const out = buildRouteSheetHtml(model({ change_no: 1 }), { serial: "SN-001", batch: "П-5", qty: "20" });
  for (const part of ["ФРАК.10185.00007", "SN-001", "П-5", "20", "Исполнитель (ФИО, подпись)", "Партии материалов", "Отметка ОТК", "Отметка ВП", "Изделие принял: ОТК", "Автомат резка + опрессовка", "005", "010"]) expect(out).toContain(part);
  expect(out).not.toContain("CUT_WIRE");
});
