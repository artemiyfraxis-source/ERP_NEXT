/* Русские подписи справочников «База данных» (аудит дизайна 10.10, БД-1, БД-5).
   Заголовки приходят с листов SUPPLYMAP как есть — с переносами строк, опечатками и английскими словами
   («Product Name», «Series», «DATASHEET», «N»). Бэкенд их не меняет, поэтому подпись и значение приводятся здесь,
   только для показа: в форме правки и в выгрузке поле остаётся под своим ключом. */

/** Заголовок листа без переносов, двойных пробелов и пробела перед запятой. */
export function cleanHeader(header: string): string {
  return String(header ?? "").replace(/\s+/g, " ").replace(/\s+,/g, ",").trim();
}

// Целые заголовки, которые нельзя починить общей заменой (ключ — после `cleanHeader`, без регистра).
const HEADERS: Record<string, string> = {
  "product name": "Наименование",
  "series": "Серия",
  "наменование": "Наименование",
  "cрок окупаемости, мес.": "Срок окупаемости, мес.",
  "максисмальная сила тока, а": "Максимальная сила тока, А",
  "ссылка на datasheet на сайте производителя": "Даташит на сайте производителя",
  "ссылка на datasheet": "Ссылка на даташит",
};

/** Слова внутри заголовка: единицы и латиница листа → русские. */
const WORDS: Array<[RegExp, string]> = [
  [/\(REEL\)/i, "(катушка)"],
  [/\(BAG\)/i, "(пакет)"],
  [/\bDATASHEET\b/i, "даташит"],
  [/, N$/, ", Н"],
  [/разьем/gi, "разъём"],
  [/разъема/gi, "разъёма"],
];

/** Подпись колонки справочника: русская, без переносов строк. */
export function headerLabel(header: string): string {
  const clean = cleanHeader(header);
  const whole = HEADERS[clean.toLocaleLowerCase("ru-RU")];
  if (whole) return whole;
  return WORDS.reduce((text, [pattern, to]) => text.replace(pattern, to), clean);
}

// Значения «Тип контакта» на листе БД.ТЕР — по-английски; на экране — как говорит цех.
const CONTACT_SEX: Array<[RegExp, string]> = [
  [/^female(\s*\(socket\))?$/i, "гнездо"],
  [/^male(\s*\(pin\))?$/i, "штырь"],
];

// «Цар» на листе БД.ОБ вперемешку с «ЦАР»: площадки пишутся заглавными, как в остальном ERP.
const SITES: Record<string, string> = { "цар": "ЦАР", "жук": "ЖУК" };

/** Значение ячейки справочника для показа (поиск, сортировка и выгрузка идут по нему же). */
export function valueLabel(header: string, value: string): string {
  const text = String(value ?? "");
  const key = cleanHeader(header).toLocaleLowerCase("ru-RU");
  if (key === "тип контакта") {
    for (const [pattern, to] of CONTACT_SEX) if (pattern.test(text.trim())) return to;
  }
  if (key === "расположение") return SITES[text.trim().toLocaleLowerCase("ru-RU")] ?? text;
  return text;
}

/** Подписи ключей схемы БД.ТЕР/ОБ/ОП/КОАКС (поля записи приходят под ключом схемы). */
export const FIELD_TITLES: Record<string, string> = {
  baseValue: "Для базы", obType: "Тип",
  opNumber: "Номер", opName: "Название", tOp: "Время операции, с", tPrep: "Время подготовки, с", tMachine: "Время машины, с/оп; с/м",
  terManufacturer: "Производитель", terSeries: "Серия", terComponent: "Наименование", connType: "Тип разъёма",
  terType: "Тип контакта", artISL: "Артикул ISL", artSAG: "Артикул SAG", terArticle: "Артикул контакта", terLPlus: "L+",
  terLMinus: "L−", terStep: "Шаг", terApplicator: "Аппликатор", terStrip: "Длина зачистки, мм",
  terCrimpHeight: "Высота обжима проводника, мм", terPullForceMin: "Усилие обрыва от, Н", terPullForceMax: "Усилие обрыва до, Н",
  // Карточка контакта по протоколу v5.2 (TerminalCard): выгрузка «Скачать для правки» пишет их под этими подписями.
  awgFrom: "От AWG", awgTo: "До AWG", mm2From: "Сечение от, мм²", mm2To: "Сечение до, мм²", terGender: "Пол",
  terArticleBag: "Артикул BAG (россыпь)", terMaterial: "Материал / покрытие", terMaxCurrent: "Макс. ток, А",
  terDatasheet: "Даташит", terInsCrimpHeight: "Высота обжима изоляции, мм", terInsOdFrom: "Диаметр изоляции от, мм",
  terInsOdTo: "Диаметр изоляции до, мм",
  coaxArticle: "Артикул", coaxType: "Тип/серия", coaxMfr: "Производитель", supplier: "Поставщик", coaxWire: "Провод", program: "Программа",
};

/* Смысл L+ / L− терминала БД.ТЕР (владелец 16.10.2026): генератор прибавляет L+ к длине между корпусами и вычитает L−
   из длины от краёв разъёмов, поэтому L+ + L− = длина корпуса. Длина контакта — не L+. */
export const TER_L_PLUS_HINT = "L+ — глубина конца жилы от торца корпуса со стороны провода";
export const TER_L_MINUS_HINT = "L− — от конца жилы до стыковочного торца";
const TER_HINTS: Record<string, string> = {
  terLPlus: TER_L_PLUS_HINT, "L+": TER_L_PLUS_HINT, terLMinus: TER_L_MINUS_HINT, "L-": TER_L_MINUS_HINT, "L−": TER_L_MINUS_HINT,
};
/** Подсказка к полю БД.ТЕР (ключ схемы или колонка листа), если она есть. */
export const terFieldHint = (field: string): string | undefined => TER_HINTS[cleanHeader(field)];

/** Русский заголовок поля записи: ключ схемы → карта, иначе заголовок листа. */
export const fieldTitle = (field: string) => FIELD_TITLES[field] ?? headerLabel(field);
