/*
 * Логика генератора техкарт — порт SUPPLYMAP TcGenInline.html (IIFE window.TcGen) без вёрстки.
 *
 * В SUPPLYMAP состояние формы жило в DOM (галочки, селекты, поля); здесь оно в GenState, а каждая
 * функция формы читает его вместо document.getElementById. Имена функций и правила оставлены как
 * в источнике (суффикс «_» — приватные функции TcGenInline), чтобы порт сверялся построчно.
 * Выход — тот же config, что форма отправляла portalTechmapGenerate, и его принимает
 * `POST /api/techcards/projects/{id}/build` (etk_builder.py читает ровно эти ключи).
 */
import { markLabel, markSpecs, markTubeSplit, type MarkMat, type MarkRow, type MarkSpec, type MarkTube } from "./tcGenMarks";
import { twistGroupProblems, twistGroupsOf, type TwistRow } from "./twistGroups";
import { pickTerminals, type TerminalPairs } from "./terminalPick";

/** `art` — what the row actually uses (selects, gauge, terminals, ETK); `kd` — the КД article it replaced, display only. */
export type Comp = { id: string; type: string; name: string; art: string; kd?: string; qty: number | string; length?: number };
export type TerRec = {
  id?: string; article: string; step: string; strip: string; lPlus: string; lMinus: string; applicator: string;
  awgFrom: string; awgTo: string; mm2From: string; mm2To: string; fields?: Record<string, string>;
  /** The same terminal in BAG packaging (БД.ТЕР «Артикул контакта (BAG)», protocol v5.2): found by it too. */
  articleBag?: string;
};
/** Запись БД.КОАКС — данные самого разъёма. D1–D3 у разъёма нет (владелец 16.10.2026): они у кабеля, в СПР.КАБ. */
export type CoaxRec = {
  article: string; type: string; wire: string; mfr: string; program: string;
  l1: string; l2: string; l3: string;
  lPlus: string; lMinus: string; pinType: string; shieldType: string; connArticle: string;
  /** «Центральный контакт» карточки разъёма (слова поставщика): «цанговый» — контакт не паяется. */
  centerContact?: string;
};
export type OpRec = { opNumber: string; opName: string; tOp: string; tPrep: string; tMachine: string };
export type Tmpl = { id: string; title: string; category: string };
export type WireDiaRow = Record<string, number>;
export type WireDia = {
  marks: Array<{ name: string; norm: string }>;
  byAwg: Record<string, WireDiaRow>; byGost: Record<string, WireDiaRow>; gostAwg: Record<string, number>;
} | null;
export type CoaxCableDia = { byCable: Record<string, { d1: string; d2: string; d3: string }> } | null;
export type CalcOp = { code: string; comp: string; n?: string; l?: string; req?: string };

export type GenData = {
  components: Comp[];
  assemblyInfo: Record<string, string>;
  partQty?: number;
  templates: Tmpl[];
  ops: OpRec[];
  terRecords: TerRec[];
  coaxRecords: CoaxRec[];
  wireDia: WireDia;
  coaxCableDia: CoaxCableDia;
  calcOps: CalcOp[];
  /** Материалы маркировки из БД.МАТ (tcGenMarks.markMatsFromRecords) — выбор «Вид / материал» у меток. */
  markMats?: MarkMat[];
  /** Кабели и провода из БД.ПРОВ (cableRecFromFields): число жил, цвета жил по порядку, экран. */
  cableRecords?: CableRec[];
};

/** Строка «Термоусадки на жгут». `auto` (владелец 17.10, 713К.4 КБ1): true — засев из состава, владелец её не трогал:
 *  трубка, которая уже стоит поверх разъёмов, на сростках, на заделке или на срезе оболочки, на ствол не идёт; false —
 *  строку задал владелец, она считается всегда; нет поля — строка формы до 17.10 (считается, как считалась).
 *  `compId` — строка состава или трубка БД.МАТ (`REF_TUBE_` + наименование). */
export type HsRow = { compId: string; len: string; cnt: string; auto?: boolean };
/** Трубка «Термоусадки на жгут» не из состава, а из справочника материалов (БД.МАТ). */
export const REF_TUBE_ = "bd-mat:";

export type Side = "A" | "B";
/** manual — «Ручная сборка» (manualAssembly.ts): свой список операций, формы кабелей и разъёмов не участвуют. */
export type Tab = "inter" | "complex" | "coax" | "manual";
export type Endpoint = {
  id: string; side: Side; connId: string; method: "crimp" | "solder"; methodAuto: boolean;
  sdrTmpl: string | null; termId: string; wireIdxs: number[]; otp: boolean; double: boolean;
  label: string; solderStrip: string; tutLen: string; stripLen?: string; tutOff?: boolean; fold?: boolean; perTerm?: number;
  /** Двойной обжим в отдельных контактах (владелец 14.10: «с одного контакта два провода»): контакты, где два провода в одном
   *  терминале. Весь разъём двойным обжимом — `double`. */
  doublePins?: number[];
  /** Припуск на пайку, мм: сколько провода уходит в чашку/на лепесток паяльного разъёма — прибавляется к длине реза. */
  solderAllow?: string;
  /** «Термоусадка поверх разъёма»: рукав на тыльной части разъёма (корпус + выход проводов). Поля необязательны — старые
      сохранённые состояния грузятся без них. shrinkMat пуст — строка ТТК листа (иначе первая ТУТ); shrinkLen пуст — 20 мм. */
  shrinkOn?: boolean; shrinkMat?: string; shrinkLen?: string;
  /** «Сборка корпуса после пайки» паяльного разъёма (владелец 15.10): детали корпуса надеваются на кабель до пайки, корпус
   *  собирается после усадки ТУТ на чашках. Нет поля — по типу разъёма (housingDefault_: D-Sub, круглые, коаксиальные). */
  housingOn?: boolean;
  pins?: Record<number, string>;
  /** «Распиновка проверена»: подпись проверенной распиновки (pinoutCheck.ts); другая подпись — флаг снят. */
  pinChecked?: string;
  /** Терминал выбран генератором по разъёму (terminalPick.ts); false — выбран вручную, генератор его не трогает. */
  termAuto?: boolean;
  /** «Оплётка ветви, отрезок» разъёма стороны А «Сложной сборки» (владелец 16.10, 713К.4: X1 120, X2 110 мм): длина куска
   *  оплётки на эту ветвь — отдельно от длины проводов; пусто — поровну от «Длины на изделие» оплётки; «0» — ветвь без оплётки. */
  braidLen?: string;
  /** «Ветвь по КД, мм» (владелец 18.10): длина ответвления от центра разветвления до торца соединителя по сборочному чертежу
   *  (сторона А — до переднего торца, В — до заднего). Только размер схемы жгута: длины резки и операции не меняет;
   *  пусто — размер считается по длинам проводов. */
  kdBranch?: string;
};
/** Один провод «разъём · контакт → разъём · контакт» (end — «А1»/«В1» как на карточке разъёма). */
export type ConnectionEnd = { end: string; conn: string; label: string; pin: number };
export type Connection = { wire: string; from: ConnectionEnd; to: ConnectionEnd; /** Экран кабеля с контакта на контакт. */ shield?: true };
/** `coreColors[k]` — цвет k-й жилы кабеля (канонические названия палитры wireColors.ts: «красный», «бело-синий»); пусто — как в наименовании. */
export type WireEntry = { idx: number; compId: string; length: number; qty: number; cores?: number; jacket?: string; coreColors?: string[];
  /** `nets[k]` — цепь k-й жилы (после жил — экраны), как её подписывают на чертеже («GND», «CAN_H»); пусто — без имени. */
  nets?: string[];
  /** «Термоусадка на срез оболочки» кабеля в оболочке (владелец 15.10: «это настройка провода, а не разъёма»): стороны,
   *  трубка из состава (пусто — первая ТУТ, иначе ТТК) и длина (пусто — 25 мм). Нет поля — по сторонам формы
   *  `GenState.jacketShrink` (формы, сохранённые до переноса в строку «Кабели»), иначе выключена. */
  jacketShrink?: CableJacketShrink };
export type CableJacketShrink = { A?: boolean; B?: boolean; mat: string; len: string };
/** Термоусадка на место разделки оболочки (владелец 15.10: «опционально с опциональной длиной, если длина не указывается —
 *  25 мм, тут можно выбрать из состава»): трубка на срез оболочки кабеля стороны. `mat` пуст — первая ТУТ состава (иначе
 *  ТТК), `len` пуст — 25 мм. */
export type JacketShrink = { on: boolean; mat: string; len: string };
export type WrapKind = "tape" | "braid" | "under";
export type WrapSlot = { mat: string; kind: WrapKind; len: string };
export type CoaxSide = { art: string; name: string } | null;
/** Способ сростка: опрессовка гильзой, пайка, ленточный обжим, ультразвуковая сварка (владелец 16.10). */
export type SpliceMethod = "sleeve" | "solder" | "band" | "ultrasonic";
/** Карта операции «Сростки» по способу (БД.ОП; УЗ сварка — US_WELD). */
export const SPLICE_TMPL: Record<SpliceMethod, string> = { solder: "SDR_W_W", sleeve: "PRS_НШВИ", band: "PRS_COPPER", ultrasonic: "US_WELD" };
/** Способы сростка для выбора в форме — по-русски. */
export const SPLICE_METHODS: ReadonlyArray<readonly [SpliceMethod, string]> = [
  ["sleeve", "Опрессовка гильзой"], ["solder", "Пайка"], ["band", "Ленточный обжим"], ["ultrasonic", "Ультразвуковая сварка"],
];
/** Ключ выбора гильзы для сростков без своей точки на схеме (заданы числом или ответвлением). */
export const SPLICE_REST_ = "*";
/** Длина зачистки хвоста под лужение по умолчанию, мм (владелец 16.10: «2–3 мм»). */
export const TAIL_STRIP_MM_ = "3";
/** Длина зачистки конца под сросток по умолчанию, мм (владелец 17.10: «5 мм»): пустая «Зачистка под сросток» — и в
 *  сохранённых раньше конфигах — даёт её, а не «A ?» на эскизе резки. */
export const SPLICE_STRIP_MM_ = "5";
/** Длина зачистки конца под сросток: из формы, иначе SPLICE_STRIP_MM_. */
export const spliceStripMm = (S: Pick<GenState, "spl">) => (S.spl?.strip || "").trim() || SPLICE_STRIP_MM_;
/** Длина зачистки хвоста под лужение: из формы, иначе TAIL_STRIP_MM_. */
export const tailStripMm = (S: Pick<GenState, "tailTin">) => (S.tailTin?.strip || "").trim() || TAIL_STRIP_MM_;
/** Хвост «сросток → облуженный свободный конец» режется станком BZW-5.0+Z одной операцией: резка, зачистка обоих концов,
 *  лужение хвоста (БД.ОП, ОБ.098). */
export const TAIL_CUT_CODE_ = "CUT_WIRE+2strip+tin";
/** Печать и резка маркировочной трубки на маркираторе ZCUT-RT660 (ОБ.102). */
export const MARK_CUT_CODE_ = "INS_MARK_TUBE_AUTO";
/** Резка оплётки (линейных материалов). */
export const BRAID_CUT_CODE_ = "CUT_NEYLON";
export type CoaxStripMode = "machine" | "separate";
export type CoaxMachineKey = "small" | "big";
/** Роторные станки резки с разделкой коаксиала (БД.ОБ, категория «Резка с разделкой»). Карточки CUT_COAX+1strip /
 *  +2strip перечисляют оба, малый — «кабель Ø до 5,5 мм»; в ЭТК остаётся выбранный (etk_builder `_machine_tooling`). */
export const COAX_MACHINES: Record<CoaxMachineKey, { model: string; inventory: string; name: string; label: string; hint: string }> = {
  small: { model: "BZW-886+Q2", inventory: "ОБ.094", name: "Станок резки провода с роторной зачисткой мал.", label: "BZW-886+Q2 (малый)", hint: "кабель Ø до 5,5 мм" },
  big: { model: "BZW-50X", inventory: "ОБ.095", name: "Станок резки провода с роторной зачисткой больш.", label: "BZW-50X (большой)", hint: "кабель Ø свыше 5,5 мм" },
};
/** Наибольший Ø оболочки кабеля для малого станка, мм — из карточки CUT_COAX+2strip («кабель Ø до 5,5 мм»). */
export const COAX_SMALL_MACHINE_MAX_MM = 5.5;
export type OpTimes = { tmpl: string; tPrep: string; tOp: string; tMachine: string };

/** Ответвление провода (владелец 12.10: «с одного контакта в два вывести»): на контакт `pin` разъёма `from` пришёл провод `main`,
 *  второй конец идёт на контакт `toPin` разъёма `to` отдельным проводом `extra` — через сросток или двойной обжим. */
export type Branch = { main: number; extra: number; mode: "splice" | "double"; from: string; pin: number; to: string; toPin: number };

/** Конец провода: жила `unit` строки «Кабели» `idx` на стороне `side` (tcGenSplices.ts). */
export type WireEnd = { idx: number; unit: number; side: Side };
/** Сросток (обжимная гильза, владелец 13.10): концы проводов, соединённые в одной точке, — провод 0,5 с разъёма и три
 *  провода 0,2 к разъёмам и в хвост. Концы в сростке не идут на контакты разъёмов своей стороны. */
export type Splice = { id: string; ends: WireEnd[] };
/** Конец в сростке или свободный конец («хвост» без разъёма) — на этой стороне провод не ждёт контакта. */
export function endJoined(S: Pick<GenState, "splices" | "tails">, idx: number, unit: number, side: Side): "splice" | "tail" | null {
  const hit = (e: WireEnd) => e.idx === idx && e.unit === unit && e.side === side;
  if ((S.splices || []).some((s) => s.ends.some(hit))) return "splice";
  return (S.tails || []).some(hit) ? "tail" : null;
}

export type GenState = {
  tab: Tab;
  wires: WireEntry[];
  nextWireIdx: number;
  /** Ответвления проводов (pinoutCheck.addBranch); нет ключа — ответвлений нет. */
  branches?: Branch[];
  /** Сростки и свободные концы, нарисованные вручную (tcGenSplices.ts); нет ключа — их нет. */
  splices?: Splice[];
  tails?: WireEnd[];
  /** Свои цвета жил, придуманные технологом («болотный» → #6b7a3a): название → hex для схемы, чертежа и ЭТК; нет ключа — их нет. */
  colorHex?: Record<string, string>;
  endpoints: Endpoint[];
  nextEndpointId: number;
  advancedLength: boolean;
  measureMode: "inner" | "outer" | "cut";
  drawingLength: number;
  sharedLengthText: string;
  lenFromCalc: boolean;
  rotor: boolean;
  crimpCut: boolean;
  crimpCutManual: boolean;
  crimpCutSide: string;
  twist: {
    enabled: boolean; mode: "pairs" | "all"; pairs: TwistRow[];
    wires: number[] | null; pitch: string; z0: string; dia: string; diaManual: boolean;
  };
  /** `method` — способ сростка (владелец 16.10: «основа это обжим»): нет поля — пайка (формы и карты до 16.10, SDR_W_W).
   *  `sleeves` — гильза из состава по сросткам: ключ — id сростка схемы (S.splices) или SPLICE_REST_ («все»/«остальные»).
   *  `band` — материал ленточного обжима из того же списка, необязателен. */
  spl: { enabled: boolean; count: string; wires: string; tut: string; tutMat: string; strip: string; fold: boolean;
    method?: SpliceMethod; sleeves?: Record<string, string>; band?: string };
  hs: { enabled: boolean; manual: boolean; rows: HsRow[] };
  mark: boolean;
  /** Метки по КД (tcGenMarks); пусто — в маркировку идут бирки из состава, как раньше. */
  markRows: MarkRow[];
  freeStrip: { enabled: boolean; len: string };
  cap: { enabled: boolean; tut: string; len: string };
  /** «Свободные концы» → «Зачистка и лужение хвостов» (владелец 16.10, 2–3 мм): концы S.tails зачищаются и лудятся — и при
   *  разъёмах стороны В. Нет поля (формы до 16.10) — выключено. Включено — заделка трубкой (cap) не делается. */
  tailTin?: { enabled: boolean; strip: string };
  wrap: { enabled: boolean; slots: WrapSlot[] };
  /** «Термоусадка на место разделки» по сторонам — форма до переноса в строку «Кабели» (WireEntry.jacketShrink): читается
   *  запасным значением для кабеля без своего поля и больше не пишется (setCableJacketShrink). */
  jacketShrink?: Partial<Record<Side, JacketShrink>>;
  seal: { enabled: boolean; mat: string; places: string };
  tin: { enabled: boolean; wires: number[] | null; stripLen: string };
  coax: {
    len: string; tutEnabled: boolean; tutLen: string; pinA: string; pinB: string; coverA: string; coverB: string;
    stripB: boolean; shield: string;
    /** Тип экрана стороны В (владелец 19.10: у разъёмов А и В он может быть разный); нет / пусто — как у А (`shield`). */
    shieldB?: string;
    sideA: CoaxSide; sideB: CoaxSide;
    /** Разделка (владелец 14.10): «machine» — резка с разделкой на роторном станке одной операцией, «separate» — резка,
     *  затем разделка А и В. Станок: "" — по Ø кабеля (coaxMachineAuto_), иначе выбор технолога. */
    stripMode: CoaxStripMode; machine: "" | CoaxMachineKey;
  };
  selectedTemplates: Record<string, string>;
  manualTemplates: string[];
  times: Record<string, OpTimes>;
  materialsSheet: boolean;
  /** Свой порядок операций (ключи opKey) — «Выше/Ниже» в схеме полуфабрикатов; пусто — порядок генератора. Уходит в
      build_config.opOrder и возвращается из него при открытии карты. */
  opOrder: string[];
};

export type OpSpec = {
  type: string; opKey: string; label: string; side?: Side; ord?: number; epId?: string; crimpEpId?: string; crimpEpIdB?: string;
  tmplHint?: string; cat?: string; termArt?: string; firstTerm?: boolean; wireIdxs?: number[];
  /** Карта, которую операция хочет по правилу, когда tmplHint — замена за её отсутствием (CUT_WIRE+2crimp → +1crimp). */
  tmplWant?: string;
  /** «Надеть» коакса: стороны с прижимным экраном — на них гайка, шайба и уплотнитель вместо втулки экрана. */
  clampSides?: Side[];
  /** Разделка концов: lenA/lenB — зачистка конца (у кабеля в оболочке — оболочка), coreA/coreB — зачистка жил кабеля в
   *  оболочке под обжим или пайку (ротор снимает оболочку и зачищает жилы одним проходом, владелец 15.10). */
  strip?: { a: boolean; lenA: string; b: boolean; lenB: string; coreA?: string; coreB?: string }; stripCount?: number;
  tutArt?: string; tutName?: string; tutCount?: number; tutSegs?: Array<{ len: string; count: number }>;
  harness?: boolean; units?: number; twistPairs?: number[][]; wrapKind?: "tape" | "braid"; under?: boolean; wrapArt?: string; wrapName?: string;
  wrapLen?: string; marks?: Array<{ art: string; name: string; qty: number } | MarkSpec>;
  sealArt?: string; sealName?: string; sealQty?: string; sealPlaces?: number; tin?: boolean;
  /** Коакс, резка с разделкой на станке: модель станка (COAX_MACHINES) и разделываемые концы. */
  machine?: string; stripSides?: Side[];
  /** Партия резки с обжимом (владелец 16.10): жил провода в этой операции, когда их меньше, чем в строке «Кабели» (провод
   *  ×2, одна жила — к SYM, другая — к SHF); все разъёмы стороны, что обжимает партия; терминалы партии для формы. */
  wireUnits?: Record<number, number>; crimpEpIds?: string[]; crimpEpIdsB?: string[]; crimpTerms?: string;
  /** Метка-трубка из состава: резка, надевание и усадка маркировки; `tutParts` — отрезки по позициям состава. */
  markTube?: boolean; tutParts?: Array<{ art: string; name: string; count: number; len: string }>;
  /** «Сложная сборка» (владелец 16.10): ПФ схемы генератора, в который входит операция (ключ блока `build_config.route`). */
  pf?: string;
  /** Резка проводов «Сложной сборки»: жилы партии — [строка «Кабели», жила], по ним стрелки к ПФ, что их берут. */
  cutUnits?: Array<[number, number]>;
  /** Монтаж оплётки ветви: оплётку отрезала «Резка оплётки» — материал у резки, здесь его нет. */
  wrapCut?: boolean;
  /** Гильзы сростков своей группы (ПФ «Пайка С1, С2»): позиция состава и число сростков на неё. */
  sleeveItems?: Array<{ art: string; name: string; count: number }>;
  /** Чья операция ПФ «Сложной сборки» — ветвь («А1 «5»») или группа сростков («С1, С2»): в ключе операции ЭТК. */
  branch?: string;
};

/** Схема полуфабрикатов, которую строит «Сложная сборка» (владелец 16.10): блоки — ПФ (`kit` — ПФ-комплект: партия резки,
 *  резка трубок, оплётки, маркировка — делается раз на изделие для нескольких ПФ), стрелки — что идёт из ПФ в ПФ. */
export type PfBlock = { key: string; name: string; kit?: boolean; final?: boolean };
export type PfEdge = { from: string; to: string; items: string[] };
export type PfGraph = { blocks: PfBlock[]; edges: PfEdge[] };

/** Провода партии резки, что идут в один ПФ, — одной строкой: общее начало марки один раз, потом цвета («НВ4 0,12 белый,
 *  синий — 2 шт»; «НВ4 0,2 красный ×2, черный ×2 — 4 шт»). `list` — [марка, штук на изделие]. */
export function wiresItem_(list: Array<[string, number]>): string {
  const words = list.map(([art]) => art.trim().split(/\s+/));
  let common = 0;
  const most = Math.min(...words.map((w) => w.length)) - 1;
  while (common < most && words.every((w) => w[common] === words[0][common])) common++;
  const counted = list.length > 1 && list.some(([, count]) => count > 1);
  const tails = list.map(([, count], i) => `${words[i].slice(common).join(" ")}${counted ? ` ×${count}` : ""}`);
  const head = words[0].slice(0, common).join(" ");
  return `${head ? `${head} ` : ""}${tails.join(", ")} — ${list.reduce((n, [, count]) => n + count, 0)} шт`;
}

/** Терминал в имени ПФ — серия, как её зовут в цеху: «SYM-001T-P0.6» → «SYM», «T-DS1071-SC600» → «T-DS1071». */
export function termShort_(art: string): string {
  const parts = art.trim().split("-");
  return parts[0].length >= 2 || parts.length < 2 ? parts[0] : `${parts[0]}-${parts[1]}`;
}

// ── Константы TcGenInline ─────────────────────────────────────────────────
export const JACKET_STRIP_MM_ = "20";
/** Длина рукава «Термоусадка поверх разъёма» по умолчанию, мм. */
export const SHRINK_LEN_MM_ = "20";
/** Длина трубки «Термоусадка на место разделки» по умолчанию, мм (владелец 15.10). */
export const JACKET_SHRINK_LEN_MM_ = "25";
/** Паяльные разъёмы с корпусом (кожух, хвостовик, гайка): D-Sub/DH/СНП, круглые, коаксиальные — «Сборка корпуса после
 *  пайки» у них включена по умолчанию. */
const HOUSING_CONN_RE_ = /\bd[-\s]?sub\b|\bdb[-\s]?\d|\bde[-\s]?9\b|\bdh[-\s]?\d|\bdhr\b|снп|gx[\s-]*\d|\blp[\s-]*\d|lemo|\bm(?:8|12|16)\b|2рм|онц|снц|\bрс[\s-]*\d|xlr|\bdin\b|кругл|circular|\bbnc\b|\bsma\b|\btnc\b|\bn[-\s]?type\b|\bfme\b|\buhf\b|pl[-\s]?259/i;
export const DIODE_RE_ = /tvs|диод|\d[.,]\d\s*ke\d|\bsmbj\d/i;
export const LUG_RE_ = /^\s*(?:нки|нви|нвки|ншви|нши|рпи|тмл)(?![а-яa-z])/i;
export const FOLD_STRIP_MM_ = "10";
const JACKETED_RE_ = /\b\d+\s*P(?:R|AIR)?\b|\b\d+\s*C\s*[*xх×]|\b[SFU]?\/?[SFU]TP\b|\bUSB\b|витая\s*пара|экранир/i;
/** Многожильный кабель в оболочке по стилю UL AWM (владелец 14.10: «UL2575 26AWG» без записи в БД.ПРОВ генератор резал
 *  как 4 отдельных провода): стили 2000–2999 и 20000–29999 — многопроводные (UL2464, UL2547, UL2575, UL2725, UL20276…);
 *  1000-е, 3000-е и 10000-е (UL1007, UL1061, UL3239, UL10368) — одиночный провод. */
export const UL_MULTICORE_RE_ = /\bUL\s*-?\s*2\d{3}\d?(?!\d)/i;
const DEFAULT_LEN_MM_ = "5";
const RJ_STRIP_MM_ = "40";
const SEAL_RE_ = /герметик|силагерм|компаунд|sealant/i;
const PROTECT_TUBE_RE_ = /тсп[\s-]*\d|стеклотруб/i;
export const WRAP_SLOTS_ = ["", "2", "3", "4"];
const CRIMP_CUT_MIN_WIRES_ = 300;
// Резка + обжим + лужение одним станком (BZW-5.0+Z) выгоднее трёх операций (CUT_WIRE+1strip 600 с + 5 с/пр.,
// PRS_TERMINAL_auto 1800 + 6, SDR_tin 120 + 5 — против 3600 + 4 по БД.ОП) уже с 90 проводов в партии; 300 у обжима
// при резке — тот же расчёт без лужения (CUT_WIRE_auto 180 + 3,5 и PRS_TERMINAL_auto против CUT_WIRE+1crimp 3600 + 4).
const CRIMP_TIN_CUT_MIN_WIRES_ = 90;
export const CRIMP_TIN_CODE_ = "CUT_WIRE+1crimp+tin";
/** Обжим обеих сторон на автомате резки: значение `crimpCutSide` и карта БД.ОП/шаблона (4,8 с/пр. против 4 у +1crimp). */
export const CRIMP_BOTH_ = "AB";
const CRIMP_BOTH_CODE_ = "CUT_WIRE+2crimp";
/** Скрутка экрана кабеля в оболочке (БД.ОП «Скрутка экрана»). */
const SHIELD_CODE_ = "TWST_SHILED";
/** Резка коаксиального кабеля без разделки (вкладка «Коакс», разделка отдельно): своя карта, не карта провода. */
export const COAX_CUT_CODE_ = "CUT_COAX_auto";
/** Карта «Монтаж корпуса» прижимного экрана (ERP-шаблон в techcard-library-erp.json). */
const CLAMP_HOUSING_CODE_ = "INS_housing_COAX_clamp";
/** Тип пина коакс-разъёма с цанговым центральным контактом: жила зажимается цангой, пайки центрального контакта нет. */
export const COAX_PIN_COLLET = "цанга";
/** Тип пина «обжим» (владелец 19.10): центральный контакт опрессовывается на жилу — своя операция, не пайка. */
export const COAX_PIN_CRIMP = "обжим";
/** Карта опрессовки центрального контакта; пока её нет в БД.ОП — карта опрессовки коакса (втулки), с пометкой. */
const COAX_PIN_CRIMP_CODE_ = "PRS_PIN_coax";
const COLLET_RE_ = /цанг/i;
/** Тип экрана разъёма на сборку: обжим втулки, пайка экрана, прижимной (зажим: оплётка отогнута на зажимное кольцо, гайка). */
export const COAX_SHIELD_CLAMP = "прижим";
/** Компрессионный разъём (F): кольцо обжимается компрессионным инструментом — как обжим втулки, но без матрицы HEX (19.10). */
export const COAX_SHIELD_COMPRESS = "компрессия";
/** Накручивающийся / резьбовой разъём: экран зажимает сам разъём при накручивании — операции по экрану нет (19.10). */
export const COAX_SHIELD_TWIST = "накрутка";
/** Push-On (самообжим): надевается без инструмента — операции по экрану нет (19.10). */
export const COAX_SHIELD_PUSH = "самообжим";
/** Микро-коакс (MHF, U.FL): экран зажимает корпус разъёма, гильзы нет — операции по экрану и HEX нет (владелец 19.10). */
export const COAX_SHIELD_BODY = "корпус";
export const COAX_SHIELD_OPTIONS: ReadonlyArray<readonly [string, string]> = [
  ["обжим", "Обжим втулки"], ["пайка", "Пайка экрана"], ["прижим", "Прижимной (зажим)"],
  ["компрессия", "Компрессионный"], ["накрутка", "Накручивающийся"], ["самообжим", "Push-On (самообжим)"],
  ["корпус", "Корпусом (без гильзы)"],
];
/** Типы экрана, у которых нет операции по экрану: разъём садится сам. */
const COAX_SHIELD_NO_OP_ = [COAX_SHIELD_TWIST, COAX_SHIELD_PUSH, COAX_SHIELD_BODY];
export type CoaxShieldKind = "обжим" | "пайка" | "прижим" | "компрессия" | "накрутка" | "самообжим" | "корпус" | "";
/** «Тип экрана» БД.КОАКС любыми словами → обжим / пайка / прижим / компрессия / накрутка / самообжим / корпус; не распознано (или пусто) — "" (тип не выбирается по базе). */
export function coaxShieldKind_(text: string | undefined | null): CoaxShieldKind {
  const t = String(text || "").toLowerCase();
  if (/прижим|зажим|clamp/.test(t)) return COAX_SHIELD_CLAMP;
  if (/пайк|паян|solder/.test(t)) return "пайка";
  if (/компресс|compress/.test(t)) return COAX_SHIELD_COMPRESS;
  if (/накру[тч]|резьб|twist/.test(t)) return COAX_SHIELD_TWIST;
  if (/самообжим|push-?on/.test(t)) return COAX_SHIELD_PUSH;
  if (/корпус/.test(t)) return COAX_SHIELD_BODY;
  if (/обжим|crimp/.test(t)) return "обжим";
  return "";
}

/** Русское название операции генератора по её `type` (как `OPERATION_TYPE_NAMES` на сервере): когда в конфиге нет `label`. */
const OP_TYPE_NAMES: Record<string, string> = {
  cutWire: "Резка", cutWireStrip: "Резка с разделкой", cutWireCrimp: "Резка с обжимом", cutTut: "Резка ТУТ",
  prsTerm: "Опрессовка терминалов", prsTermA: "Опрессовка терминалов (А)", prsTermB: "Опрессовка терминалов (В)",
  insTerm: "Монтаж терминалов", insTermA: "Монтаж терминалов (А)", insTermB: "Монтаж терминалов (В)",
  insHousing: "Монтаж в корпус", insTut: "Надеть ТУТ", heatTut: "Усадка ТУТ", insWrap: "Обмотка", insMark: "Маркировка",
  tin: "Лужение", twist: "Свивка", splice: "Сращивание", seal: "Герметизация", solderConn: "Пайка разъёма",
  stripCable: "Разделка кабеля", foldCore: "Загибка жил", endCap: "Установка заглушек", coaxCut: "Резка кабеля",
  coaxCutTut: "Резка ТУТ", coaxStrip: "Разделка коаксиала", coaxShield: "Опрессовка втулки экрана",
  coaxSolderShield: "Пайка экрана", coaxPin: "Пайка центрального контакта", coaxPinCrimp: "Опрессовка центрального контакта", coaxCapCrimp: "Монтаж крышки (обжим)",
  coaxCapScrew: "Монтаж крышки (винт)", coaxHousing: "Монтаж корпуса", coaxInsSleeve: "Монтаж втулки экрана",
  coaxInsTut: "Монтаж ТУТ", coaxHeat: "Усадка ТУТ",
};
export const opTypeName = (type: string) => OP_TYPE_NAMES[type] || "Операция";

const OP_CATEGORY_MAP: Record<string, string> = {
  cutWire: "Резка",
  prsTermA: "Опрессовка терминалов (А)",
  insTermA: "Монтаж терминалов (А)",
  prsTermB: "Опрессовка терминалов (В)",
  insTermB: "Монтаж терминалов (В)",
  twist: "Свивка",
  tin: "Лужение",
};

const SOLDER_CONN_MAP: Array<[RegExp, string]> = [
  [/^\s*(?:плата|печатная плата|pcb)(?![a-zа-я])/i, "SDR_PCB"],
  [/xt[\s-]*90/i, "SDR_XT90"], [/xt[\s-]*60/i, "SDR_XT60"], [/xt[\s-]*30/i, "SDR_XT30"],
  [/qs[\s-]*8/i, "SDR_XT90"],
  [/gx[\s-]*12|lp[\s-]*12|^\s*sa\d{3,4}\s*\//i, "SDR_GX12"], [/gx[\s-]*16|lp[\s-]*16/i, "SDR_GX16"],
  [/lemo/i, "SDR_LEMO"], [/mr[\s-]*10/i, "SDR_MR10"], [/\brca\b/i, "SDR_RCA"],
  [/usb[\s-]*c.*3[.,]2|type[\s-]*c.*3[.,]2/i, "SDR_USB_C_3.2"], [/usb[\s-]*c|type[\s-]*c/i, "SDR_USB_C_2.0"],
  [/usb[\s-]*a.*3[.,]2/i, "SDR_USB_A_3.2"], [/usb[\s-]*a/i, "SDR_USB_A_2.0"],
  [/aux|jack\s*3[.,]5|3[.,]5\s*mm/i, "SDR_AUX"],
  [/крокодил|crocodile|alligator/i, "SDR_крокодилы"],
  [/\bd[-\s]?sub\b|\bdb[-\s]?\d|db9|db15|db25/i, "SDR_DB"], [/\bdh[-\s]?\d|\bdhr\b/i, "SDR_DH"],
  [/lemo/i, "SDR_LEMO"],
  [/banana|банан/i, "SDR_BANANA"], [/^\s*(?:нк|пм)\s*\d/i, "SDR_НК"],
  [DIODE_RE_, "SDR_W_W"],
  [/7-0026|d\s*2[.,]5\s*[xх]\s*d?\s*0[.,]7/i, "SDR_AUX"],
];

// ── Чистые помощники ──────────────────────────────────────────────────────
/** «1 кабель», «4 кабеля», «5 кабелей». */
export function pluralRu(n: number, one: string, few: string, many: string) {
  const d = n % 10, dd = n % 100;
  return d === 1 && dd !== 11 ? one : d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
}

/** Свой порядок операций поверх порядка генератора: операции из `order` встают на места друг друга в его порядке,
    новые (их в `order` нет) остаются там, куда их поставил генератор. «Первая опрессовка» — у первой по новому порядку. */
export function applyOpOrder(ops: OpSpec[], order: string[]): OpSpec[] {
  if (!order.length) return ops;
  const rank = new Map(order.map((key, index) => [key, index]));
  const known = ops.filter((op) => rank.has(op.opKey)).sort((a, b) => rank.get(a.opKey)! - rank.get(b.opKey)!);
  let next = 0;
  const sorted = ops.map((op) => (rank.has(op.opKey) ? known[next++] : op));
  const firstPrs = sorted.find((op) => op.firstTerm !== undefined);
  return sorted.map((op) => (op.firstTerm === undefined ? op : { ...op, firstTerm: op === firstPrs }));
}

/** Размеры разделки кабеля в оболочке для названия операции: «оболочка 20 мм, жилы 3,5 мм»; у сторон разные —
 *  «оболочка 20 мм, жилы А 3,5 мм, В 5 мм»; длины нет («—») — «?». */
export function jacketCoreText_(jacket: { a: string; b: string }, core: { a: string; b: string }): string {
  const mm = (v: string) => (!v || v === "—" ? "? мм" : `${v.replace(".", ",")} мм`);
  const part = (title: string, v: { a: string; b: string }) => {
    const both = [v.a, v.b].filter(Boolean);
    if (!both.length) return "";
    return new Set(both).size === 1 ? `${title} ${mm(both[0])}` : `${title} А ${mm(v.a)}, В ${mm(v.b)}`;
  };
  return [part("оболочка", jacket), part("жилы", core)].filter(Boolean).join(", ");
}

/** Свой порядок операций сохранённой карты (`build_config.opOrder`); нет — порядок генератора. */
export function opOrderFromConfig(config: unknown): string[] {
  const raw = config && typeof config === "object" ? (config as { opOrder?: unknown }).opOrder : null;
  return Array.isArray(raw) ? raw.filter((key): key is string => typeof key === "string" && key !== "") : [];
}

export function parseDecimal_(s: unknown): number {
  return parseFloat(String(s ?? "").replace(",", "."));
}
const num0 = (s: unknown) => parseDecimal_(s) || 0;
const qtyNum = (c: Comp) => Number(String(c.qty).replace(",", ".")) || 0;
export function normalizeArt_(art: string) {
  return String(art || "").toLowerCase().replace(/[\s./()_-]/g, "");
}
export function detectSolderConn_(art: string): string | null {
  const s = String(art || "");
  for (const [re, code] of SOLDER_CONN_MAP) if (re.test(s)) return code;
  return null;
}
export function cableCoresFromName_(value: string) {
  const s = String(value || "");
  const m3 = s.match(/(\d+)\s*[xх×*]\s*(\d+)\s*[xх×*]\s*\d/i);
  if (m3) return Number(m3[1]) * Number(m3[2]);
  const m2 = s.match(/(?:^|[^\d.,])(\d+)\s*[xх×*]\s*(?:\d+(?:[.,]\d+)?(?!\s*[xх×*\d])|AWG)/i);
  if (m2) return Number(m2[1]);
  // «UL2464 24AWG 4C» — 4 проводника (C — conductors) в оболочке.
  const conductors = /(?:^|\s)(\d{1,2})\s*C(?![a-zа-я\d])/i.exec(s);
  return conductors && Number(conductors[1]) >= 2 ? Number(conductors[1]) : 1;
}
export function parseLengthVal_(s: string) {
  if (!s) return 0;
  const m = String(s).replace(",", ".").match(/[\d.]+/);
  return m ? parseFloat(m[0]) : 0;
}
export function twistTakeupFactor_(d: number, p: number) {
  return d > 0 && p > 0 ? Math.sqrt(1 + Math.pow(Math.PI * d / p, 2)) : 1;
}
export function wireQtyFromNorma_(norma: number, baseNorma: number, lengthMm: number) {
  if (norma > 0 && lengthMm > 0) return Math.max(1, Math.floor(norma * 1000 / lengthMm + 1e-9));
  return baseNorma > 0 && norma > 0 ? Math.max(1, Math.round(norma / baseNorma)) : 1;
}
export function twistZ0_(d: number, D: number, p: number, eps: number) {
  if (!(d > 0 && D > d && p > 0 && eps >= 1)) return 0;
  const thetaDeg = Math.atan(Math.PI * D / p) * 180 / Math.PI;
  const q = 0.25 + 0.0004 * thetaDeg * thetaDeg;
  const epsEff = 1 + q * (eps - 1);
  const x = D / d;
  return 120 / Math.sqrt(epsEff) * Math.log(x + Math.sqrt(x * x - 1));
}
export function twistPitchForZ0_(d: number, D: number, z0: number, eps: number) {
  if (!(d > 0 && D > d && z0 > 0 && eps >= 1)) return null;
  let lo = 2, hi = 2000;
  if (z0 <= twistZ0_(d, D, lo, eps)) return { p: lo, reachable: false };
  if (z0 >= twistZ0_(d, D, hi, eps)) return { p: hi, reachable: false };
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (twistZ0_(d, D, mid, eps) < z0) lo = mid; else hi = mid;
  }
  return { p: Math.round((lo + hi) / 2 * 10) / 10, reachable: true };
}
/** ε_r изоляции по марке (ключ — колонка таблицы Ø без пробелов и дефисов). Серии базы монтажных проводов, которых не
 *  было в СПР.КАБ: МС 16-13 — фторопласт, как МГТФ/МС (2,1); МС 15-11 и МК 26-13 — правило МС (2,1), своего
 *  источника нет; UL3239 — силиконовая резина (UL AWM 3239), как «Силикон» (3,0); ПуГВ — ПВХ, как ПГВА (4,0). Марки вне
 *  базы остаются: Ø у них нет, но ручной Ø со свивкой их ещё встречает. */
const EPS_BY_MARK: Record<string, number> = {
  мгтф: 2.1, мс: 2.1, мс1613: 2.1, мс1511: 2.1, мк2613: 2.1, ptfe: 2.1, m22759: 2.5, txl: 2.3, gxl: 2.3,
  ul1061: 3.5, ul1571: 3.5, ul1007: 3.5, ul1015: 3.5, мгшв: 3.5, нв4: 3.5, flry: 3.5,
  пвам: 4.0, пгва: 4.0, пугв: 4.0, силикон: 3.0, ul3239: 3.0, ркгм: 3.0,
};
/** Провод без Ø в базе монтажных (марка вне 12 серий или размера нет): видимая подсказка вместо молчаливого 0. */
export const WIRE_DIA_MISSING_ = "Ø провода не найден в базе монтажных проводов — задайте вручную";
/** Ø взят из старой таблицы СПР.КАБ (в базе монтажных его ещё нет, wire_dia «По старой таблице»): видно, что не подтверждён. */
export const WIRE_DIA_OLD_ = "Ø провода по старой таблице, не подтверждено источником — проверьте";
const WIRE_DIA_OLD_COLUMN_ = "По старой таблице";
const OLD_FLAG_ = "old:"; // WireDiaRow[OLD_FLAG_ + norm] = 1 — Ø этой марки в строке из старой таблицы
// Латинские буквы, набранные вместо кириллических двойников («MГТФ», «HB-4»), — как cables.hookup_series.
const LATIN_TWINS_: Record<string, string> = { a: "а", b: "в", c: "с", e: "е", h: "н", k: "к", m: "м", o: "о", p: "р", t: "т", x: "х", y: "у" };
/** Марка таблицы Ø в строке артикула: целиком, не частью другой марки (МНВ-4 — не НВ-4, ПуГВнг(А)-LS — не ПуГВ,
 *  МГТФЭ — не МГТФ), пробелы и дефисы внутри названия свободны (cables.hookup_series). Возвращает строку, в которой
 *  марка нашлась (с латиницей, заменённой на кириллицу, если иначе не нашлась), — по ней ищется сечение. */
export function findWireMark_<M extends { norm: string }>(s: string, marks: M[]): { mk: M; s: string } | null {
  const twin = s.replace(/[a-z]/g, (ch) => LATIN_TWINS_[ch] || ch);
  for (const text of twin === s ? [s] : [s, twin]) {
    for (const mk of marks) {
      if (!mk.norm) continue;
      const body = mk.norm.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s-]*");
      if (new RegExp(`(?:^|[^0-9a-zа-яё])${body}(?![0-9a-zа-яё(])`).test(text)) return { mk, s: text };
    }
  }
  return null;
}
export function isRjConn_(c: Comp) { return /8p8c|rj[\s-]*45/i.test(`${c.art || ""} ${c.name || ""}`); }
function endpointOpKey_(side: Side, ordinal: number, kind: "prs" | "ins" | "solder") {
  const base = side + ordinal;
  if (kind === "solder") return "solderConn" + base;
  if (kind === "ins") return "insTerm" + base;
  return "prsTerm" + base;
}
export const sideRu_ = (s: string) => (s === "B" ? "В" : "А");

/** Контакты провода у разъёма по жилам (владелец 12.10: жилы одного кабеля — на разные разъёмы стороны): k-я позиция —
 *  k-я жила (кабелей × жил), null — эта жила сюда не идёт. Запись — как раньше, «1, 2, 3» или «1-6» с первой жилы
 *  подряд; «11:1-9» — с 11-й жилы на контакты 1…9 (жилы 1–10 сюда не идут); «_» — пропуск одной жилы. Строка старого
 *  сохранения читается как прежде: жилы с первой подряд. Мусор отбрасывается, диапазон не длиннее 200. */
/** Контакты разъёма с двойным обжимом (`doublePins`), где сейчас два провода и больше — по строкам контактов `pins`. */
export function doubledPinsOf_(ep: Pick<Endpoint, "doublePins" | "pins"> | null | undefined): number[] {
  if (!ep?.doublePins?.length) return [];
  const count: Record<number, number> = {};
  Object.values(ep.pins || {}).forEach((text) => parseCorePins_(text).forEach((p) => { if (p != null) count[p] = (count[p] || 0) + 1; }));
  return [...new Set(ep.doublePins)].filter((p) => (count[p] || 0) >= 2).sort((a, b) => a - b);
}

export function parseCorePins_(value: unknown): Array<number | null> {
  const out: Array<number | null> = [];
  String(value ?? "").split(/[,;\s]+/).forEach((token) => {
    if (token === "_") { out.push(null); return; }
    const m = /^(?:(\d+):)?(\d+)(?:-(\d+))?$/.exec(token);
    if (!m) return;
    if (m[1]) {
      const at = Number(m[1]) - 1;
      if (at < 0 || at > 2000) return;
      while (out.length < at) out.push(null);
    }
    const a = Number(m[2]), b = m[3] ? Number(m[3]) : a, step = b >= a ? 1 : -1;
    for (let p = a, n = 0; n < 200; p += step, n++) { if (p > 0) out.push(p); if (p === b) break; }
  });
  while (out.length && out[out.length - 1] == null) out.pop();
  return out;
}

/** Обратно в строку: подряд с первой жилы — «1, 2, 3» (как раньше), после пропуска — «11:1, 2, 3». */
export function formatCorePins_(list: ReadonlyArray<number | null | undefined>): string {
  const parts: string[] = [];
  let gap = false;
  list.forEach((pin, k) => {
    if (pin == null || !(pin > 0)) { gap = true; return; }
    parts.push(gap ? `${k + 1}:${pin}` : String(pin));
    gap = false;
  });
  return parts.join(", ");
}

/** Распиновка провода в порядке записи: «3, 1, 2» → [3, 1, 2], «1-6» → 1…6, «6-1» → 6…1. Повторы остаются (двойной
 *  обжим: «1, 1, 2, 2»), мусор и пропуски жил отбрасываются, диапазон не длиннее 200 (etk_builder._pins — то же, но для
 *  рисунка). Контакты разъёма без привязки к жилам — parseCorePins_ без пропусков. */
export function parsePinList_(value: unknown): number[] {
  return parseCorePins_(value).filter((p): p is number => p != null);
}

/** Запись провода из БД.ПРОВ, нужная генератору: марка, число жил, цвета жил по порядку и экран. */
export type CableRec = { mark: string; cores: number; colors: string[]; screen: string };
/** Строка БД.ПРОВ (поля по шапке листа) → CableRec; без марки — null. */
export function cableRecFromFields(f: Record<string, string>): CableRec | null {
  const field = (name: string) => String(Object.entries(f).find(([key]) => key.trim().toLowerCase() === name.toLowerCase())?.[1] ?? "").trim();
  const mark = field("Марка");
  if (!mark) return null;
  const cores = parseInt(field("Число жил"), 10);
  const colors = field("Цвета").split(/[,;]+/).map((s) => s.trim()).filter(Boolean);
  return { mark, cores: cores > 0 ? cores : 0, colors, screen: field("Экран") };
}
/** Экранирован ли кабель по записи БД.ПРОВ: «Экран» заполнен и не «нет»/«—». */
export const cableScreened_ = (rec: CableRec | null) => !!rec && !!rec.screen && !/^(?:нет|без|-|—|–|0)$/i.test(rec.screen.trim());

/** calcCutMm_ (TcGenInline.html:2295): «L=…мм» из требований строки КБn, иначе колонка L (м). */
export function calcCutMm_(o: CalcOp) {
  const m = /L\s*=\s*(\d+(?:[.,]\d+)?)\s*мм/i.exec(o.req || "");
  if (m) return parseDecimal_(m[1]);
  const l = parseDecimal_(o.l);
  return l > 0 ? Math.round(l * 1000) : 0;
}

// ── Начальное состояние (init + populateComponentSelects + сидинги) ─────────
export function emptyState(): GenState {
  return {
    tab: "inter", wires: [], nextWireIdx: 0, endpoints: [], nextEndpointId: 0, advancedLength: false,
    measureMode: "inner", drawingLength: 0, sharedLengthText: "", lenFromCalc: false,
    rotor: true, crimpCut: false, crimpCutManual: false, crimpCutSide: "",
    twist: { enabled: false, mode: "pairs", pairs: [], wires: null, pitch: "", z0: "", dia: "", diaManual: false },
    spl: { enabled: false, count: "1", wires: "3", tut: "", tutMat: "", strip: "", fold: false, method: "sleeve" },
    hs: { enabled: false, manual: false, rows: [] },
    mark: false,
    markRows: [],
    freeStrip: { enabled: false, len: "" },
    cap: { enabled: true, tut: "", len: "" },
    tailTin: { enabled: false, strip: TAIL_STRIP_MM_ },
    wrap: { enabled: false, slots: WRAP_SLOTS_.map(() => ({ mat: "", kind: "tape" as WrapKind, len: "" })) },
    seal: { enabled: false, mat: "", places: "" },
    tin: { enabled: false, wires: null, stripLen: "5" },  // зачистка под лужение по умолчанию 5 мм (владелец 12.10.2026)
    coax: {
      len: "", tutEnabled: true, tutLen: "", pinA: "пайка", pinB: "пайка", coverA: "", coverB: "",
      stripB: false, shield: "обжим", sideA: null, sideB: null, stripMode: "machine", machine: "",
    },
    selectedTemplates: {}, manualTemplates: [], times: {}, materialsSheet: false, opOrder: [],
  };
}

/** Генератор над данными D и состоянием S. Методы — функции TcGenInline с теми же правилами. */
export function makeGen(D: GenData, S: GenState) {
  const comps = () => D.components || [];
  const getCompById = (id: string | null | undefined) => (id ? comps().find((c) => c.id === id) || null : null);
  const getByTypes = (types: string[]) => comps().filter((c) => {
    const lc = (c.type || "").toLowerCase();
    return types.some((t) => lc.includes(t));
  });
  const isComplex_ = () => S.tab === "complex";
  const activeWires_ = () => S.wires.filter((w) => w.compId);
  const epById_ = (id: string) => S.endpoints.find((e) => e.id === id) || null;
  const endpointsOnSide_ = (side: Side) => S.endpoints.filter((e) => e.side === side);
  const endpointOrdinal_ = (ep: Endpoint) => endpointsOnSide_(ep.side).indexOf(ep) + 1;
  const firstCrimpEndpoint_ = (side: Side) => endpointsOnSide_(side).find((e) => e.method === "crimp") || null;
  const epPer_ = (ep: Endpoint | null) => (isComplex_() ? Math.max(2, parseInt(String(ep?.perTerm ?? ""), 10) || 2) : 2);
  /** Контакты разъёма с двойным обжимом, где сейчас действительно два провода (один терминал на двоих). */
  const epDoublePins_ = (ep: Endpoint | null) => doubledPinsOf_(ep);

  /** Длина зачистки обжимного конца (владелец 14.10: «под каждый разъём длину зачистки; если записана — из БД»): введённая у
   *  разъёма (`stripLen`) главнее длины терминала из БД.ТЕР; нет ни той ни другой — «—». */
  function epStripLen_(e: Endpoint | null | undefined): string {
    if (!e) return "—";
    const own = (e.stripLen || "").trim();
    if (own) return own;
    const t = e.termId ? getCompById(e.termId) : null;
    return ((t ? findTerRecord_(t.art) : null)?.strip || "").trim() || "—";
  }

  function findTerRecord_(art: string | undefined): TerRec | null {
    if (!art) return null;
    const records = D.terRecords || [];
    // REEL or BAG article of one row (owner 12.10.2026); the stored spelling is kept, the match ignores it.
    const articles = (r: TerRec) => [r.article, r.articleBag].filter((a): a is string => !!a);
    const exact = String(art).toLowerCase().trim();
    const norm = normalizeArt_(art);
    let found = records.find((r) => articles(r).some((a) => a.toLowerCase().trim() === exact));
    if (!found && norm.length >= 3) found = records.find((r) => articles(r).some((a) => normalizeArt_(a) === norm));
    if (!found) {
      const words = String(art).trim().split(/\s+/);
      if (words.length > 1) {
        const normNoMfr = normalizeArt_(words.slice(1).join(" "));
        if (normNoMfr.length >= 3) found = records.find((r) => articles(r).some((a) => normalizeArt_(a) === normNoMfr));
      }
    }
    return found || null;
  }

  function routedWireIdxs_(ep: Endpoint) {
    const allIdx = activeWires_().map((w) => w.idx);
    if (!isComplex_() || !ep.wireIdxs || !ep.wireIdxs.length) return allIdx.slice();
    const set = new Set(ep.wireIdxs);
    return allIdx.filter((i) => set.has(i));
  }
  const endKey_ = (ep: Endpoint) => sideRu_(ep.side) + endpointOrdinal_(ep);

  /** Соединения жила за жилой: у k-й жилы провода — её контакт на первом разъёме (А раньше В) ↔ её контакт на каждом
   *  следующем разъёме. Жилы одного кабеля расходятся по разъёмам стороны (жилы 1–10 → В1, 11–19 → В2); жила с контактом
   *  на двух разъёмах одной стороны — ветвь «Y». Перекрёст задаётся порядком: А «1-6», В «3, 1, 2, 4, 5, 6» → 1→3, 2→1,
   *  3→2… Жилы с контактом только на одном разъёме — без пары, предупреждение. */
  function connections_(): { list: Connection[]; warnings: string[] } {
    const list: Connection[] = [], warnings: string[] = [];
    const ordered = (["A", "B"] as Side[]).flatMap((side) => endpointsOnSide_(side));
    const end = (ep: Endpoint, pin: number): ConnectionEnd => {
      const conn = getCompById(ep.connId);
      return { end: endKey_(ep), conn: conn ? (conn.art || conn.name || "") : "", label: isComplex_() ? (ep.label || "").trim() : "", pin };
    };
    activeWires_().forEach((w) => {
      const ends = ordered.filter((ep) => routedWireIdxs_(ep).includes(w.idx))
        .map((ep) => ({ ep, cores: corePinsOf_(ep, w.idx) })).filter((x) => x.cores.some((p) => p != null));
      if (ends.length < 2) return;
      const comp = getCompById(w.compId);
      const wire = comp ? (comp.art || comp.name || "") : "";
      const n = Math.max(...ends.map((x) => x.cores.length));
      let paired = 0, shared = 0;
      for (let k = 0; k < n; k++) {
        const at = ends.filter((x) => x.cores[k] != null);
        if (at.length < 2) continue;
        shared++;
        // Экран на контактах двух разъёмов — тоже соединение; на одном (заземлён с одного конца) — не ошибка.
        const shield = isShieldPos_(w, k);
        if (!shield) paired++;
        const name = shield ? `${wire || "Кабель"} · экран` : wire;
        at.slice(1).forEach((x) => list.push({ wire: name, from: end(at[0].ep, at[0].cores[k]!), to: end(x.ep, x.cores[k]!), ...(shield ? { shield: true as const } : {}) }));
      }
      // Ни одна жила не стоит на двух разъёмах: кабель расходится по разъёмам, у каждой жилы свой контакт — пар нет и
      // сравнивать нечего (по порядку здесь ничего не связывается).
      if (!shared) return;
      const [from, ...rest] = ends;
      const count = (x: typeof from) => x.cores.filter((p, k) => p != null && !isShieldPos_(w, k)).length;
      const onRest = rest.reduce((s, x) => s + count(x), 0);
      if (paired !== count(from) || paired < Math.max(...ends.map(count))) {
        warnings.push(`${wire || "Провод"}: на ${endKey_(from.ep)} ${count(from)} конт., на ${rest.map((x) => endKey_(x.ep)).join(" + ")} ${onRest} — ` +
          `соединения по порядку только для ${paired}.`);
      }
    });
    return { list, warnings };
  }

  // ── Списки состава ──
  function bomLists() {
    const terms = getByTypes(["терминал", "terminal", "контакт"]);
    const conns = getByTypes(["соед", "conn", "разъ", "корпус", "housing"]);
    comps().forEach((c) => {
      if (!conns.includes(c) && /banana|банан|^\s*(?:нк|пм)\s*\d|^\s*(?:плата|печатная плата|pcb)(?![a-zа-я])/i.test(`${c.art || ""} ${c.name || ""}`)) conns.push(c);
      if (!conns.includes(c) && DIODE_RE_.test(`${c.art || ""} ${c.name || ""}`)) conns.push(c);
    });
    comps().forEach((c) => { if (!terms.includes(c) && LUG_RE_.test(`${c.art || ""} ${c.name || ""}`)) terms.push(c); });
    return { terms, conns };
  }

  function wireComps_() {
    const byType = getByTypes(["провод", "wire", "кабель", "cable"]);
    const looksWire = (c: Comp) => /u\/?utp|f\/?utp|awg|мм2|мм²|\dx\d+x\d|пвам|мгтф|кабель|провод/i.test(`${c.art || ""} ${c.name || ""}`);
    return comps().filter((c) => byType.includes(c) || (looksWire(c) && !/^маркир/i.test(c.type || "")));
  }

  /** Запись БД.ПРОВ позиции состава: марка совпадает с артикулом или наименованием (без регистра, пробелов, точек),
   *  иначе самая длинная марка, входящая в артикул/наименование (не короче 4 знаков). */
  const cableRecCache_ = new Map<string, CableRec | null>();
  function cableRecOf_(c: Comp | null): CableRec | null {
    if (!c || !(D.cableRecords || []).length) return null;
    if (cableRecCache_.has(c.id)) return cableRecCache_.get(c.id)!;
    const recs = D.cableRecords || [];
    const keys = [c.art, c.name].map((s) => normalizeArt_(s || "")).filter(Boolean);
    let found = recs.find((r) => keys.includes(normalizeArt_(r.mark))) || null;
    if (!found) {
      const text = keys.join("|");
      found = recs.filter((r) => { const m = normalizeArt_(r.mark); return m.length >= 4 && text.includes(m); })
        .sort((a, b) => normalizeArt_(b.mark).length - normalizeArt_(a.mark).length)[0] || null;
    }
    cableRecCache_.set(c.id, found);
    return found;
  }
  const wireRec_ = (w: WireEntry) => cableRecOf_(w.compId ? getCompById(w.compId) : null);
  /** Жил в кабеле: заданное в строке «Кабели», иначе из наименования («4x0,5»), иначе «Число жил» записи БД.ПРОВ. */
  function wireCores_(w: WireEntry) {
    if (w.cores) return w.cores;
    const c = w.compId ? getCompById(w.compId) : null;
    if (!c) return 1;
    const byName = cableCoresFromName_(`${c.name || ""} ${c.art || ""}`);
    if (byName > 1) return byName;
    const rec = cableRecOf_(c);
    return rec && rec.cores > 1 ? rec.cores : byName;
  }
  const wireScreened_ = (w: WireEntry) => {
    const c = w.compId ? getCompById(w.compId) : null;
    return cableScreened_(cableRecOf_(c)) || (!!c && /экран|shield|sheld|\b(?:S|SF|F)\/?[UF]TP\b|-C[YD]\b/i.test(`${c.name || ""} ${c.art || ""}`));
  };
  /** Кабель по стилю UL AWM 2xxx, у которого нет записи БД.ПРОВ с «Число жил» (запись решает сама). */
  function isUlMulticore_(c: Comp | null) {
    return !!c && UL_MULTICORE_RE_.test(`${c.name || ""} ${c.art || ""}`) && !((cableRecOf_(c)?.cores || 0) > 0);
  }
  function isJacketedCable_(w: WireEntry) {
    if (wireCores_(w) > 1) return true;
    const c = w.compId ? getCompById(w.compId) : null;
    return !!c && (JACKETED_RE_.test(`${c.name || ""} ${c.art || ""}`) || isUlMulticore_(c));
  }
  /** Жил в строке «Кабели» всего: кабелей × жил (провод без оболочки — одна жила на штуку). */
  const unitCount_ = (w: WireEntry) => Math.max(1, Math.round(Number(w.qty) || 1)) * Math.max(1, wireCores_(w) || 1);
  /** Цвета жил по порядку: выбранный на палитре, иначе из «Цвета» записи БД.ПРОВ — когда их столько же, сколько жил
   *  в кабеле (у монтажного провода «Цвета» — какие бывают, а не по жилам). "" — цвет из наименования. */
  function coreColors_(w: WireEntry): string[] {
    const n = unitCount_(w), per = Math.max(1, wireCores_(w));
    const rec = per > 1 ? wireRec_(w) : null;
    const fromRec = rec && rec.colors.length === per ? rec.colors : [];
    return Array.from({ length: n }, (_, k) => w.coreColors?.[k] || fromRec[k % per] || "");
  }
  /** Контакты жил провода `idx` у разъёма: k-я — контакт k-й жилы или null (после жил — экраны, shieldCount_). */
  const corePinsOf_ = (ep: Endpoint, idx: number) => parseCorePins_(ep.pins?.[idx]);
  /** Экранов у строки «Кабели»: у экранированного кабеля в оболочке — по одному на кабель. Экран (дренаж) можно завести
   *  на контакт, как жилу: в строке распиновки он стоит после всех жил — позиция unitCount_ + номер кабеля. */
  const shieldCount_ = (w: WireEntry) => (isJacketedCable_(w) && wireScreened_(w) ? Math.max(1, Math.round(Number(w.qty) || 1)) : 0);
  const isShieldPos_ = (w: WireEntry, k: number) => k >= unitCount_(w) && k < unitCount_(w) + shieldCount_(w);
  /** Жил провода, которые приходят на разъём: по распиновке — сколько жил с контактом здесь (экран не в счёт);
   *  распиновки у разъёма нет — все жилы (кабелей × жил), как раньше. */
  function endUnits_(ep: Endpoint, w: WireEntry) {
    const pinned = corePinsOf_(ep, w.idx).filter((p, k) => p != null && !isShieldPos_(w, k)).length;
    return pinned || unitCount_(w);
  }
  /** Контакты экранов провода у разъёма (экран, заведённый на контакт, — ещё один терминал или ещё одна пайка). */
  const shieldPinsAt_ = (ep: Endpoint, w: WireEntry) =>
    corePinsOf_(ep, w.idx).flatMap((p, k) => (p != null && isShieldPos_(w, k) ? [p] : []));
  /** Концы кабелей в оболочке на стороне: разъёмы стороны, куда идут жилы кабеля (по порядку), экранирован ли он,
   *  незадействованные на этой стороне жилы (номера с 0) — когда распиновка задана на всех его разъёмах стороны — и
   *  экраны, заведённые на контакт (разъём и контакт). */
  function cableEnds_(side: Side) {
    return activeWires_().filter(isJacketedCable_).flatMap((w) => {
      const eps = endpointsOnSide_(side).filter((ep) => routedWireIdxs_(ep).includes(w.idx));
      if (!eps.length) return [];
      const lists = eps.map((ep) => corePinsOf_(ep, w.idx));
      const unused = lists.every((list) => list.some((p, k) => p != null && !isShieldPos_(w, k)))
        ? Array.from({ length: unitCount_(w) }, (_, k) => k).filter((k) => lists.every((list) => list[k] == null)) : [];
      const shieldPins = eps.flatMap((ep) => shieldPinsAt_(ep, w).map((pin) => ({ ep, pin })));
      return [{ w, eps, screened: wireScreened_(w), unused, shields: shieldCount_(w), shieldPins }];
    });
  }
  const isRjEndpoint_ = (e: Endpoint) => { const c = e.connId ? getCompById(e.connId) : null; return !!c && isRjConn_(c); };
  const allRjEndpoints_ = () => S.endpoints.length > 0 && S.endpoints.every(isRjEndpoint_);
  function rjCableCat_() {
    const s = comps().map((c) => `${c.name || ""} ${c.art || ""}`).join(" ");
    return /cat\s*[678]/i.test(s) ? "cat6/7" : "cat5";
  }
  function bomTut_() {
    const all = comps().filter((c) => {
      const s = `${c.type || ""} ${c.name || ""} ${c.art || ""}`;
      return /тут|ттк|термоусад|трубк|shrink/i.test(s) && !PROTECT_TUBE_RE_.test(s);
    });
    const plain = all.filter((c) => !/ттк/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`));
    return plain[0] || all[0] || null;
  }
  /** Строки листа, годные в термоусадку поверх разъёма: ТТК и ТУТ (без защитных трубок). */
  function shrinkCandidates_() {
    return comps().filter((c) => /тут|ттк|термоусад|shrink/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`)
      && !PROTECT_TUBE_RE_.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`));
  }
  const tubeDia_ = (c: Comp) => { const m = /(\d+(?:[.,]\d+)?)\s*\/\s*\d/.exec(`${c.name || ""} ${c.art || ""}`); return m ? parseDecimal_(m[1]) : 99; };
  const byTubeDia_ = (list: Comp[]) => list.slice().sort((a, b) => tubeDia_(a) - tubeDia_(b));
  const isGlueSleeve_ = (c: Comp) => /ттк|клеев/i.test(`${c.name || ""} ${c.art || ""}`);
  function defaultShrinkComp_() {
    const all = shrinkCandidates_();
    return all.find(isGlueSleeve_) || all[0] || null;
  }
  /** Рукав поверх разъёма этого конца или null, если опция выключена или в листе нет ТУТ/ТТК. */
  function shrinkOf_(e: Endpoint): { comp: Comp; len: string } | null {
    if (!e.shrinkOn || !e.connId) return null;
    const comp = (e.shrinkMat ? getCompById(e.shrinkMat) : null) || defaultShrinkComp_();
    return comp ? { comp, len: (e.shrinkLen || "").trim() || SHRINK_LEN_MM_ } : null;
  }
  /** Трубка на место разделки по умолчанию: ТУТ состава (на срез оболочки клеевая не нужна), иначе ТТК. */
  function defaultJacketShrinkComp_() {
    const all = shrinkCandidates_();
    return all.find((c) => !isGlueSleeve_(c)) || all[0] || null;
  }
  /** «Термоусадка на срез оболочки» кабеля: своё поле строки «Кабели», иначе — по сторонам старой формы; не кабель в
   *  оболочке или нигде не включена — null. */
  function cableJacketShrink_(w: WireEntry): CableJacketShrink | null {
    if (!isJacketedCable_(w)) return null;
    if (w.jacketShrink) return w.jacketShrink;
    const old = S.jacketShrink;
    if (!old?.A?.on && !old?.B?.on) return null;
    const first = old.A?.on ? old.A : old.B!;
    return { A: !!old.A?.on, B: !!old.B?.on, mat: first.mat || "", len: first.len || "" };
  }
  /** Трубки на срез оболочки по сторонам: сторона, трубка, длина отрезка и отрезков на изделие (по трубке на каждый кабель
   *  стороны). Кабели с одной трубкой и длиной на стороне — одной строкой. */
  function jacketShrinkItems_(): Array<{ side: Side; comp: Comp; len: string; count: number }> {
    const out: Array<{ side: Side; comp: Comp; len: string; count: number }> = [];
    (["A", "B"] as Side[]).forEach((side) => cableEnds_(side).forEach(({ w }) => {
      const own = w.jacketShrink, old = own ? null : S.jacketShrink?.[side];
      if (own ? !own[side] : !old?.on) return;
      const mat = (own ? own.mat : old?.mat) || "", len = ((own ? own.len : old?.len) || "").trim() || JACKET_SHRINK_LEN_MM_;
      const comp = (mat ? getCompById(mat) : null) || defaultJacketShrinkComp_();
      if (!comp) return;
      const count = Math.max(1, Math.round(Number(w.qty) || 1));
      const same = out.find((x) => x.side === side && x.comp === comp && x.len === len);
      if (same) same.count += count; else out.push({ side, comp, len, count });
    }));
    return out;
  }
  /** Трубки состава, занятые другим делом (рукав поверх разъёма, ТУТ на чашки, сростки, колпачки, термоусадка на жгут):
   *  такую трубку на срез оболочки по умолчанию не ставят. */
  function busyTubes_(): Set<Comp> {
    const busy = new Set<Comp>();
    S.endpoints.forEach((e) => { const sh = shrinkOf_(e); if (sh) busy.add(sh.comp); });
    if (S.endpoints.some((e) => e.method === "solder" && !e.tutOff)) { const t = bomTut_(); if (t) busy.add(t); }
    const cap = capTube_(); if (cap) busy.add(cap);
    const spl = spliceEnds_() ? getCompById(S.spl.tutMat) : null; if (spl) busy.add(spl);
    if (S.hs.enabled) S.hs.rows.forEach((r) => { const c = getCompById(r.compId); if (c && (parseInt(r.cnt, 10) || 0) > 0) busy.add(c); });
    return busy;
  }
  /** Трубка на срез оболочки для новой формы: свободная ТУТ состава, иначе свободная ТТК; занято всё — null. */
  function freeJacketShrinkComp_(): Comp | null {
    const busy = busyTubes_();
    const free = shrinkCandidates_().filter((c) => !busy.has(c));
    return free.find((c) => !isGlueSleeve_(c)) || free[0] || null;
  }
  /** Корпус паяльного разъёма: строка «корпус/housing» состава или разъём с корпусом по названию (HOUSING_CONN_RE_). */
  const housingComp_ = () => comps().find((c) => /корпус|housing/i.test(`${c.type || ""} ${c.name || ""}`)) || null;
  function housingDefault_(e: Endpoint) {
    const conn = e.connId ? getCompById(e.connId) : null;
    return !!conn && (HOUSING_CONN_RE_.test(`${conn.art || ""} ${conn.name || ""}`) || !!housingComp_());
  }
  /** «Сборка корпуса после пайки» у этого конца: выбор технолога, иначе по типу разъёма. */
  const housingOn_ = (e: Endpoint) => e.method === "solder" && !!e.connId && (e.housingOn ?? housingDefault_(e));
  /** Карта сборки корпуса: по корпусу из состава или по разъёму (USB, GX/LP, D-Sub/DH/СНП); не подобрана — "". */
  function housingTmpl_(e: Endpoint) {
    const conn = e.connId ? getCompById(e.connId) : null, h = housingComp_();
    const s = `${h ? `${h.art || ""} ${h.name || ""}` : ""} ${conn ? `${conn.art || ""} ${conn.name || ""}` : ""}`;
    return /usb/i.test(s) ? "INS_housing_USB" : /gx|\blp[\s-]*\d/i.test(s) ? "INS_housing_GX"
      : /\bd[-\s]?sub\b|\bdb[-\s]?\d|\bde[-\s]?9\b|\bdh[-\s]?\d|\bdhr\b|снп/i.test(s) ? "INS_housing_DB/DH/СНП" : "";
  }
  function lugTmplHint_(art: string) {
    const s = String(art || "");
    if (!LUG_RE_.test(s)) return "";
    const kind = /^\s*нши|^\s*ншви/i.test(s) ? /^PRS_НШВИ$/i : /^\s*тмл/i.test(s) ? /^PRS_ТМЛ$/i : /^PRS_[HН][KК]И\/НВИ\/РПИ$/i;
    const t = (D.templates || []).find((x) => kind.test(String(x.title || "").split("|")[0].trim()));
    return t ? String(t.title).split("|")[0].trim() : "";
  }
  function stripCableTmpl_(c: Comp | null, screened = false) {
    const s = c ? `${c.art || ""} ${c.name || ""}` : "";
    if (/usb/i.test(s)) return /usb\s*3|3[.,][0-2]/i.test(s) ? "STRIP_CABLE_usb_3.2" : "STRIP_CABLE_usb_2.0";
    const m = /\b(SF|S|F|U)\s*\/\s*(FTP|UTP)\b/i.exec(s);
    if (m) return `STRIP_${m[1].toUpperCase()}/${m[2].toUpperCase()}_ethernet`;
    if (/\bFTP\b/i.test(s)) return "STRIP_F/UTP_ethernet";
    if (screened || /экран|shield|sheld/i.test(s)) return "STRIP_CABLE_sheld";
    return "STRIP_CABLE";
  }
  const markComps_ = () => comps().filter((c) => /маркир/i.test(`${c.type || ""} ${c.name || ""}`));
  function harnessSleeveComps_() {
    const solderTut = S.endpoints.some((e) => e.method === "solder" && !e.tutOff) ? bomTut_() : null;
    const connSleeves = new Set([...S.endpoints.map((e) => shrinkOf_(e)?.comp), ...jacketShrinkItems_().map((x) => x.comp)].filter(Boolean));
    return comps().filter((c) => {
      const s = `${c.type || ""} ${c.name || ""} ${c.art || ""}`;
      return c !== solderTut && !connSleeves.has(c) && /ттк|тут|термоусад|трубк|shrink/i.test(s) && !PROTECT_TUBE_RE_.test(s);
    });
  }
  const rjEndsInBom_ = () => comps().filter((c) => /^соед/i.test(c.type || "") && isRjConn_(c))
    .reduce((n, c) => n + (Number(c.qty) || 1), 0);
  const braidOn_ = () => S.wrap.enabled && S.wrap.slots.some((slot) => slot.mat && slot.kind === "braid");
  const hsDefaultOn_ = (hs: Comp[], rjEnds: number) => hs.length > 0 && (braidOn_() || rjEnds > 1);

  // ── Концы, сростки, заделка ──
  function spliceSizes_() {
    if (!isComplex_() || !S.spl.enabled) return [];
    // Сростки нарисованы на схеме — размеры берутся с неё (концов в сростке, не меньше 2); набранные числа их не переопределяют.
    const drawn = (S.splices || []).filter((s) => s.ends.length >= 2);
    // Сростки ответвлений (S.branches, режим «сросток») точки на схеме не имеют — это сростки из двух проводов, идут следом.
    if (drawn.length) return [...drawn.map((s) => s.ends.length), ...(S.branches || []).filter((b) => b.mode === "splice").map(() => 2)];
    const list = String(S.spl.wires || "").split(/[,;\s]+/).map((s) => parseInt(s, 10)).filter((n) => n > 0).map((n) => Math.max(2, n));
    if (list.length > 1) return list;
    const cnt = Math.max(0, parseInt(S.spl.count, 10) || 0);
    return Array.from({ length: cnt }, () => list[0] || 2);
  }
  const spliceEnds_ = () => spliceSizes_().reduce((s, n) => s + n, 0);
  function freeEnds_() {
    const wires = activeWires_();
    const qty = (w: WireEntry) => Number(w.qty) || 1;
    let ends = wires.reduce((s, w) => s + qty(w), 0) * 2;
    (["A", "B"] as Side[]).forEach((side) => {
      if (endpointsOnSide_(side).length) return;
      ends -= wires.reduce((s, w) => s + qty(w), 0);
    });
    S.endpoints.forEach((e) => {
      const set = new Set(routedWireIdxs_(e));
      // Провод ×N, разведённый по контактам нескольких разъёмов стороны (сростки «Сложной сборки»), приходит сюда не
      // всеми концами — столько, сколько здесь контактов.
      wires.forEach((w) => { if (set.has(w.idx)) ends -= wireCores_(w) > 1 ? qty(w) : Math.min(qty(w), endUnits_(e, w)); });
    });
    return Math.max(0, ends - spliceEnds_());
  }
  // Концы под сростки зачищает станок резки (владелец 16.10) — и тогда, когда у жгута есть хвосты: отдельной «Зачистки
  // концов под сростки» нет.
  const spliceStripAtCut_ = () => spliceEnds_() > 0;
  function capTube_() {
    if (!isComplex_() || !S.cap.enabled || tailTinOn_() || !freeEnds_()) return null;
    return getCompById(S.cap.tut) || null;
  }
  // ── Хвосты: зачистка и лужение ──
  const tailTinOn_ = () => isComplex_() && !!S.tailTin?.enabled;
  /** Хвосты схемы соединений (S.tails) у проводов, что есть в форме. */
  function tailEnds_(): WireEnd[] {
    if (!isComplex_()) return [];
    const alive = new Map(activeWires_().map((w) => [w.idx, unitCount_(w)]));
    return (S.tails || []).filter((e) => (alive.get(e.idx) ?? 0) > e.unit);
  }
  /** Конец стороны В без разъёмов, который зачищает (и лудит) прежнее «Лужение свободных концов» / «Зачистка свободных
   *  концов» — его хвостовая обработка второй раз не трогает. */
  function freeEndStripped_(e: WireEnd) {
    if (e.side !== "B" || endpointsOnSide_("B").length) return false;
    return S.freeStrip.enabled || freeEndTinned_(e);
  }
  function freeEndTinned_(e: WireEnd) {
    if (e.side !== "B" || endpointsOnSide_("B").length || !S.tin.enabled) return false;
    return tinWireIndices_().includes(activeWires_().findIndex((w) => w.idx === e.idx));
  }
  /** Хвост, который режет, зачищает и лудит станок (CUT_WIRE+2strip+tin, владелец 16.10): провод одной жилы, другой его
   *  конец — в сростке, лужение хвостов включено и карта есть в библиотеке. Хвост провода с разъёмом на другом конце —
   *  своими операциями, как раньше. */
  function tailAtCut_(e: WireEnd) {
    if (!tailTinOn_() || !hasTemplateCode_(TAIL_CUT_CODE_)) return false;
    const w = activeWires_().find((x) => x.idx === e.idx);
    return !!w && wireCores_(w) <= 1 && endJoined(S, e.idx, e.unit, e.side === "A" ? "B" : "A") === "splice";
  }
  /** Хвосты, которые зачищают и лудят свои операции (не станок резки). */
  const tailLeft_ = () => tailEnds_().filter((e) => !tailAtCut_(e));
  /** «Зачистка и лужение хвостов»: зачистка тех хвостов, что не зачистила резка (длина по умолчанию 3 мм). */
  function tailStripOp_(): OpSpec | null {
    if (!tailTinOn_()) return null;
    const ends = tailLeft_().filter((e) => !freeEndStripped_(e));
    if (!ends.length) return null;
    const len = tailStripMm(S);
    const a = ends.some((e) => e.side === "A"), b = ends.some((e) => e.side === "B");
    return { type: "stripCable", opKey: "stripTail", tmplHint: "STRIP_WIRE", cat: "Разделка", tutCount: ends.length,
      label: `Зачистка хвостов (${len.replace(".", ",")} мм) — ${ends.length} конц.`,
      strip: { a, lenA: a ? len : "", b, lenB: b ? len : "" } };
  }
  /** Хвосты, которые лудит «Зачистка и лужение хвостов» (без тех, что уже лудит «Лужение свободных концов»). */
  const tailTinEnds_ = () => (tailTinOn_() ? tailLeft_().filter((e) => !freeEndTinned_(e)) : []);
  function tailTinOp_(ends: WireEnd[]): OpSpec {
    return { type: "tin", opKey: "tinTail", cat: "Лужение", ...(hasTemplateCode_("SDR_tin") ? { tmplHint: "SDR_tin" } : {}),
      wireIdxs: [...new Set(ends.map((e) => e.idx))], units: ends.length, label: `Лужение хвостов — ${ends.length} конц.` };
  }
  // ── Способ сростка и гильзы ──
  const splMethod_ = (): SpliceMethod => S.spl.method || "solder";
  /** Гильзы состава для сростков: строки «Др»/«Терминал» с «гильз», «НШВ» или «sleeve» в названии. */
  function spliceSleeveComps_() {
    return comps().filter((c) => /^\s*(?:др|терминал)/i.test(c.type || "") && /гильз|ншв|sleeve/i.test(`${c.name || ""} ${c.art || ""}`));
  }
  /** Места выбора гильзы: сростки схемы С1…Сn по одному; сростки без точки на схеме (числом, ответвления) — одним местом. */
  function spliceSlots_(): Array<{ key: string; label: string; count: number; wires: number }> {
    const sizes = spliceSizes_();
    if (!sizes.length) return [];
    const all = S.splices || [];
    const drawn = all.filter((s) => s.ends.length >= 2).slice(0, sizes.length);
    const slots = drawn.map((s) => ({ key: s.id, label: `С${all.indexOf(s) + 1}`, count: 1, wires: s.ends.length }));
    const rest = sizes.length - drawn.length;
    if (rest > 0) slots.push({ key: SPLICE_REST_, label: drawn.length ? "остальные" : "все сростки", count: rest, wires: 0 });
    return slots;
  }
  /** Сростки схемы по строкам для формы: «С1», сколько проводов и откуда каждый (разъём и контакт противоположного конца
   *  провода; конец, ушедший в другой сросток или хвост, — «к С2» / «к хвосту»). Пусто — на схеме сростков нет. */
  function spliceRows_(): Array<{ id: string; label: string; size: number; wires: Array<{ art: string; from: string }> }> {
    const all = S.splices || [];
    return all.filter((sp) => sp.ends.length >= 2).map((sp) => ({
      id: sp.id, label: `С${all.indexOf(sp) + 1}`, size: sp.ends.length,
      wires: sp.ends.map((e) => {
        const w = activeWires_().find((x) => x.idx === e.idx);
        const c = w ? getCompById(w.compId) : null;
        const art = c ? c.art || c.name || "" : "";
        const other: Side = e.side === "A" ? "B" : "A";
        if (!w) return { art, from: "" };
        const ep = unitEndpoint_(w, e.unit, other);
        if (ep) {
          const pin = corePinsOf_(ep, e.idx)[e.unit];
          return { art, from: `${(ep.label || "").trim() || endKey_(ep)}${pin != null ? ` конт. ${pin}` : ""}` };
        }
        const next = all.find((x) => x !== sp && x.ends.some((y) => y.idx === e.idx && y.unit === e.unit && y.side === other));
        if (next) return { art, from: `к С${all.indexOf(next) + 1}` };
        return { art, from: endJoined(S, e.idx, e.unit, other) === "tail" ? "к хвосту" : "" };
      }),
    }));
  }
  /** Гильза места: выбор технолога (и «— нет —»), иначе единственная гильза состава. */
  function spliceSleeveOf_(key: string): Comp | null {
    const picked = S.spl.sleeves?.[key];
    if (picked !== undefined) return getCompById(picked);
    const list = spliceSleeveComps_();
    return list.length === 1 ? list[0] : null;
  }
  /** Гильзы сростков (ленточный обжим — его материал): строка состава и число сростков на неё; места без гильзы — missing. */
  function spliceSleeves_(): { items: Array<{ comp: Comp; count: number }>; missing: string[] } {
    const items: Array<{ comp: Comp; count: number }> = [], missing: string[] = [];
    const method = splMethod_();
    if (method === "solder" || method === "ultrasonic") return { items, missing };
    const add = (comp: Comp, count: number) => { const same = items.find((x) => x.comp === comp); if (same) same.count += count; else items.push({ comp, count }); };
    const slots = spliceSlots_();
    if (method === "band") {
      const comp = getCompById(S.spl.band), total = slots.reduce((n, s) => n + s.count, 0);
      if (comp && total) add(comp, total);
      return { items, missing };
    }
    slots.forEach((slot) => { const comp = spliceSleeveOf_(slot.key); if (comp) add(comp, slot.count); else missing.push(slot.label); });
    return { items, missing };
  }
  function foldOps_(): OpSpec[] {
    if (!isComplex_()) return [];
    let n = 0;
    S.endpoints.forEach((e) => {
      if (e.method !== "crimp" || !e.fold) return;
      const set = new Set(routedWireIdxs_(e));
      activeWires_().forEach((w) => { if (set.has(w.idx)) n += endUnits_(e, w); });
    });
    if (S.spl.fold && splMethod_() === "sleeve") n += spliceEnds_();
    return n ? [{ type: "foldCore", opKey: "foldCore", tmplHint: "PREP_FOLD", cat: "Подготовка", tutCount: n,
      label: `Складывание жилы — ${n} конц.` }] : [];
  }
  function capOps_(): OpSpec[] {
    const tube = capTube_();
    if (!tube) return [];
    const n = freeEnds_();
    const norm = qtyNum(tube);
    const len = (S.cap.len || "").trim() || (norm > 0 ? String(Math.round(norm * 1000 / n)) : "");
    return [
      { type: "cutTut", opKey: "cutCap", tmplHint: "CUT_TUT_short", cat: "Резка",
        label: `Резка ${tube.art || tube.name} на свободные концы — ${n}×${len || "?"} мм`,
        tutArt: tube.art || tube.name || "", tutName: tube.name || tube.art || "", tutCount: n, tutSegs: [{ len, count: n }] },
      { type: "endCap", opKey: "endCap", tmplHint: "INS_END_CAP", cat: "Монтаж", tutCount: n,
        label: `Заделка свободных концов — ${n} шт` },
    ];
  }
  function sealOp_(): OpSpec | null {
    if (!isComplex_() || !S.seal.enabled) return null;
    const c = getCompById(S.seal.mat);
    if (!c) return null;
    const places = Math.max(1, parseInt(S.seal.places, 10) || 1);
    return { type: "seal", opKey: "seal", tmplHint: "SEAL", cat: "Герметизация",
      label: `Герметизация — ${c.art || c.name}, мест: ${places}`,
      sealArt: c.art || c.name || "", sealName: c.name || c.art || "", sealQty: String(c.qty || ""), sealPlaces: places };
  }
  function spliceWelded_() {
    if (splMethod_() === "ultrasonic") return true;
    if (splMethod_() !== "solder") return false;
    const id = templateIdFor({ type: "splice", opKey: "splice", tmplHint: SPLICE_TMPL.solder, cat: "Пайка", label: "" });
    const t = (D.templates || []).find((x) => x.id === id);
    return !!(t && /US_WELD|сварк/i.test(t.title || ""));
  }
  /** Трубка сростков (длина и позиция состава) или null — трубки на сростки нет. */
  function spliceTube_(): { comp: Comp; len: string } | null {
    const len = (S.spl.tut || "").trim();
    const tut = getCompById(S.spl.tutMat) || bomTut_();
    return len && tut ? { comp: tut, len } : null;
  }
  /** Гильзы группы сростков (ПФ «Опрессовка С1, С2»): сростки схемы `ids` и сростки без точки на схеме (`rest`). */
  function groupSleeves_(ids: string[], rest: number): Array<{ comp: Comp; count: number }> {
    const items: Array<{ comp: Comp; count: number }> = [];
    const add = (comp: Comp | null, count: number) => {
      if (!comp || !count) return;
      const same = items.find((x) => x.comp === comp);
      if (same) same.count += count; else items.push({ comp, count });
    };
    const method = splMethod_();
    if (method === "band") add(getCompById(S.spl.band), ids.length + rest);
    else if (method === "sleeve") {
      ids.forEach((id) => add(spliceSleeveOf_(id), 1));
      add(spliceSleeveOf_(SPLICE_REST_), rest);
    }
    return items;
  }
  /** Операции одной группы сростков (владелец 17.10, окончательно): пайка / опрессовка / сварка → надеть трубку на готовый
   *  сросток → усадка. `names` — «С1, С2» (пусто — сростки без точки на схеме), `n` — номер группы в ключах операций (0 —
   *  группа одна). Зачищает концы станок резки. */
  function spliceGroupOps_(sizes: number[], names: string, n: number, sleeves: Array<{ comp: Comp; count: number }>): OpSpec[] {
    const cnt = sizes.length;
    if (!cnt) return [];
    const suffix = n ? String(n) : "";
    const wires = sizes.every((x) => x === sizes[0]) ? String(sizes[0]) : sizes.join(", ");
    const tube = spliceTube_();
    const where = names ? ` ${names}` : "";
    const ops: OpSpec[] = [];
    // Пайка — как до 16.10 (название и карта те же); опрессовка гильзой и ленточный обжим — со способом и гильзами.
    const method = splMethod_();
    const how = { sleeve: " опрессовкой гильзой", band: " ленточным обжимом", ultrasonic: " ультразвуковой сваркой", solder: "" }[method];
    const used = sleeves.map((x) => `${x.comp.art || x.comp.name} ×${x.count}`);
    // Карты УЗ сварки (US_WELD) в библиотеке может не быть — подсказка остаётся, в названии видно, что карточку надо уточнить.
    const noCard = method === "ultrasonic" && !hasTemplateCode_(SPLICE_TMPL.ultrasonic) ? " (карточка — уточнить)" : "";
    ops.push({ type: "splice", opKey: "splice" + suffix, tmplHint: SPLICE_TMPL[method],
      cat: method === "solder" ? "Пайка" : method === "ultrasonic" ? "УЗ сварка" : "Опрессовка", units: cnt,
      sleeveItems: sleeves.map((x) => ({ art: x.comp.art || x.comp.name || "", name: x.comp.name || x.comp.art || "", count: x.count })),
      label: `Сростки${where}${how} — ${cnt} шт по ${wires} провода${used.length ? `: ${used.join(", ")}` : ""}${noCard}` });
    if (tube) {
      ops.push({ type: "insTut", opKey: "insSpl" + suffix, tmplHint: "INS_TUT", cat: "Монтаж", tutCount: cnt,
        label: `Надеть трубку на ${cnt > 1 ? "сростки" : "сросток"}${where} — ${cnt} шт` });
      ops.push({ type: "heatTut", opKey: "heatSpl" + suffix, tmplHint: "HEAT_TTK_short", cat: "Усадка", tutCount: cnt,
        label: `Усадка трубки на сростках${where} — ${cnt} шт` });
    }
    return ops;
  }

  // ── Резка на станках ──
  function terHasAutoApplicator_(art: string) {
    const r = findTerRecord_(art);
    return !!(r && String(r.applicator || "").split(/[/,;]/).some((s) => /^[AА]\d+$/i.test(s.trim())));
  }
  /** Можно ли обжать концы разъёма на автомате резки: "" — можно; "applicator" — у терминала в БД.ТЕР нет автоматического
   *  аппликатора (A…); "other" — пайка, нет терминала, групповой обжим, складывание, RJ, наконечник, кабель в оболочке. */
  function crimpCutEpBlock_(e: Endpoint): "" | "applicator" | "other" {
    if (e.method !== "crimp" || !e.termId || e.double || epDoublePins_(e).length > 0 || (e.fold && isComplex_()) || isRjEndpoint_(e)) return "other";
    const t = getCompById(e.termId);
    if (!t || LUG_RE_.test(t.art || t.name || "")) return "other";
    const routed = new Set(routedWireIdxs_(e));
    if (activeWires_().some((w) => routed.has(w.idx) && isJacketedCable_(w))) return "other";
    return terHasAutoApplicator_(t.art || t.name) ? "" : "applicator";
  }
  /** Можно ли обжать сторону на автомате резки: "" — да (партиями по терминалам, владелец 16.10: разные терминалы стороны
   *  обжимаются каждый своей партией, терминал без аппликатора автомата — вручную); "applicator" — ни у одного терминала
   *  стороны нет аппликатора автомата; "other" — пайка, групповой обжим, складывание, RJ, наконечник, кабель, нет проводов. */
  function crimpCutBlock_(side: Side): "" | "applicator" | "other" {
    const eps = endpointsOnSide_(side);
    if (!eps.length || !eps.some((e) => routedWireIdxs_(e).length > 0)) return "other";
    const blocks = eps.map(crimpCutEpBlock_);
    if (blocks.includes("other")) return "other";
    return blocks.includes("") ? "" : "applicator";
  }
  function crimpCutSides_(): Side[] {
    return (["A", "B"] as Side[]).filter((side) => !crimpCutBlock_(side));
  }
  /** Разъём, концы которого станок обжимает при резке: его сторона обжимается на автомате и у терминала есть аппликатор. */
  const epCrimpAtCut_ = (e: Endpoint | null | undefined) => !!e && crimpCutActiveSides_().includes(e.side) && crimpCutEpBlock_(e) === "";
  /** Разъём, где стоит конец жилы `k` провода на стороне: по распиновке; контакта нет — единственный разъём стороны, куда
   *  заведён провод (или первый, пока распиновки у провода нет нигде). Конец в сростке или хвосте («Сложная сборка») — null. */
  function unitEndpoint_(w: WireEntry, k: number, side: Side): Endpoint | null {
    if (isComplex_() && endJoined(S, w.idx, k, side)) return null;
    const eps = endpointsOnSide_(side).filter((e) => routedWireIdxs_(e).includes(w.idx));
    const pinned = eps.find((e) => corePinsOf_(e, w.idx)[k] != null);
    if (pinned) return pinned;
    return eps.length === 1 || !eps.some((e) => corePinsOf_(e, w.idx).some((p) => p != null)) ? eps[0] || null : null;
  }
  /** Почему сторону с обжимом нельзя обжать на автомате резки: короткая строка для формы («сторона В: есть пайка —
   *  обжим вручную», «сторона В: у 2 проводов не выбран терминал») или "" — можно либо на стороне нечего обжимать. */
  function crimpCutSideNote_(side: Side): string {
    const eps = endpointsOnSide_(side);
    if (!eps.some((e) => e.method === "crimp") || !crimpCutBlock_(side)) return "";
    const tail = " — обжим вручную";
    const head = `сторона ${sideRu_(side)}: `;
    const wiresOf = (list: Endpoint[]) => {
      const routed = new Set(list.flatMap(routedWireIdxs_));
      return activeWires_().filter((w) => routed.has(w.idx)).reduce((n, w) => n + (Number(w.qty) || 1), 0);
    };
    const bare = eps.filter((e) => e.method === "crimp" && !e.termId);
    if (bare.length) {
      const n = wiresOf(bare);
      // Без терминала опрессовки нет вовсе (ни на станке, ни вручную) — только напоминание выбрать терминал.
      return `${head}у ${n} ${n % 10 === 1 && n % 100 !== 11 ? "провода" : "проводов"} не выбран терминал`;
    }
    if (eps.some((e) => e.method !== "crimp")) return `${head}есть пайка${tail}`;
    if (crimpCutBlock_(side) === "applicator") {
      const arts = [...new Set(eps.map((e) => { const t = getCompById(e.termId); return t ? t.art || t.name : ""; }).filter(Boolean))];
      return `${head}у ${arts.join(", ") || "терминала"} нет аппликатора автомата (A…)${tail}`;
    }
    return `${head}двойной обжим, сложенная жила, RJ, наконечник или кабель${tail}`;
  }
  /** Партии обжима при резке для формы (владелец 16.10): «А — SYM-001T-P0.6: 4 пр., SHF-001T-0.8BS: 2 пр.; В —
   *  T-DS1071-SC600: 4 пр.; А и В — SYM-001T-P0.6 / T-DS1071-SC600: 3 пр.», терминалы активной стороны без аппликатора
   *  автомата — «вручную: …»; концы в сростках и хвостах станок не обжимает. "" — обжима при резке нет. */
  function crimpCutBatchNote_(): string {
    const lines = crimpCutBatchLines_();
    return lines.length ? `Партии: ${lines.join("; ")}` : "";
  }
  /** Те же партии построчно для формы: «А — SYM-001T-P0.6: 4 пр., SHF-001T-0.8BS: 2 пр.», «В — …», «А и В — …», «вручную,
   *  нет аппликатора автомата: …», «концы в сростках и хвостах не обжимаются». */
  function crimpCutBatchLines_(): string[] {
    const cuts = getActiveOps().filter((o) => o.type === "cutWireCrimp");
    if (!cuts.length) return [];
    const ends = (o: OpSpec) => activeWires_().filter((w) => (o.wireIdxs || []).includes(w.idx))
      .reduce((n, w) => n + (o.wireUnits?.[w.idx] ?? unitCount_(w)), 0);
    const where = (o: OpSpec) => (o.crimpEpIdB ? "А и В" : sideRu_(o.side || "A"));
    const groups: string[] = [];
    ["А", "В", "А и В"].forEach((key) => {
      const list = cuts.filter((o) => where(o) === key).map((o) => `${o.crimpTerms || "?"}: ${ends(o)} пр.`);
      if (list.length) groups.push(`${key} — ${list.join(", ")}`);
    });
    const manual = crimpCutActiveSides_().flatMap((side) => endpointsOnSide_(side).filter((e) => crimpCutEpBlock_(e) === "applicator")
      .map((e) => { const t = getCompById(e.termId); return `${t ? t.art || t.name : "?"} (${sideRu_(side)})`; }));
    const joined = isComplex_() && ((S.splices || []).length || (S.tails || []).length);
    return [...groups, ...(manual.length ? [`вручную, нет аппликатора автомата: ${[...new Set(manual)].join(", ")}`] : []),
      ...(joined ? ["концы в сростках и хвостах не обжимаются"] : [])];
  }
  const hasTemplateCode_ = (code: string) =>
    (D.templates || []).some((t) => String(t.title || "").split("|")[0].trim().toLowerCase() === code.toLowerCase());
  /** Есть и карта операции, и строка БД.ОП с её временами: без строки карта дала бы операцию без норм. */
  const hasOperation_ = (code: string) =>
    hasTemplateCode_(code) && (D.ops || []).some((op) => (op.opNumber || "").trim().toLowerCase() === code.toLowerCase());
  /** Провода (idx), которые станок резки обжимает на стороне `side` и тут же лудит свободным концом (CUT_WIRE+1crimp+tin):
   *  стороны В нет, блок «Лужение» включён, провод лудится и заведён в разъёмы стороны. Без карты операции в библиотеке —
   *  пусто: лужение остаётся отдельной операцией. */
  function crimpTinWireIdxs_(side: Side | "" | null): Set<number> {
    if (!side || endpointsOnSide_("B").length || !S.tin.enabled) return new Set();
    if (!hasTemplateCode_(CRIMP_TIN_CODE_)) return new Set();
    const active = activeWires_();
    const tinned = new Set(tinWireIndices_().map((i) => active[i].idx));
    return new Set(endpointsOnSide_(side).flatMap(routedWireIdxs_).filter((i) => tinned.has(i)));
  }
  const crimpCutTin_ = () => { const s = crimpCutSideSelected_(); return s !== CRIMP_BOTH_ && crimpTinWireIdxs_(s).size > 0; };
  const crimpCutMinWires_ = () => (crimpCutTin_() ? CRIMP_TIN_CUT_MIN_WIRES_ : CRIMP_CUT_MIN_WIRES_);
  /** Терминал стороны А, из-за которого резка с обжимом и лужением недоступна (нет автоматического аппликатора), или "". */
  function crimpTinNoApplicator_() {
    if (endpointsOnSide_("B").length || !S.tin.enabled || !tinWireIndices_().length || crimpCutBlock_("A") !== "applicator") return "";
    const t = getCompById(endpointsOnSide_("A")[0].termId);
    return t ? t.art || t.name || "" : "";
  }
  function partQtyFromInfo_() {
    const e = Object.entries(D.assemblyInfo || {}).find(([k]) => /кол[- ]?во|количеств/i.test(k));
    return parseDecimal_(e ? String(e[1] ?? "") : "") || Number(D.partQty) || 1;
  }
  const batchWires_ = () => activeWires_().reduce((s, w) => s + (Number(w.qty) || 1), 0) * partQtyFromInfo_();
  function crimpCutEnabled_() {
    return S.crimpCutManual ? S.crimpCut : batchWires_() > crimpCutMinWires_();
  }
  /** Выбранная сторона обжима при резке: "A" / "B", "AB" — обе (станок с двумя аппликаторами; только когда обе стороны
   *  можно обжать на автомате), "" — обжимать нечего. Без выбора — обе стороны, если автомат может обжать обе
   *  (владелец 10.10.2026: два контакта по спецификации — обжим с двух сторон), иначе единственная доступная. */
  function crimpCutSideSelected_(): Side | typeof CRIMP_BOTH_ | "" {
    const sides = crimpCutSides_();
    if ((S.crimpCutSide === CRIMP_BOTH_ || !S.crimpCutSide) && sides.length === 2) return CRIMP_BOTH_;
    return (sides.includes(S.crimpCutSide as Side) ? S.crimpCutSide : sides[0] || "") as Side | "";
  }
  /** Стороны, которые обжимает автомат резки в этой схеме: [], [A], [B] или [A, B]. */
  function crimpCutActiveSides_(): Side[] {
    if (S.tab === "coax" || !crimpCutEnabled_()) return [];
    const s = crimpCutSideSelected_();
    if (s === CRIMP_BOTH_) return ["A", "B"];
    return s && crimpCutSides_().includes(s) ? [s] : [];
  }
  /** Трубка строки «Термоусадки на жгут»: строка состава или трубка БД.МАТ, которой в составе нет. */
  function hsComp_(id: string): Comp | null {
    const own = getCompById(id);
    if (own || !id.startsWith(REF_TUBE_)) return own;
    const name = id.slice(REF_TUBE_.length);
    const mat = (D.markMats || []).find((m) => m.name === name);
    const type = mat?.group || (/ттк/i.test(name) ? "ТТК" : "ТУТ");
    return name ? { id, type, name, art: name, qty: 0 } : null;
  }
  /** Чем уже занята трубка строки (рукав поверх разъёма, сростки, заделка концов, срез оболочки): "" — ничем. */
  function hsBusyOf_(c: Comp, onConnectors = true): string {
    if (onConnectors && S.endpoints.some((e) => shrinkOf_(e)?.comp.id === c.id)) return "уже на разъёмах";
    if (spliceEnds_() && S.spl.tutMat === c.id) return "уже на сростках";
    if (capTube_()?.id === c.id) return "уже на заделке концов";
    return jacketShrinkItems_().some((x) => x.comp.id === c.id) ? "уже на срезе оболочки" : "";
  }
  /** Строка на ствол не идёт: засев, а трубка занята (и строка формы до 17.10 — со старым правилом: сростки, заделка,
   *  срез оболочки). Ответ — чем занята, для подсказки в форме; "" — строка считается. */
  function hsRowSkip_(row: HsRow): string {
    const c = hsComp_(row.compId);
    if (!c || row.auto === false) return "";
    return hsBusyOf_(c, !!row.auto);
  }
  function harnessSleeves_() {
    if (!S.hs.enabled) return [];
    return S.hs.rows.map((row) => {
      const c = hsComp_(row.compId);
      if (!c || hsRowSkip_(row)) return null;
      const len = (row.len || "").trim();
      const cnt = Math.max(0, parseInt(row.cnt, 10) || 0);
      return cnt ? { comp: c, len, cnt } : null;
    }).filter((x): x is { comp: Comp; len: string; cnt: number } => !!x);
  }
  /** «Добавить трубку»: трубки состава, которых ещё нет в строках, и трубки БД.МАТ, которых нет в составе. */
  function hsTubeOptions_() {
    const taken = new Set(S.hs.rows.map((row) => row.compId));
    const kit = shrinkCandidates_().filter((c) => !taken.has(c.id));
    const names = new Set(comps().flatMap((c) => [c.art, c.name]).filter(Boolean).map((x) => String(x).trim().toLowerCase()));
    const ref = (D.markMats || []).filter((m) => /^(тут|ттк)$/i.test(m.group) && !names.has(m.name.trim().toLowerCase()) && !taken.has(REF_TUBE_ + m.name));
    return { kit, ref };
  }

  // ── Длины ──
  function terminalForWireSide_(wIdx: number, side: Side) {
    const eps = endpointsOnSide_(side).filter((e) => e.method === "crimp" && e.termId);
    const ep = eps.find((e) => routedWireIdxs_(e).indexOf(wIdx) >= 0) || null;
    return ep ? getCompById(ep.termId) : null;
  }
  function getLengthAdjustment_(wIdx?: number) {
    const byWire = wIdx != null;
    const epA = byWire ? null : firstCrimpEndpoint_("A");
    const epB = byWire ? null : firstCrimpEndpoint_("B");
    const termA = byWire ? terminalForWireSide_(wIdx, "A") : (epA && epA.termId ? getCompById(epA.termId) : null);
    const termB = byWire ? terminalForWireSide_(wIdx, "B") : (epB && epB.termId ? getCompById(epB.termId) : null);
    const terA = termA ? findTerRecord_(termA.art) : null;
    const terB = termB ? findTerRecord_(termB.art) : null;
    const mode = S.measureMode;
    const hasTermSelected = !!(termA || termB);
    if (mode === "cut") return { total: 0, noData: false, parts: [] as string[] };
    // Припуск на пайку (владелец 12.10.2026): у паяльного конца — то, что ввели в разъёме; в «длине реза по КД» он уже учтён.
    const solderEp = (side: Side) => endpointsOnSide_(side).find((e) => e.method === "solder"
      && (!byWire || routedWireIdxs_(e).indexOf(wIdx as number) >= 0)) || null;
    const saA = parseLengthVal_(solderEp("A")?.solderAllow || "");
    const saB = parseLengthVal_(solderEp("B")?.solderAllow || "");
    const solderParts = [saA ? `пайка ст.А: +${saA}` : null, saB ? `пайка ст.В: +${saB}` : null];
    if (mode === "inner") {
      const lpA = terA ? parseLengthVal_(terA.lPlus) : 0;
      const lpB = terB ? parseLengthVal_(terB.lPlus) : 0;
      return { total: lpA + lpB + saA + saB, noData: hasTermSelected && !terA && !terB,
        parts: [lpA ? `L+ ст.А: ${lpA}` : null, lpB ? `L+ ст.В: ${lpB}` : null, ...solderParts].filter(Boolean) as string[] };
    }
    const lmA = terA ? parseLengthVal_(terA.lMinus) : 0;
    const lmB = terB ? parseLengthVal_(terB.lMinus) : 0;
    return { total: -(lmA + lmB) + saA + saB, noData: hasTermSelected && !terA && !terB,
      parts: [lmA ? `L− ст.А: ${lmA}` : null, lmB ? `L− ст.В: ${lmB}` : null, ...solderParts].filter(Boolean) as string[] };
  }
  /** updateCutLength_: длина реза при общей длине + подпись формулы. */
  function cutLengthInfo() {
    const adj = getLengthAdjustment_();
    const totals = activeWires_().map((w) => getLengthAdjustment_(w.idx).total);
    const mixed = totals.length > 1 && totals.some((t) => t !== totals[0]);
    const dl = S.drawingLength;
    if (dl > 0 && adj.parts.length > 0 && adj.total !== 0) {
      const cut = Math.round((dl + adj.total) * 100) / 100;
      const sign = adj.total >= 0 ? "+" : "−";
      return { sharedLength: cut, tone: "ok" as const,
        formula: `${dl} ${sign} ${Math.abs(adj.total)} мм [${adj.parts.join(", ")}]` + (mixed ? " · у проводов разные концы — каждый режется по своим" : ""),
        value: `${cut} мм` };
    }
    if (dl > 0 && adj.noData) {
      return { sharedLength: dl, tone: "warn" as const, formula: "⚠ L+/L− не найдены в БД.ТЕР — длина реза = длине по чертежу", value: `${dl} мм` };
    }
    return { sharedLength: dl, tone: "none" as const, formula: "", value: "" };
  }
  const sharedLength = () => cutLengthInfo().sharedLength;

  // ── Свивка / лужение ──
  const twistWireIndices_ = () => (S.twist.wires === null ? activeWires_().map((_, i) => i) : S.twist.wires.filter((i) => i < activeWires_().length));
  const tinWireIndices_ = () => (S.tin.wires === null ? activeWires_().map((_, i) => i) : S.tin.wires.filter((i) => i < activeWires_().length));
  /** Пары и группы свивки (twistGroups.ts): позиции кабелей, в группе два и больше. */
  const getTwistPairs = () => twistGroupsOf(S.twist.pairs, activeWires_().length);
  /** Проверка строк свивки «Пары и группы» — стоп и предупреждения (twistGroups.ts). */
  const twistProblems_ = () => (S.twist.enabled && S.twist.mode === "pairs"
    ? twistGroupProblems(S.twist.pairs, activeWires_().map((w) => { const c = getCompById(w.compId); return c ? c.art || c.name : ""; }),
      activeWires_().map(unitCount_))
    : { error: "", warnings: [] as string[] });
  const getTwistSelectionCount = () => (S.twist.mode === "pairs" ? getTwistPairs().length : twistWireIndices_().length);
  function autoDetectTwistPairs() {
    const active = activeWires_();
    const normOf = (w: WireEntry) => { const c = getCompById(w.compId); return c ? (Number(c.qty) || 0) : 0; };
    const vals = active.map(normOf);
    const valid = vals.filter((v) => v > 0);
    if (!valid.length) return [] as Array<[number, number]>;
    const base = Math.min(...valid);
    const byVal: Record<string, number[]> = {};
    active.forEach((_w, i) => {
      const v = vals[i];
      if (v <= 0) return;
      const ratio = v / base;
      if (ratio - Math.round(ratio) > 0.08) (byVal[v.toFixed(4)] = byVal[v.toFixed(4)] || []).push(i);
    });
    const pairs: Array<[number, number]> = [];
    Object.keys(byVal).forEach((k) => { const g = byVal[k]; for (let i = 0; i + 1 < g.length; i += 2) pairs.push([g[i], g[i + 1]]); });
    return pairs;
  }

  // «Ø изоляции по маркам» из базы монтажных проводов (GET /api/references/wire-dia)
  function wireGostKey_(s: string, mk: { norm: string }, byGost: Record<string, WireDiaRow>) {
    const re = new RegExp(mk.norm.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s-]*"));
    // «ПуГВ 1х0,75»: число перед «х» — жилы, не сечение
    const nums = s.replace(re, " ").replace(/\d+\s*[хx×*]\s*(?=\d)/g, " ").match(/\d+(?:\.\d+)?/g) || [];
    const keys = nums.map((n) => String(Number(n))).filter((k) => byGost[k]);
    return keys.find((k) => byGost[k][mk.norm]) || keys[0] || null;
  }
  function wireRow_(art: string) {
    const wd = D.wireDia;
    if (!wd) return null;
    const s = String(art || "").toLowerCase().replace(/,/g, ".");
    const found = findWireMark_(s, wd.marks || []);
    if (!found) return null;
    const mk = found.mk;
    let row: WireDiaRow | null = null;
    const aw = s.match(/(\d+)\s*awg/);
    if (aw && wd.byAwg) row = wd.byAwg[aw[1]] || null;
    if (!row && wd.byGost) { const k = wireGostKey_(found.s, mk, wd.byGost); row = k ? wd.byGost[k] : null; }
    return row ? { row, mk } : null;
  }
  function lookupWireDia_(art: string) { const r = wireRow_(art); return (r && r.row[r.mk.norm]) || 0; }
  /** Ø провода (по артикулу, иначе по наименованию — как свивка его берёт) и взят ли он из старой таблицы. */
  function compWireDia_(c: Comp) {
    const art = lookupWireDia_(c.art) ? c.art : c.name;
    const r = wireRow_(art);
    const dia = (r && r.row[r.mk.norm]) || 0;
    return { dia, old: !!(r && dia && r.row[OLD_FLAG_ + r.mk.norm]) };
  }
  function wireGauge_(art: string) {
    const wd = D.wireDia;
    const s = String(art || "").toLowerCase().replace(/,/g, ".");
    const aw = s.match(/(\d+)\s*awg/);
    const awg = aw ? Number(aw[1]) : null;
    let mm2: number | null = null;
    if (wd && wd.marks && wd.byGost) {
      const found = findWireMark_(s, wd.marks);
      if (found) { const k = wireGostKey_(found.s, found.mk, wd.byGost); if (k) mm2 = Number(k); }
      // Марка вне базы монтажных (ПГВА 0,50, НВМ 0,35): сечение — отдельное число из таблицы сечений, как его берут
      // нормы обжима на сервере (technology.wire_section); «4x0,35» числом не считается.
      else mm2 = s.split(/\s+/).filter((t) => /^\d+(?:\.\d+)?$/.test(t)).map((t) => String(Number(t))).filter((k) => wd.byGost[k]).map(Number)[0] ?? null;
    }
    return { awg, mm2 };
  }
  function wireGaugeOf_(c: Comp | null) {
    if (!c) return { awg: null, mm2: null };
    const a = wireGauge_(c.art);
    return a.awg != null || a.mm2 != null ? a : wireGauge_(c.name);
  }
  function terRange_(rec: TerRec | null) {
    if (!rec) return null;
    const n = (v: string) => { const x = Number(String(v || "").replace(",", ".").replace(/[^\d.]/g, "")); return isFinite(x) && x > 0 ? x : null; };
    const af = n(rec.awgFrom), at = n(rec.awgTo), mf = n(rec.mm2From), mt = n(rec.mm2To);
    const r: { awgMin?: number; awgMax?: number; mm2Min?: number; mm2Max?: number } = {};
    if (af != null && at != null) { r.awgMin = Math.min(af, at); r.awgMax = Math.max(af, at); }
    if (mf != null && mt != null) { r.mm2Min = Math.min(mf, mt); r.mm2Max = Math.max(mf, mt); }
    return r.awgMin != null || r.mm2Min != null ? r : null;
  }
  function wireFitsTerminal_(g: { awg: number | null; mm2: number | null }, r: ReturnType<typeof terRange_>) {
    if (!g || !r) return { ok: true, basis: "", range: "" };
    if (g.awg != null && r.awgMin != null) return { ok: g.awg >= r.awgMin && g.awg <= r.awgMax!, basis: "AWG " + g.awg, range: "AWG " + r.awgMin + "–" + r.awgMax };
    if (g.mm2 != null && r.mm2Min != null) return { ok: g.mm2 >= r.mm2Min && g.mm2 <= r.mm2Max!, basis: g.mm2 + " мм²", range: r.mm2Min + "–" + r.mm2Max + " мм²" };
    return { ok: true, basis: "", range: "" };
  }
  /** Сечение провода против диапазона терминала, в который обжимается его конец (владелец 16.10: провод 0,5, чей конец В
   *  уходит в сросток, не сверяется с терминалом В). Конец в сростке или хвосте — без терминала. */
  function checkTerminalFit_() {
    const warns: string[] = [];
    activeWires_().forEach((w) => {
      const c = getCompById(w.compId);
      if (!c) return;
      const g = wireGaugeOf_(c);
      (["A", "B"] as Side[]).forEach((side) => {
        const terms: string[] = [];
        for (let k = 0; k < unitCount_(w); k++) {
          const ep = unitEndpoint_(w, k, side);
          if (ep && ep.method === "crimp" && ep.termId && !terms.includes(ep.termId)) terms.push(ep.termId);
        }
        terms.forEach((id) => {
          const t = getCompById(id);
          const r = t ? terRange_(findTerRecord_(t.art)) : null;
          if (!t || !r) return;
          const fit = wireFitsTerminal_(g, r);
          if (!fit.ok) warns.push("• «" + (c.name || c.art) + "» (" + fit.basis + ") вне диапазона терминала " + side + " «" + (t.name || t.art) + "» (" + fit.range + ")");
        });
      });
    });
    return warns;
  }
  function wireGeom_(art: string) {
    const r = wireRow_(art);
    if (!r) return null;
    const Dia = r.row[r.mk.norm];
    const d = r.row.__core;
    if (!(Dia > 0 && d > 0 && Dia > d)) return null;
    const hasEps = Object.prototype.hasOwnProperty.call(EPS_BY_MARK, r.mk.norm);
    return { d, D: Dia, eps: hasEps ? EPS_BY_MARK[r.mk.norm] : 2.5, epsMark: r.mk.name, epsExplicit: hasEps };
  }
  function twistIdx_() {
    let idx = S.twist.mode === "pairs" ? getTwistPairs().reduce<number[]>((a, p) => a.concat(p), []) : twistWireIndices_();
    if (!idx.length) idx = activeWires_().map((_, i) => i);
    return idx;
  }
  function twistGeom_() {
    const active = activeWires_();
    for (const i of twistIdx_()) {
      const w = active[i];
      const c = w ? getCompById(w.compId) : null;
      const g = c ? (wireGeom_(c.art) || wireGeom_(c.name)) : null;
      if (g) return g;
    }
    return null;
  }
  /** autofillTwistDia: один общий Ø свиваемых проводов → число в поле, разные → пусто. */
  function autoTwistDia() {
    const active = activeWires_();
    const dset = new Set<number>();
    twistIdx_().forEach((i) => {
      const w = active[i];
      const c = w ? getCompById(w.compId) : null;
      const d = c ? (lookupWireDia_(c.art) || lookupWireDia_(c.name)) : 0;
      if (d > 0) dset.add(d);
    });
    return dset.size === 1 ? String([...dset][0]).replace(".", ",") : "";
  }
  const twistDiaValue = () => (S.twist.diaManual || !S.twist.enabled ? S.twist.dia : autoTwistDia());
  /** Свиваемые провода, Ø которых нет в базе монтажных проводов (марка вне базы или размера нет), — по ним видна
   *  подсказка WIRE_DIA_MISSING_ вместо молчаливого 0. Ручной Ø снимает её: свивка считается по нему. */
  function twistWiresWhere_(test: (d: ReturnType<typeof compWireDia_>) => boolean) {
    if (!S.twist.enabled || (S.twist.diaManual && parseDecimal_(S.twist.dia) > 0)) return [] as string[];
    const active = activeWires_();
    const names = new Set<string>();
    twistIdx_().forEach((i) => {
      const w = active[i];
      const c = w ? getCompById(w.compId) : null;
      if (c && test(compWireDia_(c))) names.add(c.name || c.art);
    });
    return [...names];
  }
  const twistDiaMissing_ = () => twistWiresWhere_((d) => !d.dia);
  /** Свиваемые провода, Ø которых пришёл из старой таблицы (WIRE_DIA_OLD_), — пока у размера нет источника. */
  const twistDiaOld_ = () => twistWiresWhere_((d) => d.old);
  function twistZ0Warn_(g: NonNullable<ReturnType<typeof wireGeom_>>, z0: number, p: number) {
    if (!(g && z0 > 0)) return "";
    const min = twistZ0_(g.d, g.D, 2, g.eps), max = twistZ0_(g.d, g.D, 2000, g.eps);
    if (z0 < min) return "Z₀=" + Math.round(z0) + " Ω недостижимо: для этого провода минимум ≈ " + Math.round(min) + " Ω (нужен другой провод/ε_r)";
    if (z0 > max) return "Z₀=" + Math.round(z0) + " Ω недостижимо: для этого провода максимум ≈ " + Math.round(max) + " Ω";
    if (p > 0 && p < g.D * 4) return "шаг " + String(p).replace(".", ",") + " мм нереалистично мал (< 4× Ø провода) — Z₀ требует слишком плотной свивки";
    return "";
  }
  function twistNote() {
    const g = twistGeom_();
    if (!g) return { text: "", explicit: true, warn: "" };
    return {
      text: g.epsExplicit ? `ε_r = ${g.eps} (${g.epsMark})` : `⚠ ε_r = ${g.eps} по умолчанию — марки «${g.epsMark}» нет в справочнике коэффициентов`,
      explicit: g.epsExplicit,
      warn: twistZ0Warn_(g, parseDecimal_(S.twist.z0), parseDecimal_(S.twist.pitch)),
    };
  }

  // ── Вкладки ──
  function isComplexKit_() {
    const txt = (c: Comp) => `${c.type || ""} ${c.name || ""} ${c.art || ""}`;
    const qty = (c: Comp) => Number(String(c.qty).replace(",", ".")) || 1;
    const conns = bomLists().conns.filter((c) => !/корпус|housing/i.test(txt(c)) && !/^\s*(?:плата|печатная плата|pcb)(?![a-zа-я])/i.test(`${c.art || ""} ${c.name || ""}`))
      .reduce((n, c) => n + qty(c), 0);
    if (conns > 2) return true;
    if (comps().some((c) => LUG_RE_.test(`${c.art || ""} ${c.name || ""}`) || /^\s*гс\s*\d/i.test(`${c.art || ""} ${c.name || ""}`) ||
      DIODE_RE_.test(txt(c)) || SEAL_RE_.test(txt(c)))) return true;
    const terms = getByTypes(["терминал", "terminal", "контакт"]).reduce((n, c) => n + qty(c), 0);
    const solder = S.endpoints.some((e) => e.method === "solder");
    // Двойной обжим в контакте — один терминал на два конца.
    const ends = activeWires_().reduce((n, w) => n + (Number(w.qty) || 1) * (wireCores_(w) || 1), 0) * 2
      - S.endpoints.reduce((n, e) => n + (e.double ? 0 : epDoublePins_(e).length), 0);
    return terms > 0 && !solder && S.endpoints.length > 1 && terms !== ends;
  }
  function complexWarning() {
    const nA = endpointsOnSide_("A").length, nB = endpointsOnSide_("B").length;
    if (isComplex_() || !(nA > 1 || nB > 1 || isComplexKit_())) return "";
    return `⚠ Состав сложнее межплатной 1+1 (разъёмов на сторону: А — ${nA}, В — ${nB}). `
      + "Разводку жил, сростки, заделку концов, наконечники и герметизацию ведёт вкладка «Сложная сборка».";
  }

  // ── КОАКС ──
  // Пробелы, дефисы, точки и подчёркивания не различают записи: «MMCX-Male RG316» из состава = «MMCX-male RG-316» в
  // БД.КОАКС (владелец 14.10: генератор писал «нет в БД.КОАКС», а запись с размерами была).
  const coaxNorm_ = (s: string) => String(s || "").toLowerCase().replace(/[\s\-–—_.]+/g, "");
  function coaxBomConnectors_() {
    const list: Array<{ art: string; name: string }> = [];
    comps().forEach((c) => {
      if (!/разъ[её]м|соед|conn/i.test(c.type || "")) return;
      const q = Math.max(1, parseInt(String(c.qty), 10) || 1);
      for (let i = 0; i < q; i++) list.push({ art: c.art || c.name || "", name: c.name || "" });
    });
    return list;
  }
  const coaxBomCableComp_ = () => comps().find((x) => /кабель|провод/i.test(x.type || "")) || null;
  const coaxBomCable_ = () => { const c = coaxBomCableComp_(); return c ? (c.art || c.name || "") : ""; };
  const coaxBomTut_ = () => comps().find((x) => /тут/i.test(x.type || "")) || null;
  const coaxBomSleeve_ = () => comps().find((x) => /втулк/i.test(`${x.type || ""} ${x.name || ""}`)) || null;
  function coaxCableDiaLookup_() {
    const t = (D.coaxCableDia && D.coaxCableDia.byCable) || {};
    return t[String(coaxBomCable_()).toLowerCase().replace(/[\s-]/g, "")] || null;
  }
  // Диаметры — только у кабеля (владелец 16.10.2026: «разъём тянет L1–L3, а провод — свои данные»): разделка берёт
  // D1–D3 из СПР.КАБ по кабелю из BOM; нет строки у кабеля — D нет («—»), запись разъёма D не даёт. L1–L3, L± — у разъёма.
  function coaxDia_() {
    const dia = coaxCableDiaLookup_();
    return { d1: (dia && dia.d1) || "", d2: (dia && dia.d2) || "", d3: (dia && dia.d3) || "" };
  }
  function matchCoaxRec_(art: string, pin: string, shield: string) {
    if (!art) return null;
    const a = coaxNorm_(art);
    let cands = (D.coaxRecords || []).filter((r) => coaxNorm_(r.connArticle) === a || coaxNorm_(r.article) === a);
    if (!cands.length) return null;
    const cable = coaxBomCable_();
    if (cable) { const w = cands.filter((r) => coaxNorm_(r.wire) === coaxNorm_(cable)); if (w.length) cands = w; }
    if (pin) { const p = cands.filter((r) => r.pinType && coaxNorm_(r.pinType).indexOf(coaxNorm_(pin)) >= 0); if (p.length) cands = p; }
    if (shield) { const sf = cands.filter((r) => r.shieldType && coaxNorm_(r.shieldType).indexOf(coaxNorm_(shield)) >= 0); if (sf.length) cands = sf; }
    return cands[0] || null;
  }
  function coaxRecForSide_(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    if (!s) return null;
    return matchCoaxRec_(s.art, side === "A" ? S.coax.pinA : S.coax.pinB, coaxShieldOf_(side));
  }
  /** Тип экрана стороны: В — свой (`shieldB`), пусто — как у А. */
  function coaxShieldOf_(side: Side): string {
    return side === "B" ? S.coax.shieldB || S.coax.shield : S.coax.shield;
  }
  function isCoaxAssembly_() {
    if (S.coax.sideA && matchCoaxRec_(S.coax.sideA.art, "", "")) return true;
    if (S.coax.sideB && matchCoaxRec_(S.coax.sideB.art, "", "")) return true;
    const recWires = new Set((D.coaxRecords || []).map((r) => coaxNorm_(r.wire)).filter(Boolean));
    const coaxRe = /коакс|coax|\bRG[\s-]?\d/i;
    return comps().some((c) => {
      if (coaxRe.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`)) return true;
      const n = coaxNorm_(c.art) || coaxNorm_(c.name);
      return !!n && recWires.has(n);
    });
  }
  function asmLenFromName_() {
    const asmName = Object.values(D.assemblyInfo || {}).map((v) => String(v)).join(" ");
    const lens = [...asmName.matchAll(/(?:^|[^\d,.])(\d+)\s*(?:мм|mm)(?![\d²])/gi)].map((m) => parseDecimal_(m[1]));
    if (lens.length) return Math.max(...lens);
    const noParens = asmName.replace(/\([^)]*\)/g, " ");
    const ms = [...noParens.matchAll(/(?:^|[^\d,.\wа-яё])(\d+(?:[.,]\d+)?)\s*(?:m|м)(?![a-zа-яё\d²])/gi)].map((m) => Math.round(parseDecimal_(m[1]) * 1000));
    if (ms.length) return Math.max(...ms);
    const ccw = /\bccw[\s-]*(\d+(?:[.,]\d+)?)/i.exec(asmName);
    return ccw ? Math.round(parseDecimal_(ccw[1]) * 1000) : 0;
  }
  function calcCutLengths_(wires: Comp[]) {
    const norm = (s: string) => String(s || "").toLowerCase().replace(/["«»\s]/g, "");
    const loose = (s: string) => String(s || "").toLowerCase().replace(/[х×�]/g, "x").replace(/["«»]/g, "");
    const words = (s: string) => loose(s).split(/\s+/).filter(Boolean);
    const cuts = (D.calcOps || []).filter((o) => /^CUT_(WIRE|COAX)/i.test(o.code) && o.comp);
    const exact = (w: Comp, o: CalcOp) => [norm(w.art), norm(w.name)].filter(Boolean).includes(norm(o.comp));
    const within = (w: Comp, o: CalcOp) => words(o.comp).every((t) => loose(w.art + " " + w.name).includes(t));
    const out: Record<string, number> = {};
    wires.forEach((w) => {
      const mine = cuts.filter((o) => exact(w, o) || (!wires.some((x) => exact(x, o)) && within(w, o) && wires.filter((x) => within(x, o)).length === 1));
      const lens = [...new Set(mine.map(calcCutMm_).filter((v) => v > 0))];
      if (lens.length === 1) out[w.id] = lens[0];
    });
    return out;
  }
  function coaxInfo(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    const rec = s ? coaxRecForSide_(side) : null;
    if (!s) return { text: "", rec, missing: false };
    if (rec) {
      const dia = coaxDia_();
      const dims = ["D1:" + (dia.d1 || "—"), "D2:" + (dia.d2 || "—"), "D3:" + (dia.d3 || "—"), "L1:" + (rec.l1 || "—"), "L2:" + (rec.l2 || "—"), "L3:" + (rec.l3 || "—")];
      return { text: `✓ ${rec.type || ""} · ${rec.wire || ""} · ${dims.join(" ")}`, rec, missing: false };
    }
    return { text: `⚠ «${s.art}» нет в БД.КОАКС — добавьте ниже`, rec, missing: true };
  }
  /** Ø оболочки коакс-кабеля, мм: D3 из СПР.КАБ по кабелю, иначе Ø из таблицы проводов; 0 — неизвестен. */
  function coaxCableOuterMm_() {
    const d3 = parseDecimal_(String(coaxDia_().d3 || "")) || 0;
    return d3 > 0 ? d3 : Number(lookupWireDia_(coaxBomCable_())) || 0;
  }
  /** Станок по Ø кабеля: тонкий (до 5,5 мм, как в карточке CUT_COAX+2strip) — малый, толще или Ø неизвестен — большой. */
  function coaxMachineAuto_(): CoaxMachineKey {
    const dia = coaxCableOuterMm_();
    return dia > 0 && dia <= COAX_SMALL_MACHINE_MAX_MM ? "small" : "big";
  }
  const coaxMachine_ = (): CoaxMachineKey => S.coax.machine || coaxMachineAuto_();
  function getActiveOpsCoax_(): OpSpec[] {
    if (!S.coax.sideA) return [];
    const sideB = !!S.coax.sideB;
    const pinA = S.coax.pinA;
    const pinB = sideB ? S.coax.pinB : "";
    const pinHint = (p: string) => (p === "внутри корпуса" ? "SDR_COAX_pin_R/A" : "SDR_COAX_pin");
    const ops: OpSpec[] = [];
    const nSides = sideB ? 2 : 1;
    const x = (n: number) => (n > 1 ? ` ×${n}` : "");
    const tutOn = S.coax.tutEnabled;
    // Резка и разделка — до всего остального (владелец 14.10): либо резка с разделкой на роторном станке одной
    // операцией, либо резка, разделка А, разделка В; резка ТУТ — после них.
    const stripB = sideB || S.coax.stripB;
    if (S.coax.stripMode !== "separate") {
      const machine = COAX_MACHINES[coaxMachine_()];
      ops.push({ type: "coaxCut", opKey: "coaxCutStrip", cat: "Резка с разделкой",
        label: sideB ? "Резка с разделкой (А и В)" : stripB ? "Резка с разделкой (А) и зачисткой (В)" : "Резка с разделкой (А)",
        tmplHint: stripB ? "CUT_COAX+2strip" : "CUT_COAX+1strip", machine: machine.model, stripSides: stripB ? ["A", "B"] : ["A"] });
    } else {
      // Своя карта резки коаксиала (CUT_COAX_auto); пока её или строки БД.ОП нет — прежняя карта провода с пометкой.
      ops.push({ type: "coaxCut", opKey: "coaxCut", label: "Резка кабеля",
        tmplHint: hasOperation_(COAX_CUT_CODE_) ? COAX_CUT_CODE_ : "CUT_WIRE_auto", tmplWant: COAX_CUT_CODE_ });
      ops.push({ type: "coaxStrip", side: "A", opKey: "coaxStripA", label: "Разделка коаксиала (А)", tmplHint: "STRIP_COAX_auto" });
      if (sideB) ops.push({ type: "coaxStrip", side: "B", opKey: "coaxStripB", label: "Разделка коаксиала (B)", tmplHint: "STRIP_COAX_auto" });
      else if (S.coax.stripB) ops.push({ type: "coaxStrip", side: "B", opKey: "coaxStripB", label: "Зачистка стороны B (голый конец)", tmplHint: "STRIP_COAX_auto" });
    }
    if (tutOn) ops.push({ type: "coaxCutTut", opKey: "coaxCutTut", label: `Резка ТУТ${x(nSides)}`, units: nSides, tmplHint: "CUT_TUT_short" });
    const pinOp = (sd: Side, pin: string): OpSpec => ({ type: "coaxPin", side: sd, opKey: "coaxPin" + sd, label: `Пайка центрального контакта (${sd === "B" ? "B" : "А"})`, tmplHint: pinHint(pin) });
    // Обжимной центральный контакт (владелец 19.10) — опрессовка на жилу своей операцией, не пайка.
    const crimpOp = (sd: Side): OpSpec => ({ type: "coaxPinCrimp", side: sd, opKey: "coaxPinCrimp" + sd,
      label: `Опрессовка центрального контакта (${sd === "B" ? "B" : "А"})`,
      tmplHint: hasOperation_(COAX_PIN_CRIMP_CODE_) ? COAX_PIN_CRIMP_CODE_ : "PRS_SLEEVE_coax", tmplWant: COAX_PIN_CRIMP_CODE_ });
    // Цанговый центральный контакт (владелец 16.10, «не требует пайки») — пайки пина нет, жилу зажимает корпус.
    const pushPin = (sd: Side, pin: string) => {
      if (pin === COAX_PIN_CRIMP) ops.push(crimpOp(sd));
      else if (pin !== COAX_PIN_COLLET) ops.push(pinOp(sd, pin));
    };
    const housingLabel = (sd: Side, pin: string) => `Монтаж корпуса (${sd === "B" ? "B" : "А"})${pin === COAX_PIN_COLLET ? " — цанговый контакт, без пайки" : ""}${
      (sd === "A" ? shieldA : shieldB) === COAX_SHIELD_TWIST ? " — накручивается на кабель, экран зажимает разъём"
        : (sd === "A" ? shieldA : shieldB) === COAX_SHIELD_PUSH ? " — Push-On, без инструмента"
        : (sd === "A" ? shieldA : shieldB) === COAX_SHIELD_BODY ? " — экран зажимается корпусом разъёма, без гильзы" : ""}`;
    const insTutOp = (): OpSpec | null => (tutOn ? { type: "coaxInsTut", opKey: "coaxInsTut", label: `Монтаж ТУТ${x(nSides)}`, units: nSides, tmplHint: "INS_TUT" } : null);
    const shieldA = coaxShieldOf_("A"), shieldB = sideB ? coaxShieldOf_("B") : "";
    const sidesOf = (kind: string) => (["A", "B"] as Side[]).filter((sd) => (sd === "A" ? shieldA : shieldB) === kind);
    const clampSides = sidesOf(COAX_SHIELD_CLAMP), solderSides = sidesOf("пайка");
    const noOpSides = (["A", "B"] as Side[]).filter((sd) => COAX_SHIELD_NO_OP_.includes(sd === "A" ? shieldA : shieldB));
    const compressSides = sidesOf(COAX_SHIELD_COMPRESS);
    // Обжим — всё, что не пайка и не прижим (прежнее поведение: тип по умолчанию «обжим»).
    // Накрутка и Push-On садятся сами — ни втулки, ни опрессовки; компрессия идёт как обжим (свой инструмент, без HEX).
    const nCrimp = (["A", ...(sideB ? ["B"] : [])] as Side[]).filter((sd) => !clampSides.includes(sd) && !solderSides.includes(sd) && !noOpSides.includes(sd)).length;
    const nClamp = clampSides.length;
    // «Надеть» (владелец 19.10): на обжимной стороне — втулка экрана, на прижимной — гайка, шайба и уплотнитель; одной операцией на обе стороны.
    const sdName = (sd: Side) => (sd === "B" ? "B" : "А");
    const crimpSides = (["A", ...(sideB ? ["B"] : [])] as Side[]).filter((sd) => !clampSides.includes(sd) && !solderSides.includes(sd) && !noOpSides.includes(sd));
    const putOnLabel = !nClamp ? `Монтаж втулки экрана${x(nCrimp)}`
      : !nCrimp ? `Надеть гайку, шайбу, уплотнитель и зажимное кольцо${x(nClamp)}`
        : `Надеть втулку экрана (${crimpSides.map(sdName).join(", ")}) и гайку, шайбу, уплотнитель, зажимное кольцо (${clampSides.map(sdName).join(", ")})`;
    const sleeveOp = (): OpSpec => ({ type: "coaxInsSleeve", opKey: "coaxInsSleeve", label: putOnLabel, units: nCrimp + nClamp, tmplHint: "INS_SLEEVE_coax",
      ...(nClamp ? { clampSides } : {}) });
    const hasSleeve = nCrimp + nClamp > 0;
    const angledB = sideB && pinB === "внутри корпуса";
    const pushTut = () => { const o = insTutOp(); if (o) ops.push(o); };
    if (angledB) {
      pushPin("A", pinA); pushTut(); if (hasSleeve) ops.push(sleeveOp()); ops.push(pinOp("B", pinB));
    } else {
      pushPin("A", pinA); if (sideB) pushPin("B", pinB); pushTut(); if (hasSleeve) ops.push(sleeveOp());
    }
    // Прижимной экран — одна операция «Монтаж корпуса с прижимом»: оплётка по конусу, корпус, гайка. Карта — ERP-шаблон, пока его нет — карта корпуса.
    const clampHousing = (sd: Side, pin: string): Partial<OpSpec> => (clampSides.includes(sd)
      ? { label: `Монтаж корпуса с прижимом (${sdName(sd)})${pin === COAX_PIN_COLLET ? " — цанговый контакт, без пайки" : ""}`,
          tmplHint: hasTemplateCode_(CLAMP_HOUSING_CODE_) ? CLAMP_HOUSING_CODE_ : "INS_housing_COAX", tmplWant: CLAMP_HOUSING_CODE_ } : {});
    if (pinA !== "внутри корпуса") ops.push({ type: "coaxHousing", side: "A", opKey: "coaxHousingA", label: housingLabel("A", pinA), tmplHint: "INS_housing_COAX", ...clampHousing("A", pinA) });
    if (sideB && pinB !== "внутри корпуса") ops.push({ type: "coaxHousing", side: "B", opKey: "coaxHousingB", label: housingLabel("B", pinB), tmplHint: "INS_housing_COAX", ...clampHousing("B", pinB) });
    solderSides.forEach((sd) => ops.push({ type: "coaxSolderShield", side: sd, opKey: "coaxSolderShield" + sd, label: `Пайка экрана (${sdName(sd)})`, tmplHint: "SDR_COAX_housing" }));
    if (nCrimp > 0) ops.push({ type: "coaxShield", opKey: "coaxShield", label: `Опрессовка втулки экрана${x(nCrimp)}${compressSides.length ? ` — компрессия: ${compressSides.map(sdName).join(", ")} (компрессионный инструмент, без HEX)` : ""}`, units: nCrimp, tmplHint: "PRS_SLEEVE_coax" });
    if (tutOn) ops.push({ type: "coaxHeat", opKey: "coaxHeat", label: `Усадка ТУТ${x(nSides)}`, units: nSides, tmplHint: "HEAT_TYT_short" });
    const caps: Record<string, number> = {};
    ([["A", pinA, S.coax.coverA], ["B", sideB ? pinB : "", S.coax.coverB]] as const).forEach(([, pin, cover]) => {
      if (pin !== "внутри корпуса") return;
      if (cover) caps[cover] = (caps[cover] || 0) + 1;
    });
    if (caps["обжим"]) ops.push({ type: "coaxCapCrimp", opKey: "coaxCapCrimp", units: caps["обжим"], label: `Монтаж крышки (обжим)${x(caps["обжим"])}`, cat: "Опрессовка крышки коакс" });
    if (caps["винт"]) ops.push({ type: "coaxCapScrew", opKey: "coaxCapScrew", units: caps["винт"], label: `Монтаж крышки (винт)${x(caps["винт"])}`, tmplHint: "INS_CAP" });
    return ops;
  }

  // ── Маршрут межплатной / сложной сборки ──
  /** Операции в порядке генератора, а поверх — свой порядок технолога (S.opOrder). */
  function getActiveOps(): OpSpec[] {
    return applyOpOrder(autoOps(), S.opOrder || []);
  }
  const tubeKind_ = (c: Comp) => (isGlueSleeve_(c) ? "ТТК" : "ТУТ");
  /** Трубки, которые надеваются на кабель до обжима и пайки (владелец 15.10: «даже до обжимки она одевается, потому что с
   *  контактами неудобно; то же самое с оплёткой»; «термоусадки должны одеваться сразу на две стороны»): «Термоусадка поверх
   *  разъёма», «Термоусадка на место разделки» сторон, термоусадка на жгут и детали корпуса паяльных разъёмов. Резка — со
   *  своими отрезками; надевание — одна операция на обе стороны; усадка — у каждой стороны после её монтажа. */
  function sleeveItems_() {
    const conn: Array<{ ep: Endpoint; tag: string; comp: Comp; len: string }> = [];
    (["A", "B"] as Side[]).forEach((side) => endpointsOnSide_(side).forEach((ep) => {
      const sh = shrinkOf_(ep);
      if (sh) conn.push({ ep, tag: endKey_(ep), ...sh });
    }));
    const jacket = jacketShrinkItems_();
    const harness = harnessSleeves_();
    const housings = S.endpoints.filter(housingOn_);
    return { conn, jacket, harness, housings };
  }
  /** Резка трубок до обжима: рукава поверх разъёмов, трубки на место разделки, термоусадка на жгут. Отрезки одной трубки и
   *  длины — одной операцией; количество — на изделие (tutCount). */
  function sleeveCuts_(it: ReturnType<typeof sleeveItems_>): OpSpec[] {
    const cuts: OpSpec[] = [];
    const groups: Record<string, typeof it.conn> = {};
    it.conn.forEach((x) => { (groups[x.comp.id + "|" + x.len] || (groups[x.comp.id + "|" + x.len] = [])).push(x); });
    Object.keys(groups).forEach((k, i) => {
      const g = groups[k], c = g[0].comp;
      cuts.push({ type: "cutTut", opKey: "cutShr" + (i + 1), tmplHint: "CUT_TUT_short", cat: "Резка",
        label: `Резка ${tubeKind_(c)} поверх разъёма ${g.map((x) => x.tag).join(", ")} — ${g.length}×${g[0].len} мм`,
        tutArt: c.art || c.name || "", tutName: c.name || c.art || "", tutCount: g.length, tutSegs: [{ len: g[0].len, count: g.length }] });
    });
    const jg: Record<string, typeof it.jacket> = {};
    it.jacket.forEach((x) => { (jg[x.comp.id + "|" + x.len] || (jg[x.comp.id + "|" + x.len] = [])).push(x); });
    Object.keys(jg).forEach((k, i) => {
      const g = jg[k], c = g[0].comp, n = g.reduce((s, x) => s + x.count, 0);
      cuts.push({ type: "cutTut", opKey: "cutJs" + (i + 1), tmplHint: "CUT_TUT_short", cat: "Резка",
        label: `Резка ${tubeKind_(c)} на место разделки ${g.map((x) => sideRu_(x.side)).join(", ")} — ${n}×${g[0].len} мм`,
        tutArt: c.art || c.name || "", tutName: c.name || c.art || "", tutCount: n, tutSegs: [{ len: g[0].len, count: n }] });
    });
    it.harness.forEach((s, i) => cuts.push({ type: "cutTut", opKey: "cutHs" + (i + 1), tmplHint: "CUT_TUT_short", cat: "Резка",
      label: `Резка ${s.comp.art || s.comp.name} — ${s.cnt}×${s.len || "?"} мм`,
      tutArt: s.comp.art || s.comp.name || "", tutName: s.comp.name || s.comp.art || "", tutCount: s.cnt, tutSegs: [{ len: s.len, count: s.cnt }] }));
    return cuts;
  }
  /** «Надеть трубки (стороны А и В)»: одна операция на изделие до первого обжима, разделки у разъёма или пайки. */
  function sleeveOn_(it: ReturnType<typeof sleeveItems_>): OpSpec | null {
    const parts: string[] = [];
    const byComp = <T extends { comp: Comp }>(list: T[]) => {
      const out = new Map<string, T[]>();
      list.forEach((x) => out.set(x.comp.id, [...(out.get(x.comp.id) || []), x]));
      return [...out.values()];
    };
    // «ТУТ 6/3мм», «ТТК 6/2»; артикул без вида трубки — с видом впереди.
    const tube = (c: Comp) => { const name = c.art || c.name || ""; return /^\s*(?:тут|ттк)/i.test(name) ? name : `${tubeKind_(c)} ${name}`; };
    byComp(it.jacket).forEach((g) => parts.push(`${tube(g[0].comp)} на место разделки ${g.map((x) => sideRu_(x.side)).join(", ")} — ${g.reduce((s, x) => s + x.count, 0)} шт`));
    byComp(it.conn).forEach((g) => parts.push(`${tube(g[0].comp)} поверх разъёма ${g.map((x) => x.tag).join(", ")} — ${g.length} шт`));
    it.harness.forEach((s) => parts.push(`${s.comp.art || s.comp.name} ${allRjEndpoints_() ? "на кабель под хвостовики RJ" : "на жгут"} — ${s.cnt} шт`));
    const tubes = parts.length;
    it.housings.forEach((ep) => { const c = getCompById(ep.connId); parts.push(`детали корпуса ${endKey_(ep)}${c ? ` (${c.art || c.name})` : ""}`); });
    if (!parts.length) return null;
    const sides = (["A", "B"] as Side[]).filter((side) => endpointsOnSide_(side).length).map(sideRu_);
    const where = sides.length === 2 ? " (стороны А и В)" : sides.length ? ` (сторона ${sides[0]})` : "";
    const pieces = it.jacket.reduce((s, x) => s + x.count, 0) + it.conn.length + it.harness.reduce((s, x) => s + x.cnt, 0);
    return { type: "insTut", opKey: "insSleeves", tmplHint: "INS_TUT", cat: "Монтаж", harness: true, tutCount: pieces,
      label: `${tubes ? "Надеть трубки" : "Надеть детали корпуса"}${where}: ${parts.join("; ")}` };
  }
  /** Стороны, которые обжимает автомат резки, когда до обжима надо надеть трубку или оплётку: станок обжимает конец при
   *  резке — надеть её раньше нельзя. Пусто — порядок честный. */
  function sleeveCrimpCutConflict_(): Side[] {
    // «Сложная сборка»: трубки и оплётку надевают после партий обжима при резке (порядок владельца 16.10, 713К.4 КБ1 —
    // мелкие терминалы проходят в трубку), предупреждать не о чем.
    if (isComplex_()) return [];
    const it = sleeveItems_();
    const any = it.conn.length || it.jacket.length || it.harness.length || braidOn_();
    return any ? crimpCutActiveSides_() : [];
  }
  /** Предупреждение формы: трубку надо надеть до обжима, а сторону обжимает станок при резке. */
  function sleeveWarning_(): string {
    const sides = sleeveCrimpCutConflict_();
    if (!sides.length) return "";
    return `Трубку надо надеть до обжима — обжим на станке для ${sides.length === 2 ? "сторон А и В" : `стороны ${sideRu_(sides[0])}`} отключите или наденьте трубку до резки.`;
  }
  /** Усадка трубок у сторон: рукав поверх разъёма — после последней операции своего разъёма (монтаж, пайка, сборка
   *  корпуса), трубка на место разделки — после последней операции своей стороны. */
  function sleeveShrinks_(ops: OpSpec[], it: ReturnType<typeof sleeveItems_>) {
    it.conn.forEach((x) => {
      const side = x.ep.side, ord = endpointOrdinal_(x.ep);
      const own = (o: OpSpec) => o.epId === x.ep.id || ["stripCable", "shield", "idle"].some((k) => o.opKey === k + side + ord);
      let last = -1;
      ops.forEach((o, i) => { if (own(o)) last = i; });
      // Сторона — у усадки, как у усадки на месте разделки: без неё схема ПФ клала её в ПФ следующей стороны.
      ops.splice(last >= 0 ? last + 1 : ops.length, 0, { type: "heatTut", opKey: "heatShr" + side + ord, side, ord,
        tmplHint: isGlueSleeve_(x.comp) ? "HEAT_TTK_short" : "HEAT_TYT_short", cat: "Усадка", tutCount: 1, label: `Усадка ${tubeKind_(x.comp)} поверх разъёма ${x.tag}` });
    });
    const perSide: Partial<Record<Side, number>> = {};
    it.jacket.forEach((x) => {
      const mineSide = (o: OpSpec) => o.side === x.side && (!!o.epId || /^(stripCable|stripCores|shield|idle|heatShr|heatJs)/.test(o.opKey));
      let last = -1;
      ops.forEach((o, i) => { if (mineSide(o)) last = i; });
      const n = perSide[x.side] = (perSide[x.side] || 0) + 1;
      ops.splice(last >= 0 ? last + 1 : ops.length, 0, { type: "heatTut", opKey: "heatJs" + x.side + (n > 1 ? n : ""), side: x.side,
        tmplHint: isGlueSleeve_(x.comp) ? "HEAT_TTK_short" : "HEAT_TYT_short", cat: "Усадка", tutCount: x.count,
        label: `Усадка ${tubeKind_(x.comp)} на месте разделки ${sideRu_(x.side)} — ${x.count} шт` });
    });
  }
  /** Кабель в оболочке (владелец 15.10: «на многожильном проводе обжим сначала с двух сторон а потом монтаж»): когда
   *  все кабели формы — многожильные в оболочке и руками обжимаются обе стороны, опрессовка всех разъёмов обеих сторон
   *  (с разделкой, скруткой экрана и изоляцией незадействованных жил своего конца) идёт до первого монтажа; монтаж А,
   *  потом В. Пайка — не обжим, стоит где стояла. Одиночные провода — как были: обжим А, монтаж А, обжим В, монтаж В
   *  (владелец 14.10). */
  function multicoreCrimpsFirst_(ops: OpSpec[]) {
    const wires = activeWires_();
    if (!wires.length || !wires.every((w) => isJacketedCable_(w) && wireCores_(w) > 1)) return;
    const crimps = ops.filter((o) => o.type === "prsTerm" && o.epId);
    if (!(["A", "B"] as Side[]).every((side) => crimps.some((o) => o.side === side))) return;
    const crimpEnds = new Set(crimps.map((o) => `${o.side}${o.ord}`));
    const prep = (o: OpSpec) => o.type === "prsTerm" || (!!o.side && crimpEnds.has(`${o.side}${o.ord}`) && /^(stripCable|stripCores|shield|idle)/.test(o.opKey));
    const firstMount = ops.findIndex((o) => !!o.epId && o.type !== "prsTerm");
    if (firstMount < 0) return;
    const late = ops.filter((o, i) => i > firstMount && prep(o));
    if (!late.length) return;
    const rest = ops.filter((o, i) => !(i > firstMount && prep(o)));
    rest.splice(firstMount, 0, ...late);
    ops.splice(0, ops.length, ...rest);
  }
  function autoOps(): OpSpec[] {
    if (S.tab === "coax") return getActiveOpsCoax_();
    const ops: OpSpec[] = [];
    const activeWires = activeWires_();
    let solderEnds = 0;
    const tutByLen: Record<string, number> = {};
    S.endpoints.forEach((e) => {
      if (e.method !== "solder" || e.tutOff) return;
      const routed = routedWireIdxs_(e);
      // ТУТ — на каждую жилу, что приходит на этот разъём (часть жил кабеля может идти на другой разъём стороны), и на
      // экран, припаянный к контакту.
      const n = activeWires.filter((w) => routed.includes(w.idx)).reduce((s, w) => s + endUnits_(e, w) + shieldPinsAt_(e, w).length, 0);
      if (!n) return;
      solderEnds += n;
      const len = (e.tutLen || "").trim();
      tutByLen[len] = (tutByLen[len] || 0) + n;
    });
    if (tutByLen[""] && solderEnds > 0) {
      const t = bomTut_();
      // Тот же материал уходит и на рукава поверх разъёмов — на пайку остаётся то, что за вычетом их длины.
      const shrinkM = t ? S.endpoints.reduce((sum, e) => { const sh = shrinkOf_(e); return sh && sh.comp === t ? sum + (parseLengthVal_(sh.len) || 0) / 1000 : sum; }, 0)
        + jacketShrinkItems_().reduce((sum, js) => (js.comp === t ? sum + (parseLengthVal_(js.len) || 0) * js.count / 1000 : sum), 0) : 0;
      const m = t ? Math.max(0, qtyNum(t) - shrinkM) : 0;
      if (m > 0) {
        const auto = String(Math.round(m * 1000 / solderEnds));
        tutByLen[auto] = (tutByLen[auto] || 0) + tutByLen[""];
        delete tutByLen[""];
      }
    }
    const tutSegs = Object.keys(tutByLen).map((len) => ({ len, count: tutByLen[len] }));
    const crimpSides = crimpCutActiveSides_();
    const tinAtCut = crimpTinWireIdxs_(crimpSides.length === 1 ? crimpSides[0] : null);

    if (activeWires.length > 0) {
      const nameOf = (w: WireEntry) => { const c = getCompById(w.compId); return c ? (c.name || c.art) : "?"; };
      const cutLabel = (ws: WireEntry[], pfx: string) => { const ns = ws.map(nameOf); return ns.length === 1 ? `${pfx} — ${ns[0]}` : `${pfx} — ${ns.length} ${pluralRu(ns.length, "кабель", "кабеля", "кабелей")}`; };
      const splAtCut = spliceStripAtCut_();
      const splLen = spliceStripMm(S);
      const tinIdx = tinWireIndices_();
      // Кусок провода: его жилы, концы которых стоят на одних и тех же разъёмах сторон (провод ×2: одна жила — к «I3»,
      // другая — к «C»). Кабель в оболочке и провод без распиновки по разъёмам — целиком, по всем разъёмам стороны.
      // «Сложная сборка»: у куска ещё и то, что на концах без разъёма, — сросток или хвост (хвост «сросток → облуженный
      // конец» станок режет своей партией, CUT_WIRE+2strip+tin); `ks` — номера жил куска.
      type Join = "splice" | "tail" | null;
      type Piece = { w: WireEntry; units: number; ks: number[]; ends: Partial<Record<Side, Endpoint | null>> | null;
        joins?: Partial<Record<Side, Join>>; tail?: Side };
      const pieces: Piece[] = [];
      activeWires.forEach((w) => {
        if (wireCores_(w) > 1) { pieces.push({ w, units: unitCount_(w), ks: Array.from({ length: unitCount_(w) }, (_, k) => k), ends: null }); return; }
        const own = new Map<string, Piece>();
        for (let k = 0; k < unitCount_(w); k++) {
          const epA = unitEndpoint_(w, k, "A"), epB = unitEndpoint_(w, k, "B");
          const joins = isComplex_() ? { A: endJoined(S, w.idx, k, "A"), B: endJoined(S, w.idx, k, "B") } : undefined;
          const tail = joins ? (["A", "B"] as Side[]).find((side) => joins[side] === "tail" && tailAtCut_({ idx: w.idx, unit: k, side })) : undefined;
          const key = `${epA?.id ?? joins?.A ?? ""}|${epB?.id ?? joins?.B ?? ""}${tail ? `|T${tail}` : ""}`;
          const piece = own.get(key);
          if (piece) { piece.units++; piece.ks.push(k); }
          else {
            const next: Piece = { w, units: 1, ks: [k], ends: { A: epA, B: epB }, ...(joins ? { joins } : {}), ...(tail ? { tail } : {}) };
            own.set(key, next); pieces.push(next);
          }
        }
      });
      const tailLen = tailStripMm(S);
      const stripOf = (w: WireEntry, piece: Piece) => {
        let a = "", b = "";
        const ends = piece.ends;
        if (splAtCut) (["A", "B"] as Side[]).forEach((side) => {
          const eps = endpointsOnSide_(side);
          if (!eps.length || (ends ? !!ends[side] : eps.some((e) => routedWireIdxs_(e).includes(w.idx)))) return;
          // Хвост станок зачищает под лужение (2–3 мм), только когда сам его и лудит; иначе хвост при резке не трогают.
          const join = piece.joins?.[side];
          if (join === "tail" && piece.tail !== side) return;
          const len = join === "tail" ? tailLen : splLen;
          if (side === "A") a = len; else b = len;
        });
        const own = ends ? [ends.A, ends.B].filter((e): e is Endpoint => !!e) : S.endpoints.filter((e) => routedWireIdxs_(e).includes(w.idx));
        own.forEach((e) => {
          let len: string | null = null;
          if (epCrimpAtCut_(e)) len = epStripLen_(e);
          // Ротор снимает только оболочку — «Разделка» строки «Кабели» (владелец 15.10: «жилы у многожильного не могут
          // разделываться на станке, они либо разделываются отдельно перед обжимом, либо на ОТП»): жилы зачищает своя
          // операция у разъёма (coreStripOp_). RJ — без зачистки жил (врезной контакт), складывание жилы — по своему
          // правилу ниже.
          else if (S.rotor && isJacketedCable_(w) && (e.method === "solder" || (!isRjEndpoint_(e) && !(e.fold && isComplex_())))) {
            len = w.jacket || JACKET_STRIP_MM_;
          }
          // «Без зачистки (ОТП)» — конец приходит на обжим уже зачищенным: его зачищает резка (владелец 14.10: «обжим с одной
          // и ОТП с другой» → «резка с обжимом А и зачисткой В»). Без ОТП конец под ручной обжим резка не трогает — его
          // зачищает пресс. Кабель в оболочке — по правилу ротора ниже.
          else if (e.method === "crimp" && e.otp && !isJacketedCable_(w)) len = epStripLen_(e);
          else if (e.method === "solder") len = (e.solderStrip || "").trim() || DEFAULT_LEN_MM_;
          else if (e.method === "crimp" && e.fold && isComplex_()) len = FOLD_STRIP_MM_;
          else if (e.method === "crimp" && isRjEndpoint_(e)) len = RJ_STRIP_MM_;
          // Без ротора кабель разделывают у разъёма («Разделка кабеля под обжим»), при резке его конец не трогают.
          else if (e.method === "crimp" && isJacketedCable_(w)) len = S.rotor ? w.jacket || JACKET_STRIP_MM_ : null;
          else if (e.method === "crimp" && (e.double || epDoublePins_(e).length > 0)) {
            len = epStripLen_(e);
          }
          if (len === null) return;
          if (e.side === "A") a = len; else b = len;
        });
        // Лужёный свободный конец зачищают под лужение: его длина главнее «Зачистки свободных концов» (тот блок — без
        // лужения); пустая длина свободной зачистки больше не прячет длину под лужение (719К.2 КБ1 «B ?», 04.10).
        const tinned = !endpointsOnSide_("B").length && S.tin.enabled && tinIdx.includes(activeWires.indexOf(w));
        const freeLen = S.freeStrip.enabled ? (S.freeStrip.len || "").trim() : "";
        if (!b && tinned) b = (S.tin.stripLen || "").trim() || freeLen || "—";
        if (!b && !endpointsOnSide_("B").length && S.freeStrip.enabled) b = freeLen || "—";
        return { a, b };
      };
      // Партии резки с обжимом (владелец 16.10, 713К.4 КБ1): концы, которые станок обжимает, — по терминалу каждой стороны
      // (SYM — одна партия, SHF — другая, T-DS1071 стороны В — своя; провод с терминалами на обоих концах — партия обжима
      // А и В). Конец в сростке или хвосте станок не обжимает. Партия обжимает концы на всех разъёмах своего терминала.
      const rotor = S.rotor;
      const termOf = (e: Endpoint | null) => (e ? e.termId : "");
      type Group = { pieces: Piece[]; a: string; b: string; crimpA: Endpoint[]; crimpB: Endpoint[]; tin: boolean; rot: boolean; tail: boolean };
      const groups: Record<string, Group> = {};
      pieces.forEach((piece) => {
        const w = piece.w;
        const s = stripOf(w, piece);
        const crimpA = piece.ends && epCrimpAtCut_(piece.ends.A) ? piece.ends.A! : null;
        const crimpB = piece.ends && epCrimpAtCut_(piece.ends.B) ? piece.ends.B! : null;
        const tin = (!!crimpA || !!crimpB) && tinAtCut.has(w.idx);
        const rot = rotor && isJacketedCable_(w);
        const tail = !!piece.tail && !crimpA && !crimpB;
        const sig = `${s.a}|${s.b}|${crimpA ? "A" : ""}${crimpB ? "B" : ""}${tin ? "T" : ""}|${termOf(crimpA)}/${termOf(crimpB)}|${rot ? "R" : ""}${tail ? "|L" : ""}`;
        const g = groups[sig] || (groups[sig] = { pieces: [], a: s.a, b: s.b, crimpA: [], crimpB: [], tin, rot, tail });
        g.pieces.push(piece);
        if (crimpA && !g.crimpA.includes(crimpA)) g.crimpA.push(crimpA);
        if (crimpB && !g.crimpB.includes(crimpB)) g.crimpB.push(crimpB);
      });
      let ci = 0;
      Object.keys(groups).forEach((sig) => {
        const g = groups[sig];
        const ws = [...new Set(g.pieces.map((p) => p.w))];
        const units: Record<number, number> = {};
        g.pieces.forEach((p) => { units[p.w.idx] = (units[p.w.idx] || 0) + p.units; });
        const part = Object.fromEntries(ws.filter((w) => units[w.idx] < unitCount_(w)).map((w) => [w.idx, units[w.idx]]));
        const own = { wireIdxs: ws.map((w) => w.idx), ...(Object.keys(part).length ? { wireUnits: part } : {}),
          ...(isComplex_() ? { cutUnits: g.pieces.flatMap((p) => p.ks.map((k): [number, number] => [p.w.idx, k])) } : {}) };
        const hasA = g.a !== "", hasB = g.b !== "", n = (hasA ? 1 : 0) + (hasB ? 1 : 0);
        const key = ci === 0 ? "cutWire" : "cutWire" + ci; ci++;
        const dashless = (s: string) => (s === "—" ? "" : s);
        const strip = { a: hasA, lenA: dashless(g.a), b: hasB, lenB: dashless(g.b) };
        const artOf = (ep: Endpoint) => { const t = getCompById(ep.termId)!; return t.art || t.name; };
        const many = (list: Endpoint[]) => (list.length > 1 ? list.map((e) => e.id) : undefined);
        if (g.crimpA.length && g.crimpB.length) {
          // Обе стороны на станке с двумя аппликаторами: ручной опрессовки у этих концов нет. Терминалы сторон могут
          // различаться — в названии оба. Карты CUT_WIRE+2crimp нет в библиотеке — норма и карта односторонней резки.
          const epA = g.crimpA[0], epB = g.crimpB[0];
          const terms = artOf(epA) === artOf(epB) ? artOf(epA) : `${artOf(epA)} / ${artOf(epB)}`;
          ops.push({ type: "cutWireCrimp", opKey: key, side: "A", crimpEpId: epA.id, crimpEpIdB: epB.id, crimpEpIds: many(g.crimpA), crimpEpIdsB: many(g.crimpB),
            crimpTerms: terms, label: cutLabel(ws, `Резка с обжимом А и В (${terms})`),
            ...own, tmplHint: hasTemplateCode_(CRIMP_BOTH_CODE_) ? CRIMP_BOTH_CODE_ : "CUT_WIRE+1crimp", tmplWant: CRIMP_BOTH_CODE_, strip });
        } else if (g.crimpA.length || g.crimpB.length) {
          const crimpSide: Side = g.crimpA.length ? "A" : "B", list = g.crimpA.length ? g.crimpA : g.crimpB, crimpEp = list[0];
          const term = artOf(crimpEp);
          // С лужением свободного конца тем же станком (BZW-5.0+Z) отдельной операции «Лужение» у этих проводов нет.
          ops.push({ type: "cutWireCrimp", opKey: key, side: crimpSide, crimpEpId: crimpEp.id, crimpEpIds: many(list), crimpTerms: term,
            label: cutLabel(ws, `Резка с обжимом ${sideRu_(crimpSide)} (${term})${g.tin ? " и лужением" : (crimpSide === "A" ? hasB : hasA) ? ` и зачисткой ${sideRu_(crimpSide === "A" ? "B" : "A")}` : ""}`),
            ...own, tmplHint: g.tin ? CRIMP_TIN_CODE_ : "CUT_WIRE+1crimp", strip, ...(g.tin ? { tin: true } : {}) });
        } else if (g.tail) {
          // Хвосты «сросток → облуженный конец» (владелец 16.10): резка, зачистка конца под сросток и хвоста, лужение хвоста —
          // одной операцией на BZW-5.0+Z, без отдельных «Зачистки хвостов» и «Лужения хвостов».
          ops.push({ type: "cutWireStrip", opKey: key, label: cutLabel(ws, "Резка с зачисткой двух концов и лужением"), ...own,
            tmplHint: TAIL_CUT_CODE_, cat: "Резка", strip, tin: true });
        } else if (n === 0) {
          ops.push({ type: "cutWire", opKey: key, label: cutLabel(ws, "Резка"), ...own, tmplHint: "CUT_WIRE_auto", cat: "Резка" });
        } else {
          // Ротор — только оболочка: её длина в названии (жилы зачищает своя операция у разъёма).
          const sizes = g.rot ? `: ${jacketCoreText_({ a: g.a, b: g.b }, { a: "", b: "" })}` : "";
          ops.push({ type: "cutWireStrip", opKey: key, label: cutLabel(ws, `Резка с разделкой${g.rot ? " на роторе" : ""} (${n} ст.${sizes})`),
            ...own, tmplHint: `${g.rot ? "CUT_COAX" : "CUT_WIRE"}+${n}strip`, strip });
        }
      });
      if (solderEnds > 0 && (bomTut_() || tutSegs.some((s) => s.len))) {
        const tut = bomTut_();
        const segLbl = tutSegs.map((s) => (s.len ? `${s.count}×${s.len}мм` : `${s.count} шт`)).join(" + ");
        ops.push({ type: "cutTut", opKey: "cutTut", label: `Резка ТУТ — ${segLbl} (на пайку)`, tmplHint: "CUT_TUT_short", cat: "Резка",
          tutArt: tut ? (tut.art || tut.name || "") : "", tutName: tut ? (tut.name || tut.art || "") : "", tutCount: solderEnds, tutSegs });
        ops.push({ type: "insTut", opKey: "insTut", label: `Надеть ТУТ на провод — ${solderEnds} шт`, tmplHint: "INS_TUT", cat: "Монтаж", tutCount: solderEnds });
      }
    }

    let firstTermSeen = false;
    const solderStrips = new Set<string>();
    const sideEnds = { A: cableEnds_("A"), B: cableEnds_("B") };
    (["A", "B"] as Side[]).forEach((side) => endpointsOnSide_(side).forEach((ep) => {
      const ord = endpointOrdinal_(ep);
      const conn = ep.connId ? getCompById(ep.connId) : null;
      const term = ep.termId ? getCompById(ep.termId) : null;
      const tag = sideRu_(side) + ord;
      // Конец кабеля в оболочке: разделка, экран и незадействованные жилы — один раз на сторону, у первого разъёма,
      // куда идут его жилы (жилы 1–10 → В1, 11–19 → В2 — оболочку снимают один раз).
      const mine = sideEnds[side].filter((x) => x.eps[0] === ep);
      const tags = [...new Set(mine.flatMap((x) => x.eps.map(endKey_)))].join(", ") || tag;
      const count = (list: typeof mine) => list.reduce((s, x) => s + Math.max(1, Math.round(Number(x.w.qty) || 1)), 0);
      const cableOps = (how: string) => {
        const out: OpSpec[] = [];
        if (mine.length && !S.rotor && (conn || ep.method === "crimp")) {
          // Разделка у разъёма — оболочка и зачистка жил под обжим или пайку (владелец 15.10: «жилы у многожильного не
          // могут разделываться на станке, они либо разделываются отдельно перед обжимом, либо на ОТП»): с «Без зачистки
          // (ОТП)» жилы зачищает ОТП при обжиме — здесь только оболочка; у RJ жилы не зачищают — врезной контакт.
          const jacket = mine[0].w.jacket || JACKET_STRIP_MM_;
          const core = isRjEndpoint_(ep) || (ep.method === "crimp" && ep.otp) ? ""
            : ep.method === "solder" ? (ep.solderStrip || "").trim() || DEFAULT_LEN_MM_ : epStripLen_(ep);
          const key = side === "A" ? "a" : "b";
          const sizes = jacketCoreText_({ a: jacket, b: "" }, { a: core, b: "" });
          out.push({ type: "stripCable", opKey: "stripCable" + side + ord, side, ord, label: `Разделка кабеля ${how} ${tags} (${sizes}) — ${count(mine)} конц.`,
            tmplHint: stripCableTmpl_(getCompById(mine[0].w.compId), mine.some((x) => x.screened)), cat: "Разделка", tutCount: count(mine),
            strip: { a: key === "a", lenA: key === "a" ? jacket : "", b: key === "b", lenB: key === "b" ? jacket : "",
              ...(core ? { [key === "a" ? "coreA" : "coreB"]: core === "—" ? "" : core } : {}) } });
        }
        const shielded = mine.filter((x) => x.screened);
        if (shielded.length) {
          // Экран, заведённый на контакт, скручивают в вывод под контакт; остальные — скручивают и изолируют.
          const total = count(shielded);
          const onPin = Math.min(total, shielded.reduce((s, x) => s + x.shieldPins.length, 0));
          const where = [...new Set(shielded.flatMap((x) => x.shieldPins.map((p) => `${endKey_(p.ep)}·${p.pin}`)))].join(", ");
          const label = !onPin ? `Скрутка и изоляция экрана ${tags} — ${total} шт`
            : onPin === total ? `Скрутка экрана в вывод на контакт ${where} — ${total} шт`
              : `Скрутка экрана ${tags} — ${total} шт: на контакт ${where} — ${onPin}, изолировать — ${total - onPin}`;
          // Карты «Скрутка экрана» (БД.ОП TWST_SHILED) в библиотеке может не быть — тогда разделка экранированного кабеля.
          out.push({ type: "stripCable", opKey: "shield" + side + ord, side, ord, cat: "Разделка", tutCount: total,
            tmplHint: hasTemplateCode_(SHIELD_CODE_) ? SHIELD_CODE_ : "STRIP_CABLE_sheld", tmplWant: SHIELD_CODE_, label });
        }
        const idle = mine.reduce((s, x) => s + x.unused.length, 0);
        if (idle) {
          out.push({ type: "endCap", opKey: "idle" + side + ord, side, ord, tmplHint: "INS_END_CAP", cat: "Подготовка", tutCount: idle,
            label: `Обрезка и изоляция незадействованных жил ${tags} — ${idle} шт` });
        }
        const cores = coreStrip(how);
        if (cores) out.push(cores);
        return out;
      };
      /** Зачистка жил кабеля в оболочке после ротора (владелец 15.10: «жилы у многожильного не могут разделываться на
       *  станке, они либо разделываются отдельно перед обжимом, либо на ОТП»): своя операция у каждого разъёма, перед
       *  обжимом или пайкой, карта STRIP_WIRE (та же, что у зачистки концов под сростки). Нет — «Без зачистки (ОТП)» (жилы
       *  зачищает ОТП при обжиме), обжим на станке резки, RJ (врезной контакт), сложенная жила (своя зачистка при резке). */
      const coreStrip = (how: string): OpSpec | null => {
        if (!S.rotor) return null;
        let len: string;
        if (ep.method === "solder") {
          if (!conn) return null;
          len = (ep.solderStrip || "").trim() || DEFAULT_LEN_MM_;
        } else {
          if (!term || ep.otp || epCrimpAtCut_(ep) || isRjEndpoint_(ep) || (ep.fold && isComplex_())) return null;
          len = epStripLen_(ep);
        }
        const routed = routedWireIdxs_(ep);
        const n = activeWires_().filter((w) => isJacketedCable_(w) && routed.includes(w.idx)).reduce((s, w) => s + endUnits_(ep, w), 0);
        if (!n) return null;
        const mm = len === "—" ? "" : len;
        const key = side === "A" ? "a" : "b";
        return { type: "stripCable", opKey: "stripCores" + side + ord, side, ord, tmplHint: "STRIP_WIRE", cat: "Разделка", tutCount: n,
          label: `Зачистка жил ${how} ${tag} (${mm ? mm.replace(".", ",") : "?"} мм) — ${n} шт`,
          strip: { a: key === "a", lenA: key === "a" ? mm : "", b: key === "b", lenB: key === "b" ? mm : "" } };
      };
      if (ep.method === "solder") {
        if (!conn) return;
        const own = cableOps("под пайку");
        own.forEach((o) => { if (/^strip(Cable|Cores)/.test(o.opKey)) solderStrips.add(o.opKey); });
        ops.push(...own);
        ops.push({ type: "solderConn", opKey: endpointOpKey_(side, ord, "solder"), side, ord, epId: ep.id,
          label: `Пайка разъёма ${tag} — ${conn.art || conn.name}`, tmplHint: ep.sdrTmpl || detectSolderConn_(conn.art) || undefined });
      } else {
        ops.push(...cableOps("под обжим"));
        // Терминалы, которые обжал станок при резке (своей партией), второй раз вручную не обжимаются.
        if (term && !epCrimpAtCut_(ep)) {
          ops.push({ type: "prsTerm", opKey: endpointOpKey_(side, ord, "prs"), side, ord, epId: ep.id,
            label: `Опрессовка ${LUG_RE_.test(term.art || term.name || "") ? "наконечников" : "терминалов"} ${tag}`
              + (ep.double ? ` (${epPer_(ep) > 2 ? "по " + epPer_(ep) + " провода" : "двойной обжим"})` : epDoublePins_(ep).length ? ` (двойной обжим: конт. ${epDoublePins_(ep).join(", ")})` : "")
              + (ep.fold && isComplex_() ? " (жила сложена)" : ""),
            cat: `Опрессовка терминалов (${sideRu_(side)})`, tmplHint: lugTmplHint_(term.art || term.name) || undefined,
            termArt: term.art || term.name || "", firstTerm: !firstTermSeen });
          firstTermSeen = true;
        }
        if (conn && isRjConn_(conn)) {
          const cat = rjCableCat_();
          ops.push({ type: "insTerm", opKey: endpointOpKey_(side, ord, "ins"), side, ord, epId: ep.id,
            label: `Монтаж RJ ${tag} — ${conn.art || conn.name}`, tmplHint: cat === "cat5" ? "INS_RJ_cat5" : "INS_RJ_cat6/7", termArt: "" });
        } else if (conn) {
          ops.push({ type: "insTerm", opKey: endpointOpKey_(side, ord, "ins"), side, ord, epId: ep.id,
            label: `Монтаж в разъём ${tag} — ${conn.art || conn.name}`, cat: `Монтаж терминалов (${sideRu_(side)})`,
            termArt: term ? (term.art || term.name || "") : "" });
        }
      }
    }));
    multicoreCrimpsFirst_(ops);

    // ТУТ «на пайку» надевается прямо перед пайкой — после обжима и монтажа другой стороны, с той стороны, где паяют
    // (владелец 12.10.2026: «обычно сначала обжим, вставляем, потом одеть трубки и паять»). Режется она по-прежнему с проводами.
    const insTutAt = ops.findIndex((o) => o.opKey === "insTut");
    const solderAt = ops.findIndex((o) => solderStrips.has(o.opKey) || o.type === "solderConn");
    if (insTutAt >= 0 && solderAt > insTutAt) {
      const [insTut] = ops.splice(insTutAt, 1);
      ops.splice(solderAt - 1, 0, insTut);
    }

    if (ops.some((o) => o.opKey === "insTut")) {
      ops.push({ type: "heatTut", opKey: "heatTut", label: `Усадка ТУТ — ${solderEnds} шт`, tmplHint: "HEAT_TYT_short", cat: "Усадка", tutCount: solderEnds });
    }
    // «Сборка корпуса после пайки» (владелец 15.10): пайка → усадка ТУТ на чашках → сборка корпуса (кожух, винты, зажим
    // кабеля) → усадка наружных трубок стороны. Детали корпуса надеваются на кабель до пайки — в «Надеть трубки».
    let housingAt = ops.findIndex((o) => o.opKey === "heatTut");
    S.endpoints.filter(housingOn_).forEach((ep) => {
      const side = ep.side, ord = endpointOrdinal_(ep), conn = getCompById(ep.connId), tmpl = housingTmpl_(ep);
      const op: OpSpec = { type: "insHousing", opKey: "housing" + side + ord, side, ord, epId: ep.id, units: 1,
        ...(tmpl ? { tmplHint: tmpl, cat: "Монтаж корпуса" } : {}),
        label: `Сборка корпуса после пайки ${endKey_(ep)}${conn ? ` — ${conn.art || conn.name}` : ""}${tmpl ? "" : " (норма — уточнить)"}` };
      if (housingAt >= 0) { housingAt += 1; ops.splice(housingAt, 0, op); return; }
      const solder = ops.findIndex((o) => o.type === "solderConn" && o.epId === ep.id);
      ops.splice(solder >= 0 ? solder + 1 : ops.length, 0, op);
    });
    const sleeves = sleeveItems_();
    sleeveShrinks_(ops, sleeves);
    const seal = sealOp_();
    if (seal) { const h = ops.findIndex((o) => o.type === "insHousing"); ops.splice(h >= 0 ? h : ops.length, 0, seal); }
    if (S.twist.enabled && getTwistSelectionCount() > 0) {
      const twistOp: OpSpec = { type: "twist", opKey: "twist", label: "Свивка", cat: "Свивка" };
      // Сторона А собрана, потом свивка, потом сторона В. «Свивка после опресовки» (владелец 15.10): свивают обжатые
      // провода — опрессовка В идёт до свивки, монтаж В (разъём, пайка) — после неё. Опрессовки В, что стояли после
      // первого монтажа В (В2 после В1), уходят к ней вместе с разделкой своего конца.
      const firstB = ops.findIndex((o) => o.side === "B" && o.epId);
      const mountB = ops.findIndex((o, i) => i >= firstB && o.side === "B" && o.epId && o.type !== "prsTerm");
      if (firstB < 0) ops.push(twistOp);
      else if (mountB < 0) {
        // У стороны В только обжим (терминалы без колодки) — свивка сразу после последней опрессовки.
        const lastPrs = ops.reduce((last, o, i) => (o.type === "prsTerm" ? i : last), -1);
        ops.splice(Math.max(firstB, lastPrs + 1), 0, twistOp);
      } else {
        const lateCrimps = new Set(ops.filter((o, i) => i > mountB && o.side === "B" && o.type === "prsTerm").map((o) => o.ord));
        const late = (o: OpSpec, i: number) => i > mountB && o.side === "B" && lateCrimps.has(o.ord) && !solderStrips.has(o.opKey)
          && (o.type === "prsTerm" || o.type === "stripCable" || o.type === "endCap");
        const moved = ops.filter(late);
        const rest = ops.filter((o, i) => !late(o, i));
        rest.splice(mountB, 0, ...moved, twistOp);
        ops.splice(0, ops.length, ...rest);
      }
    }
    const tinOn = endpointsOnSide_("B").length === 0 && S.tin.enabled;
    const tinLeft = tinWireIndices_().map((i) => activeWires[i].idx).filter((idx) => !tinAtCut.has(idx));
    // Хвосты под лужение: без стороны В вместе с «Лужением свободных концов» — одна операция, иначе своя после резки.
    const tailTin = tailTinEnds_();
    if (tinOn && tinLeft.length > 0) {
      if (!tailTin.length) ops.push({ type: "tin", opKey: "tin", label: "Лужение", cat: "Лужение", ...(tinAtCut.size ? { wireIdxs: tinLeft } : {}) });
      else {
        const units = activeWires.filter((w) => tinLeft.includes(w.idx)).reduce((n, w) => n + unitCount_(w), 0) + tailTin.length;
        ops.push({ type: "tin", opKey: "tin", label: `Лужение свободных концов и хвостов — ${units} конц.`, cat: "Лужение",
          wireIdxs: [...new Set([...tinLeft, ...tailTin.map((e) => e.idx)])], units });
      }
    }
    const tailPrep = [tailStripOp_(), !(tinOn && tinLeft.length > 0) && tailTin.length ? tailTinOp_(tailTin) : null].filter((o): o is OpSpec => !!o);

    const dress: OpSpec[] = [];
    // Термоусадка на жгут режется и надевается вместе с прочими трубками до обжима (sleeveCuts_/sleeveOn_), здесь — усадка.
    const hsCount = sleeves.harness.reduce((n, s) => n + s.cnt, 0);
    if (hsCount) {
      dress.push({ type: "heatTut", opKey: "heatHs", tmplHint: "HEAT_TTK_short", cat: "Усадка", tutCount: hsCount, harness: true, label: `Усадка термоусадки на жгуте — ${hsCount} шт` });
    }
    const allKdMarks = S.mark ? markSpecs(S.markRows || [], comps()) : [];
    // «Сложная сборка»: метки-трубки из состава — свои резка, надевание и усадка (markTubeOps_), в «Маркировку» не идут.
    const tubeMarks = S.mark && isComplex_() ? markTubeSplit(S.markRows || [], comps()) : null;
    const kdMarks = tubeMarks ? tubeMarks.rest : allKdMarks;
    const markOps = markTubeOps_(tubeMarks ? tubeMarks.tubes : []);
    if (markOps.heat) dress.push(markOps.heat);
    const marks = S.mark && !allKdMarks.length ? markComps_() : [];
    if (kdMarks.length) {
      dress.push({ type: "insMark", opKey: "insMark", cat: "Монтаж маркировки", label: markLabel(kdMarks), marks: kdMarks });
    } else if (marks.length) {
      dress.push({ type: "insMark", opKey: "insMark", cat: "Монтаж маркировки", label: `Маркировка — ${marks.map((m) => m.art || m.name).join(", ")}`,
        marks: marks.map((m) => ({ art: m.art || m.name || "", name: m.name || "", qty: qtyNum(m) || 1 })) });
    }
    const mkWrap = (slotIndex: number): OpSpec | null => {
      const n = WRAP_SLOTS_[slotIndex];
      const slot = S.wrap.slots[slotIndex];
      const comp = S.wrap.enabled ? getCompById(slot.mat) : null;
      if (!comp) return null;
      const len = (slot.len || "").trim();
      const kind = slot.kind === "braid" ? "braid" : "tape";
      return { type: "insWrap", opKey: "insWrap" + n, tmplHint: kind === "braid" ? "INS_BRAID" : "INS_WRAP", cat: "Монтаж", wrapKind: kind,
        under: slot.kind === "under",
        label: `Монтаж ${kind === "braid" ? "оплётки" : "ленты"} — ${comp.art || comp.name}${len ? ", " + len + " мм" : ""}`,
        wrapArt: comp.art || comp.name || "", wrapName: comp.name || comp.art || "", wrapLen: len };
    };
    const wrapOps = (isComplex_() ? [0, 1, 2, 3] : [0]).map(mkWrap).filter((o): o is OpSpec => !!o);
    const braidOps = wrapOps.filter((o) => o.wrapKind === "braid");
    if (!braidOps.length) wrapOps.forEach((o) => { o.under = false; });
    // Оплётка (и лента под ней) надевается до обжима, как трубки (владелец 15.10) — вместе с ними, ниже.
    const slid = [...wrapOps.filter((o) => o.under), ...braidOps];
    if (dress.length && allRjEndpoints_()) {
      const glue = sleeves.harness.some((s) => /ттк|клеев/i.test(`${s.comp.name || ""} ${s.comp.art || ""}`));
      dress.forEach((o) => {
        if (o.opKey === "heatHs" && !glue) o.tmplHint = "HEAT_TYT_short";
        if (o.opKey === "heatHs") o.label = `Усадка термоусадки на хвостовиках RJ — ${hsCount} шт`;
      });
      let lastEp = -1;
      ops.forEach((o, i) => { if (o.epId) lastEp = i; });
      ops.splice(lastEp >= 0 ? lastEp + 1 : ops.length, 0, ...dress);
    } else if (dress.length) {
      const firstB = ops.findIndex((o) => o.side === "B" && o.epId);
      const tinAt = ops.findIndex((o) => o.type === "tin");
      ops.splice(firstB >= 0 ? firstB : (tinAt >= 0 ? tinAt : ops.length), 0, ...dress);
    }
    wrapOps.filter((o) => o.wrapKind === "tape" && !o.under).forEach((o) => ops.push(o));
    const prep = capOps_().concat(foldOps_(), tailPrep);
    if (prep.length) {
      let lastCut = -1;
      ops.forEach((o, i) => { if (/^cutWire/.test(o.type)) lastCut = i; });
      ops.splice(lastCut + 1, 0, ...prep);
    }
    // Трубки и оплётка — после резки и подготовки, до первого обжима, разделки у разъёма или пайки любой стороны: резка
    // трубок, «Надеть трубки (стороны А и В)», оплётка. ТУТ «на пайку» режется после них — её надевает своя операция
    // перед пайкой (иначе её отрезки ушли бы в «Надеть трубки»).
    const on = sleeveOn_(sleeves);
    const cuts = [...sleeveCuts_(sleeves), ...(markOps.cut ? [markOps.cut] : [])];
    const ons = [...(on ? [on] : []), ...(markOps.on ? [markOps.on] : [])];
    if (ons.length || cuts.length || slid.length) {
      const prepOp = (o: OpSpec) => /^cutWire/.test(o.type) || o.type === "cutTut" || o.type === "foldCore" || (o.type === "endCap" && !o.side);
      let at = ops.findIndex((o) => !prepOp(o));
      if (at < 0) at = ops.length;
      const solderCut = ops.findIndex((o, i) => i < at && o.opKey === "cutTut");
      const moved = solderCut >= 0 ? ops.splice(solderCut, 1) : [];
      if (moved.length) at -= 1;
      ops.splice(at, 0, ...cuts, ...ons, ...slid, ...moved);
    }
    return isComplex_() ? complexGraph_(ops) : ops;
  }
  /** Метки-трубки из состава «Сложной сборки» (владелец 16.10, 713К.4 КБ1 «Маркировка "5" 4/2мм», длина 10–15 мм по КД —
   *  верхняя): резка отрезков (по позициям состава), «Надеть маркировку» до монтажа разъёмов и усадка термоусадочной. */
  function markTubeOps_(tubes: MarkTube[]): { cut: OpSpec | null; on: OpSpec | null; heat: OpSpec | null } {
    if (!tubes.length) return { cut: null, on: null, heat: null };
    const count = tubes.reduce((n, t) => n + t.spec.qty, 0);
    const title = (t: MarkTube) => `«${t.spec.text || t.spec.target}»`;
    const parts: Array<{ art: string; name: string; count: number; len: string }> = [];
    tubes.forEach((t) => {
      const same = parts.find((p) => p.art === t.art && p.len === t.len);
      if (same) same.count += t.spec.qty; else parts.push({ art: t.art, name: t.name, count: t.spec.qty, len: t.len });
    });
    const segs: Array<{ len: string; count: number }> = [];
    parts.forEach((p) => { const same = segs.find((s) => s.len === p.len); if (same) same.count += p.count; else segs.push({ len: p.len, count: p.count }); });
    const sizes = tubes.map((t) => `${title(t)} ${t.spec.qty > 1 ? `${t.spec.qty}×` : ""}${t.len ? t.len.replace(".", ",") : "?"} мм`).join(", ");
    const one = parts.every((p) => p.art === parts[0].art) ? parts[0] : null;
    const atConn = tubes.every((t) => t.spec.placeKind === "conn");
    const heat = tubes.filter((t) => t.shrink).reduce((n, t) => n + t.spec.qty, 0);
    // Печать и резка на маркираторе ZCUT-RT660 (INS_MARK_TUBE_AUTO); нет карты в библиотеке — резка трубки, как раньше.
    const printed = hasTemplateCode_(MARK_CUT_CODE_);
    return {
      cut: { type: "cutTut", opKey: "cutMark", tmplHint: printed ? MARK_CUT_CODE_ : "CUT_TUT_short", tmplWant: MARK_CUT_CODE_, cat: "Резка", markTube: true,
        label: `${printed ? "Печать и резка" : "Резка"} маркировки — ${sizes}`,
        tutArt: one ? one.art : "", tutName: one ? one.name : "", tutCount: count, tutSegs: segs, tutParts: parts },
      on: { type: "insTut", opKey: "onMark", tmplHint: "INS_TUT", cat: "Монтаж", markTube: true, tutCount: count,
        label: `Надеть маркировку${atConn ? " у разъёмов" : ""} — ${tubes.map(title).join(", ")}` },
      heat: heat ? { type: "heatTut", opKey: "heatMark", tmplHint: "HEAT_TYT_short", cat: "Усадка", markTube: true, tutCount: heat,
        label: `Усадка маркировки — ${heat} шт` } : null,
    };
  }
  // ── Схема ПФ «Сложной сборки» (владелец 16.10, 713К.4 КБ1: «остальное сам додумай») ──
  /** Слоты оплётки формы (вид «оплётка», материал выбран). */
  function braidSlots_(): Array<{ comp: Comp; len: string }> {
    if (!S.wrap.enabled) return [];
    return S.wrap.slots.slice(0, isComplex_() ? 4 : 1).flatMap((slot) => {
      const comp = slot.kind === "braid" ? getCompById(slot.mat) : null;
      return comp ? [{ comp, len: (slot.len || "").trim() }] : [];
    });
  }
  /** Разъём стороны А, на ветвь которого надевают оплётку: разъём с проводами, «Оплётка ветви» не «0». */
  const braidEp_ = (ep: Endpoint) => isComplex_() && ep.side === "A" && !!ep.connId && braidSlots_().length > 0
    && activeWires_().some((w) => routedWireIdxs_(ep).includes(w.idx)) && (ep.braidLen ?? "").trim() !== "0";
  /** Длина оплётки ветви по умолчанию: «Длина на изделие» оплётки поровну на ветви с оплёткой; не задана — "". */
  function braidLenDefault_(slotLen?: string): string {
    const total = parseDecimal_(slotLen ?? braidSlots_()[0]?.len ?? "");
    const n = S.endpoints.filter(braidEp_).length;
    return total > 0 && n > 0 ? String(Math.round(total / n)) : "";
  }
  /** Отрезок оплётки ветви: свой у разъёма, иначе поровну от длины на изделие. */
  const braidLenOf_ = (ep: Endpoint, slotLen?: string) => (ep.braidLen || "").trim().replace(",", ".") || braidLenDefault_(slotLen);

  /** Схема ПФ, которую построил последний вызов списка операций «Сложной сборки». */
  let graph_: PfGraph | null = null;

  /** «Сложная сборка» (владелец 16.10): генератор сам режет жгут на ПФ и ставит операции в порядке схемы.
   *  1. Источники — ПФ-комплекты: каждая партия резки (обжим при резке зачищает и концы под сростки), хвосты «сросток →
   *     облуженный конец» (CUT_WIRE+2strip+tin), одна резка всех трубок, одна резка оплётки (отрезок на ветвь), печать и
   *     резка маркировки.
   *  2. Ветви — разъём стороны А с трубкой, оплёткой, маркировкой или свитой парой: монтаж контактов в разъём → свивка своей пары → надеть
   *     оплётку → надеть ТТК → надеть маркировку → усадка ТТК на разъёме → усадка маркировки.
   *  3. Группы сростков — сростки, провода которых приходят с одних и тех же разъёмов стороны А: свивка пар с разных
   *     разъёмов → сросток → трубка на готовый сросток → усадка (владелец 17.10).
   *  4. «ТУТ на месте Б» (владелец 17.10) — есть термоусадка на жгут: сростки и ветви прямо на сторону В → надеть её по КД
   *     → усадка. Нет её — нет и ПФ.
   *  5. Изделие — прочие трубки на ствол → усадка → разъёмы стороны В (и разъёмы А без ветви) → остальное.
   *  Стрелки — что идёт из ПФ в ПФ на одно изделие («НВ4 0,5 красный, черный — 2 шт», «ТТК 8/2 — 1 шт», «Оплётка XP-3
   *  120 мм — 1 шт», «Маркировка «5» — 1 шт»; из ветви и группы — их имя): сервер строит по ним «Вход» первой операции ПФ. */
  function complexGraph_(generic: OpSpec[]): OpSpec[] {
    const active = activeWires_();
    const wireOf = (idx: number) => active.find((w) => w.idx === idx) || null;
    const artOf = (c: Comp | null) => (c ? c.art || c.name || "" : "");
    const short = (art: string) => { const m = /^(.*?\d+(?:[.,]\d+)?\s*\/\s*\d+(?:[.,]\d+)?)/.exec(art); return m ? m[1].replace(/\s+/g, " ").trim() : art; };
    const mm = (len: string) => (len ? `${len.replace(".", ",")} мм` : "? мм");
    const FINAL = "pf-final", TUBES = "pf-tubes", BRAID = "pf-braid", MARK = "pf-mark", HS = "pf-hs";
    const epKey = (ep: Endpoint) => `pf-${ep.side}${endpointOrdinal_(ep)}`;
    // Разъёмы по КД — X1, X2… подряд: сначала сторона А, потом В (владелец 16.10, 713К.4: ветви X1–X4, «А и В — X4→X6»).
    const xOrder = [...endpointsOnSide_("A"), ...endpointsOnSide_("B")];
    const xKey = (ep: Endpoint) => `X${xOrder.findIndex((e) => e.id === ep.id) + 1}`;
    const xList = (eps: Array<Endpoint | null | undefined>) => xOrder.filter((e) => eps.some((x) => x && x.id === e.id)).map(xKey).join(", ");

    // ── Сростки: группы по разъёмам стороны А, с которых приходят их провода ──
    const sizes = spliceSizes_();
    const all = S.splices || [];
    const drawn = all.filter((s) => s.ends.length >= 2).slice(0, sizes.length);
    const rest = sizes.length - drawn.length;
    const groupKey = (s: Splice) => {
      const ids = (side: Side) => [...new Set(s.ends.flatMap((e) => {
        const w = wireOf(e.idx);
        const other: Side = e.side === "A" ? "B" : "A";
        const ep = w ? unitEndpoint_(w, e.unit, other) : null;
        return ep && ep.side === side ? [ep.id] : [];
      }))].sort();
      const a = ids("A"), b = ids("B");
      return a.length ? `A:${a.join(",")}` : b.length ? `B:${b.join(",")}` : `S:${s.id}`;
    };
    const keyOf = new Map(drawn.map((s) => [s.id, groupKey(s)]));
    // Провод между двумя сростками разных групп — группы сливаются (один ПФ).
    const spliceOfEnd = (idx: number, unit: number, side: Side) => drawn.find((s) => s.ends.some((e) => e.idx === idx && e.unit === unit && e.side === side)) || null;
    active.forEach((w) => {
      for (let k = 0; k < unitCount_(w); k++) {
        const a = spliceOfEnd(w.idx, k, "A"), b = spliceOfEnd(w.idx, k, "B");
        if (!a || !b) continue;
        const ka = keyOf.get(a.id)!, kb = keyOf.get(b.id)!;
        if (ka !== kb) keyOf.forEach((value, id) => { if (value === kb) keyOf.set(id, ka); });
      }
    });
    const groupKeys = [...new Set(drawn.map((s) => keyOf.get(s.id)!))];
    const verb = { solder: "Пайка", sleeve: "Опрессовка", band: "Ленточный обжим", ultrasonic: "Сварка" }[splMethod_()];
    type Group = { key: string; splices: Splice[]; rest: number; names: string; name: string };
    const groups: Group[] = groupKeys.map((key, i) => {
      const splices = drawn.filter((s) => keyOf.get(s.id) === key);
      const names = splices.map((s) => `С${all.indexOf(s) + 1}`).join(", ");
      return { key: `pf-spl${i + 1}`, splices, rest: 0, names, name: `${verb} ${names}` };
    });
    if (rest > 0) groups.push({ key: `pf-spl${groups.length + 1}`, splices: [], rest, names: "", name: drawn.length ? `${verb} остальных сростков` : `${verb} сростков` });
    const groupOfEnd = (idx: number, unit: number, side: Side) => {
      const s = spliceOfEnd(idx, unit, side);
      return s ? groups.find((g) => g.splices.includes(s)) || null : null;
    };

    // ── Свивка: пара внутри ветви (с одного разъёма А в сростки) — в ветви, пара с разных разъёмов — в группе сростков,
    //    пара до разъёмов стороны В — в изделии, там, где её ставит генератор (после опрессовки В, до монтажа В) ──
    const twistSets = !S.twist.enabled || !getTwistSelectionCount() ? []
      : S.twist.mode === "pairs" ? getTwistPairs() : [twistWireIndices_()];
    const twistAt = new Map<string, number>(); // "ep:<id>" | ключ группы | FINAL → пар
    // Свои пары каждой свивки (владелец 17.10: «Свивка X2 «I3» — 1 пара» несла в ЭТК обе пары формы) — номера проводов.
    const twistPairsAt = new Map<string, number[][]>();
    const addPairs = (at: string, list: number[][]) => {
      const own = twistPairsAt.get(at) || [];
      list.forEach((p) => { if (!own.some((x) => x.join() === p.join())) own.push(p); });
      twistPairsAt.set(at, own);
    };
    twistSets.forEach((positions) => {
      const ws = positions.map((i) => active[i]).filter((w): w is WireEntry => !!w);
      if (ws.length < 2) return;
      const n = Math.min(...ws.map(unitCount_));
      for (let k = 0; k < n; k++) {
        const eps = ws.map((w) => unitEndpoint_(w, k, "A"));
        let at = FINAL;
        if (eps[0] && eps.every((ep) => ep === eps[0]) && ws.every((w) => endJoined(S, w.idx, k, "B"))) at = `ep:${eps[0].id}`;
        else {
          const hit = [...new Set(ws.flatMap((w) => (["A", "B"] as Side[]).map((side) => groupOfEnd(w.idx, k, side)?.key).filter(Boolean)))];
          if (hit.length === 1) at = hit[0]!;
        }
        twistAt.set(at, (twistAt.get(at) || 0) + 1);
        addPairs(at, [positions]);
      }
    });

    // ── Маркировка-трубки: к разъёму по «Что маркируем» («Разъём 5 (SMR-02V-B)») ──
    const markTubes = S.mark ? markTubeSplit(S.markRows || [], comps()).tubes : [];
    const markEp = (t: MarkTube) => {
      const target = t.spec.target.trim();
      return S.endpoints.find((ep) => { const name = (ep.label || "").trim() || endKey_(ep); return target === `Разъём ${name}` || target.startsWith(`Разъём ${name} (`); }) || null;
    };
    const sleeves = sleeveItems_();
    // Термоусадка на жгут — свой ПФ «ТУТ на месте Б» перед разъёмами стороны В (владелец 17.10); нет её — нет и ПФ.
    const hsOn = sleeves.harness.some((x) => x.cnt > 0);

    // ── Ветви стороны А ──
    const branches = endpointsOnSide_("A").filter((ep) => !!ep.connId && (!!shrinkOf_(ep) || braidEp_(ep) || twistAt.has(`ep:${ep.id}`)
      || markTubes.some((t) => markEp(t) === ep)));
    // Пара на разъёме без ветви (терминал без корпуса) — свивка в изделии.
    [...twistAt.keys()].forEach((at) => {
      if (!at.startsWith("ep:") || branches.some((ep) => `ep:${ep.id}` === at)) return;
      twistAt.set(FINAL, (twistAt.get(FINAL) || 0) + twistAt.get(at)!);
      twistAt.delete(at);
      addPairs(FINAL, twistPairsAt.get(at) || []);
      twistPairsAt.delete(at);
    });
    const branchOf = (ep: Endpoint | null) => (ep && branches.includes(ep) ? ep : null);
    const blockOfEp = (ep: Endpoint | null) => (branchOf(ep) ? epKey(ep!) : FINAL);
    const markBlock = (t: MarkTube) => blockOfEp(markEp(t));
    const markTitle = (t: MarkTube) => `«${t.spec.text || t.spec.target}»`;
    /** «X2 «I3»»: разъём по КД и его надпись — текст его маркировки, иначе подпись разъёма, если она не то же «X2»
     *  (713К.4 КБ1: подписи разъёмов «X1»…«X4» давали «Ветвь X1 «X1»», владелец 17.10). */
    const xTag = (ep: Endpoint) => {
      const own = (ep.label || "").trim();
      const label = markTubes.find((t) => markEp(t) === ep && t.spec.text)?.spec.text || (own !== xKey(ep) ? own : "");
      return label ? `${xKey(ep)} «${label}»` : xKey(ep);
    };

    // ── Операции генератора: что остаётся как есть и куда ──
    const epOfOp = (o: OpSpec) => (o.epId ? epById_(o.epId) : o.side && o.ord ? endpointsOnSide_(o.side)[o.ord - 1] || null : null);
    const solderEp = S.endpoints.find((e) => e.method === "solder" && !e.tutOff && !!e.connId) || null;
    const finalTwist = twistAt.get(FINAL) || 0;
    const rebuilt = (o: OpSpec) => o.type === "cutTut"
      || ["insSleeves", "onMark", "heatMark", "insSpl", "heatSpl", "splice", "stripSpl"].includes(o.opKey)
      || (o.opKey === "twist" && !finalTwist)
      || (o.type === "insWrap" && o.wrapKind === "braid")
      || (o.type === "heatTut" && /^heatShr/.test(o.opKey) && !!branchOf(epOfOp(o)));
    const prepKeys = (o: OpSpec) => o.type === "foldCore" || (o.type === "endCap" && !o.side) || o.opKey === "stripTail" || o.opKey === "tinTail";
    const cutOps = generic.filter((o) => /^cutWire/.test(o.type));
    const blockOf = (o: OpSpec): string => {
      if (o.opKey === "insTut" || o.opKey === "heatTut") return blockOfEp(solderEp);
      const ep = epOfOp(o);
      return ep ? blockOfEp(ep) : FINAL;
    };
    // Свивка изделия — не все пары формы (часть свита в ветвях и группах сростков): своё число пар.
    const total = [...twistAt.values()].reduce((n, x) => n + x, 0);
    // «Монтаж контактов в разъём X2 «I3» — SMR-02V-B»: разъём — по КД, как в названиях ПФ.
    const mounted = (o: OpSpec): OpSpec => {
      const ep = o.type === "insTerm" ? epOfOp(o) : null;
      const m = ep ? /^Монтаж в разъём \S+ — (.*)$/.exec(o.label) : null;
      return m ? { ...o, label: `Монтаж контактов в разъём ${xTag(ep!)} — ${m[1]}` } : o;
    };
    const kept = generic.filter((o) => !/^cutWire/.test(o.type) && !rebuilt(o))
      .map((o) => mounted(o.opKey === "twist"
        ? { ...o, ...(finalTwist < total ? { units: finalTwist } : {}), twistPairs: twistPairsAt.get(FINAL) || [] } : o));

    /** Имя ПФ партии резки: что делает станок и на какие разъёмы по КД («Резка + обжим SYM, зачистка — X1, X2»,
     *  «Резка + обжим А и В — X4→X6», «Резка хвостов + лужение»). */
    const cutName = (o: OpSpec): string => {
      const eps = (ids: string[] | undefined, id: string | undefined) => (ids?.length ? ids : id ? [id] : []).map((x) => epById_(x));
      if (o.type === "cutWireCrimp" && o.crimpEpIdB) return `Резка + обжим А и В — ${xList(eps(o.crimpEpIds, o.crimpEpId))}→${xList(eps(o.crimpEpIdsB, o.crimpEpIdB))}`;
      if (o.type === "cutWireCrimp") {
        const other = o.side === "A" ? o.strip?.b : o.strip?.a;
        const ends = xList(eps(o.crimpEpIds, o.crimpEpId));
        return `Резка + обжим ${termShort_(o.crimpTerms || "")}${o.tin ? ", лужение" : other ? ", зачистка" : ""}${ends ? ` — ${ends}` : ""}`;
      }
      if (o.type === "cutWireStrip" && o.tin) return "Резка хвостов + лужение";
      const ends = xList((o.cutUnits || []).flatMap(([idx, k]) => { const w = wireOf(idx); return w ? [unitEndpoint_(w, k, "A"), unitEndpoint_(w, k, "B")] : []; }));
      return `${o.type === "cutWire" ? "Резка проводов" : "Резка + зачистка"}${ends ? ` — ${ends}` : ""}`;
    };

    // ── Стрелки ──
    const edgeItems =new Map<string, Map<string, number>>(); // "from>to" → подпись → штук (0 — без числа)
    const link = (from: string, to: string, item = "", count = 0) => {
      if (from === to) return;
      const id = `${from}>${to}`;
      const items = edgeItems.get(id) || new Map<string, number>();
      if (item) items.set(item, (items.get(item) || 0) + count);
      edgeItems.set(id, items);
    };
    const level = (key: string) => (key === FINAL ? 4 : key.startsWith("pf-spl") ? 3 : 2);
    const names = new Map<string, string>();
    branches.forEach((ep) => names.set(epKey(ep), `Ветвь ${xTag(ep)}, сторона ${sideRu_(ep.side)}`));
    groups.forEach((g) => names.set(g.key, g.name));
    const endBlock = (w: WireEntry, k: number, side: Side): string | null => {
      const join = isComplex_() ? endJoined(S, w.idx, k, side) : null;
      if (join === "splice") return groupOfEnd(w.idx, k, side)?.key ?? (groups.find((g) => g.rest)?.key || null);
      if (join === "tail") return null;
      const ep = unitEndpoint_(w, k, side);
      return ep ? blockOfEp(ep) : null;
    };
    const flows: Array<{ from: string; to: string; art: string }> = [];
    cutOps.forEach((op) => (op.cutUnits || []).forEach(([idx, k]) => {
      const w = wireOf(idx);
      if (!w) return;
      const chain = [...new Set([endBlock(w, k, "A"), endBlock(w, k, "B")].filter((x): x is string => !!x))].sort((a, b) => level(a) - level(b));
      if (!chain.length) chain.push(FINAL);
      flows.push({ from: `pf-${op.opKey}`, to: chain[0], art: artOf(getCompById(w.compId)) || "провод" });
      chain.slice(1).forEach((to, i) => link(chain[i], to, names.get(chain[i]) || ""));
    }));
    flows.forEach((f) => link(f.from, f.to, f.art, 1));

    // ── 1. Резка трубок: одна операция на все отрезки ТТК / ТУТ ──
    type Piece = { art: string; name: string; len: string; count: number; to: string; glue?: boolean };
    const tubes: Piece[] = [];
    const tubeOf = (c: Comp) => ({ art: c.art || c.name || "", name: c.name || c.art || "", glue: isGlueSleeve_(c) });
    sleeves.conn.forEach((x) => tubes.push({ ...tubeOf(x.comp), len: x.len, count: 1, to: blockOfEp(x.ep) }));
    sleeves.jacket.forEach((x) => tubes.push({ ...tubeOf(x.comp), len: x.len, count: x.count, to: FINAL }));
    sleeves.harness.forEach((x) => tubes.push({ ...tubeOf(x.comp), len: x.len, count: x.cnt, to: hsOn ? HS : FINAL }));
    const splTube = spliceTube_();
    if (splTube) groups.forEach((g) => tubes.push({ ...tubeOf(splTube.comp), len: splTube.len, count: g.splices.length + g.rest, to: g.key }));
    generic.filter((o) => o.opKey === "cutCap" || o.opKey === "cutTut").forEach((o) => (o.tutSegs || []).forEach((seg) => tubes.push({
      art: o.tutArt || "", name: o.tutName || o.tutArt || "", len: seg.len, count: seg.count, to: o.opKey === "cutCap" ? FINAL : blockOfEp(solderEp) })));
    const sources: OpSpec[] = cutOps.map((o) => ({ ...o, pf: `pf-${o.opKey}` }));
    const parts = (list: Piece[]) => {
      const out: Array<{ art: string; name: string; count: number; len: string }> = [];
      list.forEach((p) => { const same = out.find((x) => x.art === p.art && x.len === p.len); if (same) same.count += p.count; else out.push({ art: p.art, name: p.name, count: p.count, len: p.len }); });
      return out;
    };
    const segsOf = (list: Array<{ len: string; count: number }>) => {
      const out: Array<{ len: string; count: number }> = [];
      list.forEach((p) => { const same = out.find((x) => x.len === p.len); if (same) same.count += p.count; else out.push({ len: p.len, count: p.count }); });
      return out;
    };
    const tubeList = tubes.filter((p) => p.count > 0);
    if (tubeList.length) {
      const own = parts(tubeList);
      const one = own.every((p) => p.art === own[0].art) ? own[0] : null;
      sources.push({ type: "cutTut", opKey: "cutTubes", tmplHint: "CUT_TUT_short", cat: "Резка", pf: TUBES,
        label: `Резка трубок — ${own.map((p) => `${short(p.art)} ${p.count}×${mm(p.len)}`).join(", ")}`,
        tutArt: one ? one.art : "", tutName: one ? one.name : "", tutCount: own.reduce((n, p) => n + p.count, 0), tutSegs: segsOf(own), tutParts: own });
      tubeList.forEach((p) => link(TUBES, p.to, short(p.art), p.count));
    }
    // ── 1. Резка оплётки: отрезок на каждую ветвь ──
    const braids = braidSlots_();
    const braided = branches.filter(braidEp_);
    if (braids.length && braided.length) {
      const pieces: Piece[] = braids.flatMap((slot) => braided.map((ep) => ({ ...tubeOf(slot.comp), len: braidLenOf_(ep, slot.len), count: 1, to: epKey(ep) })));
      const own = parts(pieces);
      const one = own.every((p) => p.art === own[0].art) ? own[0] : null;
      sources.push({ type: "cutTut", opKey: "cutBraid", tmplHint: BRAID_CUT_CODE_, cat: "Резка", pf: BRAID, wrapKind: "braid",
        label: `Резка оплётки ${braids.map((s) => artOf(s.comp)).join(", ")} — ${braided.map((ep) => `${xTag(ep)} ${mm(braidLenOf_(ep, braids[0].len))}`).join(", ")}`,
        tutArt: one ? one.art : "", tutName: one ? one.name : "", tutCount: pieces.length, tutSegs: segsOf(own), tutParts: own });
      pieces.forEach((p) => link(BRAID, p.to, `Оплётка ${p.art} ${mm(p.len)}`, 1));
    }
    // ── 1. Печать и резка маркировки ──
    const markCut = generic.find((o) => o.opKey === "cutMark");
    if (markCut) {
      sources.push({ ...markCut, pf: MARK });
      markTubes.forEach((t) => link(MARK, markBlock(t), `Маркировка ${markTitle(t)}`, t.spec.qty));
    }

    // ── 2. Ветви ──
    // Порядок ветви (владелец 16.10, 17.10): монтаж контактов в разъём → свивка своей пары → надеть оплётку → надеть ТТК → надеть
    // маркировку → усадка ТТК на разъёме → усадка маркировки. Каждая — своя операция со своей картой;
    // детали корпуса паяльного разъёма надеваются до пайки — первыми.
    const branchOps: OpSpec[] = branches.flatMap((ep) => {
      const key = epKey(ep), side = ep.side, ord = endpointOrdinal_(ep), name = xTag(ep);
      const shrink = sleeves.conn.find((x) => x.ep === ep) || null;
      const marks = markTubes.filter((t) => markEp(t) === ep);
      const marked = marks.map(markTitle).join(", ");
      const markCount = marks.reduce((n, t) => n + t.spec.qty, 0);
      const out: OpSpec[] = [];
      if (sleeves.housings.includes(ep)) {
        out.push({ type: "insTut", opKey: `onHousing${side}${ord}`, tmplHint: "INS_TUT", cat: "Монтаж", harness: true, side, ord, tutCount: 1,
          branch: name, label: `Надеть детали корпуса ${name}` });
      }
      out.push(...kept.filter((o) => blockOf(o) === key));
      const pairs = twistAt.get(`ep:${ep.id}`) || 0;
      if (pairs) out.push({ type: "twist", opKey: `twist${side}${ord}`, cat: "Свивка", units: pairs, side, ord, branch: name,
        twistPairs: twistPairsAt.get(`ep:${ep.id}`) || [],
        label: `Свивка ${name} — ${pairs} ${pluralRu(pairs, "пара", "пары", "пар")}` });
      if (braided.includes(ep)) braids.forEach((slot, i) => {
        const len = braidLenOf_(ep, slot.len);
        out.push({ type: "insWrap", opKey: `braid${side}${ord}${i ? i + 1 : ""}`, tmplHint: "INS_BRAID", cat: "Монтаж", wrapKind: "braid", wrapCut: true,
          wrapArt: artOf(slot.comp), wrapName: slot.comp.name || artOf(slot.comp), wrapLen: len, side, ord, branch: name,
          label: `Надеть оплётку ${name} — ${artOf(slot.comp)}, ${mm(len)}` });
      });
      const tube = shrink ? { art: artOf(shrink.comp), name: shrink.comp.name || artOf(shrink.comp), kind: tubeKind_(shrink.comp) } : null;
      if (tube) {
        out.push({ type: "insTut", opKey: `onTubes${side}${ord}`, tmplHint: "INS_TUT", cat: "Монтаж", harness: true, side, ord, tutCount: 1,
          tutArt: tube.art, tutName: tube.name, branch: name, label: `Надеть ${tube.kind} ${name} — ${short(tube.art)}` });
      }
      if (marks.length) {
        out.push({ type: "insTut", opKey: `onMark${side}${ord}`, tmplHint: "INS_TUT", cat: "Монтаж", markTube: true, side, ord, tutCount: markCount,
          branch: name, label: `Надеть маркировку ${name} — ${marked}` });
      }
      if (tube) {
        out.push({ type: "heatTut", opKey: `heatShr${side}${ord}`, side, ord, harness: true, cat: "Усадка", tutCount: 1, tutArt: tube.art, tutName: tube.name,
          tmplHint: isGlueSleeve_(shrink!.comp) ? "HEAT_TTK_short" : "HEAT_TYT_short", label: `Усадка ${tube.kind} на разъёме ${name} — ${short(tube.art)}` });
      }
      const shrunk = marks.filter((t) => t.shrink);
      if (shrunk.length) {
        out.push({ type: "heatTut", opKey: `heatMark${side}${ord}`, tmplHint: "HEAT_TYT_short", cat: "Усадка", markTube: true, side, ord,
          tutCount: shrunk.reduce((n, t) => n + t.spec.qty, 0), branch: name, label: `Усадка маркировки ${name} — ${shrunk.map(markTitle).join(", ")}` });
      }
      return out.map((o) => ({ ...o, pf: key }));
    });

    // ── 3. Группы сростков ──
    const groupOps: OpSpec[] = groups.flatMap((g, i) => {
      const out: OpSpec[] = [];
      const pairs = twistAt.get(g.key) || 0;
      if (pairs) out.push({ type: "twist", opKey: `twistS${i + 1}`, cat: "Свивка", units: pairs, twistPairs: twistPairsAt.get(g.key) || [],
        label: `Свивка перед сростками${g.names ? ` ${g.names}` : ""} — ${pairs} ${pluralRu(pairs, "пара", "пары", "пар")}` });
      const groupSizes = g.splices.length ? g.splices.map((s) => Math.max(2, s.ends.length)) : sizes.slice(drawn.length);
      out.push(...spliceGroupOps_(groupSizes, g.names, groups.length > 1 ? i + 1 : 0, groupSleeves_(g.splices.map((s) => s.id), g.rest)));
      if (groups.length > 1) out.forEach((o) => { o.branch = g.names || "остальные"; });
      link(g.key, FINAL, g.name);
      // Сростки без точки на схеме: их провода — из всех партий резки.
      if (g.rest) cutOps.forEach((o) => link(`pf-${o.opKey}`, g.key));
      return out.map((o) => ({ ...o, pf: g.key }));
    });

    // ── 4. ТУТ на месте Б (владелец 17.10): сростки, ветви прямо на сторону В и трубки на жгут → надеть по КД → усадка ──
    const heatHs = kept.find((o) => o.opKey === "heatHs");
    const hsOps: OpSpec[] = [];
    if (hsOn) {
      const pieces = sleeves.harness.reduce((n, s) => n + s.cnt, 0);
      hsOps.push({ type: "insTut", opKey: "insSleeves", tmplHint: "INS_TUT", cat: "Монтаж", harness: true, tutCount: pieces,
        label: `Надеть трубки: ${sleeves.harness.map((s) => `${artOf(s.comp)} на жгут — ${s.cnt} шт`).join("; ")}` });
      if (heatHs) hsOps.push(heatHs);
    }

    // ── 5. Изделие: трубки на ствол (без ПФ «ТУТ на месте Б») → усадка → разъёмы В и разъёмы А без ветви → остальное ──
    const finalOn: string[] = [];
    let finalPieces = 0;
    const tube = (c: Comp) => { const name = artOf(c); return /^\s*(?:тут|ттк)/i.test(name) ? name : `${tubeKind_(c)} ${name}`; };
    sleeves.jacket.forEach((x) => { finalOn.push(`${tube(x.comp)} на место разделки ${sideRu_(x.side)} — ${x.count} шт`); finalPieces += x.count; });
    sleeves.conn.filter((x) => !branchOf(x.ep)).forEach((x) => { finalOn.push(`${tube(x.comp)} поверх разъёма ${x.tag}`); finalPieces += 1; });
    const finalMarks = markTubes.filter((t) => markBlock(t) === FINAL);
    finalMarks.forEach((t) => { finalOn.push(`маркировка «${t.spec.text || t.spec.target}»`); finalPieces += t.spec.qty; });
    const tubesOn = finalOn.length;
    sleeves.housings.filter((ep) => !branchOf(ep)).forEach((ep) => { const c = getCompById(ep.connId); finalOn.push(`детали корпуса ${endKey_(ep)}${c ? ` (${artOf(c)})` : ""}`); });
    const finalOps: OpSpec[] = [...kept.filter((o) => blockOf(o) === FINAL && prepKeys(o))];
    // При ПФ «ТУТ на месте Б» трубки изделия — своим ключом и подписью «перед разъёмами»: ключ ЭТК «на жгут» уже занят.
    if (finalOn.length) finalOps.push({ type: "insTut", opKey: hsOn ? "insSleevesB" : "insSleeves", tmplHint: "INS_TUT", cat: "Монтаж", harness: true,
      tutCount: finalPieces || 1, ...(hsOn ? { branch: "перед разъёмами" } : {}),
      label: `${tubesOn ? "Надеть трубки" : "Надеть детали корпуса"}: ${finalOn.join("; ")}` });
    if (heatHs && !hsOn) finalOps.push(heatHs);
    finalOps.push(...kept.filter((o) => blockOf(o) === FINAL && !prepKeys(o) && o !== heatHs));
    const markHeat = finalMarks.filter((t) => t.shrink).reduce((n, t) => n + t.spec.qty, 0);
    if (markHeat) finalOps.push({ type: "heatTut", opKey: "heatMark", tmplHint: "HEAT_TYT_short", cat: "Усадка", markTube: true, tutCount: markHeat,
      label: `Усадка маркировки — ${markHeat} шт` });

    // Ветвь, которая никуда не ведёт, и любой ПФ без выхода — в изделие.
    const ordered = [...sources, ...branchOps, ...groupOps, ...hsOps.map((o) => ({ ...o, pf: HS })), ...finalOps.map((o) => ({ ...o, pf: FINAL }))];
    const used = new Set(ordered.map((o) => o.pf!));
    if (used.has(HS)) names.set(HS, "ТУТ на месте Б");
    used.forEach((key) => { if (key !== FINAL && ![...edgeItems.keys()].some((id) => id.startsWith(`${key}>`))) link(key, FINAL, names.get(key) || ""); });
    // Ветви и группы сростков идут в изделие через ПФ «ТУТ на месте Б»: трубка на жгут надевается на собранный ствол.
    if (used.has(HS)) {
      const kits = new Set([TUBES, BRAID, MARK, ...cutOps.map((o) => `pf-${o.opKey}`)]);
      [...edgeItems.entries()].forEach(([id, items]) => {
        const [from, to] = id.split(">");
        if (to !== FINAL || from === HS || kits.has(from)) return;
        edgeItems.delete(id);
        items.forEach((count, item) => link(from, HS, item, count));
        if (!items.size) link(from, HS);
      });
    }
    // Короткие разные имена ПФ (владелец 16.10): «Резка + обжим SYM, зачистка — X1, X2», «Резка хвостов + лужение»,
    // «Резка трубок», «Резка оплётки», «Маркировка», ветви, группы сростков; изделие — наименование сборки.
    names.set(TUBES, "Резка трубок"); names.set(BRAID, "Резка оплётки"); names.set(MARK, "Маркировка");
    names.set(FINAL, findKey(/наименование/i).trim() || "Изделие");
    const taken = new Map<string, number>();
    cutOps.forEach((o) => {
      const name = cutName(o);
      const n = (taken.get(name) || 0) + 1;
      taken.set(name, n);
      names.set(`pf-${o.opKey}`, n > 1 ? `${name} (${n})` : name);
    });
    const blocks: PfBlock[] = [...used].map((key) => ({
      key, name: names.get(key) || "",
      ...(key === TUBES || key === BRAID || key === MARK || cutOps.some((o) => `pf-${o.opKey}` === key) ? { kit: true } : {}),
      ...(key === FINAL ? { final: true } : {}),
    }));
    if (!used.has(FINAL)) blocks.push({ key: FINAL, name: names.get(FINAL) || "", final: true });
    // Что идёт по стрелке — строкой «Вход» ЭТК на ПФ (владелец 16.10): провода партии одной строкой «НВ4 0,12 белый, синий —
    // 2 шт», трубки, отрезки оплётки и маркировки — каждая своей «ТТК 8/2 — 1 шт»; из ветви и группы — их имя.
    const cutKeys = new Set(cutOps.map((o) => `pf-${o.opKey}`));
    const edges: PfEdge[] = [...edgeItems.entries()].map(([id, items]) => {
      const [from, to] = id.split(">");
      const list = [...items.entries()];
      return { from, to, items: cutKeys.has(from) && list.some(([, count]) => count) ? [wiresItem_(list)]
        : list.map(([item, count]) => (count ? `${item} — ${count} шт` : item)) };
    }).filter((e) => blocks.some((b) => b.key === e.from) && blocks.some((b) => b.key === e.to));
    graph_ = { blocks, edges };
    return ordered;
  }

  // ── Шаблоны и времена ──
  function pickCoaxTemplate_(hint: string, side?: Side) {
    const norm = (s: string) => String(s || "").toLowerCase().replace(/\s+/g, "");
    const target = norm(hint);
    const cands = (D.templates || []).filter((t) => norm(String(t.title || "").split("|")[0]) === target);
    if (!cands.length) return "";
    if (!side) return cands[0].id;
    const re = side === "A" ? /\([АA]\)/ : /\([BВ]\)/;
    return (cands.find((t) => re.test(String(t.title || ""))) || cands[0]).id;
  }
  function autoPickTemplate(spec: OpSpec) {
    const opKey = spec.opKey;
    if (S.selectedTemplates[opKey]) return S.selectedTemplates[opKey];
    const cat = spec.cat || OP_CATEGORY_MAP[opKey];
    if (!cat) return "";
    const n = (s: string) => (s || "").replace(/\(A\)/g, "(А)").replace(/\(B\)/g, "(В)").toLowerCase().replace(/[_\-|.+()\s]+/g, " ").trim();
    const stripSide = (s: string) => s.replace(/ [ав]$/, "");
    const target = n(cat);
    const sideMatch = target.match(/ ([ав])$/);
    const sideWanted = sideMatch ? sideMatch[1] : "";
    const targetBase = stripSide(target);
    if (!targetBase) return "";
    const ec = (t: Tmpl) => {
      const stored = (t.category || "").trim();
      const raw = stored ? n(stored) : (() => { const p = (t.title || "").split("|"); return p.length > 1 ? n(p[p.length - 1]) : ""; })();
      return stripSide(raw);
    };
    const best = (D.templates || []).filter((t) => ec(t) === targetBase);
    if (!best.length) return "";
    const sideRe = sideWanted === "а" ? /\([АA]\)/ : sideWanted === "в" ? /\([BВ]\)/ : null;
    let cands = best;
    if (sideRe) { const sided = best.filter((t) => sideRe.test(String(t.title || ""))); if (sided.length) cands = sided; }
    if (cands.length === 1) return cands[0].id;
    if (spec.termArt) {
      const terRec = findTerRecord_(spec.termArt);
      const step = terRec ? (terRec.step || "") : "";
      if (step) {
        const sd = step.replace(",", "."), sc = step.replace(".", ",");
        const stepHit = cands.find((t) => t.title.includes(sd) || t.title.includes(sc));
        if (stepHit) return stepHit.id;
      }
    }
    return cands[0].id;
  }
  function pickTemplateForOp_(spec: OpSpec) {
    if (spec.tmplHint) return pickCoaxTemplate_(spec.tmplHint, spec.side);
    if (spec.opKey === "cutWire" && (spec.stripCount || 0) > 0) {
      const re = new RegExp("cut_wire\\+" + spec.stripCount + "strip", "i");
      const hit = (D.templates || []).find((t) => re.test(String(t.title || "").split("|")[0]));
      if (hit) return hit.id;
    }
    return autoPickTemplate(spec);
  }
  /** renderOpsCard: сохранённый шаблон операции (ручной или автоподобранный при первом показе — он «залипает»),
   *  иначе автоподбор. Резка, лента/оплётка и коакс без ручного выбора пересчитываются всегда: ключи резки
   *  cutWire1…N идут по порядку партий, и при перегруппировке тот же ключ достаётся резке другого вида. */
  function templateIdFor(spec: OpSpec) {
    // Залипший (не ручной) шаблон с другим кодом, чем просит правило генератора, тоже пересчитывается, если карта с
    // нужным кодом есть: 713К.4 КБ1 (17.10) — резка маркировки ушла на INS_MARK_TUBE_AUTO, а у ключа cutMark остался
    // CUT_TUT_short (резка трубок: не тот станок, не те дефекты).
    const stuck = S.selectedTemplates[spec.opKey];
    const want = (spec.tmplWant || spec.tmplHint || "").trim();
    const stuckCode = stuck ? String((D.templates || []).find((t) => t.id === stuck)?.title || "").split("|")[0].trim() : "";
    const outdated = !!stuck && !!want && !!stuckCode && stuckCode.toLowerCase() !== want.toLowerCase() && hasTemplateCode_(want);
    const autoRepick = (/^cutWire\d*$/.test(spec.opKey) || spec.type === "insWrap" || S.tab === "coax" || outdated)
      && !S.manualTemplates.includes(spec.opKey);
    return (autoRepick ? pickTemplateForOp_(spec) : (S.selectedTemplates[spec.opKey] || pickTemplateForOp_(spec))) || "";
  }
  /** renderOpsCard: `selectedTemplates[opKey] = sel.value` для каждой показанной операции. null — запоминать нечего. */
  function stickTemplates() {
    let next: Record<string, string> | null = null;
    for (const spec of getActiveOps()) {
      const id = templateIdFor(spec);
      if (id && S.selectedTemplates[spec.opKey] !== id) next = { ...(next || S.selectedTemplates), [spec.opKey]: id };
    }
    return next;
  }
  function matchOpByTitle(title: string) {
    const ops = D.ops || [];
    if (!title || !ops.length) return null;
    const pipeSep = title.indexOf(" | ");
    if (pipeSep <= 0) return null;
    const code = title.substring(0, pipeSep).trim().toLowerCase();
    return ops.find((op) => (op.opNumber || "").trim().toLowerCase() === code) || null;
  }
  /** onTemplateSelect: запись БД.ОП по коду шаблона + времена из неё. Ручная правка времён живёт до следующей
   *  перерисовки карточки операций (любой другой правки формы) — тогда времена снова из БД.ОП, как в SUPPLYMAP. */
  function opRow(spec: OpSpec) {
    const templateId = templateIdFor(spec);
    const tmpl = (D.templates || []).find((t) => t.id === templateId);
    const title = tmpl ? tmpl.title + " " + (tmpl.category || "") : "";
    const matched = matchOpByTitle(title);
    const nt = (v: string) => (v ? String(v).replace(",", ".") : "");
    const own = S.times[spec.opKey];
    const times = own && own.tmpl === templateId ? own
      : { tmpl: templateId, tPrep: matched ? nt(matched.tPrep) : "", tOp: matched ? nt(matched.tOp) : "", tMachine: matched ? nt(matched.tMachine) : "" };
    const info = matched ? `✓ БД.ОП: ${matched.opName}${matched.opNumber ? " | " + matched.opNumber : ""}`
      : title.trim() ? "— операция в БД.ОП не найдена, введите времена вручную" : "— выберите шаблон —";
    return { templateId, matched, times, info };
  }

  // ── Config ──
  function findKey(pattern: RegExp) {
    const e = Object.entries(D.assemblyInfo || {}).find(([k]) => pattern.test(k));
    return e ? String(e[1] ?? "") : "";
  }
  function buildConfigCoax_() {
    const sideB = !!S.coax.sideB;
    const recA = coaxRecForSide_("A");
    const recB = sideB ? coaxRecForSide_("B") : null;
    const mkSide = (rec: CoaxRec | null, art: string, pinType: string, connName: string, shield: string) => (art ? {
      article: art, connName: connName || "",
      type: rec ? rec.type : "", wire: rec ? rec.wire : "", mfr: rec ? rec.mfr : "", program: rec ? rec.program : "",
      ...coaxDia_(), l1: rec ? rec.l1 : "", l2: rec ? rec.l2 : "", l3: rec ? rec.l3 : "",
      lPlus: rec ? rec.lPlus : "", lMinus: rec ? rec.lMinus : "", pinType, shield,
    } : null);
    const ops = getActiveOps().map((spec) => {
      const row = opRow(spec);
      const machine = spec.machine ? Object.values(COAX_MACHINES).find((m) => m.model === spec.machine) : null;
      return { type: spec.type, opKey: spec.opKey, side: spec.side || "", units: spec.units || 1, templateId: row.templateId,
        opNum: row.matched ? (row.matched.opNumber || "") : "", tPrep: row.times.tPrep, tOp: row.times.tOp, tMachine: row.times.tMachine,
        ...(machine ? { machine: { model: machine.model, inventory: machine.inventory, name: machine.name } } : {}),
        ...(spec.stripSides ? { stripSides: spec.stripSides } : {}),
        ...(spec.clampSides ? { clampSides: spec.clampSides } : {}) };
    });
    const tut = coaxBomTut_(), cable = coaxBomCableComp_(), sleeve = coaxBomSleeve_();
    return {
      mode: "coax", tab: "coax",
      assemblyIndex: findKey(/индекс/i),
      assemblyName: findKey(/наименование/i),
      partQty: partQtyFromInfo_(),
      coax: {
        cableLength: parseDecimal_(S.coax.len) || "",
        tutLength: parseDecimal_(S.coax.tutLen) || 20,
        tutArt: tut?.art || "", tutName: tut?.name || "",
        cableArt: cable?.art || "", cableName: cable?.name || "",
        sleeveArt: sleeve?.art || "", sleeveName: sleeve?.name || "",
        shieldType: S.coax.shield,
        sideA: mkSide(recA, S.coax.sideA ? S.coax.sideA.art : "", S.coax.pinA, S.coax.sideA ? S.coax.sideA.name : "", coaxShieldOf_("A")),
        sideB: mkSide(recB, S.coax.sideB ? S.coax.sideB.art : "", sideB ? S.coax.pinB : "", S.coax.sideB ? S.coax.sideB.name : "", coaxShieldOf_("B")),
      },
      ops, ...(S.opOrder?.length ? { opOrder: S.opOrder } : {}),
    };
  }
  function applyTwistAllowance_(wires: Array<{ art: string; name: string; length: number }>, tw: { enabled: boolean; pitch: string; wireDia: string; mode: string; pairs: number[][]; wireIndices: number[] }) {
    if (!tw || !tw.enabled) return;
    const p = parseDecimal_(tw.pitch) || 0;
    if (p <= 0) return;
    const manualD = parseDecimal_(tw.wireDia) || 0;
    const idx = new Set<number>();
    if (tw.mode === "pairs") (tw.pairs || []).forEach((group) => group.forEach((i) => idx.add(i)));
    else (tw.wireIndices || []).forEach((i) => idx.add(i));
    wires.forEach((w, i) => {
      if (!idx.has(i) || !(w.length > 0)) return;
      const d = S.twist.diaManual && manualD > 0 ? manualD : (lookupWireDia_(w.art) || lookupWireDia_(w.name) || manualD);
      if (d > 0) w.length = Math.round(w.length * twistTakeupFactor_(d, p));
    });
  }
  /** `build_config.route` «Сложной сборки» («source»: «generated» — ручная правка «Править ПФ» его перекрывает): блоки
   *  схемы ПФ генератора с номерами операций в сборке (`list` — операции с шаблоном, в своём порядке). ПФ без операций
   *  (у всех нет шаблона) выпадает — его входы идут прямо к его выходам; изделие без операций — схемы нет (сервер режет
   *  ЭТК на ПФ своим правилом). */
  function complexRoute_(list: OpSpec[]): Record<string, unknown> | null {
    const graph = graph_;
    if (!graph || !list.length || list.some((spec) => !spec.pf)) return null;
    const positions = new Map<string, number[]>();
    list.forEach((spec, index) => positions.set(spec.pf!, [...(positions.get(spec.pf!) || []), index + 1]));
    let edges = graph.edges.map((edge) => ({ ...edge, items: [...edge.items] }));
    const join = (from: string, to: string, items: string[]) => {
      if (from === to) return;
      const same = edges.find((edge) => edge.from === from && edge.to === to);
      if (same) same.items = [...new Set([...same.items, ...items])];
      else edges.push({ from, to, items: [...items] });
    };
    graph.blocks.filter((block) => !block.final && !positions.has(block.key)).forEach((block) => {
      const ins = edges.filter((edge) => edge.to === block.key), outs = edges.filter((edge) => edge.from === block.key);
      edges = edges.filter((edge) => edge.to !== block.key && edge.from !== block.key);
      ins.forEach((input) => outs.forEach((output) => join(input.from, output.to, input.items)));
    });
    const final = graph.blocks.find((block) => block.final);
    if (!final || !positions.has(final.key)) return null;
    const blocks = graph.blocks.filter((block) => positions.has(block.key));
    return {
      source: "generated",
      blocks: blocks.map((block) => ({
        key: block.key, ...(block.name ? { name: block.name } : {}), ops: positions.get(block.key)!, final: !!block.final,
        ...(block.kit ? { kit: true } : {}),
      })),
      edges: edges.map((edge) => ({ from: edge.from, to: edge.to, per_unit: 1, ...(edge.items.length ? { item: edge.items.join("; ") } : {}) })),
    };
  }
  function buildConfig(): Record<string, unknown> {
    if (S.tab === "coax") return buildConfigCoax_();
    const sideB = endpointsOnSide_("B").length > 0;
    const epA = firstCrimpEndpoint_("A"), epB = sideB ? firstCrimpEndpoint_("B") : null;
    const termA = getCompById(epA?.termId);
    const termB = sideB ? getCompById(epB?.termId) : null;
    const activeWires = activeWires_();
    const shared = sharedLength();
    const wires = activeWires.map((w) => {
      const comp = getCompById(w.compId);
      const own = S.advancedLength ? (w.length || 0) : 0;
      const len = own > 0 ? Math.round((own + getLengthAdjustment_(w.idx).total) * 100) / 100 : shared;
      return {
        name: comp ? comp.name : "", art: comp ? comp.art : "", qty: w.qty || 1, length: len,
        ...(wireCores_(w) > 1 && !allRjEndpoints_() ? { cores: wireCores_(w) } : {}),
        ...(coreColors_(w).some(Boolean) ? { coreColors: coreColors_(w) } : {}),
      };
    });
    const epOrder: Record<string, number> = {};
    let oi = 0;
    (["A", "B"] as Side[]).forEach((side) => endpointsOnSide_(side).forEach((e) => { epOrder[e.id] = oi++; }));
    const sourceBranch_ = (ep: Endpoint, wIdx: number) => {
      for (const o of S.endpoints) {
        if (o.id === ep.id || epOrder[o.id] >= epOrder[ep.id]) continue;
        const r = o.wireIdxs && o.wireIdxs.length ? o.wireIdxs : activeWires.map((w) => w.idx);
        if (r.indexOf(wIdx) >= 0) { const c = o.connId ? getCompById(o.connId) : null; return c ? (c.name || c.art || "") : ""; }
      }
      return "";
    };
    /** Провода разъёма для его операций: у кабеля, часть жил которого идёт сюда (остальные — на другой разъём или не
     *  задействованы), — столько жил, сколько здесь контактов: по ним считаются терминалы, обжим, монтаж и время.
     *  Контакты — списком без пропусков жил (etk_builder._pins читает «1, 2, 3»). */
    const routedDetail_ = (ep: Endpoint) => {
      const routed = new Set(routedWireIdxs_(ep));
      const entries = activeWires.map((w, pos) => ({ w, pos })).filter((x) => routed.has(x.w.idx));
      const own = (w: WireEntry, base: Record<string, unknown>) => {
        const total = unitCount_(w), here = endUnits_(ep, w);
        if (here === total) return base;
        const qty = Math.max(1, Math.round(Number(w.qty) || 1));
        const { cores: _all, ...rest } = base;
        if (wireCores_(w) <= 1) return { ...rest, qty: here };
        return here % qty === 0 ? { ...rest, cores: here / qty } : { ...rest, qty: 1, cores: here };
      };
      const corePins = (w: WireEntry) => corePinsOf_(ep, w.idx).filter((p, k): p is number => p != null && !isShieldPos_(w, k)).join(", ");
      // Экран на контакте разъёма — отдельной строкой «Экран <кабель>»: ему тоже нужен терминал (или пайка) и место в ЭТК.
      const shields = entries.flatMap((x) => {
        const pins = shieldPinsAt_(ep, x.w);
        if (!pins.length) return [];
        const base = wires[x.pos] as { name?: string; art?: string; length?: unknown };
        const name = `Экран ${base.art || base.name || "кабеля"}`;
        return [{ wire: { name, art: name, qty: pins.length, length: base.length }, source: sourceBranch_(ep, x.w.idx), pins: pins.join(", ") }];
      });
      return {
        wires: [...entries.map((x) => own(x.w, wires[x.pos])), ...shields.map((s) => s.wire)],
        sources: [...entries.map((x) => sourceBranch_(ep, x.w.idx)), ...shields.map((s) => s.source)],
        pins: [...entries.map((x) => corePins(x.w)), ...shields.map((s) => s.pins)],
      };
    };
    const epSnap = (ep: Endpoint | null) => {
      const conn = ep && ep.connId ? getCompById(ep.connId) : null;
      const term = ep && ep.termId ? getCompById(ep.termId) : null;
      return {
        connArt: conn ? (conn.art || conn.name || "") : "", connName: conn ? (conn.name || "") : "",
        termArt: term ? (term.art || term.name || "") : "", termName: term ? (term.name || "") : "",
        sdrTmpl: ep ? ep.sdrTmpl : null, double: ep ? !!ep.double : false, perTerm: ep ? epPer_(ep) : 2,
        ...(ep && !ep.double && epDoublePins_(ep).length ? { doublePins: epDoublePins_(ep) } : {}),
        fold: ep ? !!ep.fold && isComplex_() : false, label: ep ? (ep.label || "") : "",
      };
    };
    // Припуск на свивку — до операций: провода разъёмов (routedDetail_) берут уже свитую длину.
    const twist = {
      enabled: S.twist.enabled, mode: S.twist.mode, pairs: getTwistPairs(), wireIndices: twistWireIndices_(),
      pitch: (S.twist.pitch || "").trim(), wireDia: (twistDiaValue() || "").trim(),
    };
    applyTwistAllowance_(wires, twist);
    const welded = spliceWelded_();
    const links = connections_().list;
    const specs = getActiveOps();
    // «Сложная сборка»: схема ПФ генератора — по операциям, что уйдут в сборку (с шаблоном), номерами после своего порядка.
    const route = isComplex_() ? complexRoute_(specs.filter((spec) => templateIdFor(spec))) : null;
    // Несколько партий резки с обжимом — у каждой в ЭТК свой терминал в названии (etk_builder: «обжим SYM-001T-P0.6»).
    const crimpBatches = specs.filter((spec) => spec.type === "cutWireCrimp").length > 1;
    const ops = specs.map((spec) => {
      const row = opRow(spec);
      const op: Record<string, unknown> = {
        type: spec.type, opKey: spec.opKey, templateId: row.templateId, opNum: row.matched ? (row.matched.opNumber || "") : "",
        tPrep: row.times.tPrep, tOp: row.times.tOp, tMachine: row.times.tMachine,
        // Сложная сборка: своё название операции («Надеть ТТК X2 «I3» — ТТК 8/2») — в блоке схемы ПФ ЭТК вместо имени карты.
        ...(route && spec.label ? { label: spec.label } : {}),
      };
      if (spec.wireIdxs) {
        const set = new Set(spec.wireIdxs);
        // Партия берёт из строки «Кабели» только свои жилы (провод ×2: одна жила — к SYM, другая — к SHF).
        const own = (w: WireEntry, wire: Record<string, unknown>) => (spec.wireUnits?.[w.idx] !== undefined ? { ...wire, qty: spec.wireUnits[w.idx] } : wire);
        op.wires = activeWires.map((w, pos) => ({ w, pos })).filter((x) => set.has(x.w.idx)).map((x) => own(x.w, wires[x.pos]));
        if (spec.strip) op.strip = spec.strip;
      }
      if (["cutTut", "insTut", "heatTut", "endCap", "foldCore", "stripCable"].includes(spec.type)) {
        op.tutArt = spec.tutArt || ""; op.tutName = spec.tutName || ""; op.tutCount = spec.tutCount || 0;
        if (spec.harness) op.harness = true;
        if (/^(?:insSpl|heatSpl)\d*$/.test(spec.opKey) && welded) op.spliceWeld = true;
        if (spec.tutSegs) op.tutSegs = spec.tutSegs;
        if (spec.markTube) op.markTube = true;
        // Резка оплётки ветвей (CUT_NEYLON): отрезки — по `tutParts`, материал в метрах — у этой операции.
        if (spec.type === "cutTut" && spec.wrapKind) op.wrapKind = spec.wrapKind;
        if (spec.tutParts) op.tutParts = spec.tutParts;
      }
      if (spec.type === "insWrap") {
        op.wrapArt = spec.wrapArt; op.wrapName = spec.wrapName; op.wrapLen = spec.wrapLen; op.wrapKind = spec.wrapKind;
        if (spec.wrapCut) op.wrapCut = true;
      }
      if (spec.type === "insMark") op.marks = spec.marks;
      if (spec.tin) op.tin = true;
      if (spec.type === "splice" || spec.type === "insHousing") op.units = spec.units || 1;
      // Свивка ветви или группы сростков — своих пар (без числа — все пары формы, как раньше).
      if (spec.type === "twist" && spec.units) op.units = spec.units;
      // …и только свои пары в ЭТК (поле «Свивка»: число, провода); шаг — общий, формы.
      if (spec.type === "twist" && spec.twistPairs?.length) op.twistPairs = spec.twistPairs;
      if (spec.branch) op.branch = spec.branch;
      // Сростки: способ и гильзы из состава (ленточный обжим — его материал) с числом сростков на каждую.
      if (spec.type === "splice") {
        op.method = splMethod_();
        op.sleeves = spec.sleeveItems
          ?? spliceSleeves_().items.map((x) => ({ art: x.comp.art || x.comp.name || "", name: x.comp.name || x.comp.art || "", count: x.count }));
      }
      if (spec.opKey === "stripTail") op.strip = spec.strip;
      // Лужение хвостов: провода хвостов (номера в `wires` карты) и число концов.
      if (spec.type === "tin" && spec.units) {
        const set = new Set(spec.wireIdxs || []);
        op.units = spec.units;
        op.wireIdxs = activeWires.flatMap((w, pos) => (set.has(w.idx) ? [pos] : []));
      }
      if (spec.type === "seal") { op.sealArt = spec.sealArt; op.sealName = spec.sealName; op.sealQty = spec.sealQty; op.sealPlaces = spec.sealPlaces; }
      // Усадка трубки стороны (поверх разъёма, на месте разделки) — со своей стороной: ПФ на сервере и ключ операции ЭТК
      // (две усадки сторон А и В иначе совпадали и сборка ЭТК отказывала).
      if (spec.type === "heatTut" && spec.side && /^heat(Shr|Js)/.test(spec.opKey)) op.side = spec.side;
      // Партия на нескольких разъёмах стороны — в подписи все они («5, I3, A6: обжаты терминалы SYM-001T-P0.6»).
      const batchSnap = (id: string, ids?: string[]) => {
        const snap = epSnap(epById_(id));
        if (!ids || ids.length < 2) return snap;
        const labels = ids.map((x) => epById_(x)).filter((e): e is Endpoint => !!e).map((e) => (e.label || "").trim() || endKey_(e));
        return { ...snap, label: labels.join(", ") };
      };
      if (spec.crimpEpId) { op.side = spec.side; op.ep = batchSnap(spec.crimpEpId, spec.crimpEpIds); }
      // Обжим обеих сторон: `ep` — сторона А, `epB` — сторона В (etk_builder: терминалы обеих сторон в комплектующих).
      if (spec.crimpEpIdB) op.epB = batchSnap(spec.crimpEpIdB, spec.crimpEpIdsB);
      if (spec.type === "cutWireCrimp" && crimpBatches) op.crimpBatch = true;
      if (spec.epId) {
        const ep = epById_(spec.epId)!;
        op.side = spec.side; op.firstTerm = !!spec.firstTerm; op.ep = epSnap(ep);
        const det = routedDetail_(ep);
        op.wires = det.wires; op.wireSources = det.sources;
        if (det.pins.some(Boolean)) op.pins = det.pins;
        if (links.length) op.epKey = endKey_(ep);
      }
      return op;
    });
    const tin = { enabled: !sideB && S.tin.enabled, wireIndices: tinWireIndices_(), stripLen: (S.tin.stripLen || "").trim() };
    const otpA = !!(epA && epA.otp), otpB = !!(epB && epB.otp);
    const tinOn = !sideB && S.tin.enabled && tinWireIndices_().length > 0;
    const stripSides = { a: !!(termA && otpA), b: !!(termB && otpB) || tinOn };
    const tinStripLen = (S.tin.stripLen || "").trim();
    const dashless = (v: string) => (v === "—" ? "" : v);
    const strip = {
      a: stripSides.a, b: stripSides.b,
      lenA: stripSides.a ? dashless(epStripLen_(epA)) : "",
      lenB: !stripSides.b ? "" : (sideB && termB && otpB ? dashless(epStripLen_(epB)) : tinStripLen),
    };
    const bigAssembly = S.endpoints.filter((e) => e.connId || e.termId).length > 2;
    return {
      tab: S.tab, assemblyIndex: findKey(/индекс/i), assemblyName: findKey(/наименование/i), partQty: partQtyFromInfo_(),
      bigAssembly, wires, twist, tin, strip, ops, ...(links.length ? { connections: links } : {}),
      ...(route ? { route } : {}),
      ...(S.opOrder?.length ? { opOrder: S.opOrder } : {}),
      ...usedColorHex_(wires),
    };
  }
  /** Свои цвета, которые реально стоят на жилах: уходят в карту, чтобы чертёж и ЭТК нарисовали их без формы. */
  function usedColorHex_(wires: Array<{ coreColors?: string[] }>): { colorHex?: Record<string, string> } {
    const used = new Set(wires.flatMap((w) => w.coreColors || []).map((c) => c.trim().toLowerCase()));
    const picked = Object.entries(S.colorHex || {}).filter(([name]) => used.has(name));
    return picked.length ? { colorHex: Object.fromEntries(picked) } : {};
  }
  function bomWarnings_(cfg: Record<string, unknown>) {
    const w: string[] = [];
    if (!cfg.assemblyName) w.push("Не указано наименование изделия — последний лист не переименуется.");
    if (S.lenFromCalc) w.push("Длины проводов взяты из расчёта (в наименовании сборки их нет) — сверьте с КД.");
    ((cfg.wires as Array<{ art?: string; name?: string; length: number }>) || []).forEach((x, i) => {
      if (!(x.length > 0)) w.push("Кабель " + (i + 1) + " (" + (x.art || x.name || "—") + "): не указана длина — норма резки уйдёт пустой.");
    });
    if (isComplex_()) {
      // Гильзы состава (НШВ) ждут сростка: не выбранная ни в одном сростке и сросток без гильзы — видно до сборки.
      const sl = spliceSleeves_();
      const used = new Set([...sl.items.map((x) => x.comp.id), ...S.endpoints.map((e) => e.termId).filter(Boolean)]);
      spliceSleeveComps_().filter((c) => !used.has(c.id)).forEach((c) => w.push(`Гильза «${c.art || c.name}» из состава не выбрана ни в одном сростке.`));
      if (spliceSleeveComps_().length) sl.missing.forEach((label) => w.push(`Сростки (${label}): не выбрана гильза из состава.`));
    }
    if (cfg.mode === "coax") {
      const cx = (cfg.coax || {}) as { cableLength?: unknown; sideA?: { article: string; type: string } | null; sideB?: { article: string; type: string } | null };
      if (!cx.cableLength) w.push("Не указана длина кабеля — норма резки будет пустой.");
      ([["A", cx.sideA], ["B", cx.sideB]] as const).forEach(([s, sd]) => {
        if (sd && sd.article && !sd.type) w.push("Сторона " + s + ": разъём «" + sd.article + "» не найден в БД.КОАКС — размеры/время не подтянутся.");
      });
    }
    return w;
  }
  /** generate(): проверки перед отправкой. error — стоп, warnings — спросить подтверждение. */
  function twistDiaWarnings_() {
    const line = (names: string[], text: string) => (names.length ? ["• Свивка: " + names.map((n) => "«" + n + "»").join(", ") + " — " + text + " (поле «Ø провода»)"] : []);
    return line(twistDiaMissing_(), WIRE_DIA_MISSING_).concat(line(twistDiaOld_(), WIRE_DIA_OLD_));
  }
  function validate() {
    const cfg = buildConfig();
    const ops = (cfg.ops as Array<{ templateId: string; type: string }>).filter((o) => o.templateId);
    if (!ops.length) return { cfg, error: "Выберите шаблон хотя бы для одной операции.", warnings: [] as string[] };
    const twistCheck = twistProblems_();
    if (twistCheck.error) return { cfg, error: twistCheck.error, warnings: [] };
    const hasCut = ops.some((o) => /^cutWire/.test(o.type));
    if (hasCut) {
      if (!S.advancedLength && !sharedLength()) return { cfg, error: 'Укажите длину кабелей (поле "Длина, мм" вверху секции).', warnings: [] };
      if (S.advancedLength && (cfg.wires as Array<{ length: number }>).some((w) => !w.length)) return { cfg, error: "Укажите длину для каждого кабеля.", warnings: [] };
    }
    return { cfg: { ...cfg, ops, materialsSheet: S.materialsSheet }, error: "", warnings: checkTerminalFit_().concat(bomWarnings_(cfg), connections_().warnings, twistDiaWarnings_(), twistCheck.warnings) };
  }

  return {
    getCompById, bomLists, wireComps_, wireCores_, isJacketedCable_, activeWires_, routedWireIdxs_, connections_, endpointsOnSide_,
    cableRecOf_, isUlMulticore_, wireScreened_, unitCount_, coreColors_, corePinsOf_, endUnits_, cableEnds_, shieldCount_, isShieldPos_, shieldPinsAt_,
    endpointOrdinal_, epPer_, epDoublePins_, epStripLen_, findTerRecord_, isComplex_, crimpCutSides_, crimpCutSideNote_, crimpCutEnabled_, crimpCutSideSelected_, batchWires_,
    crimpCutTin_, crimpCutMinWires_, crimpTinNoApplicator_, crimpCutBatchNote_, crimpCutBatchLines_, checkTerminalFit_,
    cutLengthInfo, sharedLength, getLengthAdjustment_, twistWireIndices_, tinWireIndices_, getTwistPairs, twistProblems_, autoDetectTwistPairs,
    twistGeom_, twistDiaValue, twistDiaMissing_, twistDiaOld_, twistNote, complexWarning, isComplexKit_, isCoaxAssembly_, coaxBomConnectors_, coaxBomCable_,
    coaxBomTut_, coaxCableDiaLookup_, matchCoaxRec_, coaxInfo, coaxMachineAuto_, coaxMachine_, coaxShieldOf_, coaxCableOuterMm_, getActiveOps, templateIdFor, stickTemplates, opRow, buildConfig, validate,
    harnessSleeveComps_, rjEndsInBom_, hsDefaultOn_, hsComp_, hsRowSkip_, hsTubeOptions_, markComps_, bomTut_, shrinkCandidates_, defaultShrinkComp_, shrinkOf_,
    defaultJacketShrinkComp_, cableJacketShrink_, jacketShrinkItems_, freeJacketShrinkComp_, housingOn_, housingDefault_, sleeveWarning_,
    /** Разъёмы сторон для меток: обозначение (подпись разъёма, иначе А1/В1) и артикул. */
    markConnectors_: () => (["A", "B"] as Side[]).flatMap((side) => endpointsOnSide_(side).filter((e) => e.connId).map((e) => {
      const conn = getCompById(e.connId);
      return { name: (e.label || "").trim() || endKey_(e), art: conn ? conn.art || conn.name || "" : "" };
    })), asmLenFromName_, calcCutLengths_, freeEnds_,
    wireQtyFromTerms_: (wires: Comp[]) => {
      // Кабель в оболочке (жил больше одной по наименованию или по записи БД.ПРОВ): терминалы — по жилам, не по кабелям.
      if (wires.length !== 1 || cableCoresFromName_(`${wires[0].name || ""} ${wires[0].art || ""}`) > 1 || (cableRecOf_(wires[0])?.cores || 0) > 1) return 0;
      const conns = bomLists().conns.filter((c) => !/корпус|housing/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`));
      if (!conns.length || conns.some((c) => detectSolderConn_(c.art || c.name))) return 0;
      const terms = getByTypes(["терминал", "terminal", "контакт"]).reduce((n, c) => n + qtyNum(c), 0);
      const sides = conns.reduce((n, c) => n + (qtyNum(c) || 1), 0) >= 2 ? 2 : 1;
      const n = terms / sides;
      return terms > 0 && Number.isInteger(n) ? n : 0;
    },
    detectSolderConn_, rjCableCat_, sealComps: () => comps().filter((c) => SEAL_RE_.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`)),
    /** Трубки заделки и сростка: вся термоусадка состава — ТУТ и ТТК (владелец 16.10: «ТТК 3/1мм» в списке), по Ø. */
    capTubes: () => byTubeDia_(shrinkCandidates_()),
    /** Трубка заделки, которую засев ставит сам: только ТУТ (как до 16.10 — засев старых карт не меняется). */
    capSeedTubes_: () => byTubeDia_(comps().filter((c) => /тут|термоусад/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`))),
    splMethod_, spliceSleeveComps_, spliceSlots_, spliceRows_, spliceSizes_, spliceSleeveOf_, spliceSleeves_, tailEnds_, bomWarnings_,
    braidEp_, braidLenDefault_,
    /** Схема ПФ «Сложной сборки» по текущему списку операций (до номеров ЭТК); другие вкладки — null. */
    pfGraph: (): PfGraph | null => { if (!isComplex_()) return null; getActiveOps(); return graph_; },
    wrapCandidates: () => {
      const re = new RegExp(/лент|оплет|оплёт|рукав|braid|sleeve|wrap|/.source + PROTECT_TUBE_RE_.source, "i");
      const hit = (c: Comp) => re.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`);
      const list = comps().filter((c) => hit(c) || /^др/i.test(c.type || ""));
      const autos = list.filter(hit).filter((c) => !(/медн/i.test(`${c.name || ""} ${c.art || ""}`) && rjCableCat_() === "cat5"));
      return { list, autos };
    },
    guessWrapKind_: (c: Comp | null): WrapKind => (c && /лент|tape/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`) ? "tape" : "braid"),
    prefillWrapLen_: (c: Comp | null) => { const m = c ? qtyNum(c) : 0; return m > 0 ? String(Math.round(m * 1000)) : ""; },
    num0,
  };
}

export type Gen = ReturnType<typeof makeGen>;

// ── Действия формы (обработчики TcGenInline) над состоянием ─────────────────

/** init(): сидинг из состава — эндпоинты, лента, герметик, заделка, термоусадка/бирки, кабели, свивка, вкладка, лужение. */
export function initState(D: GenData, base: GenState = emptyState()): GenState {
  let S: GenState = { ...base };
  const g0 = makeGen(D, S);
  const { conns, terms } = g0.bomLists();
  // seedEndpointsFromBom_
  const totalConnQty = conns.reduce((s, c) => s + (Number(c.qty) || 1), 0);
  const twoSides = conns.length >= 2 || totalConnQty >= 2;
  const mk = (side: Side, conn: Comp | null, term: Comp | null, n: number): Endpoint => {
    const sdr = conn ? detectSolderConn_(conn.art) : null;
    return { id: side.toLowerCase() + n, side, connId: conn ? conn.id : "", method: sdr ? "solder" : "crimp", methodAuto: true, sdrTmpl: sdr,
      termId: term ? term.id : "", wireIdxs: [], otp: false, double: false, label: "", solderStrip: "", tutLen: "" };
  };
  const isHousing = (c: Comp) => /корпус|housing/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`);
  const real = conns.filter((c) => !isHousing(c));
  const pick = real.length ? real : conns;
  const eps: Endpoint[] = [];
  if (pick[0] || terms[0]) eps.push(mk("A", pick[0] || null, terms[0] || null, 1));
  if (twoSides) eps.push(mk("B", pick[1] || pick[0] || null, terms[1] || terms[0] || null, 1));
  S = { ...S, endpoints: eps, nextEndpointId: eps.length };
  // seedWrapFromBom_
  const { autos } = makeGen(D, S).wrapCandidates();
  if (autos.length) {
    const g = makeGen(D, S);
    S = { ...S, wrap: { enabled: true, slots: S.wrap.slots.map((slot, i) => {
      const c = autos[i];
      return c ? { mat: c.id, kind: g.guessWrapKind_(c), len: g.prefillWrapLen_(c) } : slot;
    }) } };
  }
  // seedSealFromBom_
  const seals = makeGen(D, S).sealComps();
  if (seals.length) S = { ...S, seal: { enabled: true, mat: seals[0].id, places: String(S.endpoints.filter((e) => e.connId).length || 1) } };
  // seedCapFromBom_
  const tubes = makeGen(D, S).capSeedTubes_();
  if (tubes.length) S = { ...S, cap: { ...S.cap, tut: tubes[0].id } };
  // seedHarnessFromBom_
  {
    const g = makeGen(D, S);
    const hs = g.harnessSleeveComps_();
    const rjEnds = g.rjEndsInBom_();
    const per = rjEnds > 1 ? rjEnds : 1;
    const rows = hs.map((c) => {
      const mm = Math.round((Number(String(c.qty).replace(",", ".")) || 0) * 1000 / per);
      return { compId: c.id, len: mm ? String(mm) : "", cnt: String(per), auto: true };
    });
    S = { ...S, hs: { enabled: g.hsDefaultOn_(hs, rjEnds), manual: false, rows }, mark: g.markComps_().length > 0 };
  }
  // setupCoaxFromBom
  {
    const g = makeGen(D, S);
    const cs = g.coaxBomConnectors_();
    const sideA = cs[0] ? { art: cs[0].art, name: cs[0].name } : null;
    const sideB = cs.length >= 2 ? { art: cs[1].art, name: cs[1].name } : null;
    S = { ...S, coax: { ...S.coax, sideA, sideB, tutEnabled: !!g.bomTut_(), stripB: false } };
    const g2 = makeGen(D, S);
    if (!S.coax.len && g2.asmLenFromName_() > 0) {
      const lp = (s: CoaxSide) => {
        if (!s) return 0;
        const r = g2.matchCoaxRec_(s.art, "", ""), v = r ? parseLengthVal_(r.lPlus) : 0;
        return v > 0 ? v : Number(DEFAULT_LEN_MM_);
      };
      S = { ...S, coax: { ...S.coax, len: String(g2.asmLenFromName_() + lp(sideA) + lp(sideB)) } };
    }
    S = onCoaxConnChange(D, S, "A");
    if (sideB) S = onCoaxConnChange(D, S, "B");
  }
  S = autoFillAllWires(D, S);
  // maybeAutoEnableTwist
  {
    const auto = makeGen(D, S).autoDetectTwistPairs();
    if (!S.twist.enabled && auto.length) {
      S = { ...S, twist: { ...S.twist, enabled: true, pairs: auto.map(([a, b]) => ({ a: String(a), b: String(b) })) } };
    }
  }
  const g = makeGen(D, S);
  S = switchGenTab(S, g.isCoaxAssembly_() ? "coax" : g.isComplexKit_() ? "complex" : "inter");
  // «Сложная сборка»: терминал разъёма — по совместимости корпуса с терминалами состава (не нашёлся — как раньше).
  if (S.tab === "complex") S = autoPickTerminals(D, S);
  S = applyCalcTin_(D, S);
  return seedJacketShrink_(D, S);
}

/** «Термоусадка на срез оболочки» новой формы (владелец 15.10): в составе есть ТУТ/ТТК, не занятая другим делом, —
 *  включена у каждого кабеля в оболочке на обеих его сторонах. Только засев: сохранённая форма не переключается. */
export function seedJacketShrink_(D: GenData, S: GenState): GenState {
  const g = makeGen(D, S);
  const comp = g.freeJacketShrinkComp_();
  if (!comp) return S;
  const mat = comp === g.defaultJacketShrinkComp_() ? "" : comp.id;
  let changed = false;
  const wires = S.wires.map((w) => {
    if (!w.compId || w.jacketShrink || !g.isJacketedCable_(w)) return w;
    const sides = (["A", "B"] as Side[]).filter((side) => g.cableEnds_(side).some((x) => x.w.idx === w.idx));
    if (!sides.length) return w;
    changed = true;
    return { ...w, jacketShrink: { A: sides.includes("A"), B: sides.includes("B"), mat, len: "" } };
  });
  return changed ? { ...S, wires } : S;
}

/** Правка «Термоусадки на срез оболочки» строки «Кабели». Пишется только поле кабеля: значения старой формы по сторонам
 *  (`S.jacketShrink`) сначала переносятся во все кабели в оболочке, затем поле формы снимается — ничего не теряется. */
export function setCableJacketShrink(D: GenData, S: GenState, idx: number, patch: Partial<CableJacketShrink>): GenState {
  const g = makeGen(D, S);
  const wires = S.wires.map((w) => {
    const cur = g.cableJacketShrink_(w);
    const base: CableJacketShrink = cur || { A: false, B: false, mat: "", len: "" };
    if (w.idx === idx) return { ...w, jacketShrink: { ...base, ...patch } };
    return cur && !w.jacketShrink ? { ...w, jacketShrink: cur } : w;
  });
  const next: GenState = { ...S, wires };
  delete next.jacketShrink;
  return next;
}

/** Собрана ли карта на «Сложной сборке»: сборка пишет `tab`; конфиг старше — по тому, что пишет только эта вкладка
 *  (как `generator_tab` в etk_builder.py): >2 разъёмов, связи по контактам, сростки/герметизация/складывание, подпись разъёма. */
export function savedComplex(config: unknown): boolean {
  if (!config || typeof config !== "object") return false;
  const c = config as Record<string, unknown>;
  if (c.mode === "manual" || c.mode === "coax") return false;
  const tab = typeof c.tab === "string" ? c.tab.trim() : "";
  if (tab) return tab === "complex";
  const ops = (Array.isArray(c.ops) ? c.ops : []).filter((o): o is Record<string, unknown> => !!o && typeof o === "object");
  const ends = ops.flatMap((o) => [o.ep, o.epB]).filter((e): e is Record<string, unknown> => !!e && typeof e === "object");
  return !!(c.bigAssembly || c.connections || ops.some((o) => ["splice", "seal", "foldCore"].includes(String(o.type)))
    || ends.some((e) => String(e.label ?? "").trim() || e.fold));
}

/** «Способ» сростков: карта операции подбирается заново (выбранная для прежнего способа снимается). */
export function setSplMethod(S: GenState, method: SpliceMethod): GenState {
  if ((S.spl.method || "solder") === method) return S;
  // Группы сростков «Сложной сборки» — splice1, splice2…: их карты подбираются заново тоже.
  const spliceKey = (key: string) => /^splice\d*$/.test(key);
  const selectedTemplates = Object.fromEntries(Object.entries(S.selectedTemplates).filter(([key]) => !spliceKey(key)));
  return { ...S, spl: { ...S.spl, method }, selectedTemplates, manualTemplates: S.manualTemplates.filter((key) => !spliceKey(key)) };
}

/** Способ сростков собранной карты без снимка формы: у операции «Сростки» нет `method` — пайка (карты до 16.10); операции
 *  нет — null (форма берёт засев). */
export function splMethodFromConfig(config: unknown): SpliceMethod | null {
  const ops = config && typeof config === "object" ? (config as { ops?: unknown }).ops : null;
  const op = Array.isArray(ops) ? ops.find((o) => !!o && typeof o === "object" && (o as { type?: unknown }).type === "splice") as { method?: unknown } | undefined : undefined;
  if (!op) return null;
  return op.method === "sleeve" || op.method === "band" || op.method === "ultrasonic" ? op.method : "solder";
}

/** «Свободные концы»: заделка трубкой, зачистка и лужение хвостов или ничего. */
export type TailMode = "cap" | "tin" | "none";
export const tailModeOf = (S: Pick<GenState, "cap" | "tailTin">): TailMode => (S.tailTin?.enabled ? "tin" : S.cap.enabled ? "cap" : "none");
export function setTailMode(S: GenState, mode: TailMode): GenState {
  return { ...S, cap: { ...S.cap, enabled: mode === "cap" },
    tailTin: { strip: S.tailTin?.strip ?? TAIL_STRIP_MM_, enabled: mode === "tin" } };
}

/** Новая карта на «Сложной сборке» (владелец 13.10: «в сложной сборке не нужно автоподбора конфигурации проводов»):
 *  кабели, длины и свивку технолог задаёт сам — засев из состава снимается. Пакетная сборка (tcGenBatch) и пересборка
 *  карты (распиновка прошлой сборки читается по засеянным кабелям) засев сохраняют. */
export function complexByHand(S: GenState): GenState {
  if (S.tab !== "complex") return S;
  return { ...S, wires: [{ idx: 0, compId: "", length: 0, qty: 1 }], nextWireIdx: 1, advancedLength: false, drawingLength: 0, sharedLengthText: "",
    lenFromCalc: false, twist: { ...S.twist, enabled: false, pairs: [], wires: null } };
}

/** Терминалы разъёмов «Сложной сборки» по совместимости с корпусом (terminalPick.ts): у разъёма, терминал которого не
 *  выбран вручную. `pairs` — пары «корпус → терминалы» базы разъёмов. `clear` — у этих разъёмов (или у всех) терминал
 *  снимается, если совместимый не нашёлся (разъём только что выбран), у остальных — остаётся как есть. */
export function autoPickTerminals(D: GenData, S: GenState, pairs?: TerminalPairs, clear: "all" | string | null = null): GenState {
  if (S.tab !== "complex") return S;
  const picks = pickTerminals(makeGen(D, S), S, pairs);
  let next = S;
  S.endpoints.forEach((ep) => {
    if (ep.termAuto === false || ep.method !== "crimp" || !ep.connId) return;
    const want = picks[ep.id] || (clear === "all" || clear === ep.id ? "" : ep.termId);
    if (want === ep.termId) return;
    next = { ...next, selectedTemplates: clearAutoTemplates(next, ep, ["prs", "ins"]),
      endpoints: next.endpoints.map((e) => (e.id === ep.id ? { ...e, termId: want, termAuto: true } : e)) };
  });
  return next;
}

export function onCoaxConnChange(D: GenData, S: GenState, side: Side): GenState {
  const s = side === "A" ? S.coax.sideA : S.coax.sideB;
  let coax = { ...S.coax };
  if (s) {
    const rec = makeGen(D, S).matchCoaxRec_(s.art, "", "");
    const pinKey = side === "A" ? "pinA" : "pinB";
    if (!(rec && rec.pinType) && /r\s*\/\s*a\b|угл/i.test(`${s.art} ${s.name || ""}`)) coax = { ...coax, [pinKey]: "внутри корпуса" };
    if (rec) {
      if (rec.pinType) coax = { ...coax, [pinKey]: /внутри/i.test(rec.pinType) ? "внутри корпуса" : /обжим/i.test(rec.pinType) ? COAX_PIN_CRIMP : "пайка" };
      // Пустой «Тип пина» не значит «пайка»: цанговый центральный контакт (N-111C/8D…) не паяется.
      if (!/внутри/i.test(rec.pinType) && COLLET_RE_.test(rec.centerContact || "")) coax = { ...coax, [pinKey]: COAX_PIN_COLLET };
      // Тип экрана — по записи БД.КОАКС разъёма своей стороны (обжим / пайка / прижим); ручной выбор потом перекрывает.
      const kind = coaxShieldKind_(rec.shieldType);
      if (kind) coax = side === "A" ? { ...coax, shield: kind } : { ...coax, shieldB: kind };
      else if (side === "B") coax = { ...coax, shieldB: "" };
    }
  }
  return syncCoverRow_({ ...S, coax }, side);
}
/** syncCoverRow_: крышка только у углового разъёма, иначе сброс. */
export function syncCoverRow_(S: GenState, side: Side): GenState {
  const angled = (side === "A" ? S.coax.pinA : S.coax.pinB) === "внутри корпуса";
  if (angled) return S;
  return { ...S, coax: { ...S.coax, [side === "A" ? "coverA" : "coverB"]: "" } };
}

export function toggleLengthMode(S: GenState, shared: number): GenState {
  if (!S.advancedLength) {
    return { ...S, advancedLength: true, wires: shared > 0 ? S.wires.map((w) => (w.length ? w : { ...w, length: shared })) : S.wires };
  }
  const first = S.wires.find((w) => w.length > 0);
  // В источнике общая длина берётся в sharedLength (длина реза), но поле «по чертежу» показывает её же.
  return first ? { ...S, advancedLength: false, drawingLength: first.length, sharedLengthText: String(first.length) } : { ...S, advancedLength: false };
}

/** «Сложная сборка» (владелец 14.10): у каждого кабеля своя длина, общего поля нет. Старая карта с общей длиной
 *  открывается с ней в каждой строке без длины — ничего не теряется. */
export function ensureSeparateLengths(S: GenState): GenState {
  if (S.tab !== "complex" || S.advancedLength) return S;
  const shared = Number(S.drawingLength) || Number(String(S.sharedLengthText).replace(",", ".")) || 0;
  return { ...S, advancedLength: true, wires: shared > 0 ? S.wires.map((w) => (w.length ? w : { ...w, length: shared })) : S.wires };
}

/** Копия кабеля (состав, длина, кол-во, жилы, разделка) сразу под исходным. */
export function duplicateWire(S: GenState, idx: number): GenState {
  const at = S.wires.findIndex((w) => w.idx === idx);
  if (at < 0) return S;
  const copy: WireEntry = { ...S.wires[at], idx: S.nextWireIdx };
  return { ...S, wires: [...S.wires.slice(0, at + 1), copy, ...S.wires.slice(at + 1)], nextWireIdx: S.nextWireIdx + 1 };
}

/** «Длину всем ниже»: длина этой строки — в каждую строку ниже, где длина пуста. Заполненные не трогаются. */
export function fillLengthBelow(S: GenState, idx: number): GenState {
  const at = S.wires.findIndex((w) => w.idx === idx);
  const len = at < 0 ? 0 : S.wires[at].length || 0;
  if (len <= 0) return S;
  return { ...S, wires: S.wires.map((w, i) => (i > at && !(w.length > 0) ? { ...w, length: len } : w)) };
}

export function switchGenTab(S: GenState, tab: Tab): GenState {
  let next = S;
  if (tab === "inter" && S.advancedLength) next = toggleLengthMode(S, S.drawingLength);
  return { ...next, tab };
}

/** autoFillAllWires: кабели из состава; длина из наименования сборки, иначе из строк расчёта КБn. */
export function autoFillAllWires(D: GenData, S0: GenState): GenState {
  let S = S0;
  const g = makeGen(D, S);
  const wires = g.wireComps_();
  if (!wires.length) return { ...S, wires: [{ idx: S.nextWireIdx, compId: "", length: 0, qty: 1 }], nextWireIdx: S.nextWireIdx + 1 };
  const asmLen = g.asmLenFromName_();
  if (asmLen > 0 && !S.advancedLength) S = { ...S, drawingLength: asmLen, sharedLengthText: String(asmLen) };
  const normas = wires.map((w) => Number(w.qty) || 0).filter((v) => v > 0);
  const baseNorma = normas.length ? Math.min(...normas) : 0;
  const byTerms = g.wireQtyFromTerms_(wires);
  const calcLen: Record<string, number> = asmLen > 0 ? {} : g.calcCutLengths_(wires);
  let nextIdx = 0;
  const entries: WireEntry[] = [];
  wires.forEach((wire) => {
    const normaM = Number(wire.qty) || 0;
    if (normaM <= 0) return;
    const length = (wire.length || 0) > 0 ? wire.length! : (asmLen || calcLen[wire.id] || 0);
    // Кабель UL 2xxx в оболочке: терминалов по N на сторону — это жилы кабелей, а кабелей столько, сколько их укладывается
    // в норму на изделие (длина неизвестна — один). Владелец 14.10: «UL2575 26AWG» 0,42 м и 4 терминала на разъём →
    // 1 шт × 4 жилы, а не 4 провода.
    const cables = g.isUlMulticore_(wire) ? wireQtyFromNorma_(normaM, baseNorma, length) : 0;
    if (byTerms > 1 && cables > 0 && byTerms > cables && byTerms % cables === 0) {
      entries.push({ idx: nextIdx++, compId: wire.id, length, qty: cables, cores: byTerms / cables });
      return;
    }
    entries.push({ idx: nextIdx++, compId: wire.id, length, qty: byTerms || wireQtyFromNorma_(normaM, baseNorma, length) });
  });
  S = { ...S, wires: entries, nextWireIdx: nextIdx };
  const fromCalc = entries.map((w) => calcLen[w.compId] || 0);
  const lenFromCalc = fromCalc.some(Boolean);
  S = { ...S, lenFromCalc };
  if (lenFromCalc && !S.advancedLength) {
    if (fromCalc.every((l) => l === fromCalc[0])) S = { ...S, drawingLength: fromCalc[0], sharedLengthText: String(fromCalc[0]) };
    else S = toggleLengthMode(S, makeGen(D, S).sharedLength());
  }
  return S;
}

/** applyCalcTin_: строка SDR_tin расчёта включает лужение (только без стороны В), длина из «L=…». */
export function applyCalcTin_(D: GenData, S: GenState): GenState {
  const tin = (D.calcOps || []).find((o) => /^SDR_tin$/i.test(o.code));
  if (!tin || S.tin.enabled || S.endpoints.some((e) => e.side === "B")) return S;
  const m = /L\s*=\s*(\d+(?:[.,]\d+)?)/i.exec(tin.req || "");
  return { ...S, tin: { ...S.tin, enabled: true, stripLen: m ? m[1] : S.tin.stripLen } };
}

export type GrapherGroupIn = {
  compId: string; qty: number; length: number; cores?: number; jacket?: string;
  /** Длина реза из чертежа: длина + поправки концов, вверх до шага (TECHMAP-GRAPHER). */
  cut?: number | null; correctionFrom?: number; correctionTo?: number;
};

/** Есть ли в строках чертежа поправки концов (тех. запас на контакт, владелец 06.10). */
export function grapherHasCorrections(groups: Array<Pick<GrapherGroupIn, "correctionFrom" | "correctionTo">>): boolean {
  return groups.some((g) => (Number(g.correctionFrom) || 0) !== 0 || (Number(g.correctionTo) || 0) !== 0);
}

const mmRu = (value: string | number) => String(Number(value)).replace(".", ",");

/** «по чертежу 300 мм · резка 312 мм (+8/+4)» — обе длины строки чертежа. */
export function grapherLengthText(g: { length_mm: string | number | null; cut_mm: string | number | null; correction_from_mm: string | number; correction_to_mm: string | number }): string {
  if (g.length_mm == null) return "нет длины";
  const a = Number(g.correction_from_mm) || 0, b = Number(g.correction_to_mm) || 0;
  const base = `по чертежу ${mmRu(g.length_mm)} мм`;
  if (!a && !b) return base;
  const sg = (x: number) => (x >= 0 ? "+" : "−") + mmRu(Math.abs(x));
  return `${base} · резка ${g.cut_mm == null ? "—" : mmRu(g.cut_mm)} мм (${sg(a)}/${sg(b)})`;
}

/** Строки чертежа TECHMAP-GRAPHER → «Кабели» (grapherApply_). compId — позиция состава, не найдено — пусто («выбери»).
 *  Поправки концов в чертеже — тех. запас на контакт (владелец 06.10): если они есть, кабели режутся по длине реза
 *  чертежа, а режим «длина реза» отключает наш припуск L+/L− из БД.ТЕР, чтобы запас не посчитался дважды.
 *  Без поправок — как раньше: длина по чертежу и наш припуск. */
export function applyGrapherGroups(S0: GenState, groups: GrapherGroupIn[]): GenState {
  const byCut = grapherHasCorrections(groups);
  let S: GenState = byCut ? { ...S0, measureMode: "cut" } : S0;
  let next = S.nextWireIdx;
  const lengthOf = (g: GrapherGroupIn) => (byCut ? Number(g.cut ?? g.length) || 0 : g.length || 0);
  const wires = groups.map((g) => {
    const w: WireEntry = { idx: next++, compId: g.compId || "", length: lengthOf(g), qty: g.qty };
    if ((g.cores || 0) > 1) w.cores = g.cores;
    if (g.jacket) w.jacket = String(g.jacket);
    return w;
  });
  S = { ...S, wires, nextWireIdx: next };
  const lens = Array.from(new Set(groups.map(lengthOf)));
  if (lens.length === 1 && !S.advancedLength) return { ...S, drawingLength: lens[0], sharedLengthText: lens[0] ? String(lens[0]) : "" };
  if (!S.advancedLength) return { ...S, advancedLength: true };
  return S;
}

export function addEndpoint(S: GenState, side: Side): GenState {
  const ep: Endpoint = { id: "e" + S.nextEndpointId, side, connId: "", method: "crimp", methodAuto: true, sdrTmpl: null, termId: "",
    wireIdxs: [], otp: false, double: false, label: "", solderStrip: "", tutLen: "" };
  return { ...S, endpoints: [...S.endpoints, ep], nextEndpointId: S.nextEndpointId + 1 };
}

function clearAutoTemplates(S: GenState, ep: Endpoint, kinds: Array<"prs" | "ins" | "solder">): Record<string, string> {
  const ord = S.endpoints.filter((e) => e.side === ep.side).findIndex((e) => e.id === ep.id) + 1;
  const selected = { ...S.selectedTemplates };
  kinds.forEach((k) => { const key = endpointOpKey_(ep.side, ord, k); if (!S.manualTemplates.includes(key)) delete selected[key]; });
  return selected;
}

export function updateEndpoint(D: GenData, S: GenState, id: string, patch: Partial<Endpoint>): GenState {
  const ep = S.endpoints.find((e) => e.id === id);
  if (!ep) return S;
  let next: Endpoint = { ...ep, ...patch };
  let selectedTemplates = S.selectedTemplates;
  if ("connId" in patch) {
    if (ep.methodAuto) {
      const comp = D.components.find((c) => c.id === patch.connId) || null;
      const sdr = comp ? detectSolderConn_(comp.art) : null;
      next = { ...next, method: sdr ? "solder" : "crimp", sdrTmpl: sdr };
    }
    selectedTemplates = clearAutoTemplates(S, ep, ["prs", "ins", "solder"]);
  }
  if ("method" in patch) {
    next = { ...next, methodAuto: false };
    if (patch.method === "solder" && !next.sdrTmpl) {
      const comp = D.components.find((c) => c.id === next.connId) || null;
      next = { ...next, sdrTmpl: comp ? detectSolderConn_(comp.art) : null };
    }
  }
  if ("termId" in patch) { selectedTemplates = clearAutoTemplates(S, ep, ["prs", "ins"]); next = { ...next, termAuto: false }; }
  const after = { ...S, selectedTemplates, endpoints: S.endpoints.map((e) => (e.id === id ? next : e)) };
  // Выбрали разъём в «Сложной сборке» — к нему сразу его терминал из состава (владелец 13.10).
  if ("connId" in patch && !("termId" in patch) && S.tab === "complex") {
    const auto = { ...after, endpoints: after.endpoints.map((e) => (e.id === id && e.termAuto !== false ? { ...e, termAuto: true } : e)) };
    return autoPickTerminals(D, auto, undefined, id);
  }
  return after;
}

/** wireIdxs разъёма, на который не заведён ни один провод. */
export const NO_WIRES = -1;

export function toggleRoute(S: GenState, gen: Gen, id: string, wIdx: number): GenState {
  const ep = S.endpoints.find((e) => e.id === id);
  if (!ep) return S;
  const routed = new Set(gen.routedWireIdxs_(ep));
  if (routed.has(wIdx)) routed.delete(wIdx); else routed.add(wIdx);
  const allIdx = gen.activeWires_().map((w) => w.idx);
  const allOn = allIdx.length === routed.size && allIdx.every((i) => routed.has(i));
  // Пустой список значит «все провода»; разъём без проводов хранится как [NO_WIRES] — такого номера у провода нет.
  const list = allIdx.filter((i) => routed.has(i));
  return { ...S, endpoints: S.endpoints.map((e) => (e.id === id ? { ...e, wireIdxs: allOn ? [] : list.length ? list : [NO_WIRES] } : e)) };
}

/** Смена кабеля в строке: число жил снова из наименования (onWireChange). */
export function updateWire(S: GenState, idx: number, patch: Partial<WireEntry>): GenState {
  return { ...S, wires: S.wires.map((w) => {
    if (w.idx !== idx) return w;
    const next = { ...w, ...patch };
    if (patch.compId !== undefined && patch.compId !== w.compId) delete next.cores;
    return next;
  }) };
}

/** «Жил» в строке «Кабели» (владелец 14.10: Helukabel 15201 без записи в БД.ПРОВ генератор резал как 19 отдельных проводов).
 *  Технолог задаёт число жил сам; было 1 жила на штуку, а кол-во делится нацело — штуки пересчитываются в кабели,
 *  общее число жил не меняется (19 шт × 1 → 1 шт × 19). `per` — жил в кабеле до правки (wireCores_). */
export function setWireCores(S: GenState, idx: number, cores: number, per: number): GenState {
  const w = S.wires.find((x) => x.idx === idx);
  if (!w) return S;
  const n = Math.max(1, Math.round(cores) || 1), qty = Math.max(1, Math.round(Number(w.qty) || 1));
  const regroup = per <= 1 && n > 1 && qty % n === 0;
  return updateWire(S, idx, { cores: n, ...(regroup ? { qty: qty / n } : {}) });
}

/** Строку «Термоусадки на жгут» правит владелец: с этого момента она его — считается, даже если трубка занята ещё где-то. */
export function setHsRow(S: GenState, index: number, patch: Partial<Omit<HsRow, "auto">>): GenState {
  return { ...S, hs: { ...S.hs, rows: S.hs.rows.map((row, i) => (i === index ? { ...row, ...patch, auto: false } : row)) } };
}
/** «Добавить трубку»: своя строка владельца, длину и количество он вводит сам. */
export function addHsRow(S: GenState, compId: string): GenState {
  if (!compId) return S;
  return { ...S, hs: { ...S.hs, enabled: true, rows: [...S.hs.rows, { compId, len: "", cnt: "1", auto: false }] } };
}
/** «×» у строки: трубка уходит с жгута. */
export function removeHsRow(S: GenState, index: number): GenState {
  return { ...S, hs: { ...S.hs, rows: S.hs.rows.filter((_, i) => i !== index) } };
}

/** Оплётка включена/сменена — термоусадка на жгут следует за ней, пока галочку не трогали руками (syncHsWithBraid_). */
export function syncHsWithBraid_(D: GenData, S: GenState): GenState {
  if (S.hs.manual) return S;
  const g = makeGen(D, S);
  return { ...S, hs: { ...S.hs, enabled: g.hsDefaultOn_(g.harnessSleeveComps_(), g.rjEndsInBom_()) } };
}

// ── Справочники ERP → форма D (TcGenerator.gs read*ForGenerator_) ─────────────
const pick = (fields: Record<string, string>, ...keys: string[]) => {
  for (const key of keys) {
    const direct = fields[key];
    if (direct != null && String(direct).trim()) return String(direct).trim();
    const found = Object.keys(fields).find((name) => name.trim().toLowerCase() === key.toLowerCase());
    if (found && String(fields[found] || "").trim()) return String(fields[found]).trim();
  }
  return "";
};
export function terFromFields(id: string, f: Record<string, string>): TerRec {
  return { id, fields: f, article: f.terArticle || "", step: f.terStep || "", strip: f.terStrip || "", lPlus: f.terLPlus || "", lMinus: f.terLMinus || "",
    applicator: f.terApplicator || "", awgFrom: f.awgFrom || "", awgTo: f.awgTo || "", mm2From: f.mm2From || "", mm2To: f.mm2To || "",
    // A row stored before terArticleBag existed still has the sheet column «Артикул контакта (BAG)».
    articleBag: f.terArticleBag || String(Object.entries(f).find(([name]) => /\(bag\)/i.test(name))?.[1] || "").trim() };
}
export function coaxFromFields(f: Record<string, string>): CoaxRec {
  return {
    article: pick(f, "coaxArticle", "Артикул"), type: pick(f, "coaxType", "Тип/Серия", "type"), wire: pick(f, "coaxWire", "Провод", "wire"),
    mfr: pick(f, "coaxMfr", "Производитель", "mfr"), program: pick(f, "program", "Программа"),
    l1: pick(f, "l1", "L1"), l2: pick(f, "l2", "L2"), l3: pick(f, "l3", "L3"),
    lPlus: pick(f, "lPlus", "L+"), lMinus: pick(f, "lMinus", "L-", "L−"), pinType: pick(f, "pinType", "Тип пина"),
    shieldType: pick(f, "shieldType", "Тип экрана"), connArticle: pick(f, "connArticle", "Артикул разъёма", "Артикул разъема"),
    centerContact: pick(f, "Центральный контакт"),
  };
}
/** Поля записи БД.КОАКС, которые создаёт «Добавить в БД.КОАКС» (addCoaxRecordToDb). */
export function coaxRecordFields(p: Omit<CoaxRec, "article">): Record<string, string> {
  const fields: Record<string, string> = {
    coaxArticle: p.connArticle, coaxType: p.type, coaxWire: p.wire, coaxMfr: p.mfr, program: p.program,
    l1: p.l1, l2: p.l2, l3: p.l3, lPlus: p.lPlus, lMinus: p.lMinus,
    pinType: p.pinType, shieldType: p.shieldType, connArticle: p.connArticle,
  };
  return Object.fromEntries(Object.entries(fields).filter(([, v]) => String(v || "").trim()));
}
export function opFromFields(f: Record<string, string>): OpRec {
  return { opNumber: f.opNumber || "", opName: f.opName || "", tOp: f.tOp || "", tPrep: f.tPrep || "", tMachine: f.tMachine || "" };
}
/** parseWireDia_ над строками «Ø изоляции по маркам» (ключи полей = шапка по порядку: AWG · ГОСТ · Ø жилы · марки).
 *  Строки — GET /api/references/wire-dia (база монтажных проводов, 12 серий), раньше — СПР.КАБ · AWG. Размер только
 *  по ГОСТ (0,07 мм², AWG пусто) ищется по сечению. */
export function wireDiaFromRecords(rows: Array<Record<string, string>>): WireDia {
  if (!rows.length) return null;
  const headers = Object.keys(rows[0]);
  const awgCol = headers.findIndex((h) => h.trim().toLowerCase() === "awg");
  if (awgCol < 0) return null;
  const gostCol = headers.findIndex((h) => h.trim().toLowerCase() === "гост");
  const coreKey = headers[awgCol + 2];
  const marks = headers.map((h, c) => ({ name: h, norm: h.toLowerCase().replace(/[\s-]/g, ""), col: c })).filter((m) => m.col > awgCol + 2 && m.name);
  const byAwg: Record<string, WireDiaRow> = {}, byGost: Record<string, WireDiaRow> = {}, gostAwg: Record<string, number> = {};
  rows.forEach((r) => {
    const awg = String(r[headers[awgCol]] || "").trim();
    const gv = gostCol >= 0 ? parseFloat(String(r[headers[gostCol]] || "").replace(",", ".")) : NaN;
    if (!awg && !isFinite(gv)) return;
    const row: WireDiaRow = {};
    const core = parseFloat(String(r[coreKey] || "").replace(",", "."));
    if (isFinite(core) && core > 0) row.__core = core;
    marks.forEach((m) => { const v = parseFloat(String(r[m.name] || "").replace(",", ".")); if (isFinite(v) && v > 0) row[m.norm] = v; });
    String(r[WIRE_DIA_OLD_COLUMN_] || "").split(",").map((s) => s.trim().toLowerCase().replace(/[\s-]/g, "")).filter(Boolean)
      .forEach((norm) => { if (row[norm]) row[OLD_FLAG_ + norm] = 1; });
    if (awg) byAwg[awg] = row;
    if (isFinite(gv)) byGost[String(gv)] = row;
    if (isFinite(gv) && /^\d+$/.test(awg)) gostAwg[String(gv)] = Number(awg);
  });
  return { marks: marks.map((m) => ({ name: m.name, norm: m.norm })), byAwg, byGost, gostAwg };
}
/** parseCoaxDia_ над записями СПР.КАБ · коаксиал («Кабель | D1 | D2 | D3»). */
export function coaxDiaFromRecords(rows: Array<Record<string, string>>): CoaxCableDia {
  if (!rows.length) return null;
  const byCable: Record<string, { d1: string; d2: string; d3: string }> = {};
  rows.forEach((r) => {
    const cable = String(Object.values(r)[0] || "").trim();
    if (!cable) return;
    byCable[cable.toLowerCase().replace(/[\s-]/g, "")] = { d1: pick(r, "D1"), d2: pick(r, "D2"), d3: pick(r, "D3") };
  });
  return { byCable };
}
