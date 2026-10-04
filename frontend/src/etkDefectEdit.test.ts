import { describe, expect, it } from "vitest";
import { defectsDraft, defectsStructured, serializeDefects, type DefectsEdit } from "./etkDefectEdit";
import { parseDefects } from "./etkOperation";

// Тексты дефектов из библиотеки (SUPPLYMAP БД.ОП-ТК) и сборки ЭТК (etk_builder._combined_defects), сокращённо.
const COMBINED = [
  "ТИПОВЫЕ ДЕФЕКТЫ (Критерии отбраковки / NG) для резки и зачистки:",
  "Согласно IPC/WHMA-A-620F (класс 3) и КД, браком на данном этапе считаются:",
  "1. Дефекты токопроводящей жилы (Самые критичные):",
  "Подрезка жил: Обрыв, глубокие надрезы или царапины на проволочках.",
  "Распушение жил: Потеря исходной структуры скрутки жилы.",
  "2. Дефекты изоляции:",
  "Смятие изоляции: Профиль провода деформирован.",
  "Рваный край изоляции: Срез неровный, имеет бахрому или заусенцы.",
  "3. Отклонения геометрии:",
  "Длина зачистки вне допуска: Слишком короткая или длинная зачистка.",
  "Опрессовка — нарушение геометрии зоны обжатия жилы:",
  "Отсутствие задней юбки на задней кромке обжимной втулки жилы. Приводит к подрезке крайних проволочек.",
  "Отсутствие или чрезмерный вылет жил : Торцы жил не выступают за край втулки.",
  "Лужение — дефекты покрытия (смачиваемости):",
  "Избыток припоя: Наплывы, «сосульки», капли на конце провода.",
].join("\n");
const GLUED = "ТИПОВЫЕ ДЕФЕКТЫ (Критерии отбраковки / NG) со ссылками на IPC/WHMA-A-620F:\n" +
  "1. Дефекты паяного соединения чашечки (Раздел 4.3):Наплывы на наружной поверхности: Избыток припоя вытек наружу.Недолив припоя: Чашечка заполнена менее чем на 75,0% объема.\n" +
  "2. Механические дефекты жил:Распушение жил: Проволочки торчат.\n";
const ONE_LINE = "Типовые дефекты (NG): «Недоусадка» (Трубка болтается или проворачивается); прожог ТУТ; смещение ТУТ по длине.";
const SENTENCES = "ТИПОВЫЕ ДЕФЕКТЫ (NG), IPC/WHMA-A-620F (Раздел 3 «Провода»):\nНадрезы, царапины или обрыв проволочек жилы.\n" +
  "Несоответствие длины зачистки чертежу. Смятие изоляции от губок.";
const CRLF = "1. Дефекты изоляции:\r\nСмятие изоляции: Профиль деформирован.\r\n2. Геометрия:\r\nДлина: Вне допуска.\r\n";

/** Что увидит ЭТК после сохранения: разбор сохранённого текста (без служебного `origin`). */
function shown(text: string) {
  const parsed = parseDefects(text);
  return { intro: parsed.intro, groups: parsed.groups.map((group) => ({ title: group.title, items: group.items })) };
}
function plain(draft: DefectsEdit) {
  return { intro: draft.intro, groups: draft.groups.map((group) => ({ title: group.title, items: group.items })) };
}

describe("defects text ↔ list", () => {
  it("returns every library format byte for byte when nothing was edited", () => {
    for (const text of [COMBINED, GLUED, ONE_LINE, SENTENCES, CRLF, ""]) {
      expect(serializeDefects(defectsDraft(text), text)).toBe(text);
    }
  });

  it("shows the same groups and defects as the ETK view", () => {
    const draft = defectsDraft(COMBINED);
    expect(draft.groups.map((group) => group.title)).toEqual([
      "Дефекты токопроводящей жилы (Самые критичные)", "Дефекты изоляции", "Отклонения геометрии",
      "Опрессовка — нарушение геометрии зоны обжатия жилы", "Лужение — дефекты покрытия (смачиваемости)",
    ]);
    expect(plain(draft)).toEqual(shown(COMBINED));
  });

  it("rewrites only the edited group and keeps every other line as it was", () => {
    const draft = defectsDraft(COMBINED);
    draft.groups[1].items[0].text = "Профиль провода сплющен роликами.";
    const saved = serializeDefects(draft, COMBINED);
    expect(shown(saved)).toEqual(plain(draft));
    expect(saved.split("\n")).toEqual(COMBINED.split("\n").map((line) =>
      line === "Смятие изоляции: Профиль провода деформирован." ? "Смятие изоляции: Профиль провода сплющен роликами." : line));
  });

  it("drops a deleted group, renumbers the headings and keeps the lead-in lines", () => {
    const draft = defectsDraft(COMBINED);
    draft.groups.splice(0, 1);
    const saved = serializeDefects(draft, COMBINED);
    expect(shown(saved)).toEqual(plain(draft));
    expect(saved).toContain("Согласно IPC/WHMA-A-620F (класс 3) и КД, браком на данном этапе считаются:");
    expect(saved).toContain("\n1. Дефекты изоляции:\n");
    expect(saved).toContain("\n2. Отклонения геометрии:\n");
    expect(saved).not.toContain("Подрезка жил");
    // Подписанные сборкой группы («Опрессовка — …») остаются своими строками, в т. ч. «жил : Торцы».
    expect(saved).toContain("Отсутствие или чрезмерный вылет жил : Торцы жил не выступают за край втулки.");
  });

  it("writes a new group and defect in the numbered format the builder reads", () => {
    const draft = defectsDraft(COMBINED);
    draft.groups.push({ title: "Маркировка", items: [{ name: "Смещение маркировки", text: "Бирка не на своём месте." }], origin: null });
    const saved = serializeDefects(draft, COMBINED);
    // Номер — следующий за последним номерным заголовком (подписанные сборкой «Опрессовка — …» без номера).
    expect(saved.endsWith("\n4. Маркировка:\nСмещение маркировки: Бирка не на своём месте.")).toBe(true);
    expect(saved.startsWith(COMBINED)).toBe(true);
    expect(shown(saved)).toEqual(plain(draft));
  });

  it("re-reads edits of the glued and one-line formats as edited", () => {
    const glued = defectsDraft(GLUED);
    glued.groups[0].items[1].name = "Недолив";
    const savedGlued = serializeDefects(glued, GLUED);
    expect(shown(savedGlued)).toEqual(plain(glued));
    expect(savedGlued).toContain("2. Механические дефекты жил:Распушение жил: Проволочки торчат.");

    const list = defectsDraft(ONE_LINE);
    list.groups[0].items.splice(1, 1);
    const savedList = serializeDefects(list, ONE_LINE);
    expect(shown(savedList)).toEqual(plain(list));
    expect(savedList.split("\n")[0]).toBe("Типовые дефекты (NG)");
  });

  it("starts an empty card with a numbered group", () => {
    const draft = defectsDraft("");
    draft.groups.push({ title: "Дефекты изоляции", items: [{ name: "Смятие", text: "От губок." }, { name: "", text: "" }], origin: null });
    expect(serializeDefects(draft, "")).toBe("1. Дефекты изоляции:\nСмятие: От губок.");
  });

  it("keeps names and causes the parser could split or glue on one line", () => {
    const draft = defectsDraft("");
    draft.groups.push({ title: "Пайка: чашечки", items: [
      { name: "Наплыв: снаружи", text: "Припой\nвытек." },
      { name: "холодная пайка", text: "" },
      { name: "Перегрев", text: "" },
    ], origin: null });
    const saved = serializeDefects(draft, "");
    expect(saved).toBe("1. Пайка, чашечки:\nНаплыв, снаружи: Припой вытек.\nХолодная пайка\nПерегрев");
    expect(shown(saved).groups[0].items.map((item) => item.name)).toEqual(["Наплыв, снаружи", "Холодная пайка", "Перегрев"]);
  });

  it("keeps Windows line ends of untouched groups", () => {
    const draft = defectsDraft(CRLF);
    draft.groups[1].items[0].text = "Короче нормы.";
    const saved = serializeDefects(draft, CRLF);
    expect(saved.startsWith("1. Дефекты изоляции:\r\nСмятие изоляции: Профиль деформирован.\r\n")).toBe(true);
    expect(shown(saved)).toEqual(plain(draft));
  });

  it("edits as a list only what parses into defects", () => {
    expect(defectsStructured("")).toBe(true);
    expect(defectsStructured(COMBINED)).toBe(true);
    expect(defectsStructured("Типовые дефекты: по IPC-A-620")).toBe(true);
    expect(defectsStructured("Типовые дефекты (NG):")).toBe(false);
  });
});
