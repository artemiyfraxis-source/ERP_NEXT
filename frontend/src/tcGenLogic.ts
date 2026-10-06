/*
 * Логика генератора техкарт — порт SUPPLYMAP TcGenInline.html (IIFE window.TcGen) без вёрстки.
 *
 * В SUPPLYMAP состояние формы жило в DOM (галочки, селекты, поля); здесь оно в GenState, а каждая
 * функция формы читает его вместо document.getElementById. Имена функций и правила оставлены как
 * в источнике (суффикс «_» — приватные функции TcGenInline), чтобы порт сверялся построчно.
 * Выход — тот же config, что форма отправляла portalTechmapGenerate, и его принимает
 * `POST /api/techcards/projects/{id}/build` (etk_builder.py читает ровно эти ключи).
 */

/** `art` — what the row actually uses (selects, gauge, terminals, ETK); `kd` — the КД article it replaced, display only. */
export type Comp = { id: string; type: string; name: string; art: string; kd?: string; qty: number | string; length?: number };
export type TerRec = {
  id?: string; article: string; step: string; strip: string; lPlus: string; lMinus: string; applicator: string;
  awgFrom: string; awgTo: string; mm2From: string; mm2To: string; fields?: Record<string, string>;
};
export type CoaxRec = {
  article: string; type: string; wire: string; mfr: string; program: string;
  d1: string; d2: string; d3: string; l1: string; l2: string; l3: string;
  lPlus: string; lMinus: string; pinType: string; shieldType: string; connArticle: string;
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
};

export type Side = "A" | "B";
/** manual — «Ручная сборка» (manualAssembly.ts): свой список операций, формы кабелей и разъёмов не участвуют. */
export type Tab = "inter" | "complex" | "coax" | "manual";
export type Endpoint = {
  id: string; side: Side; connId: string; method: "crimp" | "solder"; methodAuto: boolean;
  sdrTmpl: string | null; termId: string; wireIdxs: number[]; otp: boolean; double: boolean;
  label: string; solderStrip: string; tutLen: string; tutOff?: boolean; fold?: boolean; perTerm?: number;
  pins?: Record<number, string>;
};
/** Один провод «разъём · контакт → разъём · контакт» (end — «А1»/«В1» как на карточке разъёма). */
export type ConnectionEnd = { end: string; conn: string; label: string; pin: number };
export type Connection = { wire: string; from: ConnectionEnd; to: ConnectionEnd };
export type WireEntry = { idx: number; compId: string; length: number; qty: number; cores?: number; jacket?: string };
export type WrapKind = "tape" | "braid" | "under";
export type WrapSlot = { mat: string; kind: WrapKind; len: string };
export type CoaxSide = { art: string; name: string } | null;
export type OpTimes = { tmpl: string; tPrep: string; tOp: string; tMachine: string };

export type GenState = {
  tab: Tab;
  wires: WireEntry[];
  nextWireIdx: number;
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
    enabled: boolean; mode: "pairs" | "all"; pairs: Array<{ a: string; b: string }>;
    wires: number[] | null; pitch: string; z0: string; dia: string; diaManual: boolean;
  };
  spl: { enabled: boolean; count: string; wires: string; tut: string; tutMat: string; strip: string; fold: boolean };
  hs: { enabled: boolean; manual: boolean; rows: Array<{ compId: string; len: string; cnt: string }> };
  mark: boolean;
  freeStrip: { enabled: boolean; len: string };
  cap: { enabled: boolean; tut: string; len: string };
  wrap: { enabled: boolean; slots: WrapSlot[] };
  seal: { enabled: boolean; mat: string; places: string };
  tin: { enabled: boolean; wires: number[] | null; stripLen: string };
  coax: {
    len: string; tutEnabled: boolean; tutLen: string; pinA: string; pinB: string; coverA: string; coverB: string;
    stripB: boolean; shield: string; sideA: CoaxSide; sideB: CoaxSide;
  };
  selectedTemplates: Record<string, string>;
  manualTemplates: string[];
  times: Record<string, OpTimes>;
  materialsSheet: boolean;
};

export type OpSpec = {
  type: string; opKey: string; label: string; side?: Side; ord?: number; epId?: string; crimpEpId?: string; crimpEpIdB?: string;
  tmplHint?: string; cat?: string; termArt?: string; firstTerm?: boolean; wireIdxs?: number[];
  strip?: { a: boolean; lenA: string; b: boolean; lenB: string }; stripCount?: number;
  tutArt?: string; tutName?: string; tutCount?: number; tutSegs?: Array<{ len: string; count: number }>;
  harness?: boolean; units?: number; wrapKind?: "tape" | "braid"; under?: boolean; wrapArt?: string; wrapName?: string;
  wrapLen?: string; marks?: Array<{ art: string; name: string; qty: number }>;
  sealArt?: string; sealName?: string; sealQty?: string; sealPlaces?: number; tin?: boolean;
};

// ── Константы TcGenInline ─────────────────────────────────────────────────
export const JACKET_STRIP_MM_ = "20";
export const DIODE_RE_ = /tvs|диод|\d[.,]\d\s*ke\d|\bsmbj\d/i;
export const LUG_RE_ = /^\s*(?:нки|нви|нвки|ншви|нши|рпи|тмл)(?![а-яa-z])/i;
export const FOLD_STRIP_MM_ = "10";
const JACKETED_RE_ = /\b\d+\s*P(?:R|AIR)?\b|\b\d+\s*C\s*[*xх×]|\b[SFU]?\/?[SFU]TP\b|\bUSB\b|витая\s*пара|экранир/i;
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

/** Русское название операции генератора по её `type` (как `OPERATION_TYPE_NAMES` на сервере): когда в конфиге нет `label`. */
const OP_TYPE_NAMES: Record<string, string> = {
  cutWire: "Резка", cutWireStrip: "Резка с разделкой", cutWireCrimp: "Резка с обжимом", cutTut: "Резка ТУТ",
  prsTerm: "Опрессовка терминалов", prsTermA: "Опрессовка терминалов (А)", prsTermB: "Опрессовка терминалов (В)",
  insTerm: "Монтаж терминалов", insTermA: "Монтаж терминалов (А)", insTermB: "Монтаж терминалов (В)",
  insHousing: "Монтаж в корпус", insTut: "Надеть ТУТ", heatTut: "Усадка ТУТ", insWrap: "Обмотка", insMark: "Маркировка",
  tin: "Лужение", twist: "Свивка", splice: "Сращивание", seal: "Герметизация", solderConn: "Пайка разъёма",
  stripCable: "Разделка кабеля", foldCore: "Загибка жил", endCap: "Установка заглушек", coaxCut: "Резка кабеля",
  coaxCutTut: "Резка ТУТ", coaxStrip: "Разделка коаксиала", coaxShield: "Опрессовка втулки экрана",
  coaxSolderShield: "Пайка экрана", coaxPin: "Пайка центрального контакта", coaxCapCrimp: "Монтаж крышки (обжим)",
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
  const m2 = s.match(/(?:^|[^\d.,])(\d+)\s*[xх×*]\s*\d+(?:[.,]\d+)?(?!\s*[xх×*\d])/i);
  return m2 ? Number(m2[1]) : 1;
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
const EPS_BY_MARK: Record<string, number> = {
  мгтф: 2.1, мс: 2.1, ptfe: 2.1, m22759: 2.5, txl: 2.3, gxl: 2.3,
  ul1061: 3.5, ul1571: 3.5, ul1007: 3.5, ul1015: 3.5, мгшв: 3.5, нв4: 3.5, flry: 3.5,
  пвам: 4.0, пгва: 4.0, силикон: 3.0, ркгм: 3.0,
};
export function isRjConn_(c: Comp) { return /8p8c|rj[\s-]*45/i.test(`${c.art || ""} ${c.name || ""}`); }
function endpointOpKey_(side: Side, ordinal: number, kind: "prs" | "ins" | "solder") {
  const base = side + ordinal;
  if (kind === "solder") return "solderConn" + base;
  if (kind === "ins") return "insTerm" + base;
  return "prsTerm" + base;
}
export const sideRu_ = (s: string) => (s === "B" ? "В" : "А");

/** Распиновка провода в порядке записи: «3, 1, 2» → [3, 1, 2], «1-6» → 1…6, «6-1» → 6…1. Повторы остаются (двойной
 *  обжим: «1, 1, 2, 2»), мусор отбрасывается, диапазон не длиннее 200 (etk_builder._pins — то же, но для рисунка). */
export function parsePinList_(value: unknown): number[] {
  const out: number[] = [];
  String(value ?? "").split(/[,;\s]+/).forEach((token) => {
    const m = /^(\d+)(?:-(\d+))?$/.exec(token);
    if (!m) return;
    const a = Number(m[1]), b = m[2] ? Number(m[2]) : a, step = b >= a ? 1 : -1;
    for (let p = a, n = 0; n < 200; p += step, n++) { out.push(p); if (p === b) break; }
  });
  return out.filter((p) => p > 0);
}

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
    spl: { enabled: false, count: "1", wires: "3", tut: "", tutMat: "", strip: "", fold: false },
    hs: { enabled: false, manual: false, rows: [] },
    mark: false,
    freeStrip: { enabled: false, len: "" },
    cap: { enabled: true, tut: "", len: "" },
    wrap: { enabled: false, slots: WRAP_SLOTS_.map(() => ({ mat: "", kind: "tape" as WrapKind, len: "" })) },
    seal: { enabled: false, mat: "", places: "" },
    tin: { enabled: false, wires: null, stripLen: "" },
    coax: {
      len: "", tutEnabled: true, tutLen: "", pinA: "пайка", pinB: "пайка", coverA: "", coverB: "",
      stripB: false, shield: "обжим", sideA: null, sideB: null,
    },
    selectedTemplates: {}, manualTemplates: [], times: {}, materialsSheet: false,
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

  function findTerRecord_(art: string | undefined): TerRec | null {
    if (!art) return null;
    const records = D.terRecords || [];
    const exact = String(art).toLowerCase().trim();
    const norm = normalizeArt_(art);
    let found = records.find((r) => (r.article || "").toLowerCase().trim() === exact);
    if (!found && norm.length >= 3) found = records.find((r) => normalizeArt_(r.article) === norm);
    if (!found) {
      const words = String(art).trim().split(/\s+/);
      if (words.length > 1) {
        const normNoMfr = normalizeArt_(words.slice(1).join(" "));
        if (normNoMfr.length >= 3) found = records.find((r) => normalizeArt_(r.article) === normNoMfr);
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

  /** Соединения провод за проводом: k-й контакт в распиновке первого разъёма провода (А раньше В) ↔ k-й контакт у
   *  следующих разъёмов того же провода, по порядку списков. Перекрёст задаётся порядком: А «1-6», В «3, 1, 2, 4, 5, 6»
   *  → 1→3, 2→1, 3→2… Разные длины списков — пары до короткого и предупреждение. */
  function connections_(): { list: Connection[]; warnings: string[] } {
    const list: Connection[] = [], warnings: string[] = [];
    const ordered = (["A", "B"] as Side[]).flatMap((side) => endpointsOnSide_(side));
    const end = (ep: Endpoint, pin: number): ConnectionEnd => {
      const conn = getCompById(ep.connId);
      return { end: endKey_(ep), conn: conn ? (conn.art || conn.name || "") : "", label: isComplex_() ? (ep.label || "").trim() : "", pin };
    };
    activeWires_().forEach((w) => {
      const ends = ordered.filter((ep) => routedWireIdxs_(ep).includes(w.idx))
        .map((ep) => ({ ep, pins: parsePinList_(ep.pins?.[w.idx]) })).filter((x) => x.pins.length);
      if (ends.length < 2) return;
      const comp = getCompById(w.compId);
      const wire = comp ? (comp.art || comp.name || "") : "";
      const [from, ...rest] = ends;
      let k = 0;
      rest.forEach((x) => x.pins.forEach((pin) => {
        if (k < from.pins.length) list.push({ wire, from: end(from.ep, from.pins[k]), to: end(x.ep, pin) });
        k++;
      }));
      if (k !== from.pins.length) {
        warnings.push(`${wire || "Провод"}: на ${endKey_(from.ep)} ${from.pins.length} конт., на ${rest.map((x) => endKey_(x.ep)).join(" + ")} ${k} — ` +
          `соединения по порядку только для ${Math.min(k, from.pins.length)}.`);
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

  function wireCores_(w: WireEntry) {
    if (w.cores) return w.cores;
    const c = w.compId ? getCompById(w.compId) : null;
    return c ? cableCoresFromName_(`${c.name || ""} ${c.art || ""}`) : 1;
  }
  function isJacketedCable_(w: WireEntry) {
    if (wireCores_(w) > 1) return true;
    const c = w.compId ? getCompById(w.compId) : null;
    return !!c && JACKETED_RE_.test(`${c.name || ""} ${c.art || ""}`);
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
  function lugTmplHint_(art: string) {
    const s = String(art || "");
    if (!LUG_RE_.test(s)) return "";
    const kind = /^\s*нши|^\s*ншви/i.test(s) ? /^PRS_НШВИ$/i : /^\s*тмл/i.test(s) ? /^PRS_ТМЛ$/i : /^PRS_[HН][KК]И\/НВИ\/РПИ$/i;
    const t = (D.templates || []).find((x) => kind.test(String(x.title || "").split("|")[0].trim()));
    return t ? String(t.title).split("|")[0].trim() : "";
  }
  function stripCableTmpl_(c: Comp | null) {
    const s = c ? `${c.art || ""} ${c.name || ""}` : "";
    if (/usb/i.test(s)) return /usb\s*3|3[.,][0-2]/i.test(s) ? "STRIP_CABLE_usb_3.2" : "STRIP_CABLE_usb_2.0";
    const m = /\b(SF|S|F|U)\s*\/\s*(FTP|UTP)\b/i.exec(s);
    if (m) return `STRIP_${m[1].toUpperCase()}/${m[2].toUpperCase()}_ethernet`;
    if (/\bFTP\b/i.test(s)) return "STRIP_F/UTP_ethernet";
    if (/экран|shield|sheld/i.test(s)) return "STRIP_CABLE_sheld";
    return "STRIP_CABLE";
  }
  const markComps_ = () => comps().filter((c) => /маркир/i.test(`${c.type || ""} ${c.name || ""}`));
  function harnessSleeveComps_() {
    const solderTut = S.endpoints.some((e) => e.method === "solder" && !e.tutOff) ? bomTut_() : null;
    return comps().filter((c) => {
      const s = `${c.type || ""} ${c.name || ""} ${c.art || ""}`;
      return c !== solderTut && /ттк|тут|термоусад|трубк|shrink/i.test(s) && !PROTECT_TUBE_RE_.test(s);
    });
  }
  const rjEndsInBom_ = () => comps().filter((c) => /^соед/i.test(c.type || "") && isRjConn_(c))
    .reduce((n, c) => n + (Number(c.qty) || 1), 0);
  const braidOn_ = () => S.wrap.enabled && S.wrap.slots.some((slot) => slot.mat && slot.kind === "braid");
  const hsDefaultOn_ = (hs: Comp[], rjEnds: number) => hs.length > 0 && (braidOn_() || rjEnds > 1);

  // ── Концы, сростки, заделка ──
  function spliceSizes_() {
    if (!isComplex_() || !S.spl.enabled) return [];
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
      wires.forEach((w) => { if (set.has(w.idx)) ends -= qty(w); });
    });
    return Math.max(0, ends - spliceEnds_());
  }
  const spliceStripAtCut_ = () => spliceEnds_() > 0 && freeEnds_() === 0;
  function capTube_() {
    if (!isComplex_() || !S.cap.enabled || !freeEnds_()) return null;
    return getCompById(S.cap.tut) || null;
  }
  function foldOps_(): OpSpec[] {
    if (!isComplex_()) return [];
    let n = 0;
    S.endpoints.forEach((e) => {
      if (e.method !== "crimp" || !e.fold) return;
      const set = new Set(routedWireIdxs_(e));
      activeWires_().forEach((w) => { if (set.has(w.idx)) n += (Number(w.qty) || 1) * (wireCores_(w) || 1); });
    });
    if (S.spl.fold) n += spliceEnds_();
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
    const id = templateIdFor({ type: "splice", opKey: "splice", tmplHint: "SDR_W_W", cat: "Пайка", label: "" });
    const t = (D.templates || []).find((x) => x.id === id);
    return !!(t && /US_WELD|сварк/i.test(t.title || ""));
  }
  function spliceOps_(): OpSpec[] {
    const sizes = spliceSizes_();
    const cnt = sizes.length;
    if (!cnt) return [];
    const wires = sizes.every((n) => n === sizes[0]) ? String(sizes[0]) : sizes.join(", ");
    const len = (S.spl.tut || "").trim();
    const tut = getCompById(S.spl.tutMat) || bomTut_();
    const ends = spliceEnds_();
    const ops: OpSpec[] = spliceStripAtCut_() ? [] : [{ type: "stripCable", opKey: "stripSpl", tmplHint: "STRIP_WIRE", cat: "Разделка",
      tutCount: ends, label: `Зачистка концов под сростки — ${ends} конц.` }];
    if (len && tut) {
      ops.push({ type: "cutTut", opKey: "cutSpl", tmplHint: "CUT_TUT_short", cat: "Резка",
        label: `Резка трубки на сростки — ${cnt}×${len} мм`,
        tutArt: tut.art || tut.name || "", tutName: tut.name || tut.art || "", tutCount: cnt, tutSegs: [{ len, count: cnt }] });
      ops.push({ type: "insTut", opKey: "insSpl", tmplHint: "INS_TUT", cat: "Монтаж", tutCount: cnt,
        label: `Надеть трубку на провода сростка — ${cnt} шт` });
    }
    ops.push({ type: "splice", opKey: "splice", tmplHint: "SDR_W_W", cat: "Пайка", units: cnt,
      label: `Сростки — ${cnt} шт по ${wires} провода` });
    if (len && tut) {
      ops.push({ type: "heatTut", opKey: "heatSpl", tmplHint: "HEAT_TTK_short", cat: "Усадка", tutCount: cnt,
        label: `Усадка трубки на сростках — ${cnt} шт` });
    }
    return ops;
  }

  // ── Резка на станках ──
  function terHasAutoApplicator_(art: string) {
    const r = findTerRecord_(art);
    return !!(r && String(r.applicator || "").split(/[/,;]/).some((s) => /^[AА]\d+$/i.test(s.trim())));
  }
  /** Можно ли обжать сторону на автомате резки: "" — можно; "applicator" — мешает только то, что у терминала в БД.ТЕР нет
   *  автоматического аппликатора (A…); "other" — групповой обжим, складывание, RJ, разные терминалы, наконечник, кабель. */
  function crimpCutBlock_(side: Side): "" | "applicator" | "other" {
    const eps = endpointsOnSide_(side);
    if (!eps.length || eps.some((e) => e.method !== "crimp" || !e.termId || e.termId !== eps[0].termId
        || e.double || (e.fold && isComplex_()) || isRjEndpoint_(e))) return "other";
    const t = getCompById(eps[0].termId);
    if (!t || LUG_RE_.test(t.art || t.name || "")) return "other";
    const routed = new Set(eps.flatMap(routedWireIdxs_));
    const ws = activeWires_().filter((w) => routed.has(w.idx));
    if (!ws.length || ws.some((w) => isJacketedCable_(w))) return "other";
    return terHasAutoApplicator_(t.art || t.name) ? "" : "applicator";
  }
  function crimpCutSides_(): Side[] {
    return (["A", "B"] as Side[]).filter((side) => !crimpCutBlock_(side));
  }
  /** Почему сторону с обжимом нельзя обжать на автомате резки: короткая строка для формы («сторона В: разные терминалы —
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
    if (new Set(eps.map((e) => e.termId)).size > 1) return `${head}разные терминалы${tail}`;
    if (crimpCutBlock_(side) === "applicator") {
      const t = getCompById(eps[0].termId);
      return `${head}у ${t ? t.art || t.name : "терминала"} нет аппликатора автомата (A…)${tail}`;
    }
    return `${head}двойной обжим, сложенная жила, RJ, наконечник или кабель${tail}`;
  }
  const hasTemplateCode_ = (code: string) =>
    (D.templates || []).some((t) => String(t.title || "").split("|")[0].trim().toLowerCase() === code.toLowerCase());
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
   *  можно обжать на автомате), "" — обжимать нечего. Без выбора — первая доступная сторона, как раньше. */
  function crimpCutSideSelected_(): Side | typeof CRIMP_BOTH_ | "" {
    const sides = crimpCutSides_();
    if (S.crimpCutSide === CRIMP_BOTH_ && sides.length === 2) return CRIMP_BOTH_;
    return (sides.includes(S.crimpCutSide as Side) ? S.crimpCutSide : sides[0] || "") as Side | "";
  }
  /** Стороны, которые обжимает автомат резки в этой схеме: [], [A], [B] или [A, B]. */
  function crimpCutActiveSides_(): Side[] {
    if (S.tab === "coax" || !crimpCutEnabled_()) return [];
    const s = crimpCutSideSelected_();
    if (s === CRIMP_BOTH_) return ["A", "B"];
    return s && crimpCutSides_().includes(s) ? [s] : [];
  }
  function harnessSleeves_() {
    if (!S.hs.enabled) return [];
    const cap = capTube_();
    const spl = spliceEnds_() ? getCompById(S.spl.tutMat) : null;
    return S.hs.rows.map((row) => {
      const c = getCompById(row.compId);
      if (c && ((cap && c.id === cap.id) || (spl && c.id === spl.id))) return null;
      const len = (row.len || "").trim();
      const cnt = Math.max(0, parseInt(row.cnt, 10) || 0);
      return c && cnt ? { comp: c, len, cnt } : null;
    }).filter((x): x is { comp: Comp; len: string; cnt: number } => !!x);
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
    if (mode === "inner") {
      const lpA = terA ? parseLengthVal_(terA.lPlus) : 0;
      const lpB = terB ? parseLengthVal_(terB.lPlus) : 0;
      return { total: lpA + lpB, noData: hasTermSelected && !terA && !terB,
        parts: [lpA ? `L+ ст.А: ${lpA}` : null, lpB ? `L+ ст.В: ${lpB}` : null].filter(Boolean) as string[] };
    }
    const lmA = terA ? parseLengthVal_(terA.lMinus) : 0;
    const lmB = terB ? parseLengthVal_(terB.lMinus) : 0;
    return { total: -(lmA + lmB), noData: hasTermSelected && !terA && !terB,
      parts: [lmA ? `L− ст.А: ${lmA}` : null, lmB ? `L− ст.В: ${lmB}` : null].filter(Boolean) as string[] };
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
  function getTwistPairs() {
    return S.twist.pairs.filter((p) => p.a !== "" && p.b !== "" && p.a !== p.b).map((p) => [Number(p.a), Number(p.b)] as [number, number]);
  }
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

  // Справочник СПР.КАБ
  function wireGostKey_(s: string, mk: { norm: string }, byGost: Record<string, WireDiaRow>) {
    const re = new RegExp(mk.norm.split("").map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("[\\s-]*"));
    const nums = s.replace(re, " ").match(/\d+(?:\.\d+)?/g) || [];
    const keys = nums.map((n) => String(Number(n))).filter((k) => byGost[k]);
    return keys.find((k) => byGost[k][mk.norm]) || keys[0] || null;
  }
  function wireRow_(art: string) {
    const wd = D.wireDia;
    if (!wd) return null;
    const s = String(art || "").toLowerCase().replace(/,/g, ".");
    const sNorm = s.replace(/[\s-]/g, "");
    const mk = (wd.marks || []).find((x) => x.norm && sNorm.includes(x.norm));
    if (!mk) return null;
    let row: WireDiaRow | null = null;
    const aw = s.match(/(\d+)\s*awg/);
    if (aw && wd.byAwg) row = wd.byAwg[aw[1]] || null;
    if (!row && wd.byGost) { const k = wireGostKey_(s, mk, wd.byGost); row = k ? wd.byGost[k] : null; }
    return row ? { row, mk } : null;
  }
  function lookupWireDia_(art: string) { const r = wireRow_(art); return (r && r.row[r.mk.norm]) || 0; }
  function wireGauge_(art: string) {
    const wd = D.wireDia;
    const s = String(art || "").toLowerCase().replace(/,/g, ".");
    const aw = s.match(/(\d+)\s*awg/);
    const awg = aw ? Number(aw[1]) : null;
    let mm2: number | null = null;
    if (wd && wd.marks && wd.byGost) {
      const sNorm = s.replace(/[\s-]/g, "");
      const mk = wd.marks.find((x) => x.norm && sNorm.includes(x.norm));
      if (mk) { const k = wireGostKey_(s, mk, wd.byGost); if (k) mm2 = Number(k); }
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
  function checkTerminalFit_() {
    const warns: string[] = [];
    const epA = firstCrimpEndpoint_("A"), epB = endpointsOnSide_("B").length ? firstCrimpEndpoint_("B") : null;
    const termA = getCompById(epA?.termId), termB = getCompById(epB?.termId);
    const rA = termA ? terRange_(findTerRecord_(termA.art)) : null;
    const rB = termB ? terRange_(findTerRecord_(termB.art)) : null;
    if (!rA && !rB) return warns;
    activeWires_().forEach((w) => {
      const c = getCompById(w.compId);
      if (!c) return;
      const g = wireGaugeOf_(c);
      ([["A", rA, termA], ["B", rB, termB]] as const).forEach(([side, r, t]) => {
        if (!r || !t) return;
        const fit = wireFitsTerminal_(g, r);
        if (!fit.ok) warns.push("• «" + (c.name || c.art) + "» (" + fit.basis + ") вне диапазона терминала " + side + " «" + (t.name || t.art) + "» (" + fit.range + ")");
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
    const ends = activeWires_().reduce((n, w) => n + (Number(w.qty) || 1) * (wireCores_(w) || 1), 0) * 2;
    return terms > 0 && !solder && S.endpoints.length > 1 && terms !== ends;
  }
  function complexWarning() {
    const nA = endpointsOnSide_("A").length, nB = endpointsOnSide_("B").length;
    if (isComplex_() || !(nA > 1 || nB > 1 || isComplexKit_())) return "";
    return `⚠ Состав сложнее межплатной 1+1 (разъёмов на сторону: А — ${nA}, В — ${nB}). `
      + "Разводку жил, сростки, заделку концов, наконечники и герметизацию ведёт вкладка «Сложная сборка».";
  }

  // ── КОАКС ──
  const coaxNorm_ = (s: string) => String(s || "").toLowerCase().replace(/\s+/g, "");
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
  function coaxCableDiaLookup_() {
    const t = (D.coaxCableDia && D.coaxCableDia.byCable) || {};
    return t[String(coaxBomCable_()).toLowerCase().replace(/[\s-]/g, "")] || null;
  }
  // Диаметры принадлежат кабелю (владелец 29.09.2026, БД.КОАКС D1–D3 = формула по СПР.КАБ): разделка берёт
  // D1–D3 из СПР.КАБ по кабелю из BOM, D1–D3 записи разъёма — только запасной вариант. L1–L3, L± — у разъёма.
  function coaxDia_(rec: CoaxRec | null) {
    const dia = coaxCableDiaLookup_();
    return {
      d1: (dia && dia.d1) || (rec ? rec.d1 : "") || "",
      d2: (dia && dia.d2) || (rec ? rec.d2 : "") || "",
      d3: (dia && dia.d3) || (rec ? rec.d3 : "") || "",
    };
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
    return matchCoaxRec_(s.art, side === "A" ? S.coax.pinA : S.coax.pinB, S.coax.shield);
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
      const dia = coaxDia_(rec);
      const dims = ["D1:" + (dia.d1 || "—"), "D2:" + (dia.d2 || "—"), "D3:" + (dia.d3 || "—"), "L1:" + (rec.l1 || "—"), "L2:" + (rec.l2 || "—"), "L3:" + (rec.l3 || "—")];
      return { text: `✓ ${rec.type || ""} · ${rec.wire || ""} · ${dims.join(" ")}`, rec, missing: false };
    }
    return { text: `⚠ «${s.art}» нет в БД.КОАКС — добавьте ниже`, rec, missing: true };
  }
  function getActiveOpsCoax_(): OpSpec[] {
    if (!S.coax.sideA) return [];
    const sideB = !!S.coax.sideB;
    const pinA = S.coax.pinA;
    const pinB = sideB ? S.coax.pinB : "";
    const shield = S.coax.shield;
    const pinHint = (p: string) => (p === "внутри корпуса" ? "SDR_COAX_pin_R/A" : "SDR_COAX_pin");
    const ops: OpSpec[] = [];
    const nSides = sideB ? 2 : 1;
    const x = (n: number) => (n > 1 ? ` ×${n}` : "");
    const tutOn = S.coax.tutEnabled;
    ops.push({ type: "coaxCut", opKey: "coaxCut", label: "Резка кабеля", tmplHint: "CUT_WIRE_auto" });
    if (tutOn) ops.push({ type: "coaxCutTut", opKey: "coaxCutTut", label: `Резка ТУТ${x(nSides)}`, units: nSides, tmplHint: "CUT_TUT_short" });
    ops.push({ type: "coaxStrip", side: "A", opKey: "coaxStripA", label: "Разделка коаксиала (А)", tmplHint: "STRIP_COAX_auto" });
    if (sideB) ops.push({ type: "coaxStrip", side: "B", opKey: "coaxStripB", label: "Разделка коаксиала (B)", tmplHint: "STRIP_COAX_auto" });
    else if (S.coax.stripB) ops.push({ type: "coaxStrip", side: "B", opKey: "coaxStripB", label: "Зачистка стороны B (голый конец)", tmplHint: "STRIP_COAX_auto" });
    const pinOp = (sd: Side, pin: string): OpSpec => ({ type: "coaxPin", side: sd, opKey: "coaxPin" + sd, label: `Пайка центрального контакта (${sd === "B" ? "B" : "А"})`, tmplHint: pinHint(pin) });
    const insTutOp = (): OpSpec | null => (tutOn ? { type: "coaxInsTut", opKey: "coaxInsTut", label: `Монтаж ТУТ${x(nSides)}`, units: nSides, tmplHint: "INS_TUT" } : null);
    const sleeveOp = (): OpSpec => ({ type: "coaxInsSleeve", opKey: "coaxInsSleeve", label: `Монтаж втулки экрана${x(nSides)}`, units: nSides, tmplHint: "INS_SLEEVE_coax" });
    const hasSleeve = shield === "обжим";
    const angledB = sideB && pinB === "внутри корпуса";
    const pushTut = () => { const o = insTutOp(); if (o) ops.push(o); };
    if (angledB) {
      ops.push(pinOp("A", pinA)); pushTut(); if (hasSleeve) ops.push(sleeveOp()); ops.push(pinOp("B", pinB));
    } else {
      ops.push(pinOp("A", pinA)); if (sideB) ops.push(pinOp("B", pinB)); pushTut(); if (hasSleeve) ops.push(sleeveOp());
    }
    if (pinA !== "внутри корпуса") ops.push({ type: "coaxHousing", side: "A", opKey: "coaxHousingA", label: "Монтаж корпуса (А)", tmplHint: "INS_housing_COAX" });
    if (sideB && pinB !== "внутри корпуса") ops.push({ type: "coaxHousing", side: "B", opKey: "coaxHousingB", label: "Монтаж корпуса (B)", tmplHint: "INS_housing_COAX" });
    if (shield === "пайка") {
      ops.push({ type: "coaxSolderShield", side: "A", opKey: "coaxSolderShieldA", label: "Пайка экрана (А)", tmplHint: "SDR_COAX_housing" });
      if (sideB) ops.push({ type: "coaxSolderShield", side: "B", opKey: "coaxSolderShieldB", label: "Пайка экрана (B)", tmplHint: "SDR_COAX_housing" });
    } else {
      ops.push({ type: "coaxShield", opKey: "coaxShield", label: `Опрессовка втулки экрана${x(nSides)}`, units: nSides, tmplHint: "PRS_SLEEVE_coax" });
    }
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
  function getActiveOps(): OpSpec[] {
    if (S.tab === "coax") return getActiveOpsCoax_();
    const ops: OpSpec[] = [];
    const activeWires = activeWires_();
    let solderEnds = 0;
    const tutByLen: Record<string, number> = {};
    S.endpoints.forEach((e) => {
      if (e.method !== "solder" || e.tutOff) return;
      const routed = routedWireIdxs_(e);
      const n = activeWires.filter((w) => routed.includes(w.idx)).reduce((s, w) => s + (Number(w.qty) || 1) * wireCores_(w), 0);
      if (!n) return;
      solderEnds += n;
      const len = (e.tutLen || "").trim();
      tutByLen[len] = (tutByLen[len] || 0) + n;
    });
    if (tutByLen[""] && solderEnds > 0) {
      const t = bomTut_(), m = t ? qtyNum(t) : 0;
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
      const cutLabel = (ws: WireEntry[], pfx: string) => { const ns = ws.map(nameOf); return ns.length === 1 ? `${pfx} — ${ns[0]}` : `${pfx} — ${ns.length} кабелей`; };
      const splAtCut = spliceStripAtCut_();
      const splLen = (S.spl.strip || "").trim() || "—";
      const tinIdx = tinWireIndices_();
      const stripOf = (w: WireEntry) => {
        let a = "", b = "";
        if (splAtCut) (["A", "B"] as Side[]).forEach((side) => {
          const eps = endpointsOnSide_(side);
          if (!eps.length || eps.some((e) => routedWireIdxs_(e).includes(w.idx))) return;
          if (side === "A") a = splLen; else b = splLen;
        });
        S.endpoints.forEach((e) => {
          if (!routedWireIdxs_(e).includes(w.idx)) return;
          let len: string | null = null;
          if (crimpSides.includes(e.side)) len = (findTerRecord_((getCompById(e.termId) || ({} as Comp)).art) || ({} as TerRec)).strip || "—";
          else if (e.method === "solder") len = (e.solderStrip || "").trim() || DEFAULT_LEN_MM_;
          else if (e.method === "crimp" && e.fold && isComplex_()) len = FOLD_STRIP_MM_;
          else if (e.method === "crimp" && isRjEndpoint_(e)) len = RJ_STRIP_MM_;
          else if (e.method === "crimp" && isJacketedCable_(w)) len = w.jacket || JACKET_STRIP_MM_;
          else if (e.method === "crimp" && e.double) {
            const t = e.termId ? getCompById(e.termId) : null;
            const r = t ? findTerRecord_(t.art) : null;
            len = r && r.strip ? r.strip : "—";
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
      // Сторона, которую обжимает станок: её первый разъём (терминал на стороне один — crimpCutBlock_) и заведённые в
      // неё провода. Провод, не заведённый в разъём этой стороны, на ней не обжимается.
      const crimpEpOf = (side: Side) => (crimpSides.includes(side) ? endpointsOnSide_(side)[0] || null : null);
      const crimpSets = (["A", "B"] as Side[]).map((side) => new Set(crimpEpOf(side) ? endpointsOnSide_(side).flatMap(routedWireIdxs_) : []));
      const rotor = S.rotor;
      const groups: Record<string, { wires: WireEntry[]; a: string; b: string; crimp: Side[]; tin: boolean; rot: boolean }> = {};
      activeWires.forEach((w) => {
        const s = stripOf(w);
        const crimp = (["A", "B"] as Side[]).filter((_, i) => crimpSets[i].has(w.idx));
        const tin = crimp.length > 0 && tinAtCut.has(w.idx);
        const rot = rotor && isJacketedCable_(w);
        const sig = `${s.a}|${s.b}|${crimp.join("")}${tin ? "T" : ""}|${rot ? "R" : ""}`;
        (groups[sig] || (groups[sig] = { wires: [], a: s.a, b: s.b, crimp, tin, rot })).wires.push(w);
      });
      let ci = 0;
      Object.keys(groups).forEach((sig) => {
        const g = groups[sig], ws = g.wires;
        const hasA = g.a !== "", hasB = g.b !== "", n = (hasA ? 1 : 0) + (hasB ? 1 : 0);
        const key = ci === 0 ? "cutWire" : "cutWire" + ci; ci++;
        const strip = { a: hasA, lenA: g.a === "—" ? "" : g.a, b: hasB, lenB: g.b === "—" ? "" : g.b };
        if (g.crimp.length === 2) {
          // Обе стороны на станке с двумя аппликаторами: ручной опрессовки у этих концов нет. Терминалы сторон могут
          // различаться — в названии оба. Карты CUT_WIRE+2crimp нет в библиотеке — норма и карта односторонней резки.
          const epA = crimpEpOf("A")!, epB = crimpEpOf("B")!;
          const artOf = (ep: Endpoint) => { const t = getCompById(ep.termId)!; return t.art || t.name; };
          const terms = artOf(epA) === artOf(epB) ? artOf(epA) : `${artOf(epA)} / ${artOf(epB)}`;
          ops.push({ type: "cutWireCrimp", opKey: key, side: "A", crimpEpId: epA.id, crimpEpIdB: epB.id,
            label: cutLabel(ws, `Резка с обжимом А и В (${terms})`),
            wireIdxs: ws.map((w) => w.idx), tmplHint: hasTemplateCode_(CRIMP_BOTH_CODE_) ? CRIMP_BOTH_CODE_ : "CUT_WIRE+1crimp", strip });
        } else if (g.crimp.length === 1) {
          const crimpSide = g.crimp[0], crimpEp = crimpEpOf(crimpSide)!;
          const term = getCompById(crimpEp.termId)!;
          // С лужением свободного конца тем же станком (BZW-5.0+Z) отдельной операции «Лужение» у этих проводов нет.
          ops.push({ type: "cutWireCrimp", opKey: key, side: crimpSide, crimpEpId: crimpEp.id,
            label: cutLabel(ws, `Резка с обжимом ${sideRu_(crimpSide)} (${term.art || term.name})${g.tin ? " и лужением" : ""}`),
            wireIdxs: ws.map((w) => w.idx), tmplHint: g.tin ? CRIMP_TIN_CODE_ : "CUT_WIRE+1crimp", strip, ...(g.tin ? { tin: true } : {}) });
        } else if (n === 0) {
          ops.push({ type: "cutWire", opKey: key, label: cutLabel(ws, "Резка"), wireIdxs: ws.map((w) => w.idx), tmplHint: "CUT_WIRE_auto", cat: "Резка" });
        } else {
          ops.push({ type: "cutWireStrip", opKey: key, label: cutLabel(ws, `Резка с разделкой${g.rot ? " на роторе" : ""} (${n} ст.)`),
            wireIdxs: ws.map((w) => w.idx), tmplHint: `${g.rot ? "CUT_COAX" : "CUT_WIRE"}+${n}strip`, strip });
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
    (["A", "B"] as Side[]).forEach((side) => endpointsOnSide_(side).forEach((ep) => {
      const ord = endpointOrdinal_(ep);
      const conn = ep.connId ? getCompById(ep.connId) : null;
      const term = ep.termId ? getCompById(ep.termId) : null;
      const tag = sideRu_(side) + ord;
      if (ep.method === "solder") {
        if (!conn) return;
        const routed = routedWireIdxs_(ep);
        const multi = activeWires.filter((w) => routed.includes(w.idx) && isJacketedCable_(w));
        const cableEnds = multi.reduce((s, w) => s + (Number(w.qty) || 1), 0);
        if (cableEnds && !S.rotor) {
          ops.push({ type: "stripCable", opKey: "stripCable" + side + ord, side, ord, label: `Разделка кабеля под пайку ${tag} — ${cableEnds} конц.`,
            tmplHint: stripCableTmpl_(getCompById(multi[0].compId)), cat: "Разделка", tutCount: cableEnds });
        }
        ops.push({ type: "solderConn", opKey: endpointOpKey_(side, ord, "solder"), side, ord, epId: ep.id,
          label: `Пайка разъёма ${tag} — ${conn.art || conn.name}`, tmplHint: ep.sdrTmpl || detectSolderConn_(conn.art) || undefined });
      } else {
        if (term && !crimpSides.includes(side)) {
          ops.push({ type: "prsTerm", opKey: endpointOpKey_(side, ord, "prs"), side, ord, epId: ep.id,
            label: `Опрессовка ${LUG_RE_.test(term.art || term.name || "") ? "наконечников" : "терминалов"} ${tag}`
              + (ep.double ? ` (${epPer_(ep) > 2 ? "по " + epPer_(ep) + " провода" : "двойной обжим"})` : "") + (ep.fold && isComplex_() ? " (жила сложена)" : ""),
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

    const splOps = spliceOps_();
    if (splOps.length) {
      const firstEp = ops.findIndex((o) => o.epId);
      ops.splice(firstEp >= 0 ? firstEp : ops.length, 0, ...splOps);
    }
    const housing = comps().find((c) => /корпус|housing/i.test(`${c.type || ""} ${c.name || ""}`));
    const solderEps = S.endpoints.filter((e) => e.method === "solder" && e.connId);
    if (housing && solderEps.length) {
      const h = `${housing.art || ""} ${housing.name || ""}`;
      const tmpl = /usb/i.test(h) ? "INS_housing_USB" : /gx/i.test(h) ? "INS_housing_GX" : /db|dh|снп/i.test(h) ? "INS_housing_DB/DH/СНП" : "";
      if (tmpl) ops.push({ type: "insHousing", opKey: "insHousing", cat: "Монтаж корпуса", tmplHint: tmpl, units: solderEps.length,
        label: `Монтаж корпуса — ${housing.art || housing.name} × ${solderEps.length}` });
    }
    if (ops.some((o) => o.opKey === "insTut")) {
      ops.push({ type: "heatTut", opKey: "heatTut", label: `Усадка ТУТ — ${solderEnds} шт`, tmplHint: "HEAT_TYT_short", cat: "Усадка", tutCount: solderEnds });
    }
    const seal = sealOp_();
    if (seal) { const h = ops.findIndex((o) => o.type === "insHousing"); ops.splice(h >= 0 ? h : ops.length, 0, seal); }
    if (S.twist.enabled && getTwistSelectionCount() > 0) {
      const twistOp: OpSpec = { type: "twist", opKey: "twist", label: "Свивка", cat: "Свивка" };
      const firstB = ops.findIndex((o) => o.side === "B" && o.epId);
      if (firstB >= 0) ops.splice(firstB, 0, twistOp); else ops.push(twistOp);
    }
    const tinOn = endpointsOnSide_("B").length === 0 && S.tin.enabled;
    const tinLeft = tinWireIndices_().map((i) => activeWires[i].idx).filter((idx) => !tinAtCut.has(idx));
    if (tinOn && tinLeft.length > 0) {
      ops.push({ type: "tin", opKey: "tin", label: "Лужение", cat: "Лужение", ...(tinAtCut.size ? { wireIdxs: tinLeft } : {}) });
    }

    const dress: OpSpec[] = [];
    const sleeves = harnessSleeves_();
    sleeves.forEach((s, i) => dress.push({ type: "cutTut", opKey: "cutHs" + (i + 1), tmplHint: "CUT_TUT_short", cat: "Резка",
      label: `Резка ${s.comp.art || s.comp.name} — ${s.cnt}×${s.len || "?"} мм`,
      tutArt: s.comp.art || s.comp.name || "", tutName: s.comp.name || s.comp.art || "", tutCount: s.cnt, tutSegs: [{ len: s.len, count: s.cnt }] }));
    const hsCount = sleeves.reduce((n, s) => n + s.cnt, 0);
    if (hsCount) {
      dress.push({ type: "insTut", opKey: "insHs", tmplHint: "INS_TUT", cat: "Монтаж", tutCount: hsCount, harness: true, label: `Надеть термоусадку на жгут — ${hsCount} шт` });
      dress.push({ type: "heatTut", opKey: "heatHs", tmplHint: "HEAT_TTK_short", cat: "Усадка", tutCount: hsCount, harness: true, label: `Усадка термоусадки на жгуте — ${hsCount} шт` });
    }
    const marks = S.mark ? markComps_() : [];
    if (marks.length) {
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
    dress.splice(sleeves.length, 0, ...wrapOps.filter((o) => o.under), ...braidOps);
    if (dress.length && allRjEndpoints_()) {
      const firstEp = ops.findIndex((o) => o.epId);
      const glue = sleeves.some((s) => /ттк|клеев/i.test(`${s.comp.name || ""} ${s.comp.art || ""}`));
      dress.forEach((o) => {
        if (o.opKey === "heatHs" && !glue) o.tmplHint = "HEAT_TYT_short";
        if (o.opKey === "insHs") o.label = `Надеть термоусадку на кабель под хвостовики RJ — ${hsCount} шт`;
        if (o.opKey === "heatHs") o.label = `Усадка термоусадки на хвостовиках RJ — ${hsCount} шт`;
      });
      const pre = dress.filter((o) => o.type === "cutTut" || o.type === "insTut" || o.type === "insWrap");
      const post = dress.filter((o) => pre.indexOf(o) < 0);
      ops.splice(firstEp >= 0 ? firstEp : ops.length, 0, ...pre);
      let lastEp = -1;
      ops.forEach((o, i) => { if (o.epId) lastEp = i; });
      ops.splice(lastEp >= 0 ? lastEp + 1 : ops.length, 0, ...post);
    } else if (dress.length) {
      const firstB = ops.findIndex((o) => o.side === "B" && o.epId);
      const tinAt = ops.findIndex((o) => o.type === "tin");
      ops.splice(firstB >= 0 ? firstB : (tinAt >= 0 ? tinAt : ops.length), 0, ...dress);
    }
    wrapOps.filter((o) => o.wrapKind === "tape" && !o.under).forEach((o) => ops.push(o));
    const prep = capOps_().concat(foldOps_());
    if (prep.length) {
      let lastCut = -1;
      ops.forEach((o, i) => { if (/^cutWire/.test(o.type)) lastCut = i; });
      ops.splice(lastCut + 1, 0, ...prep);
    }
    return ops;
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
   *  иначе автоподбор. Резка, лента/оплётка и коакс без ручного выбора пересчитываются всегда. */
  function templateIdFor(spec: OpSpec) {
    const autoRepick = (spec.opKey === "cutWire" || spec.type === "insWrap" || S.tab === "coax")
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
    const mkSide = (rec: CoaxRec | null, art: string, pinType: string, connName: string) => (art ? {
      article: art, connName: connName || "",
      type: rec ? rec.type : "", wire: rec ? rec.wire : "", mfr: rec ? rec.mfr : "", program: rec ? rec.program : "",
      ...coaxDia_(rec), l1: rec ? rec.l1 : "", l2: rec ? rec.l2 : "", l3: rec ? rec.l3 : "",
      lPlus: rec ? rec.lPlus : "", lMinus: rec ? rec.lMinus : "", pinType,
    } : null);
    const ops = getActiveOpsCoax_().map((spec) => {
      const row = opRow(spec);
      return { type: spec.type, side: spec.side || "", units: spec.units || 1, templateId: row.templateId,
        opNum: row.matched ? (row.matched.opNumber || "") : "", tPrep: row.times.tPrep, tOp: row.times.tOp, tMachine: row.times.tMachine };
    });
    const tut = coaxBomTut_(), cable = coaxBomCableComp_();
    return {
      mode: "coax",
      assemblyIndex: findKey(/индекс/i),
      assemblyName: findKey(/наименование/i),
      partQty: partQtyFromInfo_(),
      coax: {
        cableLength: parseDecimal_(S.coax.len) || "",
        tutLength: parseDecimal_(S.coax.tutLen) || 20,
        tutArt: tut?.art || "", tutName: tut?.name || "",
        cableArt: cable?.art || "", cableName: cable?.name || "",
        shieldType: S.coax.shield,
        sideA: mkSide(recA, S.coax.sideA ? S.coax.sideA.art : "", S.coax.pinA, S.coax.sideA ? S.coax.sideA.name : ""),
        sideB: mkSide(recB, S.coax.sideB ? S.coax.sideB.art : "", sideB ? S.coax.pinB : "", S.coax.sideB ? S.coax.sideB.name : ""),
      },
      ops,
    };
  }
  function applyTwistAllowance_(wires: Array<{ art: string; name: string; length: number }>, tw: { enabled: boolean; pitch: string; wireDia: string; mode: string; pairs: number[][]; wireIndices: number[] }) {
    if (!tw || !tw.enabled) return;
    const p = parseDecimal_(tw.pitch) || 0;
    if (p <= 0) return;
    const manualD = parseDecimal_(tw.wireDia) || 0;
    const idx = new Set<number>();
    if (tw.mode === "pairs") (tw.pairs || []).forEach((pr) => { idx.add(pr[0]); idx.add(pr[1]); });
    else (tw.wireIndices || []).forEach((i) => idx.add(i));
    wires.forEach((w, i) => {
      if (!idx.has(i) || !(w.length > 0)) return;
      const d = S.twist.diaManual && manualD > 0 ? manualD : (lookupWireDia_(w.art) || lookupWireDia_(w.name) || manualD);
      if (d > 0) w.length = Math.round(w.length * twistTakeupFactor_(d, p));
    });
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
    const routedDetail_ = (ep: Endpoint) => {
      const routed = new Set(routedWireIdxs_(ep));
      const entries = activeWires.map((w, pos) => ({ w, pos })).filter((x) => routed.has(x.w.idx));
      return { wires: entries.map((x) => wires[x.pos]), sources: entries.map((x) => sourceBranch_(ep, x.w.idx)), pins: entries.map((x) => (ep.pins && ep.pins[x.w.idx]) || "") };
    };
    const epSnap = (ep: Endpoint | null) => {
      const conn = ep && ep.connId ? getCompById(ep.connId) : null;
      const term = ep && ep.termId ? getCompById(ep.termId) : null;
      return {
        connArt: conn ? (conn.art || conn.name || "") : "", connName: conn ? (conn.name || "") : "",
        termArt: term ? (term.art || term.name || "") : "", termName: term ? (term.name || "") : "",
        sdrTmpl: ep ? ep.sdrTmpl : null, double: ep ? !!ep.double : false, perTerm: ep ? epPer_(ep) : 2,
        fold: ep ? !!ep.fold && isComplex_() : false, label: ep ? (ep.label || "") : "",
      };
    };
    const welded = spliceWelded_();
    const links = connections_().list;
    const ops = getActiveOps().map((spec) => {
      const row = opRow(spec);
      const op: Record<string, unknown> = {
        type: spec.type, templateId: row.templateId, opNum: row.matched ? (row.matched.opNumber || "") : "",
        tPrep: row.times.tPrep, tOp: row.times.tOp, tMachine: row.times.tMachine,
      };
      if (spec.wireIdxs) {
        const set = new Set(spec.wireIdxs);
        op.wires = activeWires.map((w, pos) => ({ w, pos })).filter((x) => set.has(x.w.idx)).map((x) => wires[x.pos]);
        if (spec.strip) op.strip = spec.strip;
      }
      if (["cutTut", "insTut", "heatTut", "endCap", "foldCore", "stripCable"].includes(spec.type)) {
        op.tutArt = spec.tutArt || ""; op.tutName = spec.tutName || ""; op.tutCount = spec.tutCount || 0;
        if (spec.harness) op.harness = true;
        if ((spec.opKey === "insSpl" || spec.opKey === "heatSpl") && welded) op.spliceWeld = true;
        if (spec.tutSegs) op.tutSegs = spec.tutSegs;
      }
      if (spec.type === "insWrap") { op.wrapArt = spec.wrapArt; op.wrapName = spec.wrapName; op.wrapLen = spec.wrapLen; op.wrapKind = spec.wrapKind; }
      if (spec.type === "insMark") op.marks = spec.marks;
      if (spec.tin) op.tin = true;
      if (spec.type === "splice" || spec.type === "insHousing") op.units = spec.units || 1;
      if (spec.type === "seal") { op.sealArt = spec.sealArt; op.sealName = spec.sealName; op.sealQty = spec.sealQty; op.sealPlaces = spec.sealPlaces; }
      if (spec.crimpEpId) { op.side = spec.side; op.ep = epSnap(epById_(spec.crimpEpId)); }
      // Обжим обеих сторон: `ep` — сторона А, `epB` — сторона В (etk_builder: терминалы обеих сторон в комплектующих).
      if (spec.crimpEpIdB) op.epB = epSnap(epById_(spec.crimpEpIdB));
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
    const twist = {
      enabled: S.twist.enabled, mode: S.twist.mode, pairs: getTwistPairs(), wireIndices: twistWireIndices_(),
      pitch: (S.twist.pitch || "").trim(), wireDia: (twistDiaValue() || "").trim(),
    };
    applyTwistAllowance_(wires, twist);
    const tin = { enabled: !sideB && S.tin.enabled, wireIndices: tinWireIndices_(), stripLen: (S.tin.stripLen || "").trim() };
    const otpA = !!(epA && epA.otp), otpB = !!(epB && epB.otp);
    const tinOn = !sideB && S.tin.enabled && tinWireIndices_().length > 0;
    const stripSides = { a: !!(termA && otpA), b: !!(termB && otpB) || tinOn };
    const tinStripLen = (S.tin.stripLen || "").trim();
    const strip = {
      a: stripSides.a, b: stripSides.b,
      lenA: stripSides.a ? ((findTerRecord_(termA?.art) || ({} as TerRec)).strip || "") : "",
      lenB: !stripSides.b ? "" : (sideB && termB && otpB ? ((findTerRecord_(termB.art) || ({} as TerRec)).strip || "") : tinStripLen),
    };
    const bigAssembly = S.endpoints.filter((e) => e.connId || e.termId).length > 2;
    return {
      assemblyIndex: findKey(/индекс/i), assemblyName: findKey(/наименование/i), partQty: partQtyFromInfo_(),
      bigAssembly, wires, twist, tin, strip, ops, ...(links.length ? { connections: links } : {}),
    };
  }
  function bomWarnings_(cfg: Record<string, unknown>) {
    const w: string[] = [];
    if (!cfg.assemblyName) w.push("Не указано наименование изделия — последний лист не переименуется.");
    if (S.lenFromCalc) w.push("Длины проводов взяты из расчёта (в наименовании сборки их нет) — сверьте с КД.");
    ((cfg.wires as Array<{ art?: string; name?: string; length: number }>) || []).forEach((x, i) => {
      if (!(x.length > 0)) w.push("Кабель " + (i + 1) + " (" + (x.art || x.name || "—") + "): не указана длина — норма резки уйдёт пустой.");
    });
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
  function validate() {
    const cfg = buildConfig();
    const ops = (cfg.ops as Array<{ templateId: string; type: string }>).filter((o) => o.templateId);
    if (!ops.length) return { cfg, error: "Выберите шаблон хотя бы для одной операции.", warnings: [] as string[] };
    const hasCut = ops.some((o) => /^cutWire/.test(o.type));
    if (hasCut) {
      if (!S.advancedLength && !sharedLength()) return { cfg, error: 'Укажите длину кабелей (поле "Длина, мм" вверху секции).', warnings: [] };
      if (S.advancedLength && (cfg.wires as Array<{ length: number }>).some((w) => !w.length)) return { cfg, error: "Укажите длину для каждого кабеля.", warnings: [] };
    }
    return { cfg: { ...cfg, ops, materialsSheet: S.materialsSheet }, error: "", warnings: checkTerminalFit_().concat(bomWarnings_(cfg), connections_().warnings) };
  }

  return {
    getCompById, bomLists, wireComps_, wireCores_, isJacketedCable_, activeWires_, routedWireIdxs_, connections_, endpointsOnSide_,
    endpointOrdinal_, epPer_, findTerRecord_, isComplex_, crimpCutSides_, crimpCutSideNote_, crimpCutEnabled_, crimpCutSideSelected_, batchWires_,
    crimpCutTin_, crimpCutMinWires_, crimpTinNoApplicator_,
    cutLengthInfo, sharedLength, getLengthAdjustment_, twistWireIndices_, tinWireIndices_, getTwistPairs, autoDetectTwistPairs,
    twistGeom_, twistDiaValue, twistNote, complexWarning, isComplexKit_, isCoaxAssembly_, coaxBomConnectors_, coaxBomCable_,
    coaxBomTut_, coaxCableDiaLookup_, matchCoaxRec_, coaxInfo, getActiveOps, templateIdFor, stickTemplates, opRow, buildConfig, validate,
    harnessSleeveComps_, rjEndsInBom_, hsDefaultOn_, markComps_, bomTut_, asmLenFromName_, calcCutLengths_, freeEnds_,
    wireQtyFromTerms_: (wires: Comp[]) => {
      if (wires.length !== 1 || cableCoresFromName_(`${wires[0].name || ""} ${wires[0].art || ""}`) > 1) return 0;
      const conns = bomLists().conns.filter((c) => !/корпус|housing/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`));
      if (!conns.length || conns.some((c) => detectSolderConn_(c.art || c.name))) return 0;
      const terms = getByTypes(["терминал", "terminal", "контакт"]).reduce((n, c) => n + qtyNum(c), 0);
      const sides = conns.reduce((n, c) => n + (qtyNum(c) || 1), 0) >= 2 ? 2 : 1;
      const n = terms / sides;
      return terms > 0 && Number.isInteger(n) ? n : 0;
    },
    detectSolderConn_, rjCableCat_, sealComps: () => comps().filter((c) => SEAL_RE_.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`)),
    capTubes: () => {
      const dia = (c: Comp) => { const m = /(\d+(?:[.,]\d+)?)\s*\/\s*\d/.exec(`${c.name || ""} ${c.art || ""}`); return m ? parseDecimal_(m[1]) : 99; };
      return comps().filter((c) => /тут|термоусад/i.test(`${c.type || ""} ${c.name || ""} ${c.art || ""}`)).sort((a, b) => dia(a) - dia(b));
    },
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
  const tubes = makeGen(D, S).capTubes();
  if (tubes.length) S = { ...S, cap: { ...S.cap, tut: tubes[0].id } };
  // seedHarnessFromBom_
  {
    const g = makeGen(D, S);
    const hs = g.harnessSleeveComps_();
    const rjEnds = g.rjEndsInBom_();
    const per = rjEnds > 1 ? rjEnds : 1;
    const rows = hs.map((c) => {
      const mm = Math.round((Number(String(c.qty).replace(",", ".")) || 0) * 1000 / per);
      return { compId: c.id, len: mm ? String(mm) : "", cnt: String(per) };
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
  S = applyCalcTin_(D, S);
  return S;
}

export function onCoaxConnChange(D: GenData, S: GenState, side: Side): GenState {
  const s = side === "A" ? S.coax.sideA : S.coax.sideB;
  let coax = { ...S.coax };
  if (s) {
    const rec = makeGen(D, S).matchCoaxRec_(s.art, "", "");
    const pinKey = side === "A" ? "pinA" : "pinB";
    if (!(rec && rec.pinType) && /r\s*\/\s*a\b|угл/i.test(`${s.art} ${s.name || ""}`)) coax = { ...coax, [pinKey]: "внутри корпуса" };
    if (rec) {
      if (rec.pinType) coax = { ...coax, [pinKey]: /внутри/i.test(rec.pinType) ? "внутри корпуса" : "пайка" };
      if (side === "A" && rec.shieldType) coax = { ...coax, shield: /пайк/i.test(rec.shieldType) ? "пайка" : "обжим" };
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
  if ("termId" in patch) selectedTemplates = clearAutoTemplates(S, ep, ["prs", "ins"]);
  return { ...S, selectedTemplates, endpoints: S.endpoints.map((e) => (e.id === id ? next : e)) };
}

export function toggleRoute(S: GenState, gen: Gen, id: string, wIdx: number): GenState {
  const ep = S.endpoints.find((e) => e.id === id);
  if (!ep) return S;
  const routed = new Set(gen.routedWireIdxs_(ep));
  if (routed.has(wIdx)) routed.delete(wIdx); else routed.add(wIdx);
  const allIdx = gen.activeWires_().map((w) => w.idx);
  const allOn = allIdx.length === routed.size && allIdx.every((i) => routed.has(i));
  return { ...S, endpoints: S.endpoints.map((e) => (e.id === id ? { ...e, wireIdxs: allOn ? [] : allIdx.filter((i) => routed.has(i)) } : e)) };
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
    applicator: f.terApplicator || "", awgFrom: f.awgFrom || "", awgTo: f.awgTo || "", mm2From: f.mm2From || "", mm2To: f.mm2To || "" };
}
export function coaxFromFields(f: Record<string, string>): CoaxRec {
  return {
    article: pick(f, "coaxArticle", "Артикул"), type: pick(f, "coaxType", "Тип/Серия", "type"), wire: pick(f, "coaxWire", "Провод", "wire"),
    mfr: pick(f, "coaxMfr", "Производитель", "mfr"), program: pick(f, "program", "Программа"),
    d1: pick(f, "d1", "D1"), d2: pick(f, "d2", "D2"), d3: pick(f, "d3", "D3"), l1: pick(f, "l1", "L1"), l2: pick(f, "l2", "L2"), l3: pick(f, "l3", "L3"),
    lPlus: pick(f, "lPlus", "L+"), lMinus: pick(f, "lMinus", "L-", "L−"), pinType: pick(f, "pinType", "Тип пина"),
    shieldType: pick(f, "shieldType", "Тип экрана"), connArticle: pick(f, "connArticle", "Артикул разъёма", "Артикул разъема"),
  };
}
/** Поля записи БД.КОАКС, которые создаёт «Добавить в БД.КОАКС» (addCoaxRecordToDb). */
export function coaxRecordFields(p: Omit<CoaxRec, "article">): Record<string, string> {
  const fields: Record<string, string> = {
    coaxArticle: p.connArticle, coaxType: p.type, coaxWire: p.wire, coaxMfr: p.mfr, program: p.program,
    d1: p.d1, d2: p.d2, d3: p.d3, l1: p.l1, l2: p.l2, l3: p.l3, lPlus: p.lPlus, lMinus: p.lMinus,
    pinType: p.pinType, shieldType: p.shieldType, connArticle: p.connArticle,
  };
  return Object.fromEntries(Object.entries(fields).filter(([, v]) => String(v || "").trim()));
}
export function opFromFields(f: Record<string, string>): OpRec {
  return { opNumber: f.opNumber || "", opName: f.opName || "", tOp: f.tOp || "", tPrep: f.tPrep || "", tMachine: f.tMachine || "" };
}
/** parseWireDia_ над записями СПР.КАБ · AWG (ключи полей = шапка листа по порядку). */
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
    if (!awg) return;
    const row: WireDiaRow = {};
    const core = parseFloat(String(r[coreKey] || "").replace(",", "."));
    if (isFinite(core) && core > 0) row.__core = core;
    marks.forEach((m) => { const v = parseFloat(String(r[m.name] || "").replace(",", ".")); if (isFinite(v) && v > 0) row[m.norm] = v; });
    byAwg[awg] = row;
    const gv = gostCol >= 0 ? parseFloat(String(r[headers[gostCol]] || "").replace(",", ".")) : NaN;
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
