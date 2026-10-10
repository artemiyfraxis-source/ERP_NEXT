import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  assemblyApi,
  harnessApi,
  kitsetsApi,
  referencesApi,
  techcardsApi,
  type AssemblyDraft,
  type GrapherImportResult,
  type KitsetBom,
  type ProjectTechcard,
  type TerminalResolution,
} from "./api";
import {
  CRIMP_BOTH_,
  FOLD_STRIP_MM_,
  JACKET_STRIP_MM_,
  WRAP_SLOTS_,
  addEndpoint,
  applyGrapherGroups,
  grapherHasCorrections,
  grapherLengthText,
  coaxDiaFromRecords,
  coaxFromFields,
  coaxRecordFields,
  emptyState,
  initState,
  makeGen,
  onCoaxConnChange,
  opFromFields,
  parseDecimal_,
  sideRu_,
  switchGenTab,
  syncCoverRow_,
  syncHsWithBraid_,
  terFromFields,
  toggleLengthMode,
  toggleRoute,
  twistPitchForZ0_,
  twistZ0_,
  updateEndpoint,
  updateWire,
  wireDiaFromRecords,
  type Comp,
  type Connection,
  type Endpoint,
  type GenData,
  type GenState,
  type OpSpec,
  type Side,
  type Tab,
  type Tmpl,
  type WrapKind,
} from "./tcGenLogic";
import { RouteEditor } from "./RouteEditor";
import { documentMinutes, parseRoute, routePayload, routeProblem, type Route, type RouteOp } from "./routeLogic";
import { ManualAssembly, ManualSummary, type KdOffer } from "./ManualAssembly";
import { AssemblyDrafts } from "./AssemblyDrafts";
import {
  MANUAL_MODE, cardCategory, cardLabel, emptyManual, fillKdCards, isEmptyManual, kdStale, kdSummary, kdVersionOf, manualConfig, manualFromConfig,
  manualProblem, proposeFromKd, selectBlock, type KdCard, type LibraryCard, type ManualState,
} from "./manualLogic";
import { CardSelect, CheckMark, Chip, Field, Seg, Toggle, UnitInput, type CardOption } from "./GenControls";
import { MarkTable, markTotal } from "./GenMarkTable";
import { GeneratorDraftNote, useGeneratorDraft } from "./useGeneratorDraft";
import { MARK_KIND_DEFAULT, markMatsFromRecords, markRowsByConnectors, markRowsFromConfig, markRowsFromHarness, markTargets, mergeMarkRows } from "./tcGenMarks";

/** library — карточки библиотеки операций целиком (код, сторона, нормы) для «Ручной сборки». */
type Refs = Pick<GenData, "templates" | "ops" | "terRecords" | "coaxRecords" | "wireDia" | "coaxCableDia" | "markMats"> & { library: LibraryCard[] };

const EMPTY_REFS: Refs = { templates: [], ops: [], terRecords: [], coaxRecords: [], wireDia: null, coaxCableDia: null, markMats: [], library: [] };
const TABS: Array<[Tab, string]> = [["inter", "Межплатная"], ["complex", "Сложная сборка"], ["coax", "КОАКС"], ["manual", "Ручная сборка"]];
const WRAP_KINDS: Array<[WrapKind, string]> = [
  ["tape", "Лента — последней операцией"], ["braid", "Оплётка — до термоусадки"], ["under", "Лента под оплётку — перед ней"],
];
const MEASURE_MODES = [
  ["inner", "Между разъёмами", "+L+"], ["outer", "От краёв разъёмов", "−L−"], ["cut", "Длина реза по КД", "без припуска"],
] as const;
const OP_TIMES = [["tPrep", "Подготовка", "Подготовка, сек"], ["tOp", "На штуку", "Операция, сек/шт"], ["tMachine", "Машинное", "Машинное, сек/шт"]] as const;

function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10, dd = n % 100;
  return d === 1 && dd !== 11 ? one : d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
}

/** Сводка соединений формы: по проводу и паре разъёмов — «провод · А1 → В1» и пары контактов по порядку. */
function linkGroups(list: Connection[]) {
  const groups: Array<{ key: string; title: string; pairs: Array<[number, number]> }> = [];
  list.forEach((item) => {
    const key = `${item.wire}::${item.from.end}::${item.to.end}`;
    let group = groups.find((g) => g.key === key);
    if (!group) groups.push(group = { key, title: `${item.wire || "Провод"} · ${item.from.end} → ${item.to.end}`, pairs: [] });
    group.pairs.push([item.from.pin, item.to.pin]);
  });
  return groups;
}

function badgeCls(t: string) {
  const lc = (t || "").toLowerCase();
  if (lc.includes("провод") || lc.includes("wire") || lc.includes("кабель")) return "badge-wire";
  if (lc.includes("терминал") || lc.includes("terminal") || lc.includes("контакт")) return "badge-term";
  if (lc.includes("соед") || lc.includes("conn") || lc.includes("разъ") || lc.includes("корпус")) return "badge-conn";
  return "badge-other";
}

/* Справочники генератора (БД.ОП, БД.ТЕР, БД.КОАКС, СПР.КАБ, шаблоны) — одни на все сборки: читаются один раз за
   сессию страницы, переключение КБ и вкладок их не перечитывает. После правки справочника — `loadRefs(true)`. */
let refsCache: Promise<Refs> | null = null;
/** Тесты: каждый тест подменяет справочники своими — кэш сбрасывается после теста (src/test/setup.ts). */
export function resetGeneratorRefs() { refsCache = null; }

function loadRefs(force = false): Promise<Refs> {
  if (!refsCache || force) refsCache = readRefs();
  return refsCache;
}

async function readRefs(): Promise<Refs> {
  const safe = <T,>(promise: Promise<T>, fallback: T) => promise.catch(() => fallback);
  const noRecords = { metadata: null, columns: [], records: [] };
  const [templates, op, ter, coax, kabAwg, kabCoax, mat] = await Promise.all([
    safe(techcardsApi.templates(), { metadata: null, items: [] }),
    safe(referencesApi.techOperations("op", "", 5000), noRecords),
    safe(referencesApi.techOperations("ter", "", 5000), noRecords),
    safe(referencesApi.techOperations("coax", "", 5000), noRecords),
    safe(referencesApi.techOperations("kab_awg", "", 5000), noRecords),
    safe(referencesApi.techOperations("kab_coax", "", 5000), noRecords),
    safe(referencesApi.techOperations("mat", "", 5000), noRecords),
  ]);
  return {
    templates: (templates?.items || []).map((item): Tmpl => ({ id: item.id, title: item.title, category: item.category })),
    library: (templates?.items || []).map((item): LibraryCard => ({
      id: item.id, code: item.code || "", side: item.side || "", title: item.title, name: item.name || "", category: item.category,
      norms: item.norms, first_step: item.first_step,
    })),
    ops: (op?.records || []).map((record) => opFromFields(record.fields)),
    terRecords: (ter?.records || []).map((record) => terFromFields(record.id, record.fields)).filter((record) => record.article),
    coaxRecords: (coax?.records || []).map((record) => coaxFromFields(record.fields)),
    wireDia: wireDiaFromRecords((kabAwg?.records || []).map((record) => record.fields)),
    coaxCableDia: coaxDiaFromRecords((kabCoax?.records || []).map((record) => record.fields)),
    markMats: markMatsFromRecords((mat?.records || []).map((record) => record.fields)),
  };
}

/**
 * Генератор техкарт — порт формы SUPPLYMAP TcGenInline.html. Собирает тот же config, что уходил в
 * portalTechmapGenerate, и отдаёт его в `onBuild` (→ POST /api/techcards/projects/{id}/build).
 * Данные — `portalTcGenData` → `tcLoadBomFromKit`: состав сборки из набора проекта и строки листа КБn расчёта
 * (`kit`, GET /api/techcards/kitsets/{project}/bom/{index}); нет набора — ошибка вместо формы (`kitError`).
 */
export function TcGenerator({ card, kit, kitError, busy, onBuild, onDiscard, onConfig, more, builtId: builtProp, onBuiltChange, userId }: {
  card: ProjectTechcard;
  kit?: KitsetBom | null;
  kitError?: string;
  busy: boolean;
  /** draftId — «Ручная сборка» открыта из черновика с чертежа: сборка завершает его. */
  onBuild: (config: Record<string, unknown>, draftId?: string) => Promise<boolean | void> | boolean | void;
  onDiscard: () => Promise<void> | void;
  /** Экспертное (JSON-конфигурация) — под «Ещё», рядом со сводом материалов. */
  more?: ReactNode;
  onConfig?: (config: Record<string, unknown>) => void;
  /** Собранный по черновику, но ещё без урока: живёт у владельца вкладок, чтобы переживать переход на «ТК КБn». */
  builtId?: string;
  onBuiltChange?: (draftId: string) => void;
  /** Пользователь: рабочее состояние формы сохраняется черновиком за ним, проектом и КБ (useGeneratorDraft). */
  userId?: string;
}) {
  const [refs, setRefs] = useState<Refs | null>(null);
  const [S, setS] = useState<GenState>(emptyState);
  const [status, setStatus] = useState<{ text: string; error: boolean }>({ text: "", error: false });
  const [grapher, setGrapher] = useState<{ data: GrapherImportResult; pick: number } | null>(null);
  const [terEdits, setTerEdits] = useState<Record<string, Record<string, string>>>({});
  const [terNorms, setTerNorms] = useState<Record<string, TerminalResolution | null>>({});
  const [coaxAdd, setCoaxAdd] = useState<Record<string, Record<string, string>>>({});

  useEffect(() => { void loadRefs().then(setRefs); }, []);

  // Карточки операций для выбора: русское название (с стороной), код — вторым планом; по категориям, «Прочее» — в конце.
  const cards = useMemo(() => {
    const byId = new Map<string, CardOption>();
    const groups = new Map<string, CardOption[]>();
    for (const card of refs?.library || []) {
      const option = { id: card.id, label: cardLabel(card), code: card.code || "" };
      byId.set(card.id, option);
      const category = cardCategory(card);
      groups.set(category, [...(groups.get(category) || []), option]);
    }
    const sorted = [...groups.entries()].sort(([a], [b]) => (a === "Прочее" ? 1 : b === "Прочее" ? -1 : a.localeCompare(b, "ru")));
    return { byId, groups: sorted };
  }, [refs]);

  const components = useMemo<Comp[]>(() => (kit?.components || []).map((item) => ({
    id: item.id, type: item.type || "", name: item.name || "", art: item.art || "", kd: item.kd || "", qty: item.qty,
  })), [kit]);
  // tcLoadBomFromKit: «Кол-во» — тираж сборки, партию генератор берёт отсюда.
  const assemblyInfo = useMemo<Record<string, string>>(() => ({
    "Индекс": kit?.index || "", "Наименование": kit?.name || "", "Кол-во": kit?.run || "",
  }), [kit]);
  const D: GenData = useMemo(() => ({
    ...(refs || EMPTY_REFS), components, assemblyInfo, partQty: Number(kit?.part_qty) || 1, calcOps: kit?.calc_ops || [],
  }), [refs, components, assemblyInfo, kit]);
  const gen = useMemo(() => makeGen(D, S), [D, S]);

  // «Ручная сборка»: свой список операций живёт отдельно от формы кабелей — переключение вкладок его не теряет.
  // Режим запоминается за КБ: карта, собранная вручную, открывается на этой вкладке со своим списком и схемой.
  const [manual, setManual] = useState<ManualState>(emptyManual);
  const savedManual = (card.document?.build_config as { mode?: unknown } | undefined)?.mode === MANUAL_MODE;
  // Черновик формы (владелец 10.10): переключение вкладок карточки и КБ размонтирует генератор — введённое не теряется.
  const draftValue = useMemo(() => ({ S, manual, terEdits, coaxAdd }), [S, manual, terEdits, coaxAdd]);
  const draft = useGeneratorDraft({ user: userId, project: card.project, index: card.item_index, document: card.document, value: draftValue,
    apply: (value) => { setS(value.S); setManual(value.manual); setTerEdits(value.terEdits); setCoaxAdd(value.coaxAdd); } });

  // «Черновики с чертежей» по позиции: список читается один раз за карту и после загрузки/сборки; открытый черновик
  // (draftId) уходит в сборку, собранный (builtId) ждёт «Что поправили и почему».
  const [drafts, setDrafts] = useState<{ items: AssemblyDraft[]; error: string }>({ items: [], error: "" });
  const [draftId, setDraftId] = useState("");
  const errorBanner = useRef<HTMLDivElement>(null);
  useEffect(() => { if (status.error && status.text) errorBanner.current?.scrollIntoView?.({ block: "nearest" }); }, [status]);
  const [builtOwn, setBuiltOwn] = useState("");
  const builtId = builtProp ?? builtOwn;
  const setBuiltId = (id: string) => { setBuiltOwn(id); onBuiltChange?.(id); };
  const reloadDrafts = (live: () => boolean = () => true) =>
    assemblyApi.drafts(card.project, card.item_index)
      .then((result) => { if (live()) setDrafts({ items: result?.items ?? [], error: "" }); })
      .catch((reason) => { if (live()) setDrafts({ items: [], error: reason instanceof Error ? reason.message : "Черновики не прочитаны" }); });
  useEffect(() => {
    let alive = true;
    setDraftId(""); setDrafts({ items: [], error: "" });
    void reloadDrafts(() => alive);
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.project, card.item_index]);

  // «Предложить из КД»: чертёж жгута позиции (есть ли он и его версия) читается один раз за карту при открытии «Ручной
  // сборки»; предложение — схема из КД (`derived`) с версией, сводка считается по текущей схеме мастера.
  const harnessKey = `${card.project}::${card.item_index}`;
  const [drawing, setDrawing] = useState<{ key: string; version: string | null; error: string } | null>(null);
  const [kdBusy, setKdBusy] = useState(false);
  const [kdProposal, setKdProposal] = useState<{ derived: ManualState | null; version: string; warnings: string[]; taken?: number } | null>(null);
  useEffect(() => { setKdProposal(null); }, [card.id]);

  // init(): каждый монтаж засевает форму из состава набора и строк расчёта (autoFillAllWires, applyCalcTin_).
  // Layout-эффект: вкладки появляются в том же коммите, что и справочники; обычный эффект стартует позже, и клик по
  // вкладке в этом окне затирался бы засевом (под нагрузкой тесты «Ручной сборки» теряли клик).
  useLayoutEffect(() => {
    if (!refs || !kit) return;
    const restored = manualFromConfig(card.document?.build_config);
    // Метки по КД собранной карты возвращаются в форму: следующая сборка их не теряет.
    const markRows = markRowsFromConfig(card.document?.build_config);
    const seeded = markRows.length ? { ...initState(D), mark: true, markRows } : initState(D);
    // Несохранённый черновик поверх текущей сборки возвращается вместо засева.
    const form = draft.open({ S: restored ? switchGenTab(seeded, "manual") : seeded, manual: restored ?? emptyManual(), terEdits: {}, coaxAdd: {} });
    setManual(form.manual); setS(form.S); setTerEdits(form.terEdits); setCoaxAdd(form.coaxAdd);
    setStatus({ text: "", error: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refs, card.id, kit]);

  // renderOpsCard: шаблон, показанный первым, запоминается (selectedTemplates) и дальше не пересчитывается.
  useEffect(() => {
    const next = gen.stickTemplates();
    if (next) setS((current) => ({ ...current, selectedTemplates: next }));
  }, [gen]);

  const ops = useMemo(() => gen.getActiveOps(), [gen]);
  const config = useMemo(() => gen.buildConfig(), [gen]);
  const manualTab = S.tab === "manual";
  const partQty = Number((config as Record<string, unknown>).partQty) || 1;
  const manualCfg = useMemo(() => ({ ...manualConfig(manual, config), materialsSheet: S.materialsSheet }), [manual, config, S.materialsSheet]);

  useEffect(() => {
    if (!manualTab || drawing?.key === harnessKey) return;
    let alive = true;
    harnessApi.drawing(card.project, card.item_index)
      .then((found) => { if (alive) setDrawing({ key: harnessKey, version: kdVersionOf(found), error: "" }); })
      .catch((reason) => { if (alive) setDrawing({ key: harnessKey, version: null, error: reason instanceof Error ? reason.message : "КД не прочитана" }); });
    return () => { alive = false; };
  }, [manualTab, harnessKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const harnessNow = drawing?.key === harnessKey ? drawing : null;

  // Карточка операции из КД — тем же подбором, что у генератора (подсказка шаблона / категория, шаг терминала по БД.ТЕР,
  // нормы из БД.ОП); состояние формы кабелей не участвует, чтобы выбор на типовых вкладках не подмешивался.
  const kdGen = useMemo(() => makeGen(D, emptyState()), [D]);
  const pickKdCard = (spec: OpSpec): KdCard | null => {
    const row = kdGen.opRow(spec);
    if (!row.templateId) return null;
    const code = (refs?.library || []).find((item) => item.id === row.templateId)?.code || row.matched?.opNumber || "";
    return { templateId: row.templateId, code, prepSec: row.times.tPrep, opSec: row.times.tOp };
  };

  async function proposeKd() {
    setKdBusy(true); setKdProposal(null);
    try {
      const version = kdVersionOf(await harnessApi.drawing(card.project, card.item_index));
      setDrawing({ key: harnessKey, version, error: "" });
      if (!version) { setStatus({ text: "В КД нет чертежа жгута.", error: true }); return; }
      const result = await harnessApi.derive(card.project, card.item_index);
      const read = manualFromConfig(result.build_config);
      if (!read) throw new Error("Схема из КД не прочитана");
      const derived = fillKdCards(read, pickKdCard);
      setStatus({ text: "", error: false });
      if (isEmptyManual(manual)) {
        setManual(proposeFromKd(manual, derived, version)); setRouteError("");
        setKdProposal({ derived: null, version, warnings: result.warnings ?? [], taken: derived.ops.length });
      } else {
        setKdProposal({ derived, version, warnings: result.warnings ?? [] });
      }
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Схема из КД не предложена", error: true });
    } finally {
      setKdBusy(false);
    }
  }

  const kdOffer: KdOffer = {
    available: !!harnessNow?.version,
    hint: !harnessNow ? "Проверяю КД…" : harnessNow.error ? `КД не прочитана: ${harnessNow.error}` : "В КД нет чертежа жгута",
    busy: kdBusy,
    stale: kdStale(manual, harnessNow?.version),
    onPropose: () => void proposeKd(),
    ...(kdProposal ? {
      proposal: kdProposal.derived
        ? {
          summary: kdSummary(manual, proposeFromKd(manual, kdProposal.derived, kdProposal.version)), warnings: kdProposal.warnings,
          onApply: () => {
            const { derived, version } = kdProposal;
            setManual((current) => proposeFromKd(current, derived!, version)); setRouteError(""); setKdProposal(null);
          },
          onCancel: () => setKdProposal(null),
        }
        : { taken: kdProposal.taken, warnings: kdProposal.warnings, onCancel: () => setKdProposal(null) },
    } : {}),
  };
  useEffect(() => { onConfig?.(manualTab ? manualCfg : config); }, [config, manualCfg, manualTab]); // eslint-disable-line react-hooks/exhaustive-deps

  // Схема полуфабрикатов (ARCH-TRACK-1). В сборку идут операции с выбранным шаблоном (validate), номер = место в этом списке.
  // Время блоков — из последней сборки ЭТК этой карты, если её операции те же; правки схемы лежат в routeEdit вместе с
  // набором операций, под который сделаны (сменились операции — правки не подходят и не уходят на сервер).
  const routeSpecs = useMemo(() => ops.filter((spec) => gen.opRow(spec).templateId), [gen, ops]);
  const docMinutes = useMemo(() => documentMinutes(card.document, routeSpecs.map((spec) => spec.type)), [card.document, routeSpecs]);
  const routeOps = useMemo<RouteOp[]>(() => routeSpecs.map((spec, index) => {
    const title = D.templates.find((tmpl) => tmpl.id === gen.opRow(spec).templateId)?.title || "";
    return {
      position: index + 1, key: spec.opKey, type: spec.type, code: title.includes("|") ? title.split("|")[0].trim() : "",
      label: spec.label || spec.cat || `Операция ${index + 1}`, minutes: docMinutes ? docMinutes.minutes[index] : null,
      number: ops.indexOf(spec) + 1,
    };
  }), [routeSpecs, gen, D.templates, docMinutes, ops]);
  const routeSig = routeOps.map((op) => op.key).join("|");
  const finalName = String((config as Record<string, unknown>).assemblyName || "");
  const [routeEdit, setRouteEdit] = useState<{ route: Route | null; sig: string } | undefined>();
  const [routeError, setRouteError] = useState("");
  useEffect(() => { setRouteEdit(undefined); setRouteError(""); }, [card.id, card.version]);
  // Схема ручной сборки — для её списка операций, типовым вкладкам она не подходит.
  const savedRoute = useMemo(() => (savedManual ? null : parseRoute((card.document?.build_config as { route?: unknown } | undefined)?.route)), [card.document, savedManual]);
  const savedFits = !!savedRoute && !!docMinutes && !routeProblem(savedRoute, routeOps, finalName);
  const routeOverride = routeEdit ? (routeEdit.sig === routeSig ? routeEdit.route : null) : (savedFits ? savedRoute : null);
  const routeStale = routeOps.length > 0 && (routeEdit ? !!routeEdit.route && routeEdit.sig !== routeSig : !!savedRoute && !savedFits);

  // Нормы терминала по сечению проводов разъёма (TcGenerator.gs:762-788) — через справочник ERP.
  const crimpEndpoints = S.tab === "coax" || manualTab ? [] : S.endpoints.filter((ep) => ep.method === "crimp" && ep.termId);
  const normKey = JSON.stringify(crimpEndpoints.map((ep) => {
    const term = gen.getCompById(ep.termId);
    const routed = new Set(gen.routedWireIdxs_(ep));
    const wires = gen.activeWires_().filter((w) => routed.has(w.idx)).map((w) => { const c = gen.getCompById(w.compId); return c ? c.art || c.name : ""; }).filter(Boolean);
    return [ep.id, term ? term.art || term.name : "", wires, !!ep.fold && S.tab === "complex"];
  }));
  const gostAwg = D.wireDia?.gostAwg;
  useEffect(() => {
    const items = JSON.parse(normKey) as Array<[string, string, string[], boolean]>;
    let alive = true;
    void Promise.all(items.map(async ([id, article, wires, folded]) => {
      if (!article || !wires.length) return [id, null] as const;
      const result = await referencesApi.resolveTerminal({ article, wires, folded, ...(gostAwg ? { gost_awg: gostAwg } : {}) }).catch(() => null);
      return [id, result] as const;
    })).then((pairs) => { if (alive) setTerNorms(Object.fromEntries(pairs)); });
    return () => { alive = false; };
  }, [normKey, gostAwg]);

  // Любая правка формы перерисовывает карточку операций (refresh → renderOpsCard): ручные времена сбрасываются
  // к БД.ОП. Правка самих времён — setTime, она карточку не перерисовывает.
  const update = (patch: Partial<GenState> | ((current: GenState) => GenState)) =>
    setS((current) => ({ ...(typeof patch === "function" ? patch(current) : { ...current, ...patch }), times: {} }));

  // «Маркировка по КД»: материал по умолчанию — бирка из состава (её строка набора и спишется).
  const [markDrawing, setMarkDrawing] = useState<{ busy: boolean; note: string }>({ busy: false, note: "" });
  const kitMarkArt = () => { const first = gen.markComps_()[0]; return (first && (first.art || first.name)) || MARK_KIND_DEFAULT; };
  const marksByConnectors = () => {
    const rows = markRowsByConnectors(gen.markConnectors_(), kitMarkArt());
    setMarkDrawing({ busy: false, note: rows.length ? "" : "В форме нет разъёмов — выберите разъёмы сторон А и В." });
    if (rows.length) update((current) => ({ ...current, markRows: mergeMarkRows(current.markRows, rows) }));
  };
  async function marksFromDrawing() {
    setMarkDrawing({ busy: true, note: "" });
    try {
      const found = await harnessApi.drawing(card.project, card.item_index);
      const rows = found?.design ? markRowsFromHarness(found.design, kitMarkArt()) : [];
      const note = !found?.design ? "У позиции нет чертежа жгута." : rows.length ? "" : "В чертеже жгута нет обозначений разъёмов и маркировки проводов.";
      setMarkDrawing({ busy: false, note });
      if (rows.length) update((current) => ({ ...current, markRows: mergeMarkRows(current.markRows, rows) }));
    } catch (reason) {
      setMarkDrawing({ busy: false, note: reason instanceof Error ? reason.message : "Чертёж жгута не прочитан" });
    }
  }
  const complex = S.tab === "complex";
  const { terms: bomTerms, conns: bomConns } = gen.bomLists();
  const wireOptions = gen.wireComps_();
  const active = gen.activeWires_();
  const compLabel = (id: string) => { const c = gen.getCompById(id); return c ? c.art || c.name : "?"; };
  const links = gen.connections_();
  const cut = gen.cutLengthInfo();
  const hasB = gen.endpointsOnSide_("B").length > 0;
  const crimpSides = gen.crimpCutSides_();
  const crimpTinTerm = gen.crimpTinNoApplicator_();
  // Одну сторону автомат обжать может, другую — нет: почему другая остаётся ручной.
  const crimpSideNote = crimpSides.length === 1 ? gen.crimpCutSideNote_(crimpSides[0] === "A" ? "B" : "A") : "";
  const note = gen.twistNote();

  function selectComps(list: Comp[], value: string, onChange: (id: string) => void, label: string, empty = "— нет —") {
    return <select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">{empty}</option>
      {list.map((c) => <option key={c.id} value={c.id}>{c.art || c.name}</option>)}
    </select>;
  }

  async function grapherFile(file: File) {
    setStatus({ text: `Читаю чертёж «${file.name}»…`, error: false });
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      const data = await techcardsApi.importGrapher(file.name, dataUrl.split(",", 2)[1] || "",
        wireOptions.map((c) => ({ id: c.id, article: c.art || "", name: c.name || "" })),
        components.map((c) => ({ id: c.id, type: c.type || "", name: c.name || "", article: c.art || "", kd: c.kd || "", qty: c.qty ?? null })));
      if (!data.harnesses.length) { setStatus({ text: "В проекте чертежа нет жгутов.", error: true }); return; }
      const index = data.harnesses.findIndex((h) => h.designation.trim().toLowerCase() === card.item_index.trim().toLowerCase());
      setGrapher({ data, pick: index >= 0 ? index : 0 });
      setStatus({ text: `Чертёж прочитан · жгутов: ${data.harnesses.length}`, error: false });
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Экспорт TECHMAP-GRAPHER не разобран", error: true });
    }
  }

  function grapherApply() {
    if (!grapher) return;
    const harness = grapher.data.harnesses[grapher.pick];
    // Не найденный в составе материал остаётся пустым кабелем — «не найдено — выбери».
    const groups = harness.groups.map((group) => ({
      compId: group.component_id && components.some((c) => c.id === group.component_id) ? group.component_id : "",
      qty: group.quantity, length: Number(group.length_mm) || 0, cores: group.cores,
      jacket: group.jacket_mm == null ? "" : String(group.jacket_mm),
      cut: group.cut_mm == null ? null : Number(group.cut_mm),
      correctionFrom: Number(group.correction_from_mm) || 0, correctionTo: Number(group.correction_to_mm) || 0,
    }));
    update((current) => applyGrapherGroups(current, groups));
    setStatus({ text: `Кабели из ${harness.designation || "чертежа"} подставлены в «Кабели»${grapherHasCorrections(groups) ? " — по длине реза с поправками чертежа" : ""}`, error: false });
  }

  async function saveTer(ep: Endpoint) {
    const term = gen.getCompById(ep.termId);
    if (!term) return;
    const rec = gen.findTerRecord_(term.art);
    const edits = terEdits[ep.id] || {};
    const keys: Array<[string, string]> = [["lPlus", "terLPlus"], ["lMinus", "terLMinus"], ["step", "terStep"], ["applicator", "terApplicator"]];
    try {
      if (rec?.id) {
        const changes = keys.filter(([key]) => key in edits && edits[key] !== (rec as unknown as Record<string, string>)[key])
          .map(([key, field]) => ({ field, old: rec.fields?.[field] || "", value: edits[key].trim() }));
        if (changes.length) await referencesApi.editRecord(rec.id, changes);
      } else {
        if (!window.confirm(`Артикул «${term.art}» не найден в БД.ТЕР.\nДобавить новую запись?`)) return;
        await referencesApi.createRecord("ter", { terArticle: term.art, ...Object.fromEntries(keys.map(([key, field]) => [field, (edits[key] || "").trim()])) });
      }
      setTerEdits((current) => { const next = { ...current }; delete next[ep.id]; return next; });
      setRefs(await loadRefs(true));
      setStatus({ text: rec?.id ? "✓ Готово" : "✓ Добавлено в БД", error: false });
    } catch (reason) {
      setStatus({ text: "⚠ " + (reason instanceof Error ? reason.message : "Ошибка"), error: true });
    }
  }

  async function saveCoaxToDb(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    if (!s || !s.art) return;
    const v = (key: string) => (coaxAdd[side]?.[key] || "").trim();
    const payload = {
      connArticle: s.art, type: v("type"), wire: v("wire") || gen.coaxBomCable_(), mfr: "", program: "",
      // D1–D3 не пишем: диаметры у кабеля (СПР.КАБ), генератор берёт их оттуда.
      d1: "", d2: "", d3: "",
      l1: v("l1"), l2: v("l2"), l3: v("l3"), lPlus: v("lPlus"), lMinus: v("lMinus"),
      pinType: side === "A" ? S.coax.pinA : S.coax.pinB, shieldType: S.coax.shield,
    };
    try {
      await referencesApi.createRecord("coax", coaxRecordFields(payload));
      const fresh = await loadRefs(true);
      setRefs(fresh);
      setS((current) => onCoaxConnChange({ ...D, ...fresh }, current, side));
      setStatus({ text: `«${s.art}» добавлен в БД.КОАКС`, error: false });
    } catch (reason) {
      window.alert("Ошибка сохранения: " + (reason instanceof Error ? reason.message : String(reason)));
    }
  }

  /** Ручная сборка: схема и операции в её блоках — всегда ручная схема (`build_config.route`), производство идёт по ней. */
  function manualPayload(): Record<string, unknown> | null {
    const problem = manualProblem(manual, finalName);
    if (problem) { setStatus({ text: problem, error: true }); return null; }
    setRouteError("");
    return manualCfg;
  }

  function typicalPayload(): Record<string, unknown> | null {
    const { cfg, error, warnings } = gen.validate();
    if (error) { setStatus({ text: error, error: true }); return null; }
    // Ручная схема уходит как build_config.route; без неё сервер выводит схему сам.
    const problem = routeOverride ? routeProblem(routeOverride, routeOps, finalName) : null;
    if (routeOverride && problem) { setRouteError(`Схема полуфабрикатов: ${problem}`); return null; }
    setRouteError("");
    if (warnings.length && !window.confirm("⚠ Предупреждения:\n\n" + warnings.join("\n") + "\n\nВсё равно создать техкарты?")) {
      setStatus({ text: "Генерация отменена — проверьте предупреждения.", error: true });
      return null;
    }
    const tab = TABS.find(([key]) => key === S.tab)?.[1] || "";
    if (savedManual && !window.confirm(`Техкарта ${card.item_index} собрана ручной сборкой.\n\nСобрать по вкладке «${tab}»? Ручной список операций в ЭТК не сохранится.`)) {
      setStatus({ text: "Сборка отменена — техкарта осталась ручной сборкой.", error: true });
      return null;
    }
    return routeOverride ? { ...cfg, route: routePayload(routeOverride) } : cfg;
  }

  /** «Поправить вручную»: текущая типовая сборка уходит на сервер как есть и возвращается блоками с операциями. */
  async function editByHand() {
    const { cfg, error } = gen.validate();
    if (error) { setStatus({ text: error, error: true }); return; }
    // Русское название операции — как её назвал генератор (в конфиге его нет); порядок тот же, что у операций с шаблоном.
    const ops = (cfg.ops as Array<Record<string, unknown>>).map((op, index) => (routeSpecs[index]?.label ? { ...op, label: routeSpecs[index].label } : op));
    setStatus({ text: "Открываю в ручной сборке…", error: false });
    try {
      const restored = manualFromConfig(await assemblyApi.fromGenerator({ ...cfg, ops }));
      if (!restored) throw new Error("Сборку не удалось открыть в ручной сборке");
      setManual(restored); setDraftId(""); setBuiltId(""); setRouteError("");
      update((current) => switchGenTab(current, "manual"));
      setStatus({ text: "", error: false });
    } catch (reason) {
      setStatus({ text: reason instanceof Error ? reason.message : "Сборку не удалось открыть в ручной сборке", error: true });
    }
  }

  function openDraft(draft: AssemblyDraft) {
    const restored = manualFromConfig(draft.draft_config);
    if (!restored) { setStatus({ text: "Черновик не открыт: в нём нет ручной сборки.", error: true }); return; }
    setManual(restored); setDraftId(draft.id); setBuiltId(""); setRouteError("");
    update((current) => switchGenTab(current, "manual"));
    setStatus({ text: "", error: false });
  }

  async function generate() {
    const payload = manualTab ? manualPayload() : typicalPayload();
    if (!payload) return;
    const fromDraft = manualTab ? draftId : "";
    if (card.status === "Выдана" && !window.confirm(`Техкарта ${card.item_index} уже выдана.\n\nПересобрать? ЭТК пересоберётся новой ревизией.`)) {
      setStatus({ text: "Пересборка отменена.", error: true });
      return;
    }
    setStatus({ text: card.status === "Выдана" ? "Пересобираю техкарты…" : "Создаю техкарты…", error: false });
    try {
      const built = await onBuild(payload, fromDraft || undefined);
      setStatus({ text: "", error: false });
      if (built !== false) draft.clear();
      if (fromDraft && built !== false) { setDraftId(""); setBuiltId(fromDraft); void reloadDrafts(); }
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "ЭТК не собрана";
      if (text.startsWith("Схема полуфабрикатов")) { setRouteError(text); setStatus({ text: "", error: false }); }
      else setStatus({ text, error: true });
    }
  }

  async function deleteGenerated() {
    if (!card.document) { setStatus({ text: "Техкарта ещё не собиралась.", error: true }); return; }
    if (card.status === "Выдана") { setStatus({ text: "Выданную ЭТК удалить нельзя — пересоберите её новой ревизией.", error: true }); return; }
    if (!window.confirm("Удалить собранный черновик ЭТК?")) return;
    await onDiscard();
  }

  function twistPitchInput(value: string) {
    const g = gen.twistGeom_();
    const p = parseDecimal_(value);
    const z = g && p > 0 ? twistZ0_(g.d, g.D, p, g.eps) : 0;
    update((current) => ({ ...current, twist: { ...current.twist, pitch: value, z0: g && p > 0 ? (z > 0 ? String(Math.round(z)) : "") : current.twist.z0 } }));
  }
  function twistZ0Input(value: string) {
    const g = gen.twistGeom_();
    const z0 = parseDecimal_(value);
    const res = g && z0 > 0 ? twistPitchForZ0_(g.d, g.D, z0, g.eps) : null;
    update((current) => ({ ...current, twist: { ...current.twist, z0: value, pitch: res ? String(res.p).replace(".", ",") : current.twist.pitch } }));
  }

  /** Разъём стороны: шапка «А1 · артикул · обжим», поля плотной сеткой, нормы терминала, подбор по сечению, параметры
      чипами, распиновка — свёрнута. */
  function endpointCard(ep: Endpoint) {
    const ord = gen.endpointOrdinal_(ep);
    const name = sideRu_(ep.side) + ord;
    const crimp = ep.method === "crimp";
    const routedList = gen.routedWireIdxs_(ep);
    const routed = new Set(routedList);
    const conn = gen.getCompById(ep.connId);
    const term = gen.getCompById(ep.termId);
    const ter = term ? gen.findTerRecord_(term.art) : null;
    const set = (patch: Partial<Endpoint>) => update((current) => updateEndpoint(D, current, ep.id, patch));
    const single = gen.endpointsOnSide_(ep.side).length < 2;
    const showRouting = complex && active.length > 0 && !(single && !S.endpoints.some((e) => e.side !== ep.side));
    const chips = active.map((w, pos) => <Chip key={w.idx} checked={routed.has(w.idx)} onChange={() => update((current) => toggleRoute(current, makeGen(D, current), ep.id, w.idx))}>
      {`Кабель ${pos + 1} · ${compLabel(w.compId)}`}</Chip>);
    const terValues = { lPlus: ter?.lPlus || "", lMinus: ter?.lMinus || "", step: ter?.step || "", applicator: ter?.applicator || "", ...(terEdits[ep.id] || {}) };
    const terDirty = !!terEdits[ep.id] && Object.entries(terEdits[ep.id]).some(([key, value]) => value !== ((ter as unknown as Record<string, string>)?.[key] || ""));
    const setTer = (key: string, value: string) => setTerEdits((current) => ({ ...current, [ep.id]: { ...(current[ep.id] || {}), [key]: value } }));
    const norm = terNorms[ep.id];
    // «Применить» подставляет аппликатор подбора в поле БД.ТЕР, когда подбор однозначен и отличается от записанного.
    const picks = norm ? norm.by_section.filter((item) => item.found && item.applicator) : [];
    const pickApplicator = picks.length && picks.every((item) => item.applicator === picks[0].applicator) ? picks[0].applicator : "";
    const allRouted = active.every((w) => routed.has(w.idx));
    const pinned = active.some((w) => routed.has(w.idx) && ep.pins?.[w.idx]);
    return <div className="ep-card" key={ep.id}>
      <div className="ep-head">
        <b className="ep-badge">{name}</b>
        <span className="ep-title">{conn ? conn.art || conn.name : "разъём не выбран"}</span>
        <span className="ep-kind">{crimp ? "обжим" : "пайка"}{ep.methodAuto ? " · авто" : ""}</span>
        <button type="button" className="btn-remove" title="Удалить разъём" aria-label={`Удалить разъём ${name}`}
          onClick={() => update((current) => ({ ...current, endpoints: current.endpoints.filter((e) => e.id !== ep.id) }))}>×</button>
      </div>
      <div className="ep-grid">
        <Field label="Разъём / корпус" className="lead">{selectComps(bomConns, ep.connId, (id) => set({ connId: id }), `Разъём ${name}`)}</Field>
        <div className="gfield"><span className="gfield-label">Монтаж</span>
          <Seg label={`Монтаж ${name}`} value={crimp ? "crimp" : "solder"} options={[["crimp", "Обжим"], ["solder", "Пайка"]]} onChange={(method) => set({ method })} /></div>
        {crimp && <Field label="Терминал"
          hint={term ? <span className={ter ? undefined : "tone-danger"}>{routedList.length} шт{ter ? "" : " · нет в БД.ТЕР"}</span> : undefined}>
          {selectComps(bomTerms, ep.termId, (id) => set({ termId: id }), `Терминал ${name}`)}</Field>}
        {complex && <Field label="Наименование по чертежу" className="lead"><input value={ep.label} placeholder="напр. GIMBAL, NODE, PWR…" onChange={(event) => set({ label: event.target.value })} /></Field>}
      </div>
      {crimp && term && <div className="ep-ter" aria-label={`Нормы терминала ${name}`}>
        {([["lPlus", "L+", "мм"], ["lMinus", "L−", "мм"], ["step", "Шаг", "мм"]] as const).map(([key, label, unit]) =>
          <Field key={key} label={label}><UnitInput unit={unit} value={terValues[key]} placeholder="—" onChange={(event) => setTer(key, event.target.value)} /></Field>)}
        <Field label="Аппликатор" className="wide"><input value={terValues.applicator} placeholder="—" onChange={(event) => setTer("applicator", event.target.value)} /></Field>
        {terDirty && <button type="button" className="secondary small" onClick={() => void saveTer(ep)}>Записать в БД</button>}
      </div>}
      {crimp && norm && norm.by_section.length > 0 && <div className="ep-norm">
        <span>Подбор по сечению: {norm.by_section.map((item) => `${item.section || "сечение?"} → ${item.found ? `${item.applicator || "аппл. —"}, h=${item.crimp_height || "—"}${item.pull_force ? `, F=${item.pull_force}` : ""}${item.range_only ? " (по вилке)" : ""}` : "нет нормы"}`).join("; ")}</span>
        {pickApplicator && pickApplicator !== terValues.applicator && <button type="button" className="btn-link" title={`Аппликатор ${pickApplicator} — в поле «Аппликатор»`} onClick={() => setTer("applicator", pickApplicator)}>Применить</button>}
      </div>}
      {crimp ? <div className="chip-row ep-opts">
        <Chip checked={ep.otp} onChange={(on) => set({ otp: on })}>Без зачистки (OTP)<small>провод зачищен заранее</small></Chip>
        {complex ? <>
          <Chip checked={ep.double} onChange={(on) => set({ double: on })}>Групповой обжим<small>норма терминалов — по группам</small></Chip>
          {ep.double && <label className="ep-per">по<input type="number" min={2} max={6} aria-label={`Проводов в терминал ${name}`} value={gen.epPer_(ep)}
            onChange={(event) => set({ perTerm: Math.max(2, parseInt(event.target.value, 10) || 2) })} />провода в терминал</label>}
          <Chip checked={!!ep.fold} onChange={(on) => set({ fold: on })}>Складывание жилы<small>зачистка {FOLD_STRIP_MM_} мм, сложить вдвое</small></Chip>
        </> : <Chip checked={ep.double} onChange={(on) => set({ double: on })}>Двойной обжим<small>2 провода в терминал</small></Chip>}
      </div> : <>
        <div className={ep.sdrTmpl ? "ep-sdr" : "ep-sdr tone-warn"}>{ep.sdrTmpl
          ? <>Паяльный разъём · карточка <span className="code">{ep.sdrTmpl}</span></>
          : "Тип не распознан — карточку пайки выберите в операциях"}</div>
        <div className="ep-ter">
          <Field label="Зачистка под пайку" className="mid"><UnitInput unit="мм" value={ep.solderStrip} placeholder="5" onChange={(event) => set({ solderStrip: event.target.value })} /></Field>
          {!ep.tutOff && <Field label="Длина ТУТ" className="mid"><UnitInput unit="мм" value={ep.tutLen} placeholder="20" onChange={(event) => set({ tutLen: event.target.value })} /></Field>}
          <Chip checked={!ep.tutOff} onChange={(on) => set({ tutOff: !on })}>ТУТ на каждый провод</Chip>
        </div>
      </>}
      {showRouting && (single
        ? <details className="ep-more" open={!allRouted}><summary>Провода в этот разъём — {allRouted ? "все" : "не все: остальные — свободные концы"}</summary><div className="chip-row">{chips}</div></details>
        : <div className="gfield"><span className="gfield-label">Провода в этот разъём</span><div className="chip-row">{chips}</div></div>)}
      {active.length > 0 && <details className="ep-more" open={pinned}>
        <summary>Распиновка — контакты разъёма{pinned ? " ✓" : ""}</summary>
        <div className="ep-pins">{active.filter((w) => routed.has(w.idx)).map((w) => <label className="ep-pin" key={w.idx}>
          <span>{compLabel(w.compId)}{w.qty > 1 ? ` ×${w.qty}` : ""}</span>
          <input value={ep.pins?.[w.idx] || ""} placeholder={w.qty > 1 ? `1, 3 или 1-${w.qty}` : "конт."} onChange={(event) => {
            const value = event.target.value.trim();
            const pins = { ...(ep.pins || {}) };
            if (value) pins[w.idx] = value; else delete pins[w.idx];
            set({ pins });
          }} />
        </label>)}</div>
        {ep.side === "B" && S.endpoints.some((e) => e.side === "A") && <small className="muted">Контакты стыкуются по порядку: k-й здесь ↔ k-й на другой стороне. Перекрёст: А «1-6», В «3, 1, 2, 4, 5, 6» → 1→3, 2→1, 3→2…</small>}
      </details>}
    </div>;
  }

  function coaxSideBlock(side: Side) {
    const s = side === "A" ? S.coax.sideA : S.coax.sideB;
    const info = gen.coaxInfo(side);
    const pinKey = side === "A" ? "pinA" : "pinB";
    const coverKey = side === "A" ? "coverA" : "coverB";
    const add = coaxAdd[side] || {};
    const addValue = (key: string, fallback = "") => add[key] ?? fallback;
    const setAdd = (key: string, value: string) => setCoaxAdd((current) => ({ ...current, [side]: { ...(current[side] || {}), [key]: value } }));
    const pin = S.coax[pinKey] === "внутри корпуса" ? "внутри корпуса" : "пайка";
    return <div className="ep-card">
      <div className="ep-head"><span className="ep-title">{s ? s.art : side === "A" ? "в составе нет разъёма" : "—"}</span><span className="ep-kind">из состава</span></div>
      <div className="ep-grid coax">
        <div className="gfield"><span className="gfield-label">Тип пина</span>
          <Seg label={`Тип пина ${sideRu_(side)}`} value={pin} options={[["пайка", "Пайка"], ["внутри корпуса", "Пайка внутри корпуса"]]}
            onChange={(value) => update((current) => syncCoverRow_({ ...current, coax: { ...current.coax, [pinKey]: value } }, side))} /></div>
        {S.coax[pinKey] === "внутри корпуса" && <div className="gfield"><span className="gfield-label">Крышка (угловой разъём)</span>
          <Seg label={`Крышка ${sideRu_(side)}`} value={S.coax[coverKey]} options={[["", "Без крышки"], ["обжим", "Обжим"], ["винт", "Винт"]]}
            onChange={(value) => update((current) => ({ ...current, coax: { ...current.coax, [coverKey]: value } }))} /></div>}
      </div>
      <div className={info.missing ? "coax-info tone-danger" : "coax-info"}>{info.text}</div>
      {info.missing && <div className="coax-add">
        <span className="gfield-label">Нет в БД.КОАКС — введите размеры и добавьте</span>
        <div className="opt-grid">
          <Field label="Тип / серия" className="span2"><input value={addValue("type")} placeholder="напр. SMA-male R/A" onChange={(event) => setAdd("type", event.target.value)} /></Field>
          <Field label="Провод" className="span2"><input value={addValue("wire", gen.coaxBomCable_())} onChange={(event) => setAdd("wire", event.target.value)} /></Field>
          {/* D1–D3 — у кабеля (СПР.КАБ), у разъёма только длины разделки. */}
          {(["l1", "l2", "l3", "lPlus", "lMinus"] as const).map((key) => <Field key={key} label={{ l1: "L1", l2: "L2", l3: "L3", lPlus: "L+", lMinus: "L−" }[key]}>
            <UnitInput unit="мм" aria-label={`${key} ${side}`} placeholder="—" value={addValue(key)} onChange={(event) => setAdd(key, event.target.value)} /></Field>)}
        </div>
        <button className="secondary small" disabled={busy} onClick={() => void saveCoaxToDb(side)}>Добавить в БД.КОАКС</button>
      </div>}
    </div>;
  }

  /** Операция генератора строкой таблицы: название (код карточки — серым), карточка, времена. Та же строка стоит в
      общем списке и в открытом блоке схемы. */
  function opItem(spec: OpSpec, number: number) {
    const row = gen.opRow(spec);
    const code = cards.byId.get(row.templateId)?.code || "";
    const setTime = (key: "tPrep" | "tOp" | "tMachine", value: string) => setS((current) => ({ ...current, times: { ...current.times, [spec.opKey]: { ...row.times, [key]: value } } }));
    return <div className={row.templateId ? "op-row" : "op-row is-off"} key={spec.opKey}>
      <span className="op-num">{number}</span>
      <div className="op-name"><span className="op-title">{spec.label}</span>{code && <small className="op-code">{code}</small>}</div>
      <div className="op-card">
        <CardSelect label={`Карточка: ${spec.label}`} value={row.templateId} groups={cards.groups} byId={cards.byId} warn={!row.templateId}
          onChange={(id) => update((current) => ({ ...current, selectedTemplates: { ...current.selectedTemplates, [spec.opKey]: id }, manualTemplates: current.manualTemplates.includes(spec.opKey) ? current.manualTemplates : [...current.manualTemplates, spec.opKey] }))} />
        {row.matched ? <CheckMark title={`Нормы из БД.ОП: ${row.matched.opName}${row.matched.opNumber ? ` · ${row.matched.opNumber}` : ""}`} />
          : <small className="op-nodb">{row.templateId ? "нет в БД.ОП — время вручную" : "без карточки не войдёт в ТК"}</small>}
      </div>
      {OP_TIMES.map(([key, , aria]) => <UnitInput key={key} unit="сек" type="number" min={0} placeholder="0" aria-label={`${aria}: ${spec.label}`}
        value={row.times[key]} onChange={(event) => setTime(key, event.target.value)} />)}
    </div>;
  }

  const moreOnly = more ? <details className="gen-more"><summary>Ещё</summary>{more}</details> : null;
  if (kitError) return <div className="tcgen"><div className="error-banner" role="alert">{kitError}</div>{moreOnly}</div>;
  if (!refs || !kit) return <div className="tcgen"><div className="empty">Читаю состав {card.item_index}…</div>{moreOnly}</div>;

  const wrapOpts = gen.wrapCandidates().list;
  const capTubes = gen.capTubes();
  const hsComps = S.hs.rows.map((row) => ({ row, comp: gen.getCompById(row.compId) }));
  const marks = gen.markComps_();
  const kdMarks = S.markRows.filter((row) => row.text.trim() || row.target.trim());
  const typical = S.tab !== "coax" && !manualTab;
  const epsA = gen.endpointsOnSide_("A"), epsB = gen.endpointsOnSide_("B");
  const sidesFull = !epsA.length || !epsB.length;
  const anyCores = S.wires.some((w) => gen.wireCores_(w) > 1 || !!w.cores);
  const wireCols = `wire-row${S.advancedLength ? " adv" : ""}${anyCores ? " cores" : ""}`;
  const warning = typical ? gen.complexWarning() : "";
  const opsCount = manualTab ? manual.ops.length : ops.length;
  const summary = `${opsCount} ${plural(opsCount, "операция", "операции", "операций")} · партия ${partQty} шт`;

  const sheet = <section className={manualTab ? "gsec full" : "gsec"} aria-label="Данные из листа">
    <div className="gsec-head"><h3>Данные из листа</h3>
      <span className="gsec-meta">{Object.entries(assemblyInfo).filter(([, value]) => value !== "").map(([key, value]) => <span className="kv" key={key}>{key} <b>{value}</b></span>)}</span></div>
    <div className="gtable-wrap"><table className="gtable"><thead><tr><th>Тип</th><th>Название</th><th>Артикул</th><th className="num">Кол-во</th></tr></thead><tbody>
      {components.map((c) => <tr key={c.id}><td><span className={`gbadge ${badgeCls(c.type)}`}>{c.type}</span></td><td>{c.name}</td><td className="muted">{c.art}{c.kd && <small>по КД: {c.kd}</small>}</td><td className="num">{String(c.qty)}</td></tr>)}
      {!components.length && <tr><td colSpan={4} className="gtable-empty">СПЯ не найдена</td></tr>}
    </tbody></table></div>
  </section>;

  const sideSection = (side: Side) => {
    const list = side === "A" ? epsA : epsB;
    const addButton = complex && <button type="button" className="btn-link" aria-label={`Добавить разъём на сторону ${sideRu_(side)}`} onClick={() => update((current) => addEndpoint(current, side))}>+ Разъём</button>;
    if (!list.length) return <section key={side} className="gsec full ep-side is-empty" aria-label={`Сторона ${sideRu_(side)}`}>
      <div className="gsec-head"><h3>Сторона {sideRu_(side)}</h3><span className="gsec-meta">{side === "B" && !complex ? "разъёма нет — свободные концы" : "разъёма нет"}</span>{addButton}</div>
    </section>;
    return <section key={side} className={sidesFull ? "gsec full ep-side" : "gsec ep-side"} aria-label={`Сторона ${sideRu_(side)}`}>
      <div className="gsec-head"><h3>Сторона {sideRu_(side)}</h3><span className="gsec-meta">{list.length} {plural(list.length, "разъём", "разъёма", "разъёмов")}</span>{addButton}</div>
      {list.map(endpointCard)}
    </section>;
  };

  const twistToggle = <Toggle key="twist" checked={S.twist.enabled} title="Свивка" hint="парами или все провода вместе" onChange={(on) => update((current) => {
    const auto = makeGen(D, current).autoDetectTwistPairs();
    const pairs = on && !current.twist.pairs.length ? (auto.length ? auto.map(([a, b]) => ({ a: String(a), b: String(b) })) : [{ a: "", b: "" }]) : current.twist.pairs;
    return { ...current, twist: { ...current.twist, enabled: on, pairs } };
  })}>
    <Seg label="Как свивать" value={S.twist.mode} options={[["pairs", "Парами"], ["all", "Все вместе"]]}
      onChange={(mode) => update((current) => ({ ...current, twist: { ...current.twist, mode } }))} />
    {S.twist.mode === "pairs" ? <div className="twist-pairs">
      <span className="gfield-label">Пары для свивки</span>
      {S.twist.pairs.map((pair, i) => <div className="twist-pair-row" key={i}>
        {(["a", "b"] as const).map((key) => <select key={key} aria-label={`Пара ${i + 1} ${key}`} value={pair[key]} onChange={(event) => update((current) => ({ ...current, twist: { ...current.twist, pairs: current.twist.pairs.map((p, j) => (j === i ? { ...p, [key]: event.target.value } : p)) } }))}>
          <option value="">—</option>{active.map((w, pos) => <option key={w.idx} value={pos}>{compLabel(w.compId)}</option>)}</select>)}
        <button type="button" className="btn-remove" title="Убрать пару" aria-label={`Убрать пару ${i + 1}`} onClick={() => update((current) => ({ ...current, twist: { ...current.twist, pairs: current.twist.pairs.filter((_, j) => j !== i) } }))}>×</button>
      </div>)}
      <div className="row-actions">
        <button type="button" className="btn-add" onClick={() => update((current) => ({ ...current, twist: { ...current.twist, pairs: [...current.twist.pairs, { a: "", b: "" }] } }))}>+ Добавить пару</button>
        <button type="button" className="btn-add" title="Определить пары по запасу длины из спецификации" onClick={() => update((current) => {
          const auto = makeGen(D, current).autoDetectTwistPairs();
          return { ...current, twist: { ...current.twist, pairs: auto.length ? auto.map(([a, b]) => ({ a: String(a), b: String(b) })) : [{ a: "", b: "" }] } };
        })}>Авто</button>
      </div>
    </div> : <div className="chip-row">{active.map((w, pos) => <Chip key={w.idx} checked={gen.twistWireIndices_().includes(pos)} onChange={() => update((current) => {
      const now = makeGen(D, current).twistWireIndices_();
      return { ...current, twist: { ...current.twist, wires: now.includes(pos) ? now.filter((i) => i !== pos) : [...now, pos] } };
    })}>{compLabel(w.compId)}</Chip>)}</div>}
    <div className="opt-grid">
      <Field label="Шаг свивки"><UnitInput unit="мм" aria-label="Шаг свивки" value={S.twist.pitch} placeholder="—" onChange={(event) => twistPitchInput(event.target.value)} /></Field>
      <Field label="Z₀"><UnitInput unit="Ом" aria-label="Z0" value={S.twist.z0} placeholder="по шагу" onChange={(event) => twistZ0Input(event.target.value)} /></Field>
      <Field label="Ø провода"><UnitInput unit="мм" aria-label="Ø провода" className={gen.twistDiaValue().trim() ? undefined : "is-warn"} value={gen.twistDiaValue()} placeholder="из СПР.КАБ"
        onChange={(event) => update((current) => ({ ...current, twist: { ...current.twist, dia: event.target.value, diaManual: !!event.target.value.trim() } }))} /></Field>
    </div>
    {note.text && <small className={note.explicit ? "opt-note" : "opt-note tone-danger"}>{note.text}{note.warn && <><br /><span className="tone-danger">⚠ {note.warn}</span></>}</small>}
  </Toggle>;

  // Обработка — по смыслу: концы провода, жгут целиком, автомат при резке. Пустая группа не показывается.
  const ends = [
    <Toggle key="free" checked={S.freeStrip.enabled} title="Зачистка свободных концов" hint="без стороны В, без лужения"
      onChange={(on) => update((current) => ({ ...current, freeStrip: { ...current.freeStrip, enabled: on } }))}>
      <Field label="Длина зачистки" className="short"><UnitInput unit="мм" value={S.freeStrip.len} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, freeStrip: { ...current.freeStrip, len: event.target.value } }))} /></Field>
    </Toggle>,
    !hasB && <Toggle key="tin" checked={S.tin.enabled} title="Лужение свободных концов" onChange={(on) => update((current) => ({ ...current, tin: { ...current.tin, enabled: on } }))}>
      <div className="gfield"><span className="gfield-label">Какие концы лудить<small>по умолчанию все</small></span>
        <div className="chip-row">{active.map((w, pos) => <Chip key={w.idx} checked={gen.tinWireIndices_().includes(pos)} onChange={() => update((current) => {
          const now = makeGen(D, current).tinWireIndices_();
          return { ...current, tin: { ...current.tin, wires: now.includes(pos) ? now.filter((i) => i !== pos) : [...now, pos] } };
        })}>{compLabel(w.compId)}</Chip>)}</div></div>
      <Field label="Зачистка под лужение" className="short"><UnitInput unit="мм" value={S.tin.stripLen} placeholder="—" onChange={(event) => update((current) => ({ ...current, tin: { ...current.tin, stripLen: event.target.value } }))} /></Field>
    </Toggle>,
    complex && <Toggle key="cap" checked={S.cap.enabled} title="Заделка свободных концов" hint="жилы, не заведённые в разъём своей стороны"
      onChange={(on) => update((current) => ({ ...current, cap: { ...current.cap, enabled: on } }))}>
      <div className="opt-grid">
        <Field label="Трубка из состава" className="span2">{selectComps(capTubes, S.cap.tut, (id) => update((current) => ({ ...current, cap: { ...current.cap, tut: id } })), "Трубка заделки")}</Field>
        <Field label="Отрезок на конец"><UnitInput unit="мм" value={S.cap.len} placeholder="из нормы" onChange={(event) => update((current) => ({ ...current, cap: { ...current.cap, len: event.target.value } }))} /></Field>
      </div>
    </Toggle>,
    complex && <Toggle key="seal" checked={S.seal.enabled} title="Герметизация" hint="клей-герметик на пайку и хвостовики разъёмов"
      onChange={(on) => update((current) => ({ ...current, seal: { ...current.seal, enabled: on } }))}>
      <div className="opt-grid">
        <Field label="Герметик из состава" className="span2">{selectComps(gen.sealComps(), S.seal.mat, (id) => update((current) => ({ ...current, seal: { ...current.seal, mat: id } })), "Герметик")}</Field>
        <Field label="Мест на изделие"><input value={S.seal.places} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, seal: { ...current.seal, places: event.target.value } }))} /></Field>
      </div>
    </Toggle>,
  ].filter(Boolean);
  const harness = [
    twistToggle,
    complex && <Toggle key="spl" checked={S.spl.enabled} title="Сростки" hint="соединение проводов в стволе"
      onChange={(on) => update((current) => ({ ...current, spl: { ...current.spl, enabled: on } }))}>
      <div className="opt-grid">
        <Field label="Сростков на изделие"><input type="number" min={1} value={S.spl.count} onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, count: event.target.value } }))} /></Field>
        <Field label="Проводов в сростке" hint="разные — через запятую"><input value={S.spl.wires} placeholder="3 или 4, 2, 2" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, wires: event.target.value } }))} /></Field>
        <Field label="Трубка сростка, длина"><UnitInput unit="мм" value={S.spl.tut} placeholder="40" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, tut: event.target.value } }))} /></Field>
        <Field label="Трубка сростка из состава" className="span2">{selectComps(capTubes, S.spl.tutMat, (id) => update((current) => ({ ...current, spl: { ...current.spl, tutMat: id } })), "Трубка сростка")}</Field>
        <Field label="Зачистка под сросток"><UnitInput unit="мм" value={S.spl.strip} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, spl: { ...current.spl, strip: event.target.value } }))} /></Field>
      </div>
      <div className="chip-row"><Chip checked={S.spl.fold} onChange={(on) => update((current) => ({ ...current, spl: { ...current.spl, fold: on } }))}>Складывание жилы перед гильзой<small>663К.1 КБ4: ГС 2,5, «Схема увеличения сечения»</small></Chip></div>
    </Toggle>,
    <Toggle key="hs" checked={S.hs.enabled} title="Термоусадка на жгут" hint="концы оплётки — ТТК/ТУТ из состава"
      onChange={(on) => update((current) => ({ ...current, hs: { ...current.hs, enabled: on, manual: true } }))}>
      {hsComps.length === 0 && <span className="opt-note">В составе проекта нет термоусадки (ТТК/ТУТ) — добавьте её в состав, и здесь появятся отрезки.</span>}
      {hsComps.map(({ row, comp }, i) => <div className="opt-grid" key={row.compId}>
        <Field label={`${comp ? comp.art || comp.name : row.compId} — отрезок`} className="span2"><UnitInput unit="мм" value={row.len} onChange={(event) => update((current) => ({ ...current, hs: { ...current.hs, rows: current.hs.rows.map((r, j) => (j === i ? { ...r, len: event.target.value } : r)) } }))} /></Field>
        <Field label="Отрезков на изделие"><input value={row.cnt} onChange={(event) => update((current) => ({ ...current, hs: { ...current.hs, rows: current.hs.rows.map((r, j) => (j === i ? { ...r, cnt: event.target.value } : r)) } }))} /></Field>
      </div>)}
    </Toggle>,
    <Toggle key="wrap" checked={S.wrap.enabled} title="Лента / оплётка на жгут"
      onChange={(on) => update((current) => syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, enabled: on } }))}>
      {S.wrap.slots.map((slot, i) => (i === 0 || complex) && <div className="opt-grid" key={WRAP_SLOTS_[i] || "1"}>
        <Field label={["Материал из состава", "Второй материал", "Третий материал", "Четвёртый материал"][i]} hint={i ? "необязательно" : undefined} className="span2">
          {selectComps(wrapOpts, slot.mat, (id) => update((current) => {
            const c = makeGen(D, current).getCompById(id);
            const g = makeGen(D, current);
            const slots = current.wrap.slots.map((s, j) => (j === i ? { mat: id, kind: c ? g.guessWrapKind_(c) : s.kind, len: g.prefillWrapLen_(c) } : s));
            return syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, slots } });
          }), `Материал ${i + 1}`)}</Field>
        <Field label="Вид" className="span2"><select value={slot.kind} onChange={(event) => update((current) => syncHsWithBraid_(D, { ...current, wrap: { ...current.wrap, slots: current.wrap.slots.map((s, j) => (j === i ? { ...s, kind: event.target.value as WrapKind } : s)) } }))}>
          {WRAP_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
        <Field label="Длина на изделие"><UnitInput unit="мм" value={slot.len} placeholder="по КД" onChange={(event) => update((current) => ({ ...current, wrap: { ...current.wrap, slots: current.wrap.slots.map((s, j) => (j === i ? { ...s, len: event.target.value } : s)) } }))} /></Field>
      </div>)}
    </Toggle>,
  ].filter(Boolean);
  const machine = [
    active.some(gen.isJacketedCable_) && <Toggle key="rotor" checked={S.rotor} title="Роторный станок" hint="многожильный кабель разделывается при резке — оболочка и жилы"
      onChange={(on) => update({ rotor: on })} />,
    crimpSides.length > 0 && <Toggle key="crimp" checked={gen.crimpCutEnabled_()} title={gen.crimpCutTin_() ? "Обжим и лужение на автомате при резке" : "Обжим на автомате при резке"}
      hint={`${gen.batchWires_()} ${plural(gen.batchWires_(), "провод", "провода", "проводов")} в партии${S.crimpCutManual ? "" : `, по умолчанию — при > ${gen.crimpCutMinWires_()}`}`}
      onChange={(on) => update({ crimpCut: on, crimpCutManual: true })}>
      {crimpSides.length > 1
        ? <div className="gfield"><span className="gfield-label">Сторона обжима</span>
          <Seg label="Сторона обжима при резке" value={gen.crimpCutSideSelected_()}
            options={[...crimpSides.map((side) => [side, `Сторона ${sideRu_(side)}`] as const), [CRIMP_BOTH_, "Обе стороны"] as const]}
            onChange={(side) => update({ crimpCutSide: side })} /></div>
        : <span className="opt-note">Обжим на стороне {sideRu_(crimpSides[0])}{crimpSideNote && `; ${crimpSideNote}`}</span>}
    </Toggle>,
    !crimpSides.length && crimpTinTerm && <p key="crimp-off" className="opt-note">Резка с обжимом и лужением недоступна: у {crimpTinTerm} в БД.ТЕР нет аппликатора автомата (A…).</p>,
  ].filter(Boolean);

  const typicalSections = <>
    <section className="gsec gen-cables" aria-label="Кабели">
      <div className="gsec-head"><h3>Кабели</h3>
        {complex && <button type="button" className="btn-link" onClick={() => update((current) => toggleLengthMode(current, makeGen(D, current).sharedLength()))}>{S.advancedLength ? "← общая длина" : "раздельная длина ▸"}</button>}
        <label className="btn-link">из чертежа<input aria-label="Экспорт TECHMAP-GRAPHER" type="file" hidden accept=".json,.zip,.techmap-project.zip" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void grapherFile(file); }} /></label>
      </div>
      <Seg label="Как задана длина" value={S.measureMode} options={MEASURE_MODES} onChange={(measureMode) => update({ measureMode })} />
      {!S.advancedLength && <div className="cut-row">
        <Field label="Длина по чертежу" className="short"><UnitInput unit="мм" aria-label="Длина по чертежу" type="number" min={1} placeholder="—" value={S.sharedLengthText}
          onChange={(event) => update({ sharedLengthText: event.target.value, drawingLength: parseDecimal_(event.target.value) || 0 })} /></Field>
        {cut.tone !== "none" && <div className={cut.tone === "warn" ? "cut-result warn" : "cut-result"}>
          <span>Длина реза <b>{cut.value}</b></span><small>{cut.formula}</small></div>}
      </div>}
      <div className="wire-list">
        {S.wires.length > 0 && <div className={`${wireCols} head`} aria-hidden="true"><span>Кабель</span>{S.advancedLength && <span>Длина по чертежу</span>}<span>Кол-во</span>{anyCores && <><span>Жил</span><span>Разделка</span></>}<span /></div>}
        {!S.wires.length && <div className="gempty">Нет кабелей — добавьте кабель из состава</div>}
        {S.wires.map((w, n) => {
          const cores = gen.wireCores_(w);
          const hasCores = cores > 1 || !!w.cores;
          return <div className={wireCols} key={w.idx}>
            {selectComps(wireOptions, w.compId, (id) => update((current) => updateWire(current, w.idx, { compId: id })), `Кабель ${n + 1}`, "— выберите —")}
            {S.advancedLength && <UnitInput unit="мм" aria-label={`Длина кабеля ${n + 1}`} type="number" min={1} value={w.length || ""} onChange={(event) => update((current) => updateWire(current, w.idx, { length: parseDecimal_(event.target.value) || 0 }))} />}
            <input aria-label={`Кол-во кабеля ${n + 1}`} type="number" min={1} value={w.qty || 1} onChange={(event) => update((current) => updateWire(current, w.idx, { qty: parseDecimal_(event.target.value) || 1 }))} />
            {anyCores && (hasCores ? <>
              <input aria-label={`Жил в кабеле ${n + 1}`} type="number" min={1} value={cores} onChange={(event) => update((current) => updateWire(current, w.idx, { cores: parseDecimal_(event.target.value) || 1 }))} />
              <UnitInput unit="мм" aria-label={`Разделка кабеля ${n + 1}`} type="number" min={1} value={w.jacket || JACKET_STRIP_MM_} onChange={(event) => update((current) => updateWire(current, w.idx, { jacket: event.target.value.trim() }))} />
            </> : <><span /><span /></>)}
            <button type="button" className="btn-remove" title="Удалить кабель" aria-label={`Удалить кабель ${n + 1}`} onClick={() => update((current) => ({ ...current, wires: current.wires.filter((x) => x.idx !== w.idx) }))}>×</button>
          </div>;
        })}
      </div>
      <button type="button" className="btn-add" onClick={() => update((current) => ({ ...current, wires: [...current.wires, { idx: current.nextWireIdx, compId: "", length: 0, qty: 1 }], nextWireIdx: current.nextWireIdx + 1 }))}>+ Добавить кабель</button>
    </section>
    {grapher && (() => {
      const h = grapher.data.harnesses[grapher.pick];
      const our = gen.getLengthAdjustment_();
      const byCut = grapherHasCorrections(h.groups.map((g) => ({ correctionFrom: Number(g.correction_from_mm), correctionTo: Number(g.correction_to_mm) })));
      return <section className="gsec full grapher-box" aria-label="Чертёж">
        <div className="gsec-head"><h3>Чертёж {grapher.data.project.designation || grapher.data.project.name}</h3>
          {grapher.data.harnesses.length > 1 ? <select aria-label="Жгут чертежа" value={grapher.pick} onChange={(event) => setGrapher({ ...grapher, pick: Number(event.target.value) })}>
            {grapher.data.harnesses.map((x, i) => <option key={i} value={i}>{x.designation} × {x.quantity}</option>)}</select>
            : <span className="gsec-meta">жгут {h.designation} × {h.quantity}</span>}
          <button className="primary small" onClick={grapherApply}>Подставить в «Кабели»</button>
          <button className="btn-link" onClick={() => setGrapher(null)}>скрыть</button></div>
        <div className="gtable-wrap"><table className="gtable"><thead><tr><th>Материал в чертеже</th><th>Позиция набора</th><th className="num">Шт</th><th>Длина</th><th>Концы</th></tr></thead><tbody>
          {h.groups.map((g, i) => <tr key={i}><td>{g.material || "—"}{g.cores > 1 ? ` · ${g.cores} жил${g.jacket_mm != null ? ", разделка " + g.jacket_mm : ""}` : ""}</td>
            <td>{g.component_id && gen.getCompById(g.component_id) ? compLabel(g.component_id) : <span className="tone-danger">не найдено — выбери</span>}</td>
            <td className="num">{g.quantity}</td>
            <td>{g.length_mm == null ? <span className="tone-danger">нет длины</span> : grapherLengthText(g)}{g.mixed_corrections ? " (поправки разные)" : ""}</td>
            <td className="muted">{g.ends.join("; ")}{g.quantity > g.ends.length ? " …" : ""}</td></tr>)}
        </tbody></table></div>
        <small className="muted">{byCut ? "Режем по длине реза чертежа (с поправками концов) — припуск L+/L− из БД.ТЕР не добавляется."
          : <>Припуск у нас: {our.parts.length ? `${our.parts.join(", ")} = ${our.total} мм` : "концы ещё не выбраны"} — длина реза считается по нему.</>} Разъёмы: {h.connectors.map((c) => `${c.designation} ${c.part_number}${c.terminals.length ? ` (терм. ${c.terminals.join(", ")})` : ""}`).join("; ") || "нет"}{h.pairs ? `. Свитых пар в чертеже: ${h.pairs} — включи свивку ниже.` : "."}</small>
        {[...h.warnings, ...(h.sheet_warnings ?? [])].map((item) => <div className="warning-inline" key={item}>{item}</div>)}
      </section>;
    })()}
    {sideSection("A")}
    {sideSection("B")}
    {(links.list.length > 0 || links.warnings.length > 0) && <section className="gsec full ep-links" aria-label="Соединения по распиновке">
      <div className="gsec-head"><h3>Соединения по распиновке</h3></div>
      {linkGroups(links.list).map((group) => <div key={group.key}>{group.title}: {group.pairs.map(([a, b], i) =>
        <span key={i}>{i ? ", " : ""}{a === b ? `${a}→${b}` : <b>{a}→{b}</b>}</span>)}</div>)}
      {links.warnings.map((item) => <div className="warning-inline" key={item}>{item}</div>)}
    </section>}
    <section className="gsec full gen-proc" aria-label="Обработка">
      <div className="gsec-head"><h3>Обработка</h3></div>
      <div className={ends.length || machine.length ? "proc-groups" : "proc-groups solo"}>
        {([["Концы", "ends", ends], ["Жгут", "harness", harness], ["Автомат при резке", "machine", machine]] as const).filter(([, , items]) => items.length > 0).map(([title, key, items]) =>
          <div className={`proc-group pg-${key}`} key={key}><h4>{title}</h4>{items}</div>)}
      </div>
      <MarkTable enabled={S.mark} onEnabled={(on) => update({ mark: on })}
        hint={kdMarks.length ? `${kdMarks.length} ${plural(kdMarks.length, "метка", "метки", "меток")} · ${markTotal(S.markRows)} шт. на изделие` : `бирки из состава: ${marks.map((m) => `${m.art || m.name} × ${m.qty}`).join("; ") || "бирок нет"}`}
        rows={S.markRows} onChange={(markRows) => update({ markRows })} kitMarks={marks} comps={D.components} productDesignation={card.item_index} mats={D.markMats || []}
        targets={markTargets(gen.markConnectors_(), active.map((w) => compLabel(w.compId)))}
        onByConnectors={marksByConnectors} onFromDrawing={() => void marksFromDrawing()} drawingBusy={markDrawing.busy} note={markDrawing.note} />
    </section>
  </>;

  const coaxSections = <>
    <section className="gsec" aria-label="Коакс-кабель">
      <div className="gsec-head"><h3>Коакс-кабель</h3></div>
      <div className="opt-grid">
        <Field label="Длина по чертежу"><UnitInput unit="мм" aria-label="Длина коакс-кабеля" type="number" min={1} value={S.coax.len} placeholder="—" onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, len: event.target.value } }))} /></Field>
        <div className="gfield span2"><span className="gfield-label">Экран (на сборку)</span>
          <Seg label="Тип экрана" value={S.coax.shield} options={[["обжим", "Обжим втулки"], ["пайка", "Пайка экрана"]]}
            onChange={(shield) => update((current) => ({ ...current, coax: { ...current.coax, shield } }))} /></div>
      </div>
      <Toggle checked={S.coax.tutEnabled} title="ТУТ на концах" onChange={(on) => update((current) => ({ ...current, coax: { ...current.coax, tutEnabled: on } }))}>
        <Field label="Длина ТУТ" className="short"><UnitInput unit="мм" type="number" min={1} value={S.coax.tutLen} placeholder="—" onChange={(event) => update((current) => ({ ...current, coax: { ...current.coax, tutLen: event.target.value } }))} /></Field>
      </Toggle>
    </section>
    <section className="gsec ep-side" aria-label="Сторона А"><div className="gsec-head"><h3>Сторона А</h3></div>{coaxSideBlock("A")}</section>
    <section className="gsec ep-side" aria-label="Сторона В"><div className="gsec-head"><h3>Сторона В</h3>{!S.coax.sideB && <span className="gsec-meta">разъёма нет — голый конец</span>}</div>
      {S.coax.sideB ? coaxSideBlock("B")
        : <Toggle checked={S.coax.stripB} title="Зачистка стороны В" hint="голый конец кабеля" onChange={(on) => update((current) => ({ ...current, coax: { ...current.coax, stripB: on } }))} />}
    </section>
  </>;

  return <div className="tcgen" {...draft.bind}>
    <div className="seg-tabs gen-modes" role="tablist" aria-label="Вид сборки">{TABS.map(([tab, label]) =>
      <button key={tab} type="button" role="tab" aria-selected={S.tab === tab} className={S.tab === tab ? "on" : ""}
        onClick={() => { setRouteError(""); update((current) => switchGenTab(current, tab)); }}>{label}</button>)}</div>

    {status.text && status.error && <div className="error-banner gen-error" role="alert" ref={errorBanner}>{status.text}</div>}

    <div className="gen-layout">
      <div className="gen-main"><div className="gen-grid">
        {sheet}
        {typical && typicalSections}
        {S.tab === "coax" && coaxSections}
        {manualTab && <AssemblyDrafts project={card.project} itemIndex={card.item_index} items={drafts.items} error={drafts.error}
          openId={draftId} builtId={builtId} onOpen={openDraft} onUploaded={() => void reloadDrafts()}
          onChanged={(changed) => setDrafts((current) => ({ ...current, items: current.items.map((item) => (item.id === changed.id ? changed : item)) }))} />}
        {manualTab && <ManualAssembly state={manual} onChange={(next) => { setRouteError(""); setManual(next); }} library={refs.library} opRecs={D.ops} components={components}
          partQty={partQty} finalName={finalName} error={routeError} kd={kdOffer} />}
      </div></div>

      {/* Итог (владелец 04.10): операции, схема и одна кнопка — справа и всегда на виду; на планшете — внизу. */}
      <aside className="gen-side" aria-label="Итог">
        <div className="gen-side-body">
          <div className="gen-side-head"><h3>Итог</h3><span className="gsec-meta">{summary}</span></div>
          {warning && <div className="warning-inline">{warning}</div>}
          {manualTab
            ? <ManualSummary state={manual} partQty={partQty} finalName={finalName} onSelect={(key) => setManual((current) => selectBlock(current, key))} />
            : <>
              <div className="op-list" role="group" aria-label="Операции">
                {ops.length > 0 && <div className="op-row head" aria-hidden="true"><span>№</span><span>Операция</span><span>Карточка</span>{OP_TIMES.map(([key, label]) => <span key={key}>{label}</span>)}</div>}
                {!ops.length && <div className="gempty">{S.tab === "coax" ? "Выберите разъём — появятся операции" : "Добавьте кабель — появятся операции"}</div>}
                {ops.map((spec, i) => opItem(spec, i + 1))}
              </div>
              <RouteEditor key="typical" ops={routeOps} finalName={finalName} quantity={docMinutes?.quantity ?? 1} value={routeOverride}
                onChange={(route) => { setRouteError(""); setRouteEdit({ route, sig: routeSig }); }} error={routeError}
                notice={routeStale ? "Операции изменились — схема собрана заново, ручные правки не применены." : ""}
                skipped={ops.filter((spec) => !routeSpecs.includes(spec)).map((spec) => ops.indexOf(spec) + 1)}
                renderOp={(position) => { const spec = routeSpecs[position - 1]; return spec ? opItem(spec, ops.indexOf(spec) + 1) : null; }} />
            </>}
          {/* Служебное (владелец 29.09) — свод материалов, удаление черновика, JSON — свёрнуто в «Ещё». */}
          <details className="gen-more"><summary>Ещё</summary>
            <Toggle checked={S.materialsSheet} title="Свод материалов отдельным листом в ЭТК" onChange={(on) => update({ materialsSheet: on })} />
            {card.document && card.status !== "Выдана" && <button className="secondary small" disabled={busy} onClick={() => void deleteGenerated()}>Удалить черновик ЭТК</button>}
            {more}
          </details>
        </div>
        <GeneratorDraftNote draft={draft} />
        {/* Одна кнопка (владелец 29.09): нет ТК — «Создать», ТК уже выдана — «Пересобрать». */}
        <div className="gen-foot">
          {status.text && !status.error ? <div className="gen-status">{status.text}</div>
            : <span className="gen-foot-sum">{summary}</span>}
          {!manualTab && <button className="secondary" disabled={busy || !ops.length} onClick={() => void editByHand()}>Поправить вручную</button>}
          <button className="primary" disabled={busy} onClick={() => void generate()}>{busy ? "Собираю…" : card.status === "Выдана" ? "Пересобрать" : "Создать"}</button>
        </div>
      </aside>
    </div>
  </div>;
}
