// @ts-ignore — типы node в проекте не подключены, файл СТО читается напрямую из backend/data
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import type { ProjectTechcard } from "./api";
import { buildRouteSheetHtml } from "./routeSheetPrint";
import { buildStoHtml } from "./stoPrint";
import { buildTdKitHtml, cleanCodes } from "./tdPrintSheets";
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
  // «kd» заполнен только при аналоге: «art» — применённый аналог; без «kd» «art» и есть артикул по КД.
  components: [
    { art: "ANALOG-777", name: "Провод ПВАМ 1,5", kd: "ПВАМ-1,5 ТУ", norm: "700", unit: "М." },
    { art: "ПОС-61 ГОСТ 21931-76", name: "Припой ПОС-61", kind: "Материал", norm: "70", unit: "г" },
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
  components: [
    { art: "ANALOG-777", name: "Провод ПВАМ 1,5", kd: "ПВАМ-1,5 ТУ", norm: "700", unit: "М." },
    { art: "39000038", name: "Контакт Mini-Fit Jr.", norm: "140", unit: "Шт." },
    // Провод из БД.ПРОВ: наименования нет, марка — в «art»; два цвета не сливаются в одну строку ВМ.
    { art: "ПВАМ 0,5 черный", name: "", kd: "", norm: "50", unit: "М." },
    { art: "ПВАМ 0,5 белый", name: "", kd: "", norm: "50", unit: "М." },
  ],
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
      notice_no: "", reason: "", workshop: "Цех электромонтажа", site: "ЦАР",
      created_at: "2026-10-20T10:00:00", created_by: "x", approved_at: null, annulled_at: null, ...over,
    },
    card: { id: "t", project: "713К.4", item_index: "КБ1", product_name: "Жгут 713К.4", revision: 4,
      document: { kit: { part_quantity: "700" }, operations: [cut, second, third] },
      td_ops: {
        "CUT_WIRE_auto::::": { profession: "Монтажник радиоэлектронной аппаратуры и приборов", grade: "2", source: "по умолчанию" },
        "SDR_TIN::XS1::A": { profession: "Паяльщик", grade: "4", source: "БД.ОП" },
      } } as unknown as ProjectTechcard,
    sto_sections: {
      "CUT_WIRE_auto::::": { section: "4.1", title: "Резка", norms: [
        { doc: "ГОСТ 23587-96", clauses: "п. 5.10", subject: "Монтаж электрический" },
        { doc: "ГОСТ 23592-96", clauses: "п. 4.2.2, 4.2.12", subject: "Монтаж электрический" },
      ] },
      "SDR_TIN::XS1::A": { section: "4.4", title: "Лужение и пайка", norms: [
        { doc: "ГОСТ 23587-96", clauses: "п. 5.5, 5.10", subject: "Монтаж электрический" },
        { doc: "ГОСТ 23585-79", clauses: "п. 11 (табл. 3), 12, 13, 16", subject: "Проводники" },
      ] },
      "INS_HSG::::": null,
    },
    changes: [{ change_no: 1, notice_no: "ИЗВ-1", reason: "Смена провода", designation: "ФРАК.10185.00007", approved_at: "2026-10-21T00:00:00", developer: "Иванов И.И." }],
    sto: { designation: "СТО ФРАК-001-2026", revision: "2", title: "Типовые дефекты и критерии приёмки" },
  };
}

const html = buildTdKitHtml(model());
const sheets = html.split('<section class="sheet">').slice(1);
const byHeading = (title: string): string => sheets.find((sheet) => sheet.includes(`<div class="content"><h2>${title}`)) ?? "";
const textOf = (value: string) => value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

it("комплект ТД заказчику не несёт рисунков стандартов (IPC туда нельзя; ГОСТ — только через customerFigures)", async () => {
  const { indexStdFigures, customerFigures } = await import("./stdFigures");
  const base = model();
  const document = base.card.document as { operations: Record<string, unknown>[] };
  const withSto = { ...base, card: { ...base.card, document: { ...document, operations: [
    { ...cut, card: { ...cut.card, defects: "4.3.14 Высота обжима вне допуска; 4.1.2 Длина вне допуска" } }, second, third,
  ] } } } as TdPrintModel;
  const kit = buildTdKitHtml(withSto);
  expect(kit).not.toContain("/api/techcards/std-figures");
  expect(kit).not.toMatch(/<img\b/i);
  const ipc = { id: "f", sto_code: "4.3.14", position: 1, role: "defect", source_kind: "ipc", caption: "IPC/WHMA-A-620E, рис. 3-12",
    content_type: "image/png", sha256: "c".repeat(64), width: 1, height: 1, size: 1, customer_allowed: false, created_at: "", created_by: "" } as const;
  expect(customerFigures(indexStdFigures([ipc]).get("4.3.14") ?? [])).toEqual([]);
});

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
  expect(tl).toContain("ООО «ЗЭП «ФРАКСИС» Цех электромонтажа, участок ЦАР");
  expect(buildTdKitHtml(model({ method_code: "88" }))).toContain("технологический процесс сборки");
  expect(buildTdKitHtml(model({ method_code: "80" }))).toContain("технологический процесс пайки");
});

it("ВТД перечисляет документы комплекта, СТО с ревизией и только те стандарты, что названы в операциях, — с пунктами", () => {
  const vtd = textOf(byHeading("Ведомость технологических документов"));
  for (const part of ["Маршрутная карта", "Операционные карты", "Операционная карта технического контроля", "Ведомость материалов", "Ведомость оснастки",
    "Лист регистрации изменений", "СТО ФРАК-001-2026, ред. 2", "разд. 4.1, 4.4"]) expect(vtd).toContain(part);
  // пункты одного стандарта из разных операций слиты без повторов; стандарт без пунктов не печатается
  expect(vtd).toContain("ГОСТ 23587-96 Монтаж электрический п. 5.5, 5.10");
  expect(vtd).toContain("ГОСТ 23592-96 Монтаж электрический п. 4.2.2, 4.2.12");
  expect(vtd).toContain("ГОСТ 23585-79 Проводники п. 11 (табл. 3), 12, 13, 16");
  expect(vtd).not.toContain("ГОСТ 23586-96");
  expect(vtd).not.toContain("72182.2");
  expect(html).not.toContain("REFERENCE");
});

it("МК: строки А/Б/К/М, номера 005, 010…, время в минутах, ссылка на СТО", () => {
  const mk = byHeading("Маршрутная карта");
  expect(mk).toContain("<td>А</td>");
  expect(mk).toContain("<td>Б</td>");
  expect(mk).toContain("<td>К</td>");
  expect(mk).toContain("<td>М</td>");
  expect(mk).toContain("ПВАМ 0,5 черный"); // провод без наименования — марка из БД.ПРОВ, строка «М»
  for (const number of ["005", "010", "015"]) expect(mk).toContain(`<td>${number}</td>`);
  expect(mk).toContain("<td>1,50</td>"); // 90 с подготовки
  expect(mk).toContain("<td>0,20</td>"); // 12 с на штуку
  expect(mk).toContain("Тпз, мин");
  expect(mk).toContain("СТО ФРАК-001-2026, разд. 4.1");
  // графа «Документы» — компактно: стандарт и пункты, по документу в строке, без пояснений в скобках
  expect(mk).toContain("ГОСТ 23587-96 п. 5.10\nГОСТ 23592-96 п. 4.2.2, 4.2.12");
  expect(mk).toContain("ГОСТ 23585-79 п. 11, 12, 13, 16");
  // Обозначение по КД — «kd» при аналоге, иначе «art»; аналог — ниже.
  expect(mk).toContain("<td>Провод ПВАМ 1,5, ПВАМ-1,5 ТУ\nаналог: ANALOG-777</td>");
  expect(mk).toContain("<td>Контакт Mini-Fit Jr., 39000038</td>");
  expect(mk).toContain("<td>ПВАМ 0,5 черный</td>");
});

it("МК: цех, участок и РМ в строке «А», профессия и разряд из снимка в строке «Б»", () => {
  const mk = byHeading("Маршрутная карта");
  expect(mk).toContain("<td>А</td><td>Цех электромонтажа</td><td>ЦАР</td><td>ИН-17</td><td>005</td>");
  expect(mk).toContain("<td>А</td><td>Цех электромонтажа</td><td>ЦАР</td><td></td><td>010</td>"); // у паяльной станции нет инв. №
  expect(mk).toMatch(/<td>Б<\/td>(<td><\/td>){4}<td>BZW-2\.0 \| Станок резки<\/td><td><\/td><td>Монтажник РЭА и приборов<\/td><td>2<\/td>/);
  expect(mk).toMatch(/<td>Паяльщик<\/td><td>4<\/td>/);
  expect(mk).toMatch(/<td>015<\/td>[\s\S]*?<td>Б<\/td>(<td><\/td>){6}<td><\/td><td><\/td><td>1<\/td>/); // разряда в снимке нет — пусто
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
  expect(first).toContain("Технические требования — по ГОСТ 23587-96, п. 5.10; ГОСТ 23592-96, п. 4.2.2, 4.2.12");
  expect(ok[1]).toContain("ГОСТ 23585-79, п. 11 (табл. 3), 12, 13, 16");
  expect(ok[2]).not.toContain("Технические требования"); // раздела СТО нет — нормы не придумываются
  expect(first).toContain("Исполнитель: Монтажник радиоэлектронной аппаратуры и приборов, разряд 2");
  expect(ok[1]).toContain("Исполнитель: Паяльщик, разряд 4");
  expect(ok[2]).toContain("Исполнитель: —, разряд —");
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

it("ВМ: обозначение по КД, аналог с разрешением и норма на изделие", () => {
  const vm = byHeading("Ведомость материалов");
  expect(vm).toContain("<td>Провод ПВАМ 1,5\nаналог: ANALOG-777</td><td>ПВАМ-1,5 ТУ</td>");
  expect(vm).toContain("<td>аналог вместо ПВАМ-1,5 ТУ — по разрешению; Материал; оп. 005, 010</td>");
  expect(vm).toContain("<td>Контакт Mini-Fit Jr.</td><td>39000038</td>"); // без аналога «art» — артикул по КД
  expect(vm).toContain("<td>ПВАМ 0,5 черный</td><td>ПВАМ 0,5 черный</td>");
  expect(vm).toContain("<td>2</td>"); // 700 + 700 м на партию 700 — 2 м на изделие
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

it("СТО печатается целиком: все 320 кодов дефектов, повтор шапки таблиц, действия и лист изменений", () => {
  const out = buildStoHtml(sto);
  const codes: string[] = [];
  const walk = (section: Sto["sections"][number]): void => { section.rows.forEach((row) => codes.push(row.code)); (section.subsections ?? []).forEach(walk); };
  sto.sections.forEach(walk);
  expect(codes).toHaveLength(320);
  for (const code of codes) expect(out).toContain(`<td class="code">${code}</td>`);
  expect(out).toContain("thead { display: table-header-group; }");
  expect(out).toContain("A4 landscape");
  expect(out).toContain("<th>Основание</th>");
  expect(out).toContain("Нормативные требования:</b> ГОСТ 23587-96, п. 5.10; ГОСТ 23592-96, п. 4.2.2, 4.2.12");
  expect(out).toContain("ГОСТ 3.1129-93, п. 6.3)");
  expect(out).toContain("Признак / критерий отбраковки");
  expect(out).toContain("<ol>");
  expect(out).toContain("Лист регистрации изменений");
  expect(out).toContain("Первая редакция");
});

it("сопроводительный лист: шапка, операции, места для отметок и принятие изделия", () => {
  const out = buildRouteSheetHtml(model({ change_no: 1 }), { serial: "SN-001", batch: "П-5", qty: "20" });
  for (const part of ["ФРАК.10185.00007", "SN-001", "П-5", "20", "<th>Участок</th><td>ЦАР</td>", "Исполнитель (ФИО, подпись)", "Партии материалов", "Отметка ОТК", "Отметка ВП", "Изделие принял: ОТК", "Автомат резка + опрессовка", "005", "010"]) expect(out).toContain(part);
  expect(out).not.toContain("CUT_WIRE");
});

it("коды БД.ОП убираются, наименование по КД с «_» и префикс с запятой — печатаются как надо", () => {
  expect(cleanCodes("INS_terminal_2,54-6,3 | Монтаж терминалов (A)")).toBe("Монтаж терминалов (A)");
  expect(cleanCodes("CUT_WIRE+1crimp+tin | Резка")).toBe("Резка");
  expect(cleanCodes("Кабельная сборка SHR-03V-S-B to Open-End 28AWG 100mm LED_STRIP"))
    .toBe("Кабельная сборка SHR-03V-S-B to Open-End 28AWG 100mm LED_STRIP");
});
